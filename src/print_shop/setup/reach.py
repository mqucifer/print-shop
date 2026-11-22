"""Whether a setting reaches the slice: the profile it's written to, and what Orca sliced with.

mqucifer/print-shop#206. Orca takes a filament's setting from the filament profile only: written
to the process, it's ignored without a word, and the slice keeps the filament's own value. So
one rule says which profile a key is written to (`profile_of`), for the review, the presets a
proposal reads and `apply`, and a key it can't place is refused (`unplaced`). Then each change
is read back from the G-code's config block (`not_sliced`): written isn't sliced.

How Orca 2.4.2 echoes a value there, from slices of the store's profiles with one setting
changed in each (`~/print-shop/proofs/m20-filament-keys/`):
- a percent-only setting given as a number is written as one (`20` is `20%`);
- a filament's override that's `nil` isn't written at all;
- `chamber_temperatures`, as the stored profiles name it, is written `chamber_temperature`,
  and it wins: `chamber_temperature` set beside it is sliced at its value;
- a value it won't take becomes another (`overhang_fan_threshold` 25 is sliced 95%, 30.5 on a
  fan is 30), and a key it no longer knows (`wall_infill_order`) isn't written: neither reached
  the slice.
"""

from __future__ import annotations

from typing import Any, Literal

from print_shop.setup.bounds import MATERIAL, SETTINGS
from print_shop.sliced import NOT_SETTINGS, PROFILE_IDS, same_setting

__all__ = ["Tier", "not_sliced", "profile_of", "unplaced"]

Tier = Literal["process", "filament"]

#: A preset's record of itself, which no correction sets.
_OWN_RECORD = NOT_SETTINGS | frozenset(PROFILE_IDS.values()) | {"description", "renamed_from"}
#: A stored key that Orca writes under another name in the config block.
_WRITTEN_AS = {"chamber_temperatures": "chamber_temperature"}


def profile_of(key: str, process: dict[str, Any], filament: dict[str, Any]) -> Tier | None:
    """The profile `key` is written to, or None when it can't be placed.

    In this order:
    1. the profile it's declared for: `bounds.SETTINGS` says, and `bounds.MATERIAL` is the
       filament's. A preset may leave a declared key out (Bambu PETG HF has no
       `additional_cooling_fan_speed`), and it's still that profile's;
    2. else the profile that has the key: the process's and the filament's share only their
       own records, which aren't settings;
    3. else it can't be placed. Nothing is the process's by default.
    """
    if key in _OWN_RECORD:
        return None
    setting = SETTINGS.get(key)
    if setting is not None and not setting.filament:
        return "process"
    if setting is not None or key in MATERIAL:
        return "filament" if filament else None
    if key in filament:
        return None if key in process else "filament"
    return "process" if key in process else None


def unplaced(key: str, process: dict[str, Any], filament: dict[str, Any]) -> str:
    """Why `key` can't be placed in a profile, in words, or '' when it can."""
    if profile_of(key, process, filament) is not None:
        return ""
    if key in _OWN_RECORD:
        return f"'{key}' is a preset's record of itself, not a setting"
    if key in SETTINGS or key in MATERIAL:
        return f"'{key}' is a filament setting, and this print has no filament preset"
    if key in filament and key in process:
        return f"'{key}' is in both the filament and the process preset, so it can't be placed"
    return f"'{key}' isn't a setting in the filament or the process preset"


def not_sliced(
    key: str, value: str, process: dict[str, Any], filament: dict[str, Any],
    config: dict[str, str] | None,
) -> str:
    """Why `key=value` didn't reach the slice, as asked and sliced, or '' when it did.

    `process` and `filament` are the presets it was written over, and `config` is the sliced
    G-code's config block, None where that couldn't be read: nothing unread is said to be sliced.
    """
    tier = profile_of(key, process, filament)
    if tier is None:
        return f"asked {value}, not written: {unplaced(key, process, filament)}"
    if config is None:
        return f"asked {value}, and the slice's settings couldn't be read"
    written = _WRITTEN_AS.get(key, key)
    if written not in config:
        if tier == "filament" and value == "nil":
            return ""
        return f"asked {value}, and the slice's settings don't have it"
    sliced = config[written]
    if same_setting(key, value, sliced):
        return ""
    if sliced.endswith("%") and same_setting(key, f"{value}%", sliced):
        return ""
    return f"asked {value}, sliced {sliced}"
