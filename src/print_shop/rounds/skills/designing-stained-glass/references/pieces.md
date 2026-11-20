---
name: reading-a-piece-map
description: Explains the stained-glass piece map and what each pieces command answers (trace, check, merge, split, extend, drop-line, widen, retrace, fill, paint, move-line, fix, build, dome, flatten, came, ring and seam), with the numbers in each reply. Use when tracing a stained-glass picture, reading a pieces reply, choosing a fix for a flagged piece, building a frame and panes from a map, making a dome shade's map from bands and cutting it into flat slices, or building a slice's seams, its came strips and rings, and reading how two slices meet.
---

# The piece map and the `pieces` commands

A piece map is a stained-glass panel as JSON: each piece of glass with its number, its colour and
its outline, in mm, x right and y up from the panel's bottom-left. A piece is its glass, so the
lead is the panel less every piece. `lead_mm` is the lead line's usual width, which a new line is
drawn at. Don't edit a map by hand or with code of yours: each command writes a new one.

| Command | Answers |
|---|---|
| `pieces trace PICTURE --width MM [--palette blue,red,gold=#f5c832] [--dark N] [--to FILE]` | the picture's pieces as a map, `PICTURE's name.pieces.json`: the panel is the picture inside its dark outer frame, MM wide; the lead is its dark pixels; each patch of colour between is a piece, numbered a row at a time from the top-left, in the nearest colour of `--palette`. Its picture comes to you: the picture, and beside it the map with each piece numbered and each rule break marked |
| `pieces check MAP [--min-width MM] [--min-area MM2] [--min-neck MM] [--max-notch R] [--max-colour-run N] [--max-lead MM] [--min-lead MM] [--lip FULL[,LEAST]] [--min-visible S]` | the map against glass-cutting rules: each flag with its piece, its figure, its limit and where; the glass share against the trace's; the lead's widest point. `FAIL` with any flag, `OK` with none |
| `pieces merge MAP A B [--to FILE]` | piece A joins its neighbour B, which keeps its number and colour, and the line between them is glass. Refused when they don't touch, naming A's neighbours |
| `pieces split MAP ID --line x1,y1,x2,y2,... [--lead MM] [--to FILE]` | a new lead line across piece ID along those points, from one of its edges to another: the largest part keeps the number, each other takes a new one |
| `pieces extend MAP --at X,Y [--aim X,Y] [--to FILE]` | the dead-end line at X,Y runs straight on, or toward `--aim`, until it leaves the piece's glass: the piece is cut in two |
| `pieces drop-line MAP --at X,Y [--to FILE]` | the dead-end line at X,Y is filled with its piece's glass. On a line between two pieces, the smaller joins the larger |
| `pieces widen MAP A B [--lead MM] [--to FILE]` | the line between A and B, where it's under MM (0.4, a nozzle's width), becomes that wide: each piece's edge moves back by half of what's short, only along that stretch, so the line keeps its path. `widened_from_mm`, `widened_to_mm` and `widened_along_mm` say how much and how far |
| `pieces retrace MAP --box X0,Y0,X1,Y1 [--dark N] [--palette ...] [--image FILE] [--to FILE]` | the picture traced again inside the box, at `--dark` (the map's own unless given), and spliced in: nothing outside the box changes, a piece that reaches out of it keeps its number and colour, and a piece wholly inside takes the nearest of `--palette` (the map's colours). `pieces_in_box_before` and `_after`, `lines_in_box_before` and `_after`, `dead_ends_in_box_before` and `_after`; `lines_across_box_edge`; and `lines_ending_at_box_edge`, each with where |
| `pieces fill MAP --at X,Y [--lead MM] [--to FILE]` | the patch of lead at X,Y, lead wider than a line and a half, becomes glass: each piece round it takes the part nearer to it than to any other, and a line as wide as the map's is left between each two, drawn along its own path. A scrap of lead beside the patch, too short to be a line, is that piece's glass too (`patch_scraps_count`). `patch_mm2`, and each piece's share under `patch_shares`. Refused on a line. With `retrace` and `move-line`, it says each changed piece's outline points before and after and whether it builds (`outlines`), and is `FAIL`, naming the piece, where one that built before doesn't now |
| `pieces paint MAP ID COLOUR [--image FILE] [--to FILE]` | piece ID takes COLOUR: one of the map's, one the bench has a name for, or a new one as `NAME=#rrggbb`. `colour_off` is how far it is from the picture's own colour in that piece, as a distance in RGB, and `colour_was_off` how far the old one was |
| `pieces move-line MAP A B --line x1,y1,x2,y2,... [--to FILE]` | the line between neighbours A and B drawn again along those points, as wide as it was, from one end of the old line to the other: the glass each side follows it. Refused, naming which, where the path crosses another line, or leaves A or B a sliver, a small piece or a neck |
| `pieces fix MAP STEPS [--to FILE]` | a file of those fixes, made in order in one call: each step's result, then the pieces, the glass share and the lead's widest point before and after them all. STEPS is a JSON list, each step `{"fix": "merge", "a": A, "b": B}`, `{"fix": "split", "piece": ID, "line": [[x1, y1], [x2, y2]]}`, `{"fix": "extend", "at": [x, y]}`, `{"fix": "drop-line", "at": [x, y]}`, `{"fix": "widen", "a": A, "b": B}`, `{"fix": "retrace", "box": [x0, y0, x1, y1], "dark": N}`, `{"fix": "fill", "at": [x, y]}`, `{"fix": "paint", "piece": ID, "colour": NAME, "rgb": [r, g, b]}` or `{"fix": "move-line", "a": A, "b": B, "line": [[x1, y1], [x2, y2]]}`. `PARTIAL` where a step is refused: the steps before it are made, the map up to it is written, and none after it is tried |
| `pieces build MAP --thickness T --pane P --lip FULL[,LEAST] --play C [--min-visible S] [--layer L] [--border MM] [--fit MM]` | `frame.step` and `pane_COLOUR.step` for each colour: each pane its piece inset by the play, each pocket the pane and the play round it, and a lip over the pane's edge on both faces, FULL wide on every piece, or with `--min-visible S` the rim `capture` asks of each pane: FULL where it keeps S percent visible, else the widest that does, never under LEAST. A piece with a waist the lip closes over is refused, with the line to split it along |
| `pieces dome --diameter D --height H --repeats N --bands FILE [--top MM] [--to FILE]` | a dome shade's map, `dome.pieces.json`: the bands of FILE from the top to the hem, laid N times round a dome D across at its hem and H tall, open at its top. The map is the dome's chart, the way round by the way up from the hem; its picture comes to you: the dome from the side and from above, one slice laid flat with its folds, and the chart |
| `pieces flatten MAP [--slices N] [--rows R] [--bed X,Y] [--max-off MM] [--to NAME]` | the dome's map cut along lead lines into N slices, each laid flat as its own map, `MAP's name.slice-01.pieces.json` and so on: each slice's size and whether it fits the bed, the angle each fold bends through, and which slices are the same map |
| `pieces build SLICE ... --seam [--seam-gap MM]` | a slice's frame with its two long edges built as seams: each straight length bevelled by half the angle between the two slices there, set back half the gap (0.6 mm), and with a groove in the inner face for a came strip's rib. `seam` has each length's bevel, `grooves` each groove. `FAIL`, with nothing built, where a face would leave under 0.4 mm of lead beside a pocket, or a groove under 0.4 mm beside an opening, with each place and `lead_needed_mm` |
| `pieces came SLICE --thickness T --lip L [--gap MM] [--bar REACH,THICK]` | the came strips for the slice's right seam, one for each straight length, each a bar inside the dome with a rib into each slice's groove: `strips` has each one's file, its length, the angle between its ribs and how it prints (`least_feature_mm`, `on_bed_mm2`). Nothing lies over the outer face. `came/placed-A-B.step` is the strips on the seam. `FAIL` where a strip would reach a pane's opening, has anything under two lines, or needs a support |
| `pieces ring SLICE --thickness T [--border MM] [--hole MM]` | the rings: `hem-ring`, and the top ring as `top-seat` and `top-cap`, each with a groove for every slice's edge, and each with a `-laid` file for the bed. `edges_held` of `edges_asked` says the edges are in the grooves |
| `pieces seam A B --frames FA FB [--came FILE] [--rings FILE...]` | two neighbouring slices' built frames, bent by their folds and placed round the dome: how far apart their faces stand (`gap_mm`, against `--gap`), what they share (`overlap_mm3`); with `--came`, `over_outer_face`, `strips_holding`, `press_mm` and `strips_over_openings`; with `--rings`, `edges_in_rings`. It writes both as placed (`placed`), for `render` |

## Reading them

- **The limits** are the bench's unless you give the request's: a sliver is under 3 mm at its widest, a small piece under 25 mm², a neck under 1.2 mm with glass 3 mm wide on both sides, a notch deeper than its mouth is wide, a gradient a run of more than 40 of 255, wide lead over three times the map's usual lead, and thin lead under 0.4 mm between two pieces, a nozzle's width.
- **Specks aren't dead ends.** `pieces trace` fills a dark speck inside one piece, joined to no line and shorter than the lead is wide, as that piece's glass, and says how many (`specks_filled`). Each `dead-end` left is a real line: fix each.
- **A spur isn't a neck.** A thin end of a piece, with wide glass on one side of it only, isn't flagged: the pane and the opening simply lose it, and `pieces build` says how much glass that was.
- **`--at` is a point on the line,** as `pieces check` gives each dead end's: its end in the glass, or anywhere along it.
- **A split's line** needs only reach both edges: it's run on a lead's width past each end. A line that ends in the piece's glass is refused, saying which end.
- **Every fix's reply** has `map`, the new file; `pieces_before` and `pieces_after`; `glass_share_before_pct` and `glass_share_after_pct`; `lead_widest_before_mm` and `lead_widest_after_mm`; and under `changed` each changed piece as `pieces check` reads it, with its flags. No other piece's outline changes.
- **A piece a fix has changed has its colour read from the picture again,** so two colours a merge joined are flagged `gradient`. The picture is the one the trace read. Where it isn't found, `pieces check` is `PARTIAL` and its verdict names the pieces whose colour wasn't read (`colour_unread`): give `--image FILE`, the picture, and check again.
- **`pieces trace --dark N`,** N other than 40, says its `pieces_count`, `lines_count` and `dead_ends_count` against `pieces_at_default_dark`, `lines_at_default_dark` and `dead_ends_at_default_dark`: what that darkness cost over the whole picture.
- **`pieces build`'s reply** has `pause_z`, the height the print pauses at; `pocket_z`, `panes_count`, and `glass`, which is what `done`'s `glass` takes; `least_lip_before_play_mm` and each piece's lip under `lips`; and `glass_left_as_lead_mm2`, the tips and spurs no nozzle prints as a pane. A piece that can't be built is `FAIL`, and nothing is built: its line says what stops it, with where, and the command that fixes it.
- **Its curves:** a turn sharper than 30° is a corner; between corners the outline is lines and arcs within `--fit` of its points. A pane's corners are rounded by half a nozzle, as the nozzle rounds them.
- **`pieces fix`'s reply** has `steps_given` and `steps_made`, and under `steps` each step with `made`, what it did (`said`), and `pieces_after` and `glass_share_after_pct` after it; then what every fix's reply has, for all the steps together. A map's `history` is a steps file: the fixes it has had, as they were given.
- **A second reading has its own resolution.** A neck is read by the nearest points facing each other, and again by twice its pixels' distance to the edge there (`narrowest_by_pixels_mm`, good to `narrowest_px_mm`). A notch's depth by its pixels (`notch_depth_by_pixels_mm`) is short by whatever of it is too thin for a pixel, up to `notch_px_mm`. Two readings closer than that are the same, and a `note:` says what's between them. Rays square to a neck's edges are a note too: on a slant they measure another line. A dead end the pixels can't show, too thin or with another piece too near, is flagged by its outline with a note. A note is said only where a rule flags the place.
- **`pieces check` reads what `pieces build` reads.** Every piece's pane is read and its outline fitted as the build does it, at the build's `--fit` and `--play`, with no `--lip` needed. A piece the play leaves no one pane of, or whose outline can't be fitted, is flagged `no-pane` in the build's words: the narrow place that cuts it and where, or for an outline that can't be fitted, what its pixels say: one pane, and it's the outline's points; none or two, and it's a thin place.
- **`pieces check --lip L`** makes `pieces build`'s own plan at that lip (and `--play`, `--fit`, the build's unless given), and flags each piece the build would refuse for want of one opening (`no-opening`), in the build's words: a waist comes with the line to split it along.
- **`pieces merge` of two pieces with no line between them,** further apart than a lead and a quarter, joins them across the lead between: the reply says how far apart they were.
- **`DISAGREE`** means two readings of the same thing are on opposite sides of a limit, each beyond the other's resolution: read both figures, and don't pick one. A fix that isn't sure of a piece says which.

## A dome's bands file

JSON, the bands from the top to the hem. Each has a `pattern`, its `height_mm` down the dome, the pattern's own figures, and its cells' `colours`. The heights add up to the dome's side, which `pieces dome` says (`meridian_mm`); one band may leave its height out, and takes what the others leave.

```json
{"lead_mm": 1.4, "bands": [
 {"pattern": "grid", "height_mm": 48, "rows": 2, "columns": 2, "colours": ["amber", "green"]},
 {"pattern": "swag", "height_mm": 38, "depth_mm": 12, "colours": ["blue", "cream"]},
 {"pattern": "jewels", "height_mm": 18, "diameter_mm": 8, "count": 2, "colours": ["red", "gold"]},
 {"pattern": "strip", "height_mm": 8, "colours": ["green"]},
 {"pattern": "triangles", "height_mm": 16, "count": 2, "colours": ["amber", "blue"]},
 {"pattern": "scallop", "depth_mm": 10, "colours": ["red"]}
]}
```

| Pattern | In one repeat | Figures | `colours` |
|---|---|---|---|
| `grid` | `rows` by `columns` cells | `rows`, `columns` | by row + column, round the list |
| `swag` | a drape hung between the repeat's sides, over its ground | `depth_mm`, `shoulder_mm` | the drape, the ground |
| `jewels` | `count` round jewels, each with its ground in two pieces | `diameter_mm` of the glass, `count` | the jewel, the ground |
| `strip` | `count` pieces side by side | `count` | each in turn |
| `triangles` | `count` point down, the rest point up between them | `count` | down, up |
| `scallop` | the hem: `count` cells with a curved bottom edge. The last band only | `depth_mm`, `count` | each in turn |

- **A dome's map isn't checked or built:** it's drawn wider than the dome near its top. `pieces flatten` it, then `pieces check` and `pieces build` each different slice.
- **`alike`** groups the slices that are the same map: check and build one of each group.
- **`folds`** are the lead lines a slice bends along, each with `y_mm` on the slice's map and `bend_deg`. A slice bends only there: don't merge two pieces across a fold.
- **`pieces build MAP --border B`** with B half the map's lead, as the reply gives it: the frame's edge is then the middle of the cut line, and two slices meet in one line.
- **`rows_count`** is how many flat rows a slice is: every lead line all the way round is a fold, unless `--rows R` asks for fewer. `rows_choice` says, for each count, how far the rows would stand from the dome and the sharpest bend: fewer rows are fewer bends, each sharper, and further off.
- **`rows_off_dome_mm`** is how far the flat rows stand from the curved dome. It's a reading unless the request limits it: then give `--max-off`, and with `FAIL`, cut more slices or add a band.
- **`--seam` on every slice that's joined,** with the same `--seam-gap`. The frame's side is then set by the cut, and `--border` is the frame at the top and the hem.
- **A refused seam names what to change:** a wider lead in the bands file, `lead_needed_mm`, or a smaller `--seam-gap`.
- **`pieces came` and `pieces ring` take one slice** and the frames' thickness. Give `came` the `--gap` the slices were built with and the least `--lip`, and `ring` the `--border`. Where the slices are alike, one set of strips is every seam's.
- **`pieces seam`** takes two neighbours, slice N and slice N + 1. `INTERFERE` means a part was made for another thickness, gap or border: make it again with the frames' own figures.
