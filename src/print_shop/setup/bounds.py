"""Each setting the proposal may change, and the bound its value must fall in.

These are guards: they hold whatever the model reads in the print-settings
skill (`skills/print-settings/`, mqucifer/print-shop#7). What a setting does
and when to change it is the skill's; the bound is code's. A value outside its
bound is refused, never clamped. The bounds come from the presets themselves
(the machine's layer limits, the Sponsor's tuned speeds as a ceiling, the
filament maker's temperature range) or from a source (at least 3 shell
layers, OrcaSlicer's wiki), and the choices from the Sponsor's practice
(tree supports only).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["SETTINGS", "Setting", "check"]


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
