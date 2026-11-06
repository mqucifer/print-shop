---
name: mounting-boards
description: Mounts boards in a printed enclosure, on bought standoffs or printed bosses, with bought screws or heat-set inserts, each hole and wall sized from the references' tables. Use when placing a board's mounting holes, sizing a boss, or choosing between an insert, a tapped hole and a bought standoff.
---

# Bosses, standoffs and screws

## Where they go

Every hole's position comes from the board's reference: its mounting holes' centres, read with `info` or from its outline, never by eye. Name the hole pattern once and place every boss from it, so a change moves them all. The board rests on its standoffs' tops, its underside at the standoffs' height above the floor.

## Bought standoffs

When the request gives standoffs as references, the case holds them: a hole through the floor for each one's screw, at a clearance hole (`ref-fasteners`, medium), counterbored from below so the screw's head sits flush. A boss isn't needed.

## Printed bosses

A boss is a cylinder from the floor, its top where the board's underside sits.

| Into the boss | Hole | Wall round it | Source |
|---|---|---|---|
| a heat-set insert | `ref-fdm`'s hole for that size, at least 1 mm deeper than the insert | at least `ref-fdm`'s minimum wall for that size | `ref-fdm` [P8] [P9] [P10] |
| a screw cutting its own thread | `ref-fasteners`'s tap drill for that size, a starting point: printed holes come out small | | `ref-fasteners`; `ref-fdm` [P5] [P6] |
| a screw passing through, into a nut | `ref-fasteners`'s clearance hole, with a nut trap below | | `ref-fasteners` |

So a boss is at least the hole plus twice its wall across. Round its foot into the floor with a fillet, which stiffens it, and keep its top flat for the board.

Check each boss's wall with `wall base.step --hole X,Y,Z --min-wall W`, any point inside the hole, W the figure above or the request's.

## The board's room

A board rests on its standoffs, so it touches them, and `clearance` of the board and the base reads gap 0. Check what must clear: the case's walls and lid against the board and its tallest parts, each as one pair, `clearance base.step references/board.step --min-gap G`, with the board's own standoffs in another reference or in no need.
