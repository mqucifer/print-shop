---
name: designing-stained-glass
description: Designs a printed stained-glass piece, a frame of leading with panes set in at a pause, from the 2D layout through each bench check. Use when the request's kind is stained glass, or it asks for panes, leading, lips, a sun-catcher or a panel that light passes through, and before settling needs for light, lips, pane thickness or a hanging hole.
---

# Designing stained glass

A printed stained-glass piece is a frame printed in plastic, with panes printed apart and set into it at a pause, then printed over so the frame's shape holds them. Nothing is glued. This skill is the workflow, the terms, and the exact checks. The design itself is yours.

## The terms

Each is used in one sense only, here and in every bench reply.

- **cell**: one region of the 2D layout, where one pane goes.
- **pane**: the piece of glass for one cell: its own file, such as `pane_1.step`.
- **leading**: the frame between cells, as a person sees it from the front. It has to be one piece.
- **pocket**: the space in the frame a pane sits in, from the pocket's floor to its top.
- **play**: the gap all round between a pane's edge and its pocket's wall.
- **opening**: where light passes through the frame: the pocket less its lips.
- **lip**: the frame over a pane's edge, on each face. Its width is said before or after play.
- **lip before play**: from the pocket's wall to the opening's edge.
- **lip after play**: the lip before play less the play: how far the lip covers the pane, with the pane in the middle of its pocket. `capture`'s `least_lip_mm`.
- **rim**: a request's figure for a lip before play, unless it says after: its **full rim**, the most a pane has, and its **smallest rim**, the least.

With play of 0.25 mm and the opening 1.2 mm in from the pocket's wall, the lip before play is 1.2 mm and the lip after play 0.95 mm.

## The workflow

Copy this checklist into your notes and tick it off as you go:

```
- [ ] 1. How the piece is built, written down
- [ ] 2. The 2D layout, checked
- [ ] 3. Every height named once
- [ ] 4. The needs, each with what it measures and the request's own limits
- [ ] 5. The frame and the panes in 3D
- [ ] 6. Each bench check: run it, fix, run it again, until it passes
- [ ] 7. `ask` or `done`
```

If the request has `## Stops`, each stop goes where it names: the plan after step 1, the 2D layout after step 2. Show it with `ask` and wait for the pick before going on.

### 1. How the piece is built

Before any layout, write down how the reference and the request are built, not only what they show: the cells, the leading between them, and what joins the leading into one piece. A ring of cells is held to the next ring and to the border by bars across it. A ring with no bars falls out of the frame as a separate piece, and a floor under the panes isn't the fix, as it blocks the light.

### 2. The 2D layout

Lay the piece out flat first, as numbers, and check it with Shapely before any CAD. The numbers are the layout: each circle's centre and radius, each bar's angle and width, each straight cell's corners, the leading, the play and the rims, as constants at the top of the script. Shapely checks them in 2D. In 3D, step 5 builds the solids from the same numbers with CadQuery's own curves, never from Shapely's points: Shapely's circles are polygons.

- Split the outline into cells that share their edges. Don't cut holes from a solid: that leaves separate bodies, and panes that merge.
- Take every shape of a cell from that one cell, by offsets, so they always fit:

```python
import shapely

leading = 1.0  # the leading's width between cells, mm
play = 0.25  # from the request
rim = 1.2  # the request's full rim: a lip before play, from the pocket's wall
pocket = cell.buffer(-leading / 2, join_style="mitre")
pane = pocket.buffer(-play, join_style="mitre")
opening = pocket.buffer(-rim, join_style="mitre")
frame_2d = outline.difference(
    shapely.unary_union([c.buffer(-leading / 2, join_style="mitre") for c in cells])
)
assert frame_2d.geom_type == "Polygon", "the leading is in pieces: add bars"
```

