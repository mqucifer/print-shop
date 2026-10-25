# Walls and shells

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Wall loops (`wall_loops`)
[OrcaSlicer wiki: Walls](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_walls)

- **Effect:** The number of walls printed on each layer. More walls give better layer adhesion, strength and rigidity, and reduce infill ghosting (the infill pattern showing through the outer surface).
- **Trade-off:** More walls take longer to print. The wiki names no other cost.
- **Change it when:** Raise it for a part on a load path, or one that must be rigid. Also raise it on a visible surface where the infill shows through. The wiki gives no recommended counts.
- **Bound:** 1 to 10 loops. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Top shell layers (`top_shell_layers`)
[OrcaSlicer wiki: Top and bottom shells](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_top_bottom_shells)

- **Effect:** The number of solid layers on top surfaces, the surface layer included, printed over the sparse infill. More layers improve part strength and top surface quality. If these layers come out thinner than the Shell Thickness setting, the slicer adds layers.
- **Trade-off:** The wiki names no cost. With more than one layer, the top surface pattern covers only the outermost layer and the rest use the internal solid infill pattern. At 0, top surfaces are treated as internal, and "only one wall on top" is not available ([Wall and surfaces](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_wall_and_surfaces)).
- **Change it when:** Raise it for flat tops that must look good or carry load. The wiki suggests at least 3 for most prints. With a small layer height, it points to Shell Thickness to keep the shell thick enough.
- **Bound:** 3 to 15 layers (the wiki: at least 3 for most prints). Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Bottom shell layers (`bottom_shell_layers`)
[OrcaSlicer wiki: Top and bottom shells](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_top_bottom_shells)

- **Effect:** The number of solid layers on bottom surfaces, the surface layer included. As with the top shell, more layers add strength, and Shell Thickness raises the count when the layers are too thin.
- **Trade-off:** The wiki names no cost. At 0, "only one wall on first layer" is not available ([Wall and surfaces](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_wall_and_surfaces)).
- **Change it when:** Raise it for a part whose bottom carries load. The wiki suggests at least 3 for most prints, and Shell Thickness when the layer height is small. It gives no guidance specific to bottom surfaces.
- **Bound:** 3 to 15 layers (the wiki: at least 3 for most prints). Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.
