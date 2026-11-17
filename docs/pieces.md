# The piece map

A stained-glass panel as closed pieces of glass, each with its colour, and the lead lines between them (M20 in mqucifer/project-print `ROADMAP.md`, mqucifer/print-shop#152, mqucifer/print-shop#153). It's the handover between what draws a panel's pieces and what fixes and builds them. Two things draw one: a traced picture (`pieces trace`), and rules for a repeated pattern (mqucifer/print-shop#154). Every `pieces` command reads and writes this one format (`bench/piecemap.py`), so both ways in share everything after the pieces are drawn. [The bench](bench.md#a-piece-map-traced-checked-fixed-and-built) says what each command answers.

## The format

JSON, in millimetres, x to the right and y up from the panel's bottom-left:

```json
{
 "piece_map": 1,
 "units": "mm",
 "panel": {"width_mm": 220.0, "height_mm": 160.0},
 "lead_mm": 1.4,
 "colours": {"blue": {"rgb": [40, 110, 220]}},
 "pieces": [
  {"id": 1, "colour": "blue", "outline": [[0, 80], [0, 40.7], [39.3, 40.7], [39.3, 80]]},
  {"id": 2, "colour": "gold", "outline": [[40.7, 80], [40.7, 40.7], [120, 40.7], [120, 80]],
   "holes": [[[60, 55], [70, 55], [70, 65], [60, 65]]]}
 ],
 "flags": [{"flag": "sliver", "piece": 7, "figure": 2.0, "limit": 3.0, "at": [41.7, 60.4]}],
 "source": {"by": "pieces trace", "image": "scene.png", "px_per_mm": 5.0, "glass_share_pct": 91.2},
 "history": [{"fix": "merge", "a": 12, "b": 9}]
}
```

| Key | It holds | Read by |
|---|---|---|
| `piece_map` | the format's version, 1 | every command: anything else isn't a piece map |
| `panel` | the panel's size, a rectangle from (0, 0), or with `outline` any closed shape | every command: the lead is the panel less every piece |
| `lead_mm` | the lead line's usual width | `split` and `extend`, which draw a new line at it; `check`, whose dead ends and wide lead are sized by it; `build`, whose border is twice it unless given |
| `colours` | each colour's name and its `rgb` | `build`, which writes one pane file a colour; the pictures |
| `pieces` | each piece: `id`, a whole number from 1; `colour`; `outline`, three points or more; `holes`, where another piece or a line lies inside it; and from a trace `paint`, the picture's colour over it and how far it runs across | every command |
| `thickness_mm` (a piece's) | its pane's own thickness, where it isn't the frame's usual | `build` refuses a map that names several: panes of several thicknesses in one frame aren't built yet |
| `flags` | the rule breaks as `trace` or a fix last read them | a reader only: `check` reads the pieces again, and no command trusts these |
| `source` | what drew the map, and its glass share then | `check` and each fix, which say the share now against it |
| `history` | each fix made, in order | a reader only |

**A piece is its glass.** Its outline is where the glass meets the lead, so the lead is the panel less every piece and no line is stored twice. Two pieces never overlap, and every piece lies inside the panel: a map that breaks either is refused, saying which pieces. A map from rules writes each cell inset by half its lead.

**A map is written to a tenth of a micron,** and read back before it's saved, so no command writes a map the next can't read. A fix writes a new file and leaves the one it was given: `scene.pieces.json`, then `scene.pieces-1.json`, `-2` and so on, or `--to FILE`.

## What a map from rules needs

For mqucifer/print-shop#154's dome, and any other: `piece_map`, `panel`, `lead_mm`, and `pieces` with `id`, `colour` and `outline`. `colours` gives the pictures their colours, and `source.glass_share_pct` gives later readings something to be said against. Curves are points close enough that the lines and arcs `build` fits to them, within `--fit` of each point, are the curve meant: `tests/bench_cases/planted_pieces.py` writes such a map (`rules.json`).
