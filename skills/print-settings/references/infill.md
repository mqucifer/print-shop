# Infill

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Sparse infill density (`sparse_infill_density`)
[OrcaSlicer wiki: Infill](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_infill)

- **Effect:** How much material fills the part's interior, as a percentage of the infill volume (not the whole print). 100% is solid. Higher density gives a stronger part.
- **Trade-off:** Higher density uses more material and takes longer to print. Patterns read density differently, so material use at a given percentage depends on the pattern.
- **Change it when:** Raise it for a part on a load path or one that must be strong. Lower it to save material and time when strength matters less. The wiki gives no recommended percentages.
- **Bound:** 0 to 100 %, written with `%`. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Sparse infill pattern (`sparse_infill_pattern`)
[OrcaSlicer wiki: Infill patterns](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_patterns)

- **Effect:** The shape of the lines inside the part. At the same density, different patterns give different strength, flexibility, print speed and layer-time consistency.
- **Trade-off:** Strong patterns tend to cost print time or material. The fast, light patterns are weak (see the table).
- **Change it when:** The wiki says no pattern suits every print. Use gyroid when strength must be equal in all directions, for flexible prints, or to reduce warping. Use adaptive cubic for large prints, to save material and time. Use lightning for quick prints and non-structural prototypes. Use concentric for 100% infill and flexible prints. For gears, the wiki suggests Fill Multiline with the pattern to transfer torque better.
- **Bound:** one of gyroid, cubic, grid, honeycomb, adaptivecubic, crosshatch, lightning, zig-zag, concentric. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

### Patterns allowed in print-shop

The ratings come from the wiki's [quick-reference table](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_patterns_quick_reference). That table shows icon levels, and the [patterns page](https://github.com/OrcaSlicer/OrcaSlicer/wiki/strength_settings_patterns) puts the same levels in the words used below. The wiki measured them on a few test models and treats them as rough guidance.

| Pattern (`value`) | X-Y strength | Z strength | Print time | Note |
|---|---|---|---|---|
| Gyroid (`gyroid`) | High | High | Extra-high | Equal strength in all directions. Curves reduce warping. Slow to slice. |
| Cubic (`cubic`) | High | High | Normal-low | Spreads force in all directions. |
| Grid (`grid`) | High | High | Normal-low | Lines cross, which can cause noise or artifacts. |
| Honeycomb (`honeycomb`) | High | High | Ultra-high | Double walls use more material (normal-high). |
| Adaptive cubic (`adaptivecubic`) | Normal-high | Normal-high | Low | Denser near walls, so it uses little material. Suits large prints. |
| Cross hatch (`crosshatch`) | Normal-high | Normal-high | Normal-high | Weak points at internal corners. Layer times likely vary. |
| Lightning (`lightning`) | Low | Low | Extra-low | Lowest material use. For non-structural prototypes. |
| Zig-zag (`zig-zag`) | Normal-low | Low | Normal-low | Like rectilinear, the same on every layer. |
| Concentric (`concentric`) | Low | Normal | Normal | Follows the outer contour. Uses more material (high). |
