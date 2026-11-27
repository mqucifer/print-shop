---
name: using-the-bench
description: Explains what each core bench command answers, how to read a bench reply and its exit code, and how a need names its check and what it measures. Use when choosing a bench command, reading a bench answer, or writing a need's check, in any run.
---

# The bench

Commands over the files in your directory, each answering in plain text and numbers. Tools have reported success when they were wrong, so every answer has a second check by a different method, and the bench says when the two differ. Nothing fences you off: these are tools you may use, and you can also write your own scripts, importing CadQuery, trimesh, Manifold, Shapely and the rest.

`bench <command> --help` gives a command's arguments.

## Reading an answer

The first line is the verdict. Then come the numbers, each with how it was read, at least one `second check:` line, and last `saved:`, naming the text and JSON files under `bench-out/`.

| Exit | Verdicts | It means |
|---|---|---|
| 0 | `OK`, `CLEAR`, `TOUCHING`, `HELD` | both methods agree, and nothing's wrong |
| 1 | `FAIL`, `INTERFERE`, `TOO CLOSE`, `TOO LOOSE`, `NO CHANGE`, `PARTIAL`, `NOT HELD`, `HELD BY A SEPARATE BODY`, `BLOCKED` | both methods agree that something's wrong |
| 2 | `DISAGREE`, `NO ANSWER` | there's no answer to rely on: read the numbers, and don't pick one |
| 3 | | the command itself was wrong |

## The commands

