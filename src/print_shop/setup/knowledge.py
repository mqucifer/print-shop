"""What a setting does, and the bounds it must stay in: sourced knowledge the reasoning cites.

The Sponsor, 2026-10-25, on mqucifer/print-shop#7: there are no fixed
defaults; "it's all very dependent on the actual part". So nothing here is a
value to apply. Each entry states an **effect** the proposal's reasoning
uses, citing its source, and each setting a **bound** any proposed value must
fall in, set by the presets themselves or by a source. A value outside its
bound is refused, never clamped.

Sources: OrcaSlicer's wiki, cited by page (github.com/OrcaSlicer/OrcaSlicer/
wiki/<page>), read 2026-10-25; the filament maker's range, as the filament
preset carries it; and the Sponsor's own practice, written on the issue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["KNOWLEDGE", "SETTINGS", "Knowledge", "Setting", "check"]


@dataclass(frozen=True)
class Knowledge:
    id: str
    settings: tuple[str, ...]
    effect: str
    source: str


KNOWLEDGE: tuple[Knowledge, ...] = (
    Knowledge("walls", ("wall_loops",),
              "More wall loops raise strength and rigidity and layer adhesion, and cost "
              "time; they also reduce infill showing through.",
              "OrcaSlicer wiki: strength_settings_walls"),
    Knowledge("shells", ("top_shell_layers", "bottom_shell_layers"),
              "Solid shell layers over the infill; at least 3 for most prints. More of them "
              "give a stronger skin and a better top surface.",
              "OrcaSlicer wiki: strength_settings_top_bottom_shells"),
    Knowledge("infill", ("sparse_infill_density", "sparse_infill_pattern"),
              "Gyroid, cubic, grid and honeycomb are rated strongest in X-Y and Z; adaptive "
              "cubic and cross hatch are strong but weaker in Z; lightning is weakest and only "
              "holds up top surfaces. Density adds strength at the cost of time and filament.",
              "OrcaSlicer wiki: strength_settings_patterns_quick_reference"),
    Knowledge("supports", ("enable_support", "support_type", "support_threshold_angle"),
              "Support is generated for overhangs whose slope is below the threshold angle. "
              "The Sponsor uses tree supports when supports are used at all, and would rather "
              "an overhang be designed out (a chamfer, a designed-in support) where it can be.",
              "OrcaSlicer wiki: support_settings_support; the Sponsor, mqucifer/print-shop#7"),
    Knowledge("brim", ("brim_type",),
              "A brim helps parts with small footprints or prone to warping; auto sizes it "
              "from the first layer, the height and the material. Outer is easiest to remove.",
              "OrcaSlicer wiki: others_settings_brim"),
    Knowledge("layer_height", ("layer_height",),
              "Thinner layers resolve finer vertical detail and shallow curves, at the cost of "
              "time roughly in proportion; the machine sets the range.",
              "OrcaSlicer wiki: quality_settings_layer_height; the machine preset's limits"),
    Knowledge("speed", ("outer_wall_speed", "inner_wall_speed", "sparse_infill_speed"),
              "Slower outer walls give a cleaner surface and more accurate dimensions; the "
              "Sponsor's tuned speeds are the ceiling.",
              "The process preset (the Sponsor's tuning)"),
    Knowledge("temperature", ("nozzle_temperature",),
              "Hotter improves layer bonding and strength, and adds stringing and sag; the "
              "filament maker's range bounds it.",
              "OrcaSlicer wiki: material_temperatures; the filament preset's maker range"),
    Knowledge("cooling", ("fan_max_speed", "fan_min_speed"),
              "More part cooling sharpens overhangs and bridges and weakens layer bonding; "
              "PETG and TPU usually want less than PLA.",
              "OrcaSlicer wiki: material_cooling"),
)


@dataclass(frozen=True)
class Setting:
    """A setting the proposal may change, where it lives, and its bound."""

    key: str
    filament: bool  # a filament-preset setting, else the process's
    bound: Callable[[dict[str, Any]], tuple[float, float]] | None = None
    choices: tuple[str, ...] = ()

    def check(self, value: str, presets: dict[str, dict[str, Any]]) -> str:
        """Why `value` is refused, or '' when it's inside the bound."""
        if self.choices:
            return "" if value in self.choices else f"not one of {', '.join(self.choices)}"
        assert self.bound is not None
        try:
            number = float(value.rstrip("%"))
        except ValueError:
            return "not a number"
        low, high = self.bound(presets)
        return "" if low <= number <= high else f"outside {low:g}-{high:g}"


def _num(presets: dict[str, dict[str, Any]], tier: str, key: str) -> float:
    value = presets[tier].get(key, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    try:
        return float(str(value).rstrip("%"))
    except ValueError:
        return 0.0


SETTINGS: dict[str, Setting] = {
    s.key: s
    for s in (
        Setting("layer_height", False, lambda p: (_num(p, "machine", "min_layer_height"),
                                                  _num(p, "machine", "max_layer_height"))),
        Setting("wall_loops", False, lambda p: (1, 10)),
        Setting("top_shell_layers", False, lambda p: (3, 15)),
        Setting("bottom_shell_layers", False, lambda p: (3, 15)),
        Setting("sparse_infill_density", False, lambda p: (0, 100)),
        Setting("sparse_infill_pattern", False, choices=(
            "gyroid", "cubic", "grid", "honeycomb", "adaptivecubic", "crosshatch", "lightning",
            "zig-zag", "concentric")),
        Setting("enable_support", False, choices=("0", "1")),
        Setting("support_type", False, choices=("tree(auto)",)),
        Setting("support_threshold_angle", False, lambda p: (0, 90)),
        Setting("brim_type", False, choices=("no_brim", "auto_brim", "outer_only", "brim_ears")),
        Setting("outer_wall_speed", False, lambda p: (1, _num(p, "process", "outer_wall_speed"))),
        Setting("inner_wall_speed", False, lambda p: (1, _num(p, "process", "inner_wall_speed"))),
        Setting("sparse_infill_speed", False,
                lambda p: (1, _num(p, "process", "sparse_infill_speed"))),
        Setting("nozzle_temperature", True,
                lambda p: (_num(p, "filament", "nozzle_temperature_range_low"),
                           _num(p, "filament", "nozzle_temperature_range_high"))),
        Setting("fan_max_speed", True, lambda p: (0, 100)),
        Setting("fan_min_speed", True, lambda p: (0, _num(p, "filament", "fan_max_speed"))),
    )
}


def check(key: str, value: str, presets: dict[str, dict[str, Any]]) -> str:
    """Why `key=value` is refused, or '' when it may be proposed."""
    setting = SETTINGS.get(key)
    if setting is None:
        return "not a setting the proposal may change"
    return setting.check(value, presets)
