---
name: reading-a-piece-map
description: Explains the stained-glass piece map and what each pieces command answers (trace, check, merge, split, extend, drop-line and build), with the numbers in each reply. Use when tracing a stained-glass picture, reading a pieces reply, choosing a fix for a flagged piece, or building a frame and panes from a map.
---

# The piece map and the `pieces` commands

A piece map is a stained-glass panel as JSON: each piece of glass with its number, its colour and
its outline, in mm, x right and y up from the panel's bottom-left. A piece is its glass, so the
lead is the panel less every piece. `lead_mm` is the lead line's usual width, which a new line is
drawn at. Don't edit a map by hand or with code of yours: each command writes a new one.

| Command | Answers |
|---|---|
| `pieces trace PICTURE --width MM [--palette blue,red,gold=#f5c832] [--dark N] [--to FILE]` | the picture's pieces as a map, `PICTURE's name.pieces.json`: the panel is the picture inside its dark outer frame, MM wide; the lead is its dark pixels; each patch of colour between is a piece, numbered a row at a time from the top-left, in the nearest colour of `--palette`. Its picture comes to you: the picture, and beside it the map with each piece numbered and each rule break marked |
| `pieces check MAP [--min-width MM] [--min-area MM2] [--min-neck MM] [--max-notch R] [--max-colour-run N] [--max-lead MM]` | the map against glass-cutting rules: each flag with its piece, its figure, its limit and where; the glass share against the trace's; the lead's widest point. `FAIL` with any flag, `OK` with none |
| `pieces merge MAP A B [--to FILE]` | piece A joins its neighbour B, which keeps its number and colour, and the line between them is glass. Refused when they don't touch, naming A's neighbours |
| `pieces split MAP ID --line x1,y1,x2,y2,... [--lead MM] [--to FILE]` | a new lead line across piece ID along those points, from one of its edges to another: the largest part keeps the number, each other takes a new one |
| `pieces extend MAP --at X,Y [--aim X,Y] [--to FILE]` | the dead-end line at X,Y runs straight on, or toward `--aim`, until it leaves the piece's glass: the piece is cut in two |
| `pieces drop-line MAP --at X,Y [--to FILE]` | the dead-end line at X,Y is filled with its piece's glass. On a line between two pieces, the smaller joins the larger |
| `pieces build MAP --thickness T --pane P --lip FULL,LEAST --play C [--layer L] [--border MM] [--fit MM]` | `frame.step` and `pane_COLOUR.step` for each colour: each pane its piece inset by the play, each pocket the pane and the play round it, and a lip over the pane's edge on both faces, FULL wide where the opening stays one piece and down to LEAST where it doesn't |

## Reading them

- **The limits** are the bench's unless you give the request's: a sliver is under 3 mm at its widest, a small piece under 25 mm², a neck under 1.2 mm with glass 3 mm wide on both sides, a notch deeper than its mouth is wide, a gradient a run of more than 40 of 255, and wide lead over three times the map's usual lead.
- **A spur isn't a neck.** A thin end of a piece, with wide glass on one side of it only, isn't flagged: the pane and the opening simply lose it, and `pieces build` says how much glass that was.
- **`--at` is a point on the line,** as `pieces check` gives each dead end's: its end in the glass, or anywhere along it.
- **A split's line** needs only reach both edges: it's run on a lead's width past each end. A line that ends in the piece's glass is refused, saying which end.
- **Every fix's reply** has `map`, the new file; `pieces_before` and `pieces_after`; `glass_share_before_pct` and `glass_share_after_pct`; `lead_widest_before_mm` and `lead_widest_after_mm`; and under `changed` each changed piece as `pieces check` reads it, with its flags. No other piece's outline changes.
- **`pieces build`'s reply** has `pause_z`, the height the print pauses at; `pocket_z`, `panes_count`, and `glass`, which is what `done`'s `glass` takes; `least_lip_before_play_mm` and each piece's lip under `lips`; and `glass_left_as_lead_mm2`, the tips and spurs no nozzle prints as a pane. A piece with no pane or no opening is `FAIL`, and nothing is built.
- **Its curves:** a turn sharper than 30° is a corner; between corners the outline is lines and arcs within `--fit` of its points. A pane's corners are rounded by half a nozzle, as the nozzle rounds them.
- **`DISAGREE`** means two readings of the same thing differ: read both figures, and don't pick one. A fix that isn't sure of a piece says which.
