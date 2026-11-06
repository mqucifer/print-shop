---
name: sizing-walls-and-lids
description: Gives an enclosure's wall and floor thickness for FDM, the clearance to a board and between printed parts, and the three ways a lid closes (friction, snap clips, bought screws), each with what it needs and its source. Use when sizing a case's walls, the room round its boards, its lid's lip and play, or its clips.
---

# Walls, clearances and lids

Figures marked **practice** are printers' experience, not a standard; figures marked **a choice** have no source and are a starting point to say at the plan stop. Each other figure names its source, below or in `ref-fdm`.

## Walls and floors

| What | Do | Source |
|---|---|---|
| the thinnest printed wall | at least 0.8 mm, and 1.0 mm for a wall that stands alone | `ref-fdm` [P11] [P1] |
| a wall thinner than one nozzle's line | never: it doesn't print | [E2] |
| a case's walls, floor and lid | start at 2.0 mm: five lines of a 0.4 mm nozzle, ten layers of 0.2 mm, stiff enough to carry clips and bosses | a choice |
| a wall with a clip or a screw in it | keep the wall round its hole to the insert's or screw's figure in `ref-fdm` | `ref-fdm` [P8] [P9] |

Keep one wall thickness all round, named once: a rounded outside with an inside cut by a box rounded `R - WALL`, or square, keeps it even.

## Clearances

| Between | Do | Source |
|---|---|---|
| a board's outline and the case's inside | 0.5 mm all round, from the board's reference, plus whatever the request adds | `ref-fdm` [P1] [P2] [P3]; [E1] gives 0.5 mm for FDM |
| a part that moves or comes apart, printed against printed | 0.5 mm between them, in general | `ref-fdm` [P1] [P2] [P3] |
| a part that must move freely | at least 0.3 mm | [E2] |
| a fit by class, per face | press −0.05, location +0.05, sliding +0.15, loose +0.3 | `ref-fdm` [P4] |
| the tallest part on a board and the lid's underside | the request's figure; with none, ask | |

A request's own clearance beats every figure here. Each is held by a `clearance` need with `--min-gap`.

## How a lid closes

Whichever way, the lid sits on its **seat**, the wall's top, and its **lip** fits inside the walls and locates it. The lip is the inside's outline less the play on each face, 3 mm or more deep (a choice), and its play is the request's range, held by `clearance base.step lid-lifted.step --min-gap G1 --max-gap G2` with the lid lifted off its seat by more than G2.

**Friction.** The lip alone holds the lid, so its play is small: a location fit, +0.05 mm a face, grips; a sliding fit, +0.15 mm, comes off by hand and holds only by gravity (`ref-fdm` [P4]). Printed sizes vary by about a tenth of a millimetre (`ref-fdm` [P5]), so a friction lid often needs a test print: say so at the ask.

**Snap clips.** A clip is a cantilever with a hook at its end that springs into a ledge or a slot on the other part.
- **Make each clip bend along the layers, never across them.** Lay its arm level, in the plane of the bed, so it bends with its length in the layers: for a clip in an upright wall, cut the arm free with a U-shaped slot so it runs level along the wall, its hook standing proud. Avoid a clip whose arm stands upright in Z: the layers' bond is its weakest direction, and bending across it breaks it ([E1]).
- Give a clip at least 5 mm of width and a fillet at its root at least half the arm's thickness ([E1]).
- Its hook catches by about the arm's thickness or less, with 0.5 mm of clearance round it ([E1]).
- Whether a clip bends without breaking isn't read by any bench command: it's an eye need, and the request's to judge from a print.

**Bought screws.** Through the lid at a clearance hole (`ref-fasteners`, medium), into a boss on the base with a heat-set insert or a tapped hole (`references/mounting.md`, the skill `mounting-boards`). Counterbore the head so it sits flush, and keep the boss's wall to `ref-fdm`'s figure.

## A fan held in the lid

A fan held without screws sits in a recess the fan's frame plus the play on each face, against a ledge that stops it, held by clips that hook over its frame: two or more on opposite sides. The clips follow the rules above. Centre the fan on the point the request names, such as over a processor, from the references, never by eye. How firmly it's held is an eye need.

## Sources

- [E1]: https://www.hubs.com/knowledge-base/how-design-snap-fit-joints-3d-printing/ ("If feasible, avoid snap-fit cantilevers built vertically in the Z direction"; 0.5 mm nominal clearance for FDM; a root fillet at least 0.5 times the base thickness; a minimum width of 5 mm)
- [E2]: https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135 ("Walls thinner than one nozzle perimeter are not printable"; at least 0.3 mm for movable parts)
- `ref-fdm`'s [P1] to [P11], in the `references` skill.