- **The leading is one piece:** `frame_2d.geom_type == "Polygon"`. A `MultiPolygon` is a frame in pieces. Add bars until it's one, before any CAD.
- **Every rim starts at the full rim:** give every cell the request's full rim, and never less than its smallest rim. Don't work out a smaller one yourself. After you build, `capture` measures how much of each pane its rim leaves visible, and tells you which panes need a smaller rim and exactly what, or which pane to remove (step 6). Keep the rim of each cell a number of its own in the script, so one can change.
- **No sharp tips:** a cell that tapers to a point (a triangle's apex, a feather's tip) gets its tip cut off or rounded in 2D, so no pane or pocket is narrower than two nozzle widths (0.8 mm).
- Draw the layout to a picture with matplotlib and `read` it, each cell in its colour, before going on.

### 3. Heights

Name each height once at the top of the script, and derive every other from those: the pocket's floor, the pane's thickness, the lip's thickness above and below. The pocket's top is its floor plus the pane's thickness plus the room the request asks over it. Pass z to one helper that makes every solid, and print each solid's z range after each run. Parts at the wrong height cost more runs than anything else.

### 4. The needs

Each bench need gives `measures`, what it's about, and its command has to measure that. Every limit is the request's, quoted. If the request gives no figure for something it asks, ask with `questions`: there's no default.

| The request asks | `measures` | Its check |
|---|---|---|
| light through each pane | `light` | `light FRAME PANE... --min-open P` (P in percent) |
| panes no thicker than | `pane-thickness` | `thickness PANE... --max T` |
| each pane held by its lips, at a full rim and a smallest rim, with a share of each pane visible | `pane-hold` | `capture FRAME --pocket-z Z --clearance PLAY --pane-thickness T --full-rim R --least-rim R2 --min-visible P --panes N` |
| each pane held by one lip all round | `pane-hold` | `capture FRAME --pocket-z Z --clearance PLAY --pane-thickness T --min-lip M --panes N` |
| the frame in one piece | `one-piece` | `thin FRAME --one-body` |
| wall round the hanging hole | `wall` | `wall FRAME --hole X,Y,Z --min-wall W` |
| room over the pane at the pause | `insert` | `pause FRAME --pocket-z Z --pane-thickness T --min-room R` |

- `--pocket-z` is a height inside the pocket, never on a level face: the pocket's floor plus half the pane's thickness.
- `--full-rim` and `--least-rim` are the request's rims, before play, as it gives them; if it gives them after play, add the play. `--min-visible` is its share of each pane visible, in percent. `--clearance` is the play.
- `--min-lip` is one lip for every pane, the lip after play less the play again, for a pane slid to one side. Use it only where the request gives one lip and no visible share.
- `--panes` is the number of panes, from the layout.
- The hanging need is `wall` at the hole. `info`'s size and `thin`'s thinnest figures are the whole part's, not the wall round a hole.

`references/checks.md` (the skill `checking-stained-glass`) says what each of these answers and how to read it.

### 5. The frame and the panes

Build the solids last, from the layout's numbers: the frame with each pocket and opening cut at its heights, and each pane as its own file. The panes are named by cell, `pane_1.step` and so on, and the frame is one file, or two if the request prints a cap apart.

**Build curves as curves.** A circle, an arc or a rounded corner is built with CadQuery's own: `circle`, `threePointArc` or `radiusArc` between two points, `offset2D(d, kind="arc")` to round corners, or a `Sketch`. Each shape that's an offset of a cell in 2D is the cell built again from its numbers, further in: the pocket `leading / 2` in, the pane `leading / 2 + play` in, the opening `leading / 2 + rim` in. A circle `inset` in is `circle(r - inset)`, a ring `circle(r1 - inset).circle(r0 + inset)`, and a bar between cells is cut `2 * inset` wide. One function of the inset makes them all, so they always fit:

