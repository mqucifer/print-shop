"""A setting by its plain name, found by plain words, and a wrong name said for what it is.

mqucifer/print-shop#222. The review took Orca's keys only: the Sponsor had to leave it to ask
for `overhang_fan_speed`, and `enable_overhang_bridge_fan`, a real Orca setting in the slice's
config block, was refused as if it weren't one. So:

- **Each setting has a plain name** (`PLAIN`), with its unit. Every key `bounds.SETTINGS` and
  `bounds.MATERIAL` name has one, and so do Orca's others that a correction is likely to name.
  Any other key is said as its words (`plain`). The key goes beside or behind the name
  wherever a setting is shown, as it's the key that's typed.
- **Plain words find settings, with no model** (`find`): "bridge fan" lists the settings this
  print's filament and process take that fit the words, best first, each with its plain name,
  today's value and its key. Finding changes nothing: a change is typed as a correction, and
  read back from the slice as one is.
- **A key that can't be used says which case it is** (`refused`): not an Orca setting, with
  the nearest ones this print takes; or a real Orca setting (the slice's config block has it)
  that this print's presets don't list, with the one that does that job here, where it's known
  (`STANDS_IN`). The other cases are `reach.unplaced`'s.
- **A setting that takes words says them** (mqucifer/print-shop#279), where the settings table
  holds every word Orca takes for it (`bounds.Setting.orcas`): beside it as the nearest, and
  under it in `find`, with the line that turns it off where one does. A correction to another
  word is refused before anything slices (`not_a_choice`). The table is the one source: the
  shop's bound on the model's proposals (`support_type`'s tree only) is never said as Orca's.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from print_shop.setup.bounds import MATERIAL, SETTINGS
from print_shop.setup.reach import profile_of, unplaced
from print_shop.sliced import NOT_SETTINGS, PROFILE_IDS

__all__ = ["PLAIN", "STANDS_IN", "Found", "choices", "either", "find", "named", "not_a_choice",
           "off", "plain", "refused", "switched", "turns_off", "typed", "with_unit"]

_PERCENT, _SPEED, _TEMP, _MM, _S = "%", " mm/s", " °C", " mm", " s"
#: A switch's "unit": its 0 and 1 are shown off and on, and may be typed so (`typed`).
_SWITCH = "on/off"

#: Each setting's plain name and the unit its value is in ("" for a count or a choice).
PLAIN: dict[str, tuple[str, str]] = {
    # The process's, that the proposal may change (`bounds.SETTINGS`).
    "layer_height": ("Layer height", _MM),
    "wall_loops": ("Walls", ""),
    "top_shell_layers": ("Top layers", ""),
    "bottom_shell_layers": ("Bottom layers", ""),
    "sparse_infill_density": ("Infill density", _PERCENT),
    "sparse_infill_pattern": ("Infill pattern", ""),
    "enable_support": ("Supports", _SWITCH),
    "support_type": ("Support type", ""),
    "support_threshold_angle": ("Steepest overhang without support", "°"),
    "brim_type": ("Brim", ""),
    "outer_wall_speed": ("Outer wall speed", _SPEED),
    "inner_wall_speed": ("Inner wall speed", _SPEED),
    "sparse_infill_speed": ("Infill speed", _SPEED),
    "elefant_foot_compensation": ("Elephant foot", _MM),
    "ironing_type": ("Ironing", ""),
    "seam_position": ("Seam position", ""),
    # The filament's (`bounds.SETTINGS` and `bounds.MATERIAL`).
    "nozzle_temperature": ("Nozzle temperature", _TEMP),
    "nozzle_temperature_initial_layer": ("Nozzle temperature, first layer", _TEMP),
    "fan_max_speed": ("Part fan, fastest", _PERCENT),
    "fan_min_speed": ("Part fan, slowest", _PERCENT),
    "additional_cooling_fan_speed": ("Side fan", _PERCENT),
    "during_print_exhaust_fan_speed": ("Exhaust fan while printing", _PERCENT),
    "complete_print_exhaust_fan_speed": ("Exhaust fan once printed", _PERCENT),
    "overhang_fan_speed": ("Fan on bridges and overhangs", _PERCENT),
    "overhang_fan_threshold": ("Overhang that turns the bridge fan on", _PERCENT),
    "close_fan_the_first_x_layers": ("Fan off for the first layers", ""),
    "full_fan_speed_layer": ("Layer the fan is full by", ""),
    "slow_down_for_layer_cooling": ("Slowing for layer cooling", _SWITCH),
    "slow_down_layer_time": ("Least time a layer takes", _S),
    "slow_down_min_speed": ("Slowest a layer slows to", _SPEED),
    "chamber_temperature": ("Chamber temperature", _TEMP),
    "textured_plate_temp": ("Bed, textured plate", _TEMP),
    "textured_plate_temp_initial_layer": ("Bed, textured plate, first layer", _TEMP),
    "cool_plate_temp": ("Bed, cool plate", _TEMP),
    "cool_plate_temp_initial_layer": ("Bed, cool plate, first layer", _TEMP),
    "hot_plate_temp": ("Bed, smooth PEI plate", _TEMP),
    "hot_plate_temp_initial_layer": ("Bed, smooth PEI plate, first layer", _TEMP),
    "eng_plate_temp": ("Bed, engineering plate", _TEMP),
    "eng_plate_temp_initial_layer": ("Bed, engineering plate, first layer", _TEMP),
    "supertack_plate_temp": ("Bed, SuperTack plate", _TEMP),
    "supertack_plate_temp_initial_layer": ("Bed, SuperTack plate, first layer", _TEMP),
    # Orca's others a correction is likely to name.
    "chamber_temperatures": ("Chamber temperature", _TEMP),
    "enable_overhang_bridge_fan": ("Bridge fan", _SWITCH),
    "fan_cooling_layer_time": ("Fan speeds up below this layer time", _S),
    "reduce_fan_stop_start_freq": ("Fewer fan stops and starts", _SWITCH),
    "filament_flow_ratio": ("Flow ratio", ""),
    "filament_max_volumetric_speed": ("Most plastic a second", " mm³/s"),
    "filament_retraction_length": ("Retraction length", _MM),
    "filament_retraction_speed": ("Retraction speed", _SPEED),
    "filament_z_hop": ("Z hop", _MM),
    "bridge_speed": ("Bridge speed", _SPEED),
    "bridge_flow": ("Bridge flow", ""),
    "internal_bridge_flow": ("Inner bridge flow", ""),
    "brim_width": ("Brim width", _MM),
    "brim_object_gap": ("Gap between brim and part", _MM),
    "skirt_loops": ("Skirt loops", ""),
    "initial_layer_speed": ("First layer speed", _SPEED),
    "initial_layer_infill_speed": ("First layer infill speed", _SPEED),
    "top_surface_speed": ("Top surface speed", _SPEED),
    "internal_solid_infill_speed": ("Solid infill speed", _SPEED),
    "gap_infill_speed": ("Gap fill speed", _SPEED),
    "travel_speed": ("Travel speed", _SPEED),
    "support_speed": ("Support speed", _SPEED),
    "support_interface_speed": ("Support interface speed", _SPEED),
    "ironing_speed": ("Ironing speed", _SPEED),
    "ironing_flow": ("Ironing flow", _PERCENT),
    "ironing_spacing": ("Ironing line spacing", _MM),
    "wall_sequence": ("Wall order", ""),
    "wall_generator": ("Wall generator", ""),
    "only_one_wall_top": ("Single wall on top surfaces", _SWITCH),
    "detect_thin_wall": ("Thin walls", _SWITCH),
    "support_on_build_plate_only": ("Plate-only supports", _SWITCH),
    "support_style": ("Support style", ""),
    "support_top_z_distance": ("Gap above support", _MM),
    "support_bottom_z_distance": ("Gap below support", _MM),
    "support_object_xy_distance": ("Gap beside support", _MM),
    "support_interface_top_layers": ("Support interface layers on top", ""),
    "tree_support_branch_angle": ("Tree support branch angle", "°"),
    "raft_layers": ("Raft layers", ""),
    "seam_slope_type": ("Scarf seam", ""),
    "outer_wall_line_width": ("Outer wall line width", _MM),
    "outer_wall_acceleration": ("Outer wall acceleration", " mm/s²"),
}

#: A real Orca setting a preset may not list, the one that does its job in a preset that
#: doesn't, and that job in words. Bambu PETG HF lists no `enable_overhang_bridge_fan`: it runs
#: the fan on bridges and overhangs by `overhang_fan_speed` (mqucifer/print-shop#222), as Orca
#: does `internal_bridge_fan_speed` at its default, -1.
STANDS_IN: dict[str, tuple[str, str]] = {
    "enable_overhang_bridge_fan": ("overhang_fan_speed", "bridges"),
    "internal_bridge_fan_speed": ("overhang_fan_speed", "bridges"),
}

#: Words said another way: a word typed finds a setting named by any of these.
_ALSO: dict[str, tuple[str, ...]] = {
    "temp": ("temperature",), "temperature": ("temp",), "heat": ("temperature",),
    "hotend": ("nozzle",), "bed": ("plate",), "plate": ("bed",),
    "aux": ("side", "additional"), "auxiliary": ("side", "additional"),
    "side": ("additional",), "cooling": ("fan", "cool"), "cool": ("fan", "cooling"),
    "first": ("initial",), "initial": ("first",), "fast": ("speed",), "slow": ("speed",),
    "elephant": ("elefant",), "foot": ("elefant",), "chamber": ("exhaust",),
    "infill": ("sparse",), "perimeter": ("wall",), "shell": ("wall", "layer"),
    "retract": ("retraction",), "flow": ("ratio",), "skirt": ("brim",),
}

#: Words that say nothing of which setting.
_FILLER = frozenset({"a", "an", "and", "the", "of", "on", "for", "in", "to", "at", "by", "it",
                     "is", "how", "what", "setting", "settings", "much", "my"})

#: How many are listed, best first.
_LISTED = 5


@dataclass(frozen=True)
class Found:
    """A setting this print takes, as it's listed: its key, plain name and value today."""

    key: str
    plain: str
    now: str
    #: That it does the job of a setting this print's presets don't list: that job, in words.
    stands_in: str = ""
    #: The words it takes, where they're all Orca's (`choices`), and the one that turns it off.
    choices: tuple[str, ...] = ()
    off: str = ""


