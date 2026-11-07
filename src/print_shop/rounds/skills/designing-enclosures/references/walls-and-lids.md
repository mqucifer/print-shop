---
name: sizing-walls-and-lids
description: Gives an enclosure's wall and floor thickness for FDM, the clearance to a board and between printed parts, and the three ways a lid closes (friction, snap clips, bought screws), each with what it needs and its source. Use when sizing a case's walls, the room round its boards, its lid's lip and play, or its clips.
---

# Walls, clearances and lids

Figures marked **practice** are printers' experience, not a standard; figures marked **a choice** have no source and are a starting point to say at the plan stop; figures marked **measured** are print-shop's own, from a slice. Each other figure names its source, below or in `ref-fdm`.

## Walls and floors

| What | Do | Source |
|---|---|---|
| the thinnest printed wall | at least 0.8 mm, and 1.0 mm for a wall that stands alone | `ref-fdm` [P11] [P1] |
| a wall thinner than one nozzle's line | never: it doesn't print | [E2] |
| a case's walls | size each to the lines that print it, never to a round millimetre: start at 1.6 mm, its four wall lines with nothing between, or 2.4 mm where it carries clips or bosses, its four lines with infill between. Never 1.68 to 2.26 mm, 2.0 among them: that leaves a sliver too narrow for a line between the lines from each face, and the slicer gap-fills it thin in every layer | measured, [M1]; the starting figures a choice |
| a case's floor and lid | a whole number of layers: 2.0 mm is ten of 0.2 mm | a choice |
| a wall with a clip or a screw in it | keep the wall round its hole to the insert's or screw's figure in `ref-fdm` | `ref-fdm` [P8] [P9] |

**Why those figures** ([M1]). The Sponsor's printer profile lays two wall lines from each face of a wall, an outer line 0.42 mm wide and an inner 0.45, at 0.2 mm layers. The slicer spaces each line its width less the layer × (1 − π/4) from the next, so four lines span 2 × (0.377 + 0.407) = 1.57 mm. A wall up to 1.66 mm is those four lines; past it, what's left between them is gap-filled, a 2.0 mm wall with a line 0.43 mm wide in every layer, until at 2.28 mm it's wide enough for infill. With other lines, from the request or a note, work it out the same way. Where the request fixes a wall in the gap-filled range, build it at the request's figure, and say at the plan stop that it gap-fills, with the nearest whole figures.

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

Whichever way, the lid sits on its **seat**, the wall's top, and its **lip** fits inside the walls and locates it. The lip is the inside's outline less the play on each face, 3 mm or more deep (a choice), and its play is the request's range, held by `clearance base.step lid.step --lift D --min-gap G1 --max-gap G2`: `--lift` moves the lid D mm off its seat before measuring, D more than G2 and less than the lip's depth, so the least gap is the lip's play, read on the printed lid. Make no lifted copy.

**Friction.** The lip alone holds the lid, so its play is small: a location fit, +0.05 mm a face, grips; a sliding fit, +0.15 mm, comes off by hand and holds only by gravity (`ref-fdm` [P4]). Printed sizes vary by about a tenth of a millimetre (`ref-fdm` [P5]), so a friction lid often needs a test print: say so at the ask.

**Snap clips.** A clip is a cantilever with a hook at its end that springs into a ledge or a slot on the other part.
- **Where the part prints so a clip can lie level, lay its arm level,** in the plane of the bed, so it bends with its length in the layers: for a clip in an upright wall, cut the arm free with a U-shaped slot so it runs level along the wall, its hook standing proud. An arm standing upright in Z bends across the layers' bond, its weakest direction ([E1]: "If feasible, avoid snap-fit cantilevers built vertically in the Z direction").
- **A clip that hangs from a part printed flat stands upright, and that's acceptable:** a lid printed on its top face, with clips hanging from it to hold a fan or to catch the base, can't print them any other way, and it's how fans are held. Keep it to the rules below, give its root the fillet, bend it only to fit and unfit, and say at the ask that it bends across its layers, so a test print judges it. Don't turn the part, or the clip, to dodge the rule.
- Give a clip at least 5 mm of width and a fillet at its root at least half the arm's thickness ([E1]).
- Its hook catches by about the arm's thickness or less, with 0.5 mm of clearance round it ([E1]).
- Whether a clip bends without breaking isn't read by any bench command: it's an eye need, and the request's to judge from a print.

**Bought screws.** Through the lid at a clearance hole (`ref-fasteners`, medium), into a boss on the base with a heat-set insert or a tapped hole (`references/mounting.md`, the skill `mounting-boards`). Counterbore the head so it sits flush, and keep the boss's wall to `ref-fdm`'s figure.

## A fan held in the lid

A fan held without screws sits in a recess the fan's frame plus the play on each face, against a ledge that stops it, held by clips that hook over its frame: two or more on opposite sides. The clips follow the rules above. Centre the fan on the point the request names, such as over a processor, from the references, never by eye. How firmly it's held is an eye need.

## Sources

- [M1]: measured for mqucifer/print-shop#65: square tubes 40 × 30 × 6 mm with walls from 1.50 to 2.60 mm, 0.02 mm apart, sliced by OrcaSlicer 2.4.2 with the Sponsor's profile (the process `0.20mm Standard @Elegoo CC 0.4 nozzle`: 2 wall loops, 0.42 mm outer and 0.45 mm inner lines, 0.2 mm layers, the classic wall generator). No gap fill from 1.50 to 1.66 mm; gap fill in every layer from 1.68 to 2.26 mm, its narrowest line the wall less 1.57 mm; infill from 2.28 mm.
- [E1]: https://www.hubs.com/knowledge-base/how-design-snap-fit-joints-3d-printing/ ("If feasible, avoid snap-fit cantilevers built vertically in the Z direction"; 0.5 mm nominal clearance for FDM; a root fillet at least 0.5 times the base thickness; a minimum width of 5 mm)
- [E2]: https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135 ("Walls thinner than one nozzle perimeter are not printable"; at least 0.3 mm for movable parts)
- `ref-fdm`'s [P1] to [P11], in the `references` skill.
