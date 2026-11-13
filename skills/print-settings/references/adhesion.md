# Adhesion

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Brim type (`brim_type`)
[OrcaSlicer wiki: others_settings_brim](https://github.com/OrcaSlicer/OrcaSlicer/wiki/others_settings_brim)

Allowed in print-shop: `no_brim`, `auto_brim`, `outer_only`, `brim_ears`.

- **Effect:** A brim is a flat layer printed around the model's base to improve adhesion to the bed. `no_brim` prints none. `auto_brim` calculates a width from model geometry, print speed and material, capped at 18 mm, and prints none if the result is under 5 mm and below 1.5x the thermal length. `outer_only` places the brim around the outer perimeter. `brim_ears` adds small extensions near corners and sharp features.
- **Trade-off:** Wider brims use more material. An outer brim is easy to remove but can affect appearance if not removed cleanly. Ears improve adhesion with less material than a full brim. A larger brim-object gap eases removal but reduces adhesion.
- **Change it when:** the part has a small footprint or is prone to warping (the wiki's two named cases). Ears suit corners and sharp features where a full brim is more than needed. `no_brim` suits a part with enough bed contact and no long, narrow run. Contact area alone doesn't hold a long part's ends down: on print-shop's first real prints (mqucifer/print-shop#107, not the wiki), a PETG part about 150 mm long and 25 mm wide lifted at its ends with no brim. For a footprint at least 100 mm long and 3x as long as it is wide (`length`, `footprint_aspect`), weigh ears at its ends or an outer brim, with the plate's temperature below.
- **Bound:** one of no_brim, auto_brim, outer_only, brim_ears. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Plate temperature: Textured PEI plate, after the first layer (`textured_plate_temp`)
[OrcaSlicer wiki: Material temperatures](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_temperatures). The values and the case for changing it are the Sponsor's practice, 2026-11-13, not the wiki's.

- **Effect:** The plate's temperature for the slice's plate (`ORCA_BED_TYPE`: the textured PEI plate by default, or the cool plate). A warmer plate keeps the first layers soft and stuck down, so a long part's ends don't lift as the layers above cool and shrink.
- **Trade-off:** Too warm and the first layers squash out (elephant's foot), and PLA softens above its glass point, the preset's `temperature_vitrification` (45 °C for Elegoo PLA, 70 °C for its PETG). Elegoo's own PLA presets heat the textured plate above it (60 °C).
- **Change it when:** a long or thin part lifted at its ends or corners. Change the first layer's and the later layers' together. The Sponsor's Rapid PETG tunings heat the plate to 60 °C, Elegoo's standard to 70 °C.
- **Bound:** 0 to 110 °C, the Centauri Carbon's heated bed limit. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Plate temperature: Textured PEI plate, first layer (`textured_plate_temp_initial_layer`)
[OrcaSlicer wiki: Material temperatures](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_temperatures). The values and the case for changing it are the Sponsor's practice, 2026-11-13, not the wiki's.

- **Effect:** The plate's temperature for the slice's plate (`ORCA_BED_TYPE`: the textured PEI plate by default, or the cool plate). A warmer plate keeps the first layers soft and stuck down, so a long part's ends don't lift as the layers above cool and shrink.
- **Trade-off:** Too warm and the first layers squash out (elephant's foot), and PLA softens above its glass point, the preset's `temperature_vitrification` (45 °C for Elegoo PLA, 70 °C for its PETG). Elegoo's own PLA presets heat the textured plate above it (60 °C).
- **Change it when:** a long or thin part lifted at its ends or corners. Change the first layer's and the later layers' together. The Sponsor's Rapid PETG tunings heat the plate to 60 °C, Elegoo's standard to 70 °C.
- **Bound:** 0 to 110 °C, the Centauri Carbon's heated bed limit. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Plate temperature: Cool plate, after the first layer (`cool_plate_temp`)
[OrcaSlicer wiki: Material temperatures](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_temperatures). The values and the case for changing it are the Sponsor's practice, 2026-11-13, not the wiki's.

- **Effect:** The plate's temperature for the slice's plate (`ORCA_BED_TYPE`: the textured PEI plate by default, or the cool plate). A warmer plate keeps the first layers soft and stuck down, so a long part's ends don't lift as the layers above cool and shrink.
- **Trade-off:** Too warm and the first layers squash out (elephant's foot), and PLA softens above its glass point, the preset's `temperature_vitrification` (45 °C for Elegoo PLA, 70 °C for its PETG). Elegoo's own PLA presets heat the textured plate above it (60 °C).
- **Change it when:** a long or thin part lifted at its ends or corners. Change the first layer's and the later layers' together. The Sponsor's Rapid PETG tunings heat the plate to 60 °C, Elegoo's standard to 70 °C.
- **Bound:** 0 to 110 °C, the Centauri Carbon's heated bed limit. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Plate temperature: Cool plate, first layer (`cool_plate_temp_initial_layer`)
[OrcaSlicer wiki: Material temperatures](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_temperatures). The values and the case for changing it are the Sponsor's practice, 2026-11-13, not the wiki's.

- **Effect:** The plate's temperature for the slice's plate (`ORCA_BED_TYPE`: the textured PEI plate by default, or the cool plate). A warmer plate keeps the first layers soft and stuck down, so a long part's ends don't lift as the layers above cool and shrink.
- **Trade-off:** Too warm and the first layers squash out (elephant's foot), and PLA softens above its glass point, the preset's `temperature_vitrification` (45 °C for Elegoo PLA, 70 °C for its PETG). Elegoo's own PLA presets heat the textured plate above it (60 °C).
- **Change it when:** a long or thin part lifted at its ends or corners. Change the first layer's and the later layers' together. The Sponsor's Rapid PETG tunings heat the plate to 60 °C, Elegoo's standard to 70 °C.
- **Bound:** 0 to 110 °C, the Centauri Carbon's heated bed limit. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Exhaust fan while printing (`during_print_exhaust_fan_speed`)
[OrcaSlicer wiki: Material cooling](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_cooling). The values and the case for changing it are the Sponsor's practice, 2026-11-13, not the wiki's.

- **Effect:** The enclosure's exhaust fan, in percent, during the print. It pulls warm air out of the chamber.
- **Trade-off:** A cooler chamber keeps PLA from heat-creeping, but a long PETG part shrinks more as it cools and pulls its ends off the plate. Elegoo's Rapid PETG preset runs it at 70 %.
- **Change it when:** a PETG part warped or lifted: turn it down, and keep the door shut.
- **Bound:** 0 to 100 %. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

Context only, print-shop does not set it: a raft is a sheet of support material under the model, used for warp-prone materials (ABS, ASA, PC, Nylon), a small contact area, thin features or sharp edges, or an uneven bed. It leaves a rougher underside and costs time, filament and removal; the wiki suggests trying a brim first when the problem is only adhesion or warping ([support_settings_raft](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_raft)).