| Command | Answers |
|---|---|
| `info FILE` | size, volume, bodies, flat heights |
| `clearance FILE... [--min-gap G] [--max-gap G2] [--lift D [--along A]] [--box NAME=X0,Y0,Z0,X1,Y1,Z1]... [--facing NAME=DIR]... [--exact] [--plan]` | overlap and gap, every pair, across files or a file's solids. `--max-gap` fails a pair whose least gap is over it, `TOO LOOSE`, for parts meant to mate. Both limits hold `gap_mm`, OCCT's exact distance on STEP files, so a gap drawn at a limit passes. A part resting on another touches it, gap 0, which is never too loose: `--lift D` moves the last file named D mm along A (z unless said) before measuring, its file left where it is, so a seated lid's play is read on the printed lid. A pair with two plays, such as a pane's edge and its faces, reads only the smaller: `--box NAME=...` round the faces one play is on reads that play alone, as `pairs.0.places.NAME.gap_mm`, and with a place named the limits hold each place's gap. Past 3 pairs, a pair farther apart than any gap judged is read by its boxes alone, `CLEAR`, counted, with `box_gap_mm` and no `gap_mm`; `--exact` reads every pair exactly, for a need on a far pair's gap. `--plan` says how many pairs would be read exactly and how long, judging nothing |
| `readback BEFORE AFTER [--cutter C] [--expect-delta V] [--min-share S]` | volume before and after, and how much of a cutter was in the body |
| `rays FILE --centre X,Y,Z [--axis A] [--d D] [--depth H]` | a cut feature measured from its axis: rays that hit nothing, the distance across, the wall beyond, the floor. From a point in air, `across` is the open space's width through it, wall to opposite wall: a hole's diameter on its axis, never an outside corner's radius |
| `thin FILE [--nozzle N] [--layer L] [--min-body V] [--one-body]` | features under the nozzle's width, floors under a layer, and tiny pieces. `--one-body` fails on more than one connected body |
| `pause FILE --pocket-z Z --pane-thickness T [--layer L] [--first-layer F] [--max-gap G] [--min-room R]` | the layer to pause after to drop an insert into its pocket (a nut, a magnet, a pane), and the room over it. `--pane-thickness` is the insert's height. `--max-gap` is the most room it may have, and `--min-room` the least |
| `render FILE... [--views iso top bottom front side] [--section Z...] [--close X,Y,Z,R[,VIEW]]... [--cut AXIS=AT[,AXIS=FROM:TO]]... [--cut at=X,Y,Z,normal=NX,NY,NZ[,up=UX,UY,UZ][,u=FROM:TO][,v=FROM:TO]]... [--gaps] [--look FILE=COLOUR[:LABEL]]... [--test-piece FILE]...` | pictures from the standard views, and a sheet of them, which come to you. A close-up is R mm all round a point, along a view, for what's too fine to judge whole. A cut is every file cut by one plane, each in its colour: `z=3` level, `x=50` upright and seen from +x, `y=40` from the front, and `x=50,y=20:40` only that stretch, so a pane can be seen in its pocket. Any plane: `at=X,Y,Z,normal=NX,NY,NZ` is the one through that point square to that direction, such as square to a sloping seam through its pin, drawn to scale with its own axes `u` (right) and `v` (up) in mm from the point on its sides; `up=UX,UY,UZ` turns it, and `u=FROM:TO`, `v=FROM:TO` draw only that stretch. `--gaps` marks each pair of files' least gap on each cut, where it lies and its figure, `clearance`'s reading on STEP files: quote those, never a figure you work out. A look gives a file its colour and label: each file is numbered on the pictures and named in the legend, and a file with none takes a colour no other has. `drawn:` lines say what each number and colour is, and a file no view shows is said. A test piece is drawn see-through in grey, and marked as not the part. A picture of a file in `scratch/` says so along its foot, and a `scratch/` line names them |
| `render --variant A=a.step --variant B=b.step,c.step [--views ...] [--close ...]... [--cut ...]... [--look ...]... [--test-piece FILE]...` | a sheet with a labelled row for each variant, to compare them |
| `wall FILE --hole X,Y,Z [--axis A] [--min-wall W] [--z FROM,TO]` | the least wall round one hole, along its whole length, and toward what: the part's outside or another opening. Any point inside the hole names it. A hole through a floor that opens into a hollow reads the hollow's walls too, and says so: `--z FROM,TO` reads only that stretch of it, as `--z 0,2` for the floor |
| `params SCRIPT --set NAME=VALUE [--max-left N]` | which dimensions, volumes and pieces moved when one parameter changed |
| `layout FILE.json [--inset D]... [--join mitre\|round] [--min-width W]` | a flat layout of named pieces, in 2D: whether each is one piece, and where each part is; its least width and where, into a sharp tip or across a neck; what an inset of D leaves of it, how many parts and the narrowest neck, and where it cuts the piece; any two that overlap; and a picture with each piece labelled, which comes to you |
| `faces FILE` | a STEP part's flat faces, by plane: the way each faces, where (`z=7.5`), its area and its box; and its round holes and bosses: centre, axis, radius, depth, and whether a hole is open at each end. A reading, for measuring a part, a bought one too, instead of a script of your own |
| `slice FILE --at z=H [--at x=H]...` | the part cut by a plane, level (`z=`) or upright (`x=`, `y=`): each separate piece's area, box and outline, and each hole in it, with its centre and, where it's round, its diameter. A reading, for a cross-section instead of a script of your own |
| `fit-on MOVING --onto FILE... --along DIR --from MM\|clear [--step MM]` | a part's way on. MOVING is the part where it sits once on, DIR its way out (`+z` for a lid lifted off upward), and it's placed at each step from MM out (`clear`: as far out as it takes to be clear of every FILE) down to where it sits and intersected with every FILE. `CLEAR` when nothing is in its way. `BLOCKED` names each place it passes through something, deepest first: where, from how far out to how far, and `depth_mm`, how deep across, which is how far what's there has to move aside for the part to pass. A snap fit is meant to be `BLOCKED`: its depth is the push `bend` takes |
| `bend FILE --arm X0,Y0,Z0,X1,Y1,Z1 --root AXIS=AT --push MM --toward DIR [--at far\|near\|both] [--layers AXIS] [--limit PCT] [--mesh MM]` | whether an arm survives being bent. The arm is FILE's material in the box, one side of the box on its root, where it joins the rest, and clear of the wall beside it; it's held at the root plane (`z=10`) and an edge of its hook is pushed MM `--toward` a way across it. It gives the strain across the layers (stacked along z unless `--layers` says), its peak and where, and how far the arm's back moves against the room behind it. `FAIL` over what the material takes across its layers (PETG 1%, or `--limit`), or where the arm meets what's behind it first. It pushes the hook's far edge and its near one, and judges the worse |

