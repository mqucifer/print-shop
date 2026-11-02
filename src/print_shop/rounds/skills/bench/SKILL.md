---
name: bench
description: what each bench command answers, how to read an answer, and how a need names a check
---

# The bench

Commands over the files in your directory, each answering in plain text and numbers. Tools have reported success when they were wrong, so every answer has a second check by a different method, and the bench says when the two differ. Nothing fences you off: these are tools you may use, and you can also write your own scripts, importing CadQuery, trimesh, Manifold, Shapely and the rest.

`bench <command> --help` gives a command's arguments.

## Reading an answer

The first line is the verdict. Then come the numbers, each with how it was read, at least one `second check:` line, and last `saved:`, naming the text and JSON files under `bench-out/`.

| Exit | Verdicts | It means |
|---|---|---|
| 0 | `OK`, `CLEAR`, `TOUCHING` | both methods agree, and nothing's wrong |
| 1 | `FAIL`, `INTERFERE`, `TOO CLOSE`, `NO CHANGE`, `PARTIAL` | both methods agree that something's wrong |
| 2 | `DISAGREE`, `NO ANSWER` | there's no answer to rely on: read the numbers, and don't pick one |
| 3 | | the command itself was wrong |

## The commands

| Command | Answers |
|---|---|
| `info FILE` | size, volume, bodies, flat heights |
| `clearance FILE... [--min-gap G]` | overlap and gap, every pair, across files or a file's solids |
| `readback BEFORE AFTER [--cutter C] [--expect-delta V] [--min-share S]` | volume before and after, and how much of a cutter was in the body |
| `rays FILE --centre X,Y,Z [--axis A] [--d D] [--depth H]` | a cut feature measured from its axis: rays that hit nothing, the distance across, the wall beyond, the floor |
| `panes FRAME --pocket-z Z [--lip-z Z] --clearance C [--pane-thickness T]` | a pane from each opening, written to `panes/FRAME/`, its fit, and the edge with no lip |
| `capture FRAME --pocket-z Z --clearance C --pane-thickness T [--pane-floor-z F] [--min-lip M] [--layer L] [--panes N]` | whether each pane is held by shape on both faces, wherever its play lets it sit: the lip, how thick, where it's thin, and whether it can slide or swing out. Nothing depends on a bond. `--panes` fails on any other count |
| `thin FILE [--nozzle N] [--layer L] [--min-body V] [--one-body]` | features under the nozzle's width, floors under a layer, and tiny pieces. `--one-body` fails on more than one connected body |
| `pause FRAME --pocket-z Z --pane-thickness T [--layer L] [--first-layer F] [--max-gap G] [--min-room R]` | the layer to pause after, and the room over the pane. `--max-gap` is the most it may be, and `--min-room` the least |
| `render FILE... [--views iso top bottom front side] [--section Z...] [--close X,Y,Z,R[,VIEW]]... [--cut AXIS=AT[,AXIS=FROM:TO]]... [--look FILE=COLOUR[:LABEL]]... [--test-piece FILE]...` | pictures from the standard views, and a sheet of them, which come to you. A close-up is R mm all round a point, along a view, for what's too fine to judge whole. A cut is every file cut by one plane, each in its colour: `z=3` level, `x=50` upright and seen from +x, `y=40` from the front, and `x=50,y=20:40` only that stretch, so a pane can be seen in its pocket. A look gives a file its colour and label: each file is numbered on the pictures and named in the legend, and a file with none takes a colour no other has. `drawn:` lines say what each number and colour is, and a file no view shows is said. A test piece is drawn see-through in grey, and marked as not the part |
| `render --variant A=a.step --variant B=b.step,c.step [--views ...] [--close ...]... [--cut ...]... [--look ...]... [--test-piece FILE]...` | a sheet with a labelled row for each variant, to compare them |
| `params SCRIPT --set NAME=VALUE [--max-left N]` | which dimensions, volumes and pieces moved when one parameter changed |

`params` reads a number assigned once at the top level of your script (`WALL = 2.0`), and runs the script in a scratch folder, so your files aren't touched. A parameter that moves nothing is `NO CHANGE`, and one that leaves pieces behind is `PARTIAL`, naming them. `--max-left N` says how many may stay and still read `OK`, for a parameter that's meant to drive only some of them. Top-level numbers are what let a later round change one thing and read what moved.

## Limits it runs under

Each command, and each `run`, is a fresh container with no network, 2 CPUs, 3 GiB of memory, 1 GB of `/tmp` and 600 seconds. Only your directory stays. Inside a container the bench's own code is at `/opt/bench`, read-only, if a script of yours wants to call it (`sys.path.insert(0, "/opt/bench")`, then `from bench.cli import main`).

Meshes are at 0.002 mm, and a figure is good to the band it prints. Nothing on the bench has been compared with a print or sliced.

## How a need names a check

A bench need names a command as you'd give it, and what must hold. The harness runs it after a clean re-run of your script.

```json
{"id": "lid-fit", "what": "the lid slides on with play", "source": "request",
 "quote": "a 0.2 mm gap all round",
 "check": {"by": "bench", "command": "clearance lid.step cup.step --min-gap 0.2"}}
```

A check should hold what the need says. A need saying "a 0.2 mm margin" asks `pause --min-room 0.2`, "one connected solid" asks `thin --one-body`, "at least 7 panes" asks `capture --panes 7` for each outline, and "moves every piece" asks `params` with no `--max-left`.

By default a need passes on exit 0. `verdicts` names which verdicts pass instead, such as `["INTERFERE"]` for a press fit. `number` names a number in the answer's saved JSON (`bench-out/*.json`, under `numbers`), with dots for anything nested (`pairs.0.gap_mesh_mm`), and `at_least` or `at_most` its limit. `DISAGREE` and `NO ANSWER` never pass, and a need can't ask for `FAIL`. To see a number's name, run the command yourself first: names differ between commands. Once settled, needs don't change, so send them with `try: true` first: each check is read against your files as they stand, and nothing is settled.

## A sheet for the Sponsor

`ask` draws its sheet with `render` and shows it to you before anything goes: `send_ask` sends it, with what you say it shows. Say what each picture shows as drawn, by the numbers and colours in its legend, not what the design is meant to be. Give each file its colour and label in `looks`, as it will be printed. A pane and its pocket are judged together in a cut (`cuts`), not from the top view, where lips above and below can carry an outline that isn't there at pane height. Whatever the question says, the harness runs the needs' checks and `thin` on the sheet's files, and puts any that fail beside it.
