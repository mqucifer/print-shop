# Adhesion

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Brim type (`brim_type`)
[OrcaSlicer wiki: others_settings_brim](https://github.com/OrcaSlicer/OrcaSlicer/wiki/others_settings_brim)

Allowed in print-shop: `no_brim`, `auto_brim`, `outer_only`, `brim_ears`.

- **Effect:** A brim is a flat layer printed around the model's base to improve adhesion to the bed. `no_brim` prints none. `auto_brim` calculates a width from model geometry, print speed and material, capped at 18 mm, and prints none if the result is under 5 mm and below 1.5x the thermal length. `outer_only` places the brim around the outer perimeter. `brim_ears` adds small extensions near corners and sharp features.
- **Trade-off:** Wider brims use more material. An outer brim is easy to remove but can affect appearance if not removed cleanly. Ears improve adhesion with less material than a full brim. A larger brim-object gap eases removal but reduces adhesion.
- **Change it when:** the part has a small footprint or is prone to warping (the wiki's two named cases). Ears suit corners and sharp features where a full brim is more than needed. `no_brim` suits a part with enough bed contact.
- **Bound:** one of no_brim, auto_brim, outer_only, brim_ears. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

Context only, print-shop does not set it: a raft is a sheet of support material under the model, used for warp-prone materials (ABS, ASA, PC, Nylon), a small contact area, thin features or sharp edges, or an uneven bed. It leaves a rougher underside and costs time, filament and removal; the wiki suggests trying a brim first when the problem is only adhesion or warping ([support_settings_raft](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_raft)).