**`fit-on` and `bend` run in a container of their own,** with FreeCAD, gmsh and CalculiX, under the same limits. They're bench commands only: a script of yours has no FreeCAD to import. `clearance --slide` reads what holds a part once it's on, and what lies on its path; `fit-on` reads how far that has to bend, and `bend` whether it can. Neither reads friction, how hard a push is, or how well a snug fit holds. **The harness runs both itself at `done`,** once every need has passed: `fit-on` on each held part along its slide's way out, and `bend` on the arm at each place it's `BLOCKED`, pushed that place's depth. An arm over its limit sends the part back before anyone sees it, so read them yourself first, and size the arm by `bend`'s "what would pass".

**`pause`'s numbers are the bench's own,** largest opening first, not your files'.

A kind's toolset adds commands of its own, such as stained glass's `capture` and `light`, an enclosure's `edges`, or furniture's `load` and `fit-bed`, and its skill says what each answers.

**A 2D question has a reading: `layout`.** Which piece an inset makes, which piece is which, how narrow a neck is, whether a frame in 2D is one piece: write the layout to a file and ask, rather than work it out by reasoning or by printing bounding boxes. The file is JSON, each piece's name and its outline as GeoJSON or WKT, in mm, which your script writes from the layout it built:

```python
import json

from shapely.geometry import mapping

pieces = {"frame": frame_2d, **{f"cell_{k}": c for k, c in enumerate(cells, start=1)}}
with open("layout.json", "w") as fh:
    json.dump({"pieces": {name: mapping(shape) for name, shape in pieces.items()}}, fh)
```

Then `bench layout layout.json --inset 0.5 --inset 1.7`, an inset for each offset the layout uses. A width is a line straight across the piece, square to its edges: a corner sharper than 75° is a tip, read down to nothing, and a blunter one isn't a width. Its picture marks each piece's narrowest neck in red and each inset's outline in black. It reads a layout, not the part: a need is checked on the part's own files.

**Look at what you built.** After each build of a part with pockets, holes or cavities, cut it through them (`render PART --cut z=Z`, a height inside the pockets, or `x=X` upright across one) and read the cut before reasoning about what the part holds: whether a pocket is blind or runs through, and what stands in an opening, are on the cut, and slow to work out from the script.

**When a bench reading and your design disagree, cut a section** (`render FILE --section Z`) at the height in question, and read its verdict before its picture: a section that fails its own check names the piece it couldn't draw.

`params` reads a number assigned once at the top level of your script (`WALL = 2.0`), and runs the script in a scratch folder, so your files aren't touched. A parameter that moves nothing is `NO CHANGE`, and one that leaves pieces behind is `PARTIAL`, naming them. `--max-left N` says how many may stay and still read `OK`, for a parameter that's meant to drive only some of them. Top-level numbers are what let a later round change one thing and read what moved.

## Limits it runs under

Each command, and each `run`, is a fresh container with no network, 2 CPUs, 3 GiB of memory, 1 GB of `/tmp` and 600 seconds. Only your directory stays. Inside a container the bench's own code is at `/opt/bench`, read-only, if a script of yours wants to call it (`sys.path.insert(0, "/opt/bench")`, then `from bench.cli import main`). Each command a script runs that way is recorded as a bench call of yours, and its reply names each with its verdict. A `FAIL` inside a script is held as any failing check is. A pass inside a script doesn't clear a held check: run that check with `bench`.

Meshes are at 0.002 mm, and a figure is good to the band it prints. Nothing on the bench has been compared with a print or sliced.

## How a need names a check

A bench need names a command as you'd give it, and what must hold. The harness runs it after a clean re-run of your script.

```json
{"id": "lid-fit", "what": "the lid slides on with play", "source": "request",
 "quote": "a 0.2 mm gap all round",
 "check": {"by": "bench", "measures": "fit", "command": "clearance lid.step cup.step --min-gap 0.2"}}
```

**`measures` says what the need is about,** and its command has to measure that, or `needs` refuses it and names the command that does. The core's:

