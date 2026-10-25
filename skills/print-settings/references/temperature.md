# Temperature

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Nozzle temperature (`nozzle_temperature`)
[OrcaSlicer wiki: Material temperatures](https://github.com/OrcaSlicer/OrcaSlicer/wiki/material_temperatures) · [Temperature calibration](https://github.com/OrcaSlicer/OrcaSlicer/wiki/temp_calib)

- **Effect:** Sets how well the filament melts and extrudes, which drives layer adhesion and print quality. The wiki treats it as one of the most important settings. Higher generally improves layer adhesion and helps flow for overhangs and bridges.
- **Trade-off:** Too low risks under-extrusion and weak layer adhesion. Too high can degrade the filament, clog the nozzle, cause stringing, and make overhangs sag. Lowering usually reduces stringing, but too low can bring it back.
- **Change it when:** the part has a load path that depends on layer bonding (toward the warmer end), overhangs and bridges (warm enough for flow, not so hot they sag), or a visible outer surface where stringing shows (toward the cooler end). The calibration page suggests the middle of the good range, or its higher end for faster printing or higher flow. Manufacturer recommendations and calibration take priority over the wiki's generic material table. In print-shop it stays within the filament maker's range carried in the filament preset.
- **Bound:** the filament maker's range, as the filament preset carries it (`nozzle_temperature_range_low` to `_high`; 190 to 240 °C for the Sponsor's Elegoo PLA). Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.
