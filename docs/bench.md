# The small CAD bench

Commands a session calls over files in its working directory, each answering in plain text and numbers, for the local model's first project (M18 in mqucifer/project-print `ROADMAP.md`, ADR 0010 there, and mqucifer/print-shop#14). A trial on 2026-11-01 showed tools reporting success when they were wrong, so **every answer gets a second check by a different method**, and the bench says when the two differ. It never touches the printer, and it holds no model call. Motion, balance, stiffness and strain come as their own issues when a project's number fails without them.

```
scripts/bench.py build                       # once: the one image, if the disk has room
scripts/bench.py check                       # the image and the disk against their limits
scripts/bench.py clearance a.step b.step     # any command below, over the current directory
scripts/bench.py python script.py            # your own script, in the same container
```

[The rounds](rounds.md) use it as a session's tool. A session inside the image can call the same commands directly, by mounting `src/print_shop/bench` read-only (`host.py` shows how). Nothing fences the model off: these are tools it may use, and it can write its own scripts.

## What comes back

The first line is the verdict. Then come the numbers, each with how it was read, then at least one `second check:` line, and last `saved:`, naming the text and JSON files written under `bench-out/`. Every figure a report may cite is in those files, and each says how it ran: the image's id, the code's commit, and the CPU, memory and network limits as the container read them.

| Exit | Verdicts | It means |
|---|---|---|
| 0 | `OK`, `CLEAR`, `TOUCHING` | both methods agree, and nothing's wrong |
| 1 | `FAIL`, `INTERFERE`, `TOO CLOSE`, `NO CHANGE`, `PARTIAL` | both methods agree that something's wrong |
| 2 | `DISAGREE`, `NO ANSWER` | there's no answer to rely on: read the numbers, and don't pick one |
| 3 | | the command itself was wrong |

An answer with no second check behind it becomes `NO ANSWER`, whatever it first said.

## The commands

| Command | Answers | First method | Second method |
|---|---|---|---|
| `info FILE` | size, volume, bodies, flat heights | the mesh | OCCT's volume on a STEP; sections stacked on an STL |
| `clearance FILE... [--min-gap G]` | overlap and gap, every pair, across files or a file's solids | Manifold on a fine mesh | OCCT `common`, `cut` and distance on a STEP; sections clipped by Shapely and sampled points on an STL |
| `readback BEFORE AFTER [--cutter C] [--expect-delta V] [--min-share S]` | volume before and after, and how much of a cutter was in the body | the mesh | OCCT, or stacked sections |
| `rays FILE --centre X,Y,Z [--axis A] [--d D] [--depth H]` | a cut feature measured from its axis: rays that hit nothing, the distance across, the wall beyond, the floor | rays on the mesh | a plane section, with a circle fit |
| `panes FRAME --pocket-z Z [--lip-z Z] --clearance C [--pane-thickness T]` | a pane from each opening, written to `panes/FRAME/`, its fit, and the edge with no lip | polygons on the section | the pane as a solid against the frame's walls over the pocket's height, and rays up from the edge |
| `capture FRAME --pocket-z Z --clearance C --pane-thickness T [--pane-floor-z F] [--min-lip M] [--layer L] [--panes N]` | whether each pane is held by shape on both faces, wherever its play lets it sit: the lip, how thick, where it's thin, and whether it can slide or swing out | sections stacked beyond the pocket, read as polygons | rays beside the pane's edge, and the pane as a solid swept out of the frame |
| `thin FILE [--nozzle N] [--layer L] [--min-body V] [--one-body]` | features under the nozzle's width, floors under a layer, and tiny pieces | sections opened by the nozzle | rays across each spot, level rays through the walls and straight rays through level floors, and OCCT's count of solids |
| `pause FRAME --pocket-z Z --pane-thickness T [--layer L] [--first-layer F] [--max-gap G] [--min-room R]` | the layer to pause after, and the room over the pane | sections layer by layer | the flat faces' heights |
| `wall FILE --hole X,Y,Z [--axis A] [--min-wall W]` | the least wall round one hole, along its whole length, and toward what: the part's outside or another opening | sections across the hole, a layer apart, as polygons | rays out to the hole's wall, then through it along the wall's normal |
| `render FILE... [--views ...] [--section Z...] [--close X,Y,Z,R[,VIEW]]... [--cut AXIS=AT[,AXIS=FROM:TO]]... [--look FILE=COLOUR[:LABEL]]... [--test-piece FILE]...`, or `render --variant NAME=FILE[,FILE...]... [--close ...]... [--cut ...]... [--look ...]... [--test-piece FILE]...` | pictures from the standard views, and a sheet of them, or a sheet with a labelled row for each variant, each file in its colour, numbered, and named in a legend. A close-up is R mm all round a point. A cut is every file cut by one plane | VTK, offscreen | the mesh projected by arithmetic against the painted outline, each close-up's triangles projected and filled against its pixels, the section's area against its pixels, and each file's area on a cut by the mesh against OCCT's section |
| `params SCRIPT --set NAME=VALUE [--max-left N]` | which dimensions, volumes and pieces moved when one parameter changed | each piece's figures, three runs | the meshes' symmetric difference, and a run with nothing changed as the control |