| `measures` | It's about | Its check |
|---|---|---|
| `size` | the part's outer size, volume, bodies or flat heights, as a whole | `info FILE` |
| `fit` | the gap or overlap between parts | `clearance A B --min-gap G`, or at one place `clearance A B --box NAME=X0,Y0,Z0,X1,Y1,Z1 --min-gap G` |
| `cut` | a cut or a join changed the body by what it should | `readback BEFORE AFTER --expect-delta V` |
| `hole` | a cut feature's size, depth or floor, from its axis | `rays FILE --centre X,Y,Z --d D` |
| `wall` | the wall round one hole, such as the hole a piece hangs from | `wall FILE --hole X,Y,Z --min-wall W` |
| `printable` | nothing narrower than the nozzle, no floor under a layer, no tiny piece | `thin FILE` |
| `one-piece` | the part is one connected solid | `thin FILE --one-body` |
| `insert` | the layer to pause after to drop an insert in, and the room over it | `pause FILE --pocket-z Z --pane-thickness T --min-room R` |
| `parameter` | a top-level number moves every piece it should | `params SCRIPT --set NAME=VALUE` |
| `way-on` | a part's way on: what it passes through, and how far that has to bend | `fit-on LID.step --onto BASE.step --along +z --from MM`; for a snap fit, `verdicts` `["BLOCKED"]` and `number` `depth_mm` with `at_most` |
| `bend` | the strain an arm takes across its layers when it's pushed aside | `bend FILE --arm X0,Y0,Z0,X1,Y1,Z1 --root AXIS=AT --push MM --toward DIR` |

A check should hold what the need says. "A 0.2 mm margin" asks `pause --min-room 0.2`, "one connected solid" asks `thin --one-body`, "2 mm of wall round the hanging hole" asks `wall --hole X,Y,Z --min-wall 2` at that hole (not `info`, whose size is the whole part's, nor `thin`, whose thinnest figures are too), and "moves every piece" asks `params` with no `--max-left`. `render` draws and measures nothing: what only an eye can judge is an eye need. Whether a bought part is held is never one: `clearance <part> <bought part> --slide <its way out>` reads it, with `HELD`, and `needs` refuses an eye need on it, giving the command.

**Which way a part prints is a bed need, never an eye need.** It names the file and the side of it, as your script stores it, that lies on the bed: `-z` as stored, `+z` turned over.

```json
{"id": "base-down", "what": "it prints on its base", "source": "request",
 "quote": "It prints on its base.", "covers": ["R2"],
 "check": {"by": "bed", "file": "tray.step", "side": "-z"}}
```

`done` checks it against its `on_bed`, before anything runs, so give that file the same side there, `-z` too. `needs` refuses an eye need whose `look_for` asks which side lies on the bed, and says what to send. An eye need on how a feature looks as printed stays an eye need: its `look_for` names only what the eye judges, and the side goes in a bed need beside it. A bed need checks the side the run states; choosing the side for strength or supports isn't a need (mqucifer/print-shop#9).

`done` names every printed file's side in `on_bed`, need or no need, unless the request leaves it to the refinement pass. Choose it by reading the side first: `slice FILE --at AXIS=AT` a layer in from the face that goes down shows what the first layer lies on and what it bridges. An assembly's part prints on a side square to its `layers`, the one `fit-bed` lays it on. At each stop its downward faces are read lying on that side, and the reading says which side and why.

By default a need passes on exit 0. `verdicts` names which verdicts pass instead, such as `["INTERFERE"]` for a press fit. `number` names a number in the answer's saved JSON (`bench-out/*.json`, under `numbers`), with dots for anything nested (`pairs.0.gap_mm`), and `at_least` or `at_most` its limit. `DISAGREE` and `NO ANSWER` never pass, and a need can't ask for `FAIL`. To see a number's name, run the command yourself first: names differ between commands. Once settled, needs don't change, so send them with `try: true` first: each check is read against your files as they stand, and nothing is settled.

## A sheet for the Sponsor

`ask` draws its sheet with `render` and shows it to you before anything goes: `send_ask` sends it, with what you say it shows. Say what each picture shows as drawn, by the numbers and colours in its legend, not what the design is meant to be. Give each file its colour and label in `looks`, as it will be printed. What sits in a pocket is judged in a cut (`cuts`), not from the top view, where the frame above and below can carry an outline that isn't there at the pocket's height. Whatever the question says, the harness runs the needs' checks and `thin` on the sheet's files, and puts any that fail beside it.