#: print-shop's own answers that are typed as a key is, and aren't Orca's.
_OWN = {"pause_at": "Pause", "colour_change": "Colour change", "color_change": "Colour change",
        "orientation": "Pose"}


def plain(key: str) -> str:
    """A setting's plain name, or its key's words where it has none."""
    if key in _OWN:
        return _OWN[key]
    found = PLAIN.get(key)
    if found is not None:
        return found[0]
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def named(key: str) -> str:
    """A setting as it's said in a sentence: its plain name, its key after."""
    return f"{plain(key)} ({key})"


def with_unit(key: str, value: str) -> str:
    """A value with its setting's unit, as it's shown: `100` of a fan is `100%`. A list's
    first, as there's one extruder; "-" where there's none."""
    if not value:
        return "-"
    first = value.split(",")[0].strip() if key not in _TEXT and not key.endswith("_gcode") \
        else value
    unit = PLAIN.get(key, ("", ""))[1]
    if unit == _SWITCH:
        return switched(key, first)
    if not unit or first.endswith(unit.strip()) or not _NUMBER.match(first):
        return first
    return f"{first}{unit}"


def switched(key: str, value: str) -> str:
    """A switch's value as it's shown, off or on; any other value as it is. A switch's name
    names what it sets, never its state, so its value says which."""
    if PLAIN.get(key, ("", ""))[1] != _SWITCH:
        return value
    return {"0": "off", "1": "on", "false": "off", "true": "on"}.get(value.strip().lower(), value)


