# zynthian-sc-engine

Two [Zynthian](https://zynthian.org/) engines that make persistent [SuperCollider](https://supercollider.github.io/) sessions behave like real synth engines: visible on the touchscreen as chains, with MIDI-learnable knobs and MIDI note-in — not just raw audio wired into a mixer channel.

`zynthian_engine_supercollider` ("SC") is the original engine (folder-picker slicer + a subtractive test patch, switchable presets within one persistent sclang/scsynth session). `zynthian_engine_sc_bass` ("SB") is a second, independent engine — a dedicated bass instrument (`\bass8`) — pointed at its OWN persistent sclang/scsynth session, so it can sound simultaneously with "SC" instead of sharing its one scsynth/JACK client/OSC port. Deliberately two purpose-built engines, not a generic arbitrary-SynthDef-as-preset system — see "Running two concurrent chains" below.

Unlike every other Zynthian engine (including Pd), neither of these spawns its own subprocess. SuperCollider (`sclang`/`scsynth`) is expected to already be running, booted independently by your own SC startup script. This keeps live-coded Ndef/Pbind state alive across adding/removing a chain, at the cost of a few behavioral differences from a normal engine — see "How it works" below.

This repo is deliberately separate from both `zynthian-ui` (the third-party Zynthian UI project) and your own SuperCollider project — it holds only the Zynthian-integration code (the Python engine classes and preset `zynconfig.yml` files).

## Layout

```
zyngine/
  zynthian_engine_supercollider.py   — the "SC" engine class
  zynthian_engine_sc_bass.py         — the "SB" engine class (subclasses the above)
presets/sc/
  <BankName>/<presetName>/zynconfig.yml   — "SC" presets, one directory per preset
presets/sc_bass/
  <BankName>/<presetName>/zynconfig.yml   — "SB" presets (own tree — see Gotchas)
```

## Installation

Symlink all four pieces into place (don't copy — keeps this repo as the single source of truth):

```sh
ln -s /path/to/zynthian-sc-engine/zyngine/zynthian_engine_supercollider.py \
      /zynthian/zynthian-ui/zyngine/zynthian_engine_supercollider.py
ln -s /path/to/zynthian-sc-engine/zyngine/zynthian_engine_sc_bass.py \
      /zynthian/zynthian-ui/zyngine/zynthian_engine_sc_bass.py

ln -s /path/to/zynthian-sc-engine/presets/sc \
      /zynthian/zynthian-my-data/presets/sc
ln -s /path/to/zynthian-sc-engine/presets/sc_bass \
      /zynthian/zynthian-my-data/presets/sc_bass
```

Then register both engines in `zynthian-ui` itself (four small edits to that third-party repo per engine code — not included here, since they touch code this repo doesn't own; not tracked by this repo's own git history since `zynthian-ui` isn't a submodule — re-apply by hand on every card/image this is installed onto):

1. `zyngine/__init__.py` — add `"zynthian_engine_supercollider"` / `"zynthian_engine_sc_bass"` to `__all__`, and the matching `from zyngine.<module> import *` lines.
2. `zyngine/zynthian_chain_manager.py` — add `'SC': zynthian_engine_supercollider` and `'SB': zynthian_engine_sc_bass` to `engine2class`, and both `"SC"` and `"SB"` to `single_processor_engines` (each persistent sclang process backs at most one chain of its own engine code — see "Running two concurrent chains").
3. `zyngine/zynthian_lv2.py` — add `"SC"` and `"SB"` entries to `standalone_engine_info` (this is what actually makes each appear in the touchscreen's "Add Instrument Chain" list — see Gotchas).

Then regenerate `/zynthian/config/engine_config.json` (delete it to force a rebuild on next boot, or add both entries directly — see Gotchas) and restart the Zynthian UI service:

```sh
systemctl restart zynthian.service
```

## Running two concurrent chains

`single_processor_engines` in `zynthian_chain_manager.py` hides an engine code from the "Add Instrument Chain" list once one processor of that code already exists — so getting two SIMULTANEOUS SC-based chains isn't just a matter of the Python class being instantiable twice, it requires two distinct engine codes (`"SC"` and `"SB"`), each individually single-instance, each pointed at its own persistent sclang/scsynth process. The SC side: `supercollider` repo's `startup.scd` boots the "SC" process's session as before, and a second `SC_ZYN_INSTANCE=bass sclang -u 57121` process (see `supercollider-bass.service`) for "SB" — see that repo's `startup.scd` and `0_startup/_includes/_zyn-patches/bass8-patch.scd`.

## How it works

- **Audio**: each engine's `jackname` is fixed (`"SuperCollider"` for SC, `"SCBass"` for SB — not generated via `get_next_jackname()`, since each is a single persistent scsynth process, not one-per-chain-instance). Once a chain exists, `zynautoconnect` wires `<jackname>:out_1/2` into that chain's `zynmixer` input automatically. **Naming gotcha**: `zynautoconnect` matches processor audio ports via `jclient.get_ports(processor.get_jackname(True), ...)`, and JACK's `get_ports` treats that name as an **unanchored regex** — so a new engine's jackname must not be a substring of `"SuperCollider"` (or vice versa), or its ports get pulled into the wrong chain. This is exactly why `"SCBass"` was chosen over e.g. `"SuperColliderBass"`.
- **MIDI**: `jackname_midi` is refined at engine-instantiation time by scanning `/proc/asound/seq/clients` for that instance's own ALSA client (keyed off `self.jackname`, so SC and SB each find their own), targeting its `in0` port specifically (regex-anchored) — `zynautoconnect` then wires `ZynMidiRouter` into that one port, leaving `in1`-`in4` free for anything else in that same sclang session.
- **Knobs**: each preset's `zynconfig.yml` declares controllers with an `osc_path`. Turning a knob triggers `zynthian_controller.send_value()`, which (since neither engine overrides `send_controller_value()`) falls back to its own generic `liblo.send(osc_target, osc_path, value)` — one OSC message per parameter, e.g. `/scengine/cutoff 500.0`, sent to that engine's own `osc_target_port` (57120 for SC, 57121 for SB).
- **Presets**: same `zynconfig.yml` schema `zynthian_engine_puredata` uses (`ctrl_group: {name: {value, value_min, value_max, osc_path, ...}}`), so any Pd-preset knowledge mostly transfers.
- **Subclassing**: `zynthian_engine_sc_bass` only overrides class attributes (`ENGINE_NAME`, `ENGINE_NICKNAME`, `JACKNAME`, `SC_OSC_PORT`, `root_bank_dirs`) — never re-runs or re-derives `osc_init()`/`_refresh_midi_jackname()` itself. Those are read as class attributes (not literals) inside the base `__init__` specifically so overriding them takes effect before `osc_init()`/`_refresh_midi_jackname()` run — `osc_init()` no-ops on any call after the first, so setting `self.osc_target_port` AFTER calling `super().__init__()` would silently not take effect.

## Gotchas (found by live debugging — read before changing either engine class)

- **`engine.start()` is never called** by `zynthian_chain_manager`/`zynthian_processor` — every engine that needs setup-on-start calls it itself (Pd does, from inside its own `set_preset()`, as part of restarting its subprocess). Since these engines have no subprocess, nothing calls `start()` for them — so `osc_init()` and the MIDI-jackname refresh both happen directly in `__init__` instead, which is guaranteed to run exactly once per persistent instance.
- **`preset_favs` crashes if left `None`.** `zynthian_processor.set_preset()` unconditionally does `if preset_id in self.engine.preset_favs:` — but that dict is only lazily initialized by `load_bank_list()`'s favorites-check, which the UI's single-bank shortcut (most presets here will only have one bank) skips entirely. Fixed by initializing `self.preset_favs = {}` directly in `__init__`.
- **`set_preset()` must call `processor.refresh_controllers()` itself** (same as Pd does) — the calling UI code does not do this after preset selection. Without it, the control screen is built once at chain-creation time (before any preset is picked) and never rebuilt, so knobs never appear even though the preset "loads" fine.
- **Preset directories need two levels of depth**: `presets/sc/<Bank>/<preset>/zynconfig.yml`, matching Pd's own `presets/puredata/<bank>/<preset>/` layout. A single-level `presets/sc/<preset>/zynconfig.yml` gets silently misread as an empty bank.
- **`root_bank_dirs` is per-engine on purpose**: SB uses its own `presets/sc_bass` tree rather than sharing `presets/sc` with SC — sharing would list SC's Slicer/subtractive1 presets under the SB chain too, and selecting one would silently do nothing (it'd try to `~zynLoadPatch` a patch name that was never loaded into the SB sclang process at all).
- **The touchscreen's engine list is a separate cache** (`/zynthian/config/engine_config.json`, generated from `zynthian_lv2.py`'s `standalone_engine_info`) — distinct from `zynthian_chain_manager.py`'s `engine2class`. Registering an engine in `engine2class` alone is not sufficient for it to show up anywhere on the touchscreen.

## SC-side counterpart

The actual SuperCollider code these engines talk to — the OSC/MIDI bridge and instrument patches — lives in the companion SuperCollider project, under `0_startup/_includes/_zyn-patches/`, loaded from that project's own `startup.scd`. See `0_startup/reference/ref_zynthian_sc_engine.scd` there for the full write-up, including how to add new patches/presets.
