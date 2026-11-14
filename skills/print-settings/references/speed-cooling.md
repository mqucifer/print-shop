# Speed and cooling

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Outer wall speed (`outer_wall_speed`)
[OrcaSlicer wiki: Other layers speed](https://github.com/OrcaSlicer/OrcaSlicer/wiki/speed_settings_other_layers_speed)

- **Effect:** Sets the speed of the outermost, visible wall. The wiki says it is usually printed slower than the inner wall for better quality and good layer adhesion. Overhang slowdowns on outer walls are percentages of this speed ([Overhang speed](https://github.com/OrcaSlicer/OrcaSlicer/wiki/speed_settings_overhang_speed)).
- **Trade-off:** Slower gives a better surface and layer adhesion, at the cost of print time. Like every speed, it is capped by maximum volumetric speed, machine motion limits, acceleration, jerk and filament cooling time.
- **Change it when:** the part has a visible outer surface that must look good, or a load path that depends on layer bonding at the walls. Slow it to improve quality; the wiki gives no reason to raise it.
- **Bound:** 1 mm/s up to the process preset's own value: slower only; the Sponsor's tuning is the ceiling. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Inner wall speed (`inner_wall_speed`)
[OrcaSlicer wiki: Other layers speed](https://github.com/OrcaSlicer/OrcaSlicer/wiki/speed_settings_other_layers_speed)

- **Effect:** Sets the speed of the inner walls, which are printed faster than the outer wall to reduce print time. Overhang slowdowns on inner walls are percentages of this speed.
- **Trade-off:** Faster saves time, but the wiki advises keeping the flow well below the maximum volumetric speed to ensure good layer adhesion and reduce internal stresses in the material.
- **Change it when:** the part has a load path that depends on layer bonding through the walls: keep this speed well clear of the volumetric limit. Keep it faster than the outer wall otherwise.
- **Bound:** 1 mm/s up to the process preset's own value: slower only; the Sponsor's tuning is the ceiling. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Sparse infill speed (`sparse_infill_speed`)
[OrcaSlicer wiki: Other layers speed](https://github.com/OrcaSlicer/OrcaSlicer/wiki/speed_settings_other_layers_speed)

- **Effect:** Sets the speed of sparse infill, which is printed faster than solid infill to reduce print time.
- **Trade-off:** Faster saves time. The wiki only names appearance as a cost: where the infill pattern is visible, a speed closer to the outer wall speed gives better quality. It says nothing about strength or bonding for this setting.
- **Change it when:** the infill pattern is a visible design feature (slow it toward the outer wall speed). In print-shop the Sponsor's tuned preset speed is the ceiling, so this only ever goes slower.
- **Bound:** 1 mm/s up to the process preset's own value: slower only; the Sponsor's tuning is the ceiling. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Maximum fan speed (`fan_max_speed`)
[OrcaSlicer wiki: Material cooling](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling)

- **Effect:** The upper limit on the part-cooling fan. With auto-cooling, fan speed rises on short layers but never above this value. Overhang fan speed is raised toward it when the minimum layer time is not met.
- **Trade-off:** The wiki does not state a cost for the fan speed itself. It frames cooling on small layers as a choice against print time (via slowdown) and against outer surface consistency.
- **Change it when:** the part has small layer times (small sections or thin features where a layer finishes quickly) that need more cooling.
- **Bound:** 0 to 100 %. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Minimum fan speed (`fan_min_speed`)
[OrcaSlicer wiki: Material cooling](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling)

- **Effect:** The speed the part-cooling fan starts at once a layer's estimated time falls below the minimum layer time; between the thresholds the fan is interpolated from this to the maximum. With "Keep fan always on" the fan never drops below it, and overhang fan speed is clamped below by it.
- **Trade-off:** The wiki does not state a cost for this value directly.
- **Change it when:** the part has small layer times, or overhangs and bridges whose dedicated fan speed must not fall below a floor.
- **Bound:** 0 % up to the filament preset's `fan_max_speed`. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Auxiliary fan (`additional_cooling_fan_speed`)
[OrcaSlicer wiki: Material cooling](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling). The values and the case for changing it are the Sponsor's practice, 2026-11-13, not the wiki's.

- **Effect:** The Centauri Carbon's side (auxiliary) part-cooling fan, in percent, once the first layers are down. It blows across the whole part, not only the nozzle's spot.
- **Trade-off:** It sharpens overhangs and bridges, but on PETG it cools a long part unevenly and lifts its ends. The Sponsor's Rapid PETG tunings run it at 70, 80 or 100 %.
- **Change it when:** a PETG part warped or lifted (turn it down), or an overhang or bridge sagged (turn it up).
- **Bound:** 0 to 100 %. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Slow down for layer cooling (`slow_down_for_layer_cooling`)
[OrcaSlicer wiki: Material cooling, slow printing down for better layer cooling](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling#slow-printing-down-for-better-layer-cooling). Read 2026-11-13.

- **Effect:** Slows a layer down so it takes at least the layer time below, giving it longer to cool before the next is laid on it. The wiki says it can improve small details. Orca estimates each layer's time as length over speed, ignoring acceleration, and caps the fastest extrusions until the layer meets the time, never below the min print speed. It applies to every layer, the first included, and the slowed speeds are written into the G-code.
- **Trade-off:** The wiki names no cost for the option itself. A slowed layer takes longer, and once the floor is reached the layer finishes early anyway. The wiki suggests printing several copies at once, so each layer takes longer.
- **Change it when:** the slice's layers take less than the filament's layer time and the part's walls or details must hold their shape. The review reads each layer's time from the slice and proposes it on when they're short (mqucifer/print-shop#105): on the first real prints, PETG walls laid at about 8 s a layer with this off came out blobby.
- **Bound:** `0` (off) or `1` (on). Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Layer time for cooling (`slow_down_layer_time`)
[OrcaSlicer wiki: Material cooling, max fan speed threshold](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling#max-fan-speed-threshold). Read 2026-11-13.

- **Effect:** The time, in seconds, the slow-down above holds each layer to: the "Layer time" threshold in the max fan speed threshold. The wiki gives no section of its own for it.
- **Trade-off:** The wiki states none and gives no default. A longer time cools each layer more, at the cost of print time on every short layer.
- **Change it when:** the wiki doesn't say. The filament presets carry the maker's figure (12 s for Elegoo's PETG, 4 s for its PLA), and the review uses it as the line a short layer is judged by.
- **Bound:** 1 to 60 s: past a minute a layer has cooled, and the setting only costs time. print-shop's bound, not the wiki's. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Min print speed (`slow_down_min_speed`)
[OrcaSlicer wiki: Material cooling, min print speed](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling#min-print-speed). Read 2026-11-13.

- **Effect:** The slowest the slow-down goes to make a layer take its layer time. At `0` there's no floor, and every extrusion on the layer is slowed by the same factor instead.
- **Trade-off:** The wiki states none. A lower floor lets a short layer take its full time; a layer that reaches the floor finishes early.
- **Change it when:** slow-down is on and the slice's layers still take less than the layer time, because the floor stopped them. The review proposes a lower floor then.
- **Bound:** 1 mm/s up to the process preset's outer wall speed: a floor above it would speed the wall up. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.
