# -*- coding: utf-8 -*-
# ******************************************************************************
# ZYNTHIAN PROJECT: Zynthian Engine (zynthian_engine_supercollider)
#
# zynthian_engine implementation for a persistent SuperCollider (SC) session.
#
# Unlike every other engine here, this one does NOT spawn its own process.
# SC (sclang + scsynth) is booted independently, once, at system boot by the
# SC repo's own 0_startup/startup.scd (zynthian host branch) — this keeps
# Ndef/Pbind live-coding state alive across chain add/remove/preset-switch
# instead of restarting audio each time, matching how the SC side of this
# integration is actually used. Consequence: self.command stays None (the
# base zynthian_basic_engine never gets a subprocess to manage), and
# stop()/removing the chain does NOT stop sound — only osc_end() runs.
#
# See: /root/Music/supercollider/0_startup/_includes/_zyn-patches/ (SC side)
# and 0_startup/startup.scd's `if(hostname == "zynthian")` branch.
# ******************************************************************************

import os
import logging
import liblo

from zyngine.zynthian_engine import zynthian_engine
from zyngine.zynthian_controller import zynthian_controller

try:
    import oyaml as yaml
except ImportError:
    import yaml

# ------------------------------------------------------------------------------
# SuperCollider Engine Class
# ------------------------------------------------------------------------------


