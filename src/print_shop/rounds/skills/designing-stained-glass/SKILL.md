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

Lay the piece out flat first, with Shapely, and check it before any CAD.

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

Extrude the 2D shapes last: the frame from `frame_2d` with each pocket and opening cut at its heights, and each pane from its `pane` polygon, as its own file. The panes are named by cell, `pane_1.step` and so on, and the frame is one file, or two if the request prints a cap apart.

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
