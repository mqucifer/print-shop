---
name: print-settings
description: How to read a part and set its print, over the Sponsor's tuned Orca presets, from its features and purpose. Load before proposing a print's settings; read only the references the part's features touch.
---

# Setting a print from the part

The Sponsor tunes Orca presets per printer, nozzle and filament. This skill is for the next step: changing them for one part. It's what print-shop's settings proposal reads (`src/print_shop/setup/`), and it grows from the Sponsor's corrections.

## The Sponsor's practice
- **Every setting depends on the part.** "It's all very dependent on the actual part. I print tons of different things even with 1 material. It's extremely dependent on the part design and its purpose." There are no defaults per material or per kind of part. Reason from this part.
- **Supports are tree,** when supports are used at all.
- **An overhang is sometimes better designed out:** a chamfer to 45°, or a support designed into the part, rather than left to Orca. Say so in a design note when you see one. Doing it is a CAD change, not a setting.
- **The presets are the Sponsor's tuning.** Keep a preset's value unless this part gives a reason. "No change" is a good answer.

## How to read a part
1. **Ask first, briefly.** Before proposing, ask what the features leave undecided: at most 4 questions, each tied to the feature that makes it matter, or to the part's purpose when its shape can't show what it's for (ask that first). Each question gets 2 to 4 options, each with the settings it implies, one line on its impact on strength, surface and risk that never restates a setting or a number (print-shop shows the settings beside it, from what the option applies), and exactly one recommended, with why. If you don't ask what the part is for, print-shop asks it first. Never ask what the features already answer, and never ask open-ended questions; a simple part may need none. Time and filament come from the slicer, never from you.
2. **Say what it is and what makes it succeed or fail.** Use the answers and the measured features: height, footprint, bed contact, slenderness, overhang and its steepest angle, bridge, flat top, section thickness, and volume. A load path, a surface that shows, a fit that must close, and a feature that will fail are what matter.
3. **For each setting you change, name the feature or purpose that drives it, and point at the part:** "the 4.8 mm flange carries the shelf in bending, so 4 walls", not "load-bearing, so 4 walls".
4. **Cite the reference group** your reasoning relies on, by its id below.
5. **Stay inside each setting's bound.** What the Sponsor chose at the intake is set; don't change it. The bounds come from the presets and the sources. A value outside one is refused, never clamped. Values are written as Orca writes them: numbers without units, infill density with `%`.
6. **Say what you weighed and kept,** with why, so the Sponsor sees the setting was considered.

## The references
Each is distilled from OrcaSlicer's wiki, which stays the source. Every setting in them links to its page. Only the groups this part's features touch are loaded.

| Id | File | Settings | Read when |
|---|---|---|---|
| `walls-shells` | `references/walls-shells.md` | `wall_loops`, `top_shell_layers`, `bottom_shell_layers` | always |
| `infill` | `references/infill.md` | `sparse_infill_density`, `sparse_infill_pattern` | always |
| `supports` | `references/supports.md` | `enable_support`, `support_type`, `support_threshold_angle` | it has an overhang or a bridge |
| `adhesion` | `references/adhesion.md` | `brim_type` | under 200 mm² on the plate, or more than 2.5x as tall as its narrowest width |
| `layer-height` | `references/layer-height.md` | `layer_height` | an intake option set it |
| `speed-cooling` | `references/speed-cooling.md` | `outer_wall_speed`, `inner_wall_speed`, `sparse_infill_speed`, `fan_max_speed`, `fan_min_speed` | it has an overhang or a bridge |
| `temperature` | `references/temperature.md` | `nozzle_temperature` | an intake option set it |

Any group is also read when one of the intake's options set one of its settings.

## What holds whatever this skill says
Code, not this skill, holds these guards (`src/print_shop/setup/bounds.py` and `proposal.py`):
- each setting's bound is refused, never clamped;
- supports are always `tree(auto)`;
- an overhang nobody decided on gets supports;
- a reply cut off at the output limit is refused and recorded, never retried.

## How this skill grows
A correction at the review ("`wall_loops=3 because it's decorative`") is the signal. When a correction would apply to other parts too, it becomes an edit here by PR:
- a "Change it when" line in a reference;
- or a line in the Sponsor's practice above.

The PR quotes the correction and its print's job id from the ledger.