def typed(key: str, value: str) -> str:
    """A switch's value typed as it's shown, `on` or `off` (or yes and no), as Orca writes it,
    1 or 0; one of a setting's words in Orca's case (`No ironing` is `no ironing`); any other as
    typed."""
    word = next((c for c in choices(key) if c == value.strip().lower()), None)
    if word is not None:
        return word
    if PLAIN.get(key, ("", ""))[1] != _SWITCH:
        return value
    return {"on": "1", "yes": "1", "off": "0", "no": "0"}.get(value.strip().lower(), value)


def choices(key: str) -> tuple[str, ...]:
    """The words `key` takes, where the settings table holds every one Orca does
    (`bounds.Setting.orcas`, mqucifer/print-shop#279); none for any other."""
    setting = SETTINGS.get(key)
    return setting.choices if setting is not None and setting.orcas else ()


def off(key: str) -> str:
    """Of `choices`, the one that turns `key` off, or '' where none does."""
    setting = SETTINGS.get(key)
    return setting.off if setting is not None and setting.orcas else ""


def either(words: Sequence[str]) -> str:
    """Choices said as a list, `no ironing, top, topmost or solid`; '' where there are none."""
    return ", ".join(words[:-1]) + f" or {words[-1]}" if len(words) > 1 else "".join(words)