`params` reads a number assigned once at the top level of a design's script (`BaseCircle = 20.0`), and runs the script in a scratch folder, so the design isn't touched. A parameter that moves nothing is `NO CHANGE`. On a Fusion copy, changing `BaseCircle` left the outer ring and motif in place while Fusion reported everything healthy (mqucifer/print-shop#15): this names each piece that moved and each that didn't. One that leaves pieces behind is `PARTIAL`, and `--max-left N` says how many may stay and still read `OK`.

**Checks hold what a need says** (mqucifer/print-shop#24). Each of these can now say what a need says, and fails otherwise:

| A need's words | What used to pass | Now |
|---|---|---|
| A margin over a pane | No room at all: `--max-gap` is only a ceiling | `pause --min-room R`, and `least_room_over_pane_mm` is a number a need can limit |
| One connected solid | Several bodies | `thin --one-body` |
| Moves every piece | One piece moved of several | `params` is `PARTIAL` when pieces stay behind |
| So many panes | Fewer panes read than files | `capture --panes N` |

No command can tell what a need means, so these are asked for. With nothing asked, they read as they did, except `params`.

**A sheet shows what it asks about** (mqucifer/print-shop#26). On `cup-1` the renderer gave each file a colour from a list of 5, starting again at the 6th, so a pane was drawn in the frame's colour and didn't show as glass, nothing on a sheet said which colour was which file, and the model described the design it meant, not the picture.
- **`--look FILE=COLOUR[:LABEL]`** draws a file in its colour, a name (`blue`, `amber`, `clear`, perhaps `light` or `dark`) or `#rrggbb`, from `bench/colours.py`, which the harness reads too. A file with none takes the colour furthest from every other on the sheet. Each file is numbered where it is on every picture, after the picture's check, and the legend under the sheet names each number's file, label and colour. `drawn:` lines say the same in text, and `numbers.files` in the record.
- **What each view shows** is counted: each view is drawn again unlit, one exact colour a file, and `numbers.seen_px` has each file's pixels. A file no view shows is said, "not seen in any view drawn", as on `cup-1`, where a pane's colour hid it.
- **`--cut`** is a view: every file cut by one plane, each in its colour, the largest first so what sits in an opening, such as an island of frame in a pane's hole, is drawn over it. `z=3` is level; `x=50` is upright, seen from +x; `y=40` from the front; and `x=50,y=20:40` draws only that stretch, so a frame 6 mm tall is more than a line and a pane can be judged in its pocket. Each file's area is checked against OCCT's section of its exact surface, and the painted area against the polygons. Two files whose sections overlap are said, by how much, which no other picture shows. A cut is a column beside the views and close-ups, in plain and variant sheets.

**An island of frame inside an opening** (mqucifer/print-shop#27). On `cup-1` one pane had holes cut in it for others, so the frame's walls round those holes stood inside its opening, joined to the rest only through the lips above and below. Every command read that opening as one solid pane, and nothing said so: `capture` read the island's volume as a pane "too thick for its pocket", `pause` read it as a ceiling mid-pocket, and `render --section` drew the opening over it. Now an opening is the hole less any island inside it, so its pane has a hole round each, and:
- `capture`, `pause`, `panes` and `render --section` read it, and say "opening N has 1 island(s) of frame inside it". `capture` reads the lip round each hole of a pane as it does the outer edge, ring by ring.
- An island closer to the opening's edge than twice the play can't have pane between: the pane goes round it open, and `capture` says so with where. One a pane can't go round at all is named, with the opening's centre and size, and not read.
- `pause` reads what each layer leaves free of the whole opening, not the hole round one point of it, which could stand on an island.
- A section is drawn piece by piece, so an island shows. Its line counts the pieces and islands, and one that fails its own area check names each piece it didn't draw.
- `panes` writes a pane with holes as a STEP too, checked by its volume.

**`capture` says where each pane is.** Its numbers are its own, largest opening first, and the model read its "pane 1" as `pane_1.step` for 33 calls. Each pane is now said with its centre, its size, its holes and, where a file beside the frame has its outline, that file: the outlines' sections at the pocket's height share 90%, and OCCT finds a point inside the bench's pane inside the file's solid. Two files lying in one opening with no wall between are named. A pane that meets the frame where it sits says whether that's the ceiling (too thick), the floor (set too low), or frame standing in its place, with where.

**A height on a level face** is still refused, and the refusal now says the middle of the stretch either side, with how many openings each has.

**`wall`** answers what `thin`'s `thinnest_flat_mm` was quoted for on `cup-1`: the hanging hole's wall. Any point inside the hole names it, and it reads the hole's whole length. The sections take the least distance from the hole's edge to any other edge of the material round it. The rays never touch a section, and meet them where the wall is least, as the least wall runs square to the hole's wall. Between two corners it isn't square to either, rays read it thicker, and that's `DISAGREE`, said why. Material beyond the hole's ends isn't counted. `least_wall_mm` is the number a need limits, or `--min-wall`, which holds a wall drawn at the limit within the mesh's chords.

**A close-up** draws R mm all round a point along a view (`top` unless named), so a fine motif or a 3 mm hole can be judged: at 512 px and `R` of 4 that's 64 px per mm, where the first real run's 3 mm holes were 27 px. It looks along the view and doesn't cut anything, so what's inside a part needs `--section`. Nothing of the parts in the window is a `FAIL`, so one aimed wrongly doesn't read as a picture.

**A test piece** is drawn see-through in grey, and each picture says it isn't the part. It's whatever the one asking says it is, as nothing can tell by looking. So a script writes test pieces to files of their own, and a piece inside the part's file is drawn as the part.

**`capture`** answers whether the frame's shape holds each pane. It's how the Sponsor prints them, like a lens in a pair of sunglasses without the snap: a pane is set in at a pause and printed over to lock it in, not to bond it. So nothing here reads a material or counts on adhesion, and the frame's material is free. For each face of each pane:
- **Can it slide out?** The pane is read at every place its play lets it sit, against everything beyond the pocket, by polygons and then as a solid swept out. Passing at any of them is `FAIL`, so a pane held only while it's centred isn't held.
- **Can it swing out?** A lip that lies all to one side of the pane's centre is `FAIL`, as it can pivot about that lip.
- **How much lip?** `least_lip_mm` is the narrowest round the edge, and `least_lip_with_play_mm` what's left once the pane has moved away from it. Under `--min-lip` after its play (0.4 mm, a nozzle's width, unless you say) is `FAIL`, with how much of the edge and where.
- **How thick?** A lip under a layer at its root is `FAIL`, and `air_mm` is how far the pane can rattle before it reaches it.
- **Edge with no lip** is reported and not judged, as in `panes`, if the rest goes round. Where a lip stops it falls away to nothing round a window's corner, which isn't counted as thin, and one that tapers over a long way is.

A pane too thick for its pocket is `FAIL`, as is a height that misses the pane. It doesn't read a pane forced past a lip that bends. Pair it with `pause`, which says when the pane goes in and whether the nozzle clears it.

## Never take a tool's word

The map's table, row by row. Each row that touches these commands is a planted case in `tests/test_bench.py`, named after it.

| What we saw | What the tool said | Here |
|---|---|---|
| Near-touching faces: results empty or disagreeing | Often zero overlap | `clearance`: 66 cases rerun on our own hinge, and a pair where OCCT reads zero is `DISAGREE` |
| A pocket that missed the body | Feature valid | `readback` is `NO CHANGE`, and `rays` finds the centre in material |
| A bearing seat mostly in mid-air | No error, watertight, one body | `rays` counts the rays that hit nothing, and `readback --cutter` the share of the cutter in the body |
| Mesh repair that kept 5% of the volume | "Watertight" | `readback` fails it, stated or not |
| Thin shelled surface: CAD volume swung, even negative | A plain number | `info` is `NO ANSWER` or `DISAGREE`, and names OCCT's number |
| A parameter that drives only part of a design | Everything healthy | `params` names which pieces moved |
| Four-bar, nothing grounded, clevis assemblies | Success, or rc -1 | Motion: not in this bench |
| Peak stress at a corner, contact with a missing constraint | A plain number, 0.0 MPa | Stress: not in this bench |

The Sponsor's way of printing panes adds three, each a planted case:

| What could go wrong | What would look right | Here |
|---|---|---|
| A pane standing above the next layer, where the nozzle hits it | The layer count alone | `pause` is `FAIL`, by both methods' layer, among several openings, and on the plate. `nozzle_over_pane_mm` is how far the tip clears it |
| A lip narrower than the pane's play | A lip in the section at one height | `capture` moves the pane by its play, and it comes out |
| A lip down one side | A lip, and no way to slide out | `capture` is `FAIL`: it can swing out |
| A pane with a hole round an island of frame (`cup-1`) | "Too thick for its pocket", a ceiling mid-pocket, a section with no walls | `capture` holds it by its lips round every ring, `pause` agrees by both methods, and the section draws the island |
| A pane number taken for a file's (`cup-1`) | `pane 1` | each pane is said with where it is, and its file |
| A hole's wall quoted from the whole part's thinnest flat (`cup-1`) | `thinnest_flat_mm` 2.0 | `wall` reads that hole, and `FAIL`s under `--min-wall` |

Building this found more, each now handled and said in the code:
- **CadQuery's `tessellate` tolerance is relative,** a share of each edge's size, not the mm it looks like. On a real part that read as a 0.03 mm false gap. The bench meshes with absolute tolerances, through OCCT directly.
- **Manifold's `RayHit.distance` is a fraction of the ray,** not a length.
- **OCCT's adaptive volume moves with its accuracy setting** on a real file by far more than its own error estimate, so the bench reads its default, and checks it against the mesh.
- **A coarser mesh isn't always further from the truth,** so how far a figure can rest on the chords comes from the chord tolerance times the curved surface's area.

A second check has to ask the same question as the first, or it only makes noise. Running over our 51 reference parts showed that: rays along a surface's normal read a cone or a thread thinner than it prints, and the top of a cylinder on its side as thin when no layer prints it. So a wall's ray runs level, across the layer, and shallow slopes aren't read as walls. A sharp point, a feather edge, a cusp and a few stray rays are noted with where they are, and don't fail a part.

## Limits

- **Docker's disk is shared,** with LiteLLM's database, crew's workspaces and any running tick, and it filled on 2026-11-01. There's one image, `print-shop/bench:0.3`, of at most 4 GB. It holds what 0.2 did, with the bench's version as a label: the bench code is mounted, so 0.3 marks the bench that reads islands and holes (mqucifer/print-shop#27) and shares all of 0.2's layers (0.1 had no `lookup` or `docs` indexes; either may still be on a machine). `build` refuses if that could leave under 5 GB free, and `check` says if the image or the disk is over. Its first layers are mqucifer/model-performance's CadQuery image, so where that's present it costs tens of MB.
- **A container** has no network, 2 CPUs, 3 GiB with no swap, a read-only image, 1 GB of `/tmp`, the invoking user, no capabilities, no log on Docker's disk, a 2 GiB limit on any file, and 600 s. `/work` is the only thing it writes, and the bench's code is mounted read-only, so changing the code never costs another image. Every package is pinned.
- **It moves to another machine** by clone and `build`: CI builds it for amd64 and runs the planted cases there, and the Mac builds it for arm64.
- **Accuracy:** the mesh is at 0.002 mm, and a figure is good to the band it prints. Nothing here has been compared with a print or sliced by Orca. A pause layer assumes a layer takes its outline from its mid-height, and the Centauri's mid-print pause hasn't been run.

## Tests

Planted fixtures are made from scratch by `tests/bench_cases/make_fixtures.py`, with right answers from arithmetic: `uv run pytest -q -m bench`. No Sponsor's design, or anything derived from one, is in this repository, and `scripts/bench_local_run.py` refuses to write inside it. `tests/test_bench_local.py` (`-m bench_local`) runs on the Sponsor's exported frame where it's present, and skips elsewhere. The frame's heights and play are in `~/print-shop/bench-local.json` on the Mac, off this repository. `scripts/bench_local_run.py triforce --out DIR` saves that run's figures to a folder off the repository, and `reference --out DIR` runs the commands over our reference parts in mqucifer/model-performance.
