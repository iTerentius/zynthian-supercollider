# zynthian-sc-engine

A [Zynthian](https://zynthian.org/) engine that makes a persistent [SuperCollider](https://supercollider.github.io/) session behave like a real synth engine: visible on the touchscreen as a chain, with MIDI-learnable knobs and MIDI note-in — not just raw audio wired into a mixer channel.

Unlike every other Zynthian engine (including Pd), this one does **not** spawn its own subprocess. SuperCollider (`sclang`/`scsynth`) is expected to already be running, booted independently by your own SC startup script. This keeps live-coded Ndef/Pbind state alive across adding/removing the chain, at the cost of a few behavioral differences from a normal engine — see "How it works" below.

This repo is deliberately separate from both `zynthian-ui` (the third-party Zynthian UI project) and your own SuperCollider project — it holds only the Zynthian-integration code (the Python engine class and preset `zynconfig.yml` files).

## Layout

```
zyngine/
  zynthian_engine_supercollider.py   — the engine class
presets/sc/
  <BankName>/<presetName>/zynconfig.yml   — one directory per preset
```

## Installation

Symlink both pieces into place (don't copy — keeps this repo as the single source of truth):

```sh
ln -s /path/to/zynthian-sc-engine/zyngine/zynthian_engine_supercollider.py \
      /zynthian/zynthian-ui/zyngine/zynthian_engine_supercollider.py

ln -s /path/to/zynthian-sc-engine/presets/sc \
      /zynthian/zynthian-my-data/presets/sc
```

Then register the engine in `zynthian-ui` itself (three small edits to that third-party repo — not included here, since they touch code this repo doesn't own):

1. `zyngine/__init__.py` — add `"zynthian_engine_supercollider"` to `__all__`, and `from zyngine.zynthian_engine_supercollider import *`.
2. `zyngine/zynthian_chain_manager.py` — add `'SC': zynthian_engine_supercollider` to `engine2class`, and `"SC"` to `single_processor_engines` (only one persistent SC process can exist).
3. `zyngine/zynthian_lv2.py` — add an `"SC"` entry to `standalone_engine_info` (this is what actually makes it appear in the touchscreen's "Add Instrument Chain" list — see Gotchas).

Then regenerate `/zynthian/config/engine_config.json` (delete it to force a rebuild on next boot, or add the `"SC"` entry directly — see Gotchas) and restart the Zynthian UI service:

```sh
systemctl restart zynthian.service
```

## How it works

- **Audio**: the engine's `jackname` is fixed to `"SuperCollider"` (not generated via `get_next_jackname()`, since there's only ever one persistent scsynth process, not one-per-chain-instance). Once the chain exists, `zynautoconnect` wires `SuperCollider:out_1/2` into the chain's `zynmixer` input automatically.
- **MIDI**: `jackname_midi` is refined at engine-instantiation time by scanning `/proc/asound/seq/clients` for SC's own ALSA client, targeting its `in0` port specifically (regex-anchored) — `zynautoconnect` then wires `ZynMidiRouter` into that one port, leaving `in1`-`in4` free for anything else in the same SC session.
- **Knobs**: each preset's `zynconfig.yml` declares controllers with an `osc_path`. Turning a knob triggers `zynthian_controller.send_value()`, which (since this engine doesn't override `send_controller_value()`) falls back to its own generic `liblo.send(osc_target, osc_path, value)` — one OSC message per parameter, e.g. `/scengine/cutoff 500.0`.
- **Presets**: same `zynconfig.yml` schema `zynthian_engine_puredata` uses (`ctrl_group: {name: {value, value_min, value_max, osc_path, ...}}`), so any Pd-preset knowledge mostly transfers.

## Gotchas (found by live debugging — read before changing the engine class)

- **`engine.start()` is never called** by `zynthian_chain_manager`/`zynthian_processor` — every engine that needs setup-on-start calls it itself (Pd does, from inside its own `set_preset()`, as part of restarting its subprocess). Since this engine has no subprocess, nothing calls `start()` for it — so `osc_init()` and the MIDI-jackname refresh both happen directly in `__init__` instead, which is guaranteed to run exactly once per persistent instance.
- **`preset_favs` crashes if left `None`.** `zynthian_processor.set_preset()` unconditionally does `if preset_id in self.engine.preset_favs:` — but that dict is only lazily initialized by `load_bank_list()`'s favorites-check, which the UI's single-bank shortcut (most presets here will only have one bank) skips entirely. Fixed by initializing `self.preset_favs = {}` directly in `__init__`.
- **`set_preset()` must call `processor.refresh_controllers()` itself** (same as Pd does) — the calling UI code does not do this after preset selection. Without it, the control screen is built once at chain-creation time (before any preset is picked) and never rebuilt, so knobs never appear even though the preset "loads" fine.
- **Preset directories need two levels of depth**: `presets/sc/<Bank>/<preset>/zynconfig.yml`, matching Pd's own `presets/puredata/<bank>/<preset>/` layout. A single-level `presets/sc/<preset>/zynconfig.yml` gets silently misread as an empty bank.
- **The touchscreen's engine list is a separate cache** (`/zynthian/config/engine_config.json`, generated from `zynthian_lv2.py`'s `standalone_engine_info`) — distinct from `zynthian_chain_manager.py`'s `engine2class`. Registering an engine in `engine2class` alone is not sufficient for it to show up anywhere on the touchscreen.

## SC-side counterpart

The actual SuperCollider code this engine talks to — the OSC/MIDI bridge and instrument patches — lives in the companion SuperCollider project, under `0_startup/_includes/_zyn-patches/`, loaded from that project's own `startup.scd`. See `0_startup/reference/ref_zynthian_sc_engine.scd` there for the full write-up, including how to add new patches/presets.