def turns_off(key: str) -> str:
    """The line that turns `key` off, its value last, as a space may be in it; '' where none
    does."""
    word = off(key)
    return f"To turn it off: {key}={word}" if word else ""


def not_a_choice(key: str, value: str) -> str:
    """Why `key=value` is refused, before anything slices, where `value` isn't one of the
    words `key` takes (`choices`), or ''. Where it has no `choices`, ''."""
    words = choices(key)
    if not words or value.strip().lower() in words:
        return ""
    said = f"'{value}' isn't one of {key}'s choices: {either(words)}"
    return f"{said}. {turns_off(key)}" if off(key) else said


_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
#: Settings whose values are text with commas in them, never a list.
_TEXT = frozenset({"compatible_printers_condition", "compatible_prints_condition"})
#: A preset's record of itself, never found.
_RECORD = NOT_SETTINGS | frozenset(PROFILE_IDS.values()) | {
    "description", "renamed_from", "filament_vendor", "filament_settings_id", "filament_cost",
    "filament_density", "filament_diameter", "filament_type", "filament_is_support",
    "filament_soluble", "required_nozzle_HRC", "nozzle_temperature_range_low",
    "nozzle_temperature_range_high", "temperature_vitrification",
}


def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in re.split(r"[^a-zA-Z0-9]+", text) if w]


def _asked(text: str) -> list[str]:
    """The words typed that say which setting."""
    return [w for w in dict.fromkeys(_words(text)) if w not in _FILLER]


def _known_words(key: str) -> set[str]:
    words = set(_words(plain(key))) | set(_words(key))
    return {w for w in words if w not in _FILLER}


def _fit(word: str, known: set[str]) -> float:
    """How well one typed word names a setting known by `known`: 1 if it's one of them or said
    another way, less for a near spelling, 0 for neither."""
    if word in known or any(_stem(a) in known for a in _ALSO.get(word, ())):
        return 1.0
    if len(word) >= 4 and difflib.get_close_matches(word, known, n=1, cutoff=0.8):
        return 0.75
    return 0.0


def _takes(process: Mapping[str, Any], filament: Mapping[str, Any]) -> list[str]:
    """Every key this print's presets take: placed in the process or the filament."""
    keys = dict.fromkeys([*SETTINGS, *sorted(MATERIAL), *process, *filament])
    return [k for k in keys if k not in _RECORD and not k.endswith("_gcode")
            and profile_of(k, dict(process), dict(filament)) is not None]


def _now(key: str, process: Mapping[str, Any], filament: Mapping[str, Any],
         config: Mapping[str, str] | None, changed: Mapping[str, str]) -> str:
    """A setting's value today: as this review changes it, else as sliced, else the preset's."""
    if key in changed:
        return changed[key]
    if config is not None and key in config:
        return config[key]
    data = filament if profile_of(key, dict(process), dict(filament)) == "filament" else process
    value = data.get(key, "")
    return ",".join(map(str, value)) if isinstance(value, list) else str(value)