class zynthian_engine_supercollider(zynthian_engine):

    # ---------------------------------------------------------------------------
    # Config variables
    # ---------------------------------------------------------------------------

    # Presets are directories (zynconfig.yml + the real patch code lives in the
    # SC repo, not here) — same shape as zynthian_engine_puredata's preset dirs,
    # minus a loadable "main_file" (SC's patch is already active in the running
    # session; there is nothing this engine needs to open/spawn per preset).
    root_bank_dirs = [
        ('User', zynthian_engine.my_data_dir + "/presets/sc")
    ]
    preset_fexts = []

    SC_OSC_PORT = 57120  # sclang's default OSC-in port

    # ---------------------------------------------------------------------------
    # Initialization
    # ---------------------------------------------------------------------------

    def __init__(self, state_manager=None):
        super().__init__(state_manager)

        self.type = "MIDI Synth"
        self.name = "SuperCollider"
        self.nickname = "SC"

        # Fixed, NOT get_next_jackname()-generated: scsynth is a single
        # persistent process whose real JACK client name is always literally
        # "SuperCollider" (set via s.options.device in startup.scd), not an
        # engine-spawned-per-instance name that needs a "-01" disambiguator.
        self.jackname = "SuperCollider"
        self.jackname_midi = "SuperCollider"  # refined once ALSA client is found

        self.osc_target_port = self.SC_OSC_PORT
        self.command = None  # persistent service — see module docstring

        self.preset = ""
        self.preset_config = None
        self.zctrl_config = None

        # Base class leaves this None until zynthian_processor.load_bank_list()
        # lazily initializes it via get_preset_favs() — but that only runs when
        # a processor actually walks the multi-bank list screen. Our engine has
        # exactly one bank, so the UI's single-bank shortcut skips straight to
        # preset selection, and zynthian_processor.set_preset() (line ~369)
        # unconditionally does `if preset_id in self.engine.preset_favs:` —
        # crashing with a TypeError on None before our own set_preset() ever
        # runs. Confirmed by reproducing the exact crash against the real
        # zynthian_processor class. Starting with an empty dict sidesteps the
        # ordering assumption entirely.
        self.preset_favs = {}

        self.reset()

        # zynthian_chain_manager never calls engine.start() anywhere — every
        # other engine that needs it (e.g. Pd) calls it itself from inside its
        # own set_preset(), as part of restarting its subprocess. We have no
        # subprocess, but we still need osc_init() to actually run (it sets up
        # self.osc_target, which zynthian_controller.send_value() sends knob
        # changes to) and the MIDI jackname to get refined — so do both here,
        # unconditionally, since __init__ runs exactly once per persistent
        # instance and nothing else is guaranteed to call start() for us.
        self.osc_init()
        self._refresh_midi_jackname()

    def get_jackname(self):
        return self.jackname_midi

    # ---------------------------------------------------------------------------
    # Process lifecycle — SC is already running; just (re)connect OSC.
    # ---------------------------------------------------------------------------

    def start(self):
        self.osc_init()
        self._refresh_midi_jackname()
        return None

    def stop(self):
        self.osc_end()

    def _refresh_midi_jackname(self):
        """Find SC's ALSA MIDI client uid so zynautoconnect wires
        ZynMidiRouter into its 'in0' port specifically — in1-in4 stay free
        for other MIDI uses in the same sclang session. Scanned once here
        (not per preset-switch, unlike Pd): SC's process persists for the
        box's uptime, so its ALSA client number is stable once found."""
        try:
            with open("/proc/asound/seq/clients", "r") as f:
                for line in f.readlines():
                    if line.startswith("Client") and '"SuperCollider" [User Legacy]' in line:
                        uid = int(line[7:10])
                        self.jackname_midi = f"SuperCollider \\[{uid}\\] \\(playback\\): in0$"
                        logging.debug(f"SC MIDI jackname => \"{self.jackname_midi}\"")
                        return
            logging.warning("Can't find SC's ALSA MIDI client — is sclang running?")
        except Exception as e:
            logging.error(f"Can't scan ALSA MIDI clients => {e}")

    # ----------------------------------------------------------------------------
    # Bank Management
    # ----------------------------------------------------------------------------

    def get_bank_list(self, processor=None):
        return self.get_bank_dirlist(recursion=2)

    def set_bank(self, processor, bank):
        return True

    # ----------------------------------------------------------------------------
    # Preset Management
    # ----------------------------------------------------------------------------

    def get_preset_list(self, bank, processor=None):
        return self.get_dirlist(bank[0])

    def set_preset(self, processor, preset, preload=False):
        # No process to (re)spawn — SC's patch code is already loaded by
        # startup.scd (every patch file registers itself into ~zynPatches at
        # boot). Loading the yml here refreshes this preset's zctrl
        # definitions (get_controllers_dict, below); the OSC message below
        # is what actually tells the already-running SC session which
        # patch's ~zynPatchNoteOn/params should be live — the preset
        # directory's own name IS the ~zynPatches key (e.g. presets/sc/
        # Basic/subtractive1 -> "subtractive1" -> ~zynPatches[\subtractive1]).
        self.load_preset_config(preset)
        self.preset = preset[0]
        if self.osc_target:
            patch_name = os.path.basename(preset[0])
            liblo.send(self.osc_target, "/scengine/loadpatch", patch_name)
        # zynthian_engine_puredata.set_preset does this itself too (doesn't rely
        # on the calling UI code to do it) — confirmed via live debug logging
        # that without this, get_controllers_dict only ever runs ONCE, before
        # preset selection (zctrl_config still None then), and never again:
        # the control screen was built empty and never rebuilt.
        processor.refresh_controllers()
        return True

    def load_preset_config(self, preset):
        config_fpath = preset[0] + "/zynconfig.yml"
        try:
            with open(config_fpath, "r") as fh:
                yml = fh.read()
                self.preset_config = yaml.load(yml, Loader=yaml.SafeLoader)
                self.zctrl_config = {}
                if self.preset_config:
                    for ctrl_group, ctrl_dict in self.preset_config.items():
                        if isinstance(ctrl_dict, dict):
                            self.zctrl_config[ctrl_group] = ctrl_dict
                    return True
                logging.error(f"Preset config '{config_fpath}' is empty.")
                return False
        except Exception as e:
            logging.error(f"Can't load preset config '{config_fpath}': {e}")
            return False

    def cmp_presets(self, preset1, preset2):
        try:
            return preset1[0] == preset2[0] and preset1[2] == preset2[2]
        except Exception:
            return False

    # ----------------------------------------------------------------------------
    # Controllers Management
    # ----------------------------------------------------------------------------

    def get_controllers_dict(self, processor):
        """zynconfig.yml-driven, same shape as zynthian_engine_puredata's
        implementation. No send_controller_value() override needed: as long
        as each option dict includes 'osc_path', zynthian_controller.send_value()
        (zyngine/zynthian_controller.py) already does
        liblo.send(self.osc_target, self.osc_path, self.get_ctrl_osc_val())
        generically whenever engine.send_controller_value() isn't implemented."""
        zctrls = {}
        self._ctrl_screens = []
        if self.zctrl_config:
            for ctrl_group, ctrl_dict in self.zctrl_config.items():
                # Chunk into screens of at most 4 controllers each — the
                # touchscreen shows 4 knobs per page with no scroll/pagination
                # within a single screen, so a 5th+ controller in one
                # unchunked screen is simply invisible. Screen naming matches
                # zynthian_engine_puredata's convention: the bare group name
                # if it fits on one screen, "<group>#1", "<group>#2", ... if
                # it needs more than one.
                screen_num = 1
                ctrl_set = []
                for name, options in ctrl_dict.items():
                    try:
                        if len(ctrl_set) >= 4:
                            self._ctrl_screens.append([f"{ctrl_group}#{screen_num}", ctrl_set])
                            ctrl_set = []
                            screen_num += 1
                        if isinstance(options, int):
                            options = {'midi_cc': options}
                        if 'midi_chan' not in options:
                            options['midi_chan'] = processor.midi_chan
                        options['name'] = name.replace('_', ' ')
                        options['processor'] = processor
                        zctrl = zynthian_controller(self, name, options)
                        zctrls[name] = zctrl
                        ctrl_set.append(name)
                    except Exception as e:
                        logging.error(f"Building controller '{name}': {e}")
                if ctrl_set:
                    screen_title = f"{ctrl_group}#{screen_num}" if screen_num > 1 else ctrl_group
                    self._ctrl_screens.append([screen_title, ctrl_set])

        if zctrls:
            processor.controllers_dict = zctrls
        else:
            zctrls = super().get_controllers_dict(processor)
        return zctrls

# ******************************************************************************
