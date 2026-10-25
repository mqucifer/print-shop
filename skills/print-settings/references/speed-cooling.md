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