def find(
    words: str, process: Mapping[str, Any], filament: Mapping[str, Any],
    config: Mapping[str, str] | None = None, changed: Mapping[str, str] | None = None,
) -> list[Found]:
    """The settings this print takes that fit `words`, best first: those every word fits before
    those only some do. None where no word fits any. `config` is the slice's config block, for
    today's values and to know Orca's settings; `changed` is what this review changes."""
    asked = _asked(words)
    changed = changed or {}
    if not asked:
        return []
    keys = _takes(process, filament)
    scored: list[tuple[float, int, int, str]] = []
    for key in keys:
        known = _known_words(key)
        fits = [_fit(w, known) for w in asked]
        score = sum(fits)
        if score <= 0:
            continue
        # Fewer other words in the name is closer; a named setting before a key's own words.
        scored.append((-score, 0 if key in PLAIN else 1, len(known), key))
    # A setting the presets don't list, named by the words, brings the one that stands in.
    stands: dict[str, str] = {}
    for gone, (key, job) in STANDS_IN.items():
        if (key in keys and gone not in keys and all(_fit(w, _known_words(gone)) for w in asked)
                and set(_words(job)) & set(asked)):
            stands[key] = job
            scored.append((-len(asked) - 0.5, 0, 0, key))
    best: dict[str, tuple[float, int, int, str]] = {}
    for row in sorted(scored):
        best.setdefault(row[3], row)
    ranked = sorted(best.values())
    whole = [r for r in ranked if -r[0] >= len(asked)]
    some = [r for r in ranked if -r[0] < len(asked)]
    out = [*whole, *some][:_LISTED]
    return [Found(key=k, plain=plain(k), now=_now(k, process, filament, config, changed),
                  stands_in=stands.get(k, ""), choices=choices(k), off=off(k)) for *_, k in out]


def _nearest(key: str, among: Iterable[str]) -> list[str]:
    """The keys spelt nearest `key`, nearest first: by spelling, else by the words they share."""
    pool = list(among)
    near = difflib.get_close_matches(key, pool, n=3, cutoff=0.6)
    if near:
        best = difflib.SequenceMatcher(None, key, near[0]).ratio()
        return [k for k in near if difflib.SequenceMatcher(None, key, k).ratio() >= best - 0.08]
    words = set(_words(key)) - _FILLER
    shared = sorted(((len(words & _known_words(k)), k) for k in pool), reverse=True)
    return [k for n, k in shared[:3] if n]


def refused(
    key: str, process: Mapping[str, Any], filament: Mapping[str, Any],
    config: Mapping[str, str] | None = None, preset: str = "",
    changed: Mapping[str, str] | None = None,
) -> str:
    """Why `key` can't be set on this print, saying which case it is, or '' when it can.

    Not an Orca setting: with the nearest this print takes. A real Orca setting, as the slice's
    config block has it (or a name `PLAIN` knows), that this print's presets don't list: with the
    one that does that job here, where it's known (`STANDS_IN`). The rest are `reach.unplaced`'s.
    `preset` is the filament's name.
    """
    why = unplaced(key, dict(process), dict(filament))
    if not why or not why.endswith("isn't a setting in the filament or the process preset"):
        return why
    changed = changed or {}
    keys = _takes(process, filament)

    def said(k: str) -> str:
        now = with_unit(k, _now(k, process, filament, config, changed))
        words = f"; {either(choices(k))}" if choices(k) else ""
        return f"{k} ({plain(k)}, now {now}{words})"

    def listed(near: list[str]) -> str:
        """The nearest, each said, then the line that turns each off, where one does."""
        offs = [line for k in near if (line := turns_off(k))]
        return "; ".join(map(said, near)) + "".join(f". {line}" for line in offs)

    orca = set(config or ()) | set(PLAIN) | set(STANDS_IN)
    if key in orca:
        stand = STANDS_IN.get(key)
        filaments = stand is not None and profile_of(stand[0], dict(process), dict(filament)) \
            == "filament"
        name = preset or "this filament"
        lists = (f"{name} doesn't list it" if filaments
                 else f"neither {name} nor the process lists it")
        out = f"'{key}' is a real Orca setting, but {lists}, so it can't be set here"
        if stand is not None and stand[0] in keys:
            return f"{out}. Here {said(stand[0])} does that job, for {stand[1]}"
        near = [k for k in _nearest(key, keys) if k != key]
        return f"{out}. Nearest here: " + listed(near) if near else out
    near = _nearest(key, keys)
    if not near:
        return (f"'{key}' isn't an Orca setting, and nothing this print takes is near it: "
                "find <words> looks one up")
    return f"'{key}' isn't an Orca setting. Nearest: " + listed(near)
