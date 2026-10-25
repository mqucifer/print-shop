# Layer height

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Layer height (`layer_height`)
[OrcaSlicer wiki: quality_settings_layer_height](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_layer_height)

- **Effect:** Sets the thickness of each layer. Smaller layers give smoother surfaces, less visible layer lines and more detail on curves, and allow steeper overhangs because the unsupported distance between layers is shorter.
- **Trade-off:** Smaller layers increase print time. Larger layers sag more on overhangs and need support at shallower angles. The wiki's optimal range is 20% to 80% of the nozzle diameter (0.08 to 0.32 mm for a 0.4 mm nozzle): below it, flow inconsistencies and "fish scale" patterns may appear; above it, layer adhesion and quality may suffer.
- **Change it when:** the part has a visible curved surface or fine detail (go smaller), or a steep overhang that should print without support (go smaller). The wiki does not address strength or fit.
- **Bound:** the machine preset's `min_layer_height` to `max_layer_height` (0.08 to 0.28 mm on the 0.4 nozzle). Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.
