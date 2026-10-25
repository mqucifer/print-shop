# Supports

Distilled from OrcaSlicer's wiki, read 2026-10-25. The wiki is the source: each setting links to its page.

## Enable support (`enable_support`)
[OrcaSlicer wiki: support_settings_support](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_support)

- **Effect:** Turns support structures on, adding stability to overhangs and complex geometry.
- **Trade-off:** Supports are extra material that must be removed after printing. The wiki's support page does not quantify time, filament or surface cost for this switch.
- **Change it when:** the part has overhangs steeper than the threshold angle allows, or a flat downward face that cannot be bridged. The bridging page describes bridges as spanning "gaps or voids without direct support underneath" but gives no maximum span, so whether a given flat face can bridge is not settled by the wiki ([quality_settings_bridging](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_bridging)).
- **Bound:** 0 or 1. If an overhang is present and nothing decided, code turns supports on. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Support type (`support_type`)
[OrcaSlicer wiki: support_settings_support](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_support), [support_settings_tree](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_tree)

In print-shop only `tree(auto)` is allowed: when supports are used at all, the Sponsor uses tree supports.

- **Effect:** Tree supports grow branches from a central trunk, using less material than normal supports while still supporting the model. Auto generates support wherever overhangs need it, plus wherever support enforcers are placed.
- **Trade-off:** Tree supports aim to minimize material. The tree page notes that a higher branch density gives better overhangs but makes the supports harder to remove.
- **Change it when:** not changed in print-shop; it is `tree(auto)` whenever `enable_support` is on.
- **Bound:** `tree(auto)` only, the Sponsor's practice; code replaces any other. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.

## Threshold angle (`support_threshold_angle`)
[OrcaSlicer wiki: support_settings_support](https://github.com/OrcaSlicer/OrcaSlicer/wiki/support_settings_support)

- **Effect:** Decides which overhangs get support: overhangs whose slope angle is below the threshold are supported. A lower value lets steeper overhangs print without support. At `0`, tree supports use 30°.
- **Trade-off:** A higher value supports more overhangs, adding material and removal; a lower value leaves more overhangs to print unsupported. The wiki does not quantify either cost.
- **Change it when:** the part's overhang angles sit near the threshold, so a small change decides whether an overhang is supported. The layer height page adds that smaller layers allow steeper overhangs and larger layers need support at shallower angles.
- **Bound:** 0 to 90 degrees. Enforced in code (`src/print_shop/setup/bounds.py`): a value outside it is refused, never clamped.
