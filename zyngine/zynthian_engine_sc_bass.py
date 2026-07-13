# -*- coding: utf-8 -*-
# ******************************************************************************
# ZYNTHIAN PROJECT: Zynthian Engine (zynthian_engine_sc_bass)
#
# Second, independent SuperCollider chain — a dedicated bass instrument (the
# \bass8 SynthDef) so it can sound simultaneously with the "SC" engine's
# slicer/subtractive1 chain instead of sharing its single scsynth/JACK
# client/OSC port/~zynActivePatch.
#
# Purpose-built, not generic: this is a second concrete engine class, not an
# attempt to make zynthian_engine_supercollider handle arbitrary SynthDefs or
# arbitrary numbers of concurrent chains. Points at a SEPARATE persistent
# sclang process (launched by supercollider-bass.service as
# `SC_ZYN_INSTANCE=bass sclang -u 57121`) with its own scsynth (port 57111)
# and its own JACK client ("SCBass" — deliberately NOT a superstring/substring
# of "SuperCollider": zynautoconnect wires processor audio via
# jclient.get_ports(processor.get_jackname(True), ...), and JACK's get_ports
# treats that name as an unanchored regex, so an overlapping name would pull
# the wrong client's ports into the wrong chain).
#
# All of the actual OSC/MIDI/preset plumbing (and its hard-won gotchas —
# preset_favs, controller-screen pagination, the is_toggle Boolean/OSC
# gotcha, etc.) lives in the base class and is reused unchanged; only the
# per-instance identity (name/jackname/OSC port/preset root) differs, via the
# class attributes zynthian_engine_supercollider defines specifically to be
# overridden this way — see that file's class docstring/comments for why
# these must be class attributes rather than set after super().__init__().
# ******************************************************************************

from zyngine.zynthian_engine import zynthian_engine
from zyngine.zynthian_engine_supercollider import zynthian_engine_supercollider


class zynthian_engine_sc_bass(zynthian_engine_supercollider):

    root_bank_dirs = [
        ('User', zynthian_engine.my_data_dir + "/presets/sc_bass")
    ]

    SC_OSC_PORT = 57121

    ENGINE_NAME = "SuperCollider Bass"
    ENGINE_NICKNAME = "SB"
    JACKNAME = "SCBass"

    # SC's ALSA MIDI client name is fixed to "SuperCollider" regardless of
    # JACKNAME (see base class's MIDI_CLIENTID_FILE comment) — this process's
    # own client ID is written here by startup.scd's SC_ZYN_INSTANCE=="bass"
    # branch, distinct from the original instance's file.
    MIDI_CLIENTID_FILE = "/tmp/scbass_midi_clientid"