```python
import math

import cadquery as cq

R_OUT, R_RING, R_DISC, BARS = 40.0, 36.0, 15.0, 6  # a disc, a ring of cells split by bars
LEADING, PLAY, RIM = 1.0, 0.25, 1.2
FLOOR, PANE, ROOM, HEIGHT = 1.5, 1.0, 0.2, 4.0  # step 3's heights


def cells(inset, z0, z1):
    """Every cell, `inset` in from its edges, from z0 to z1: true circles, cut by straight bars."""
    at = cq.Workplane("XY").workplane(offset=z0)
    ring = at.circle(R_RING - inset).circle(R_DISC + inset).extrude(z1 - z0)
    for k in range(BARS):
        bar = at.transformed(rotate=(0, 0, 360 * k / BARS)).center((R_DISC + R_RING) / 2, 0)
        ring = ring.cut(bar.rect(R_RING - R_DISC + 2, 2 * inset).extrude(z1 - z0))
    return ring.union(at.circle(R_DISC - inset).extrude(z1 - z0))


frame = (
    cq.Workplane("XY").circle(R_OUT).extrude(HEIGHT)
    .cut(cells(LEADING / 2, FLOOR, FLOOR + PANE + ROOM))  # the pockets
    .cut(cells(LEADING / 2 + RIM, 0, HEIGHT))  # the openings, through
)
panes = cells(LEADING / 2 + PLAY, FLOOR, FLOOR + PANE).solids().vals()
assert len(frame.solids().vals()) == 1 and len(panes) == 1 + BARS
assert {f.geomType() for f in frame.val().Faces()} <= {"PLANE", "CYLINDER"}  # no strips
```

- **Check it's the layout you checked:** each solid's volume over its height is its cell's area in Shapely, at `quad_segs=64`, to 0.1%.
- **Straight sides are exact as polygons.** A cell whose sides are all straight can be extruded from its Shapely offset's points with `polyline(...).close()`.
- **Where only a polygon will do,** such as a free curve with no centre or radius, give its curves a `quad_segs` and know the error: a circle of radius R is off by up to R·(1 − cos(45°/quad_segs)). At Shapely's default of 16, an 80 mm ring is 0.048 mm off, a fifth of 0.25 mm of play; at 64 it's 0.003 mm. Or draw it with `spline` through the points, which is a curve.
- **Never extrude a circle or a round join from Shapely with `polyline`.** It's stored as flat strips, a face for each segment: such a ring was 130 faces and 491 kB where circles make 4 faces and 10 kB, and the slicer loses its arcs. `info` reports the flat faces under 1 mm across.

`cadquery-sketch-extrude` has the same step for any part.

### 6. Each bench check

Run each check at the need's own command, the one you'll settle or settled. Then:

1. Read the verdict and the numbers before any picture.
2. If it fails, fix the design: the layout, a lip, a height. Never change the check's limit, or its settings, to pass. A `--min-lip`, `--min-open` or `--max` other than the need's isn't the need, and `done` runs the need's.
3. Run the same check again.
4. Go on to the next check only when this one passes.

Run them in this order, as each depends on the one before:

1. `thin FRAME --one-body`: the frame is one piece.
2. `thin PANE` on **every** pane, one file at a time. A pane that fails `thin` can't be printed, whatever the others do: fix that file and run `thin` on it again before touching the next.
3. `capture`: every pane is held on both faces, and each rim is right. Its reply names each pane to change, and what to do:
   - "set this pane's rim to X mm before play": set that cell's rim to exactly X, nothing else, and run `capture` again.
   - "remove this pane": even the smallest rim hides too much of it. Take its cell out of the layout, joining it to the leading or to a neighbouring cell, and run every check again.
4. `light`: every pane lets the light through.
5. `thickness`: no pane is over the request's limit.
6. `wall` at the hanging hole, and `pause`, if the request asks for them.

A change for one check can break another, as a narrower rim lets more light through and holds less. After a fix, run the earlier checks again.

When a check keeps failing and you can't see why, cut a section at its height (`render FRAME --section Z`) and read its verdict, then its picture. If it still fails after that, show what you have with `ask` and say what's in the way.

### 7. Asking and done

`ask` draws a sheet: give each pane its colour and the frame grey in `looks`, and add a cut through a pane in its pocket (`cuts`: `x=X,y=Y0:Y1`). Say what each picture shows as drawn. `done` names the frame and every pane in `part`.
