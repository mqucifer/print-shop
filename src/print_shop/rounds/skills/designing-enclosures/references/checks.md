---
name: checking-enclosures
description: Explains what the enclosure checks answer, the edges command by group and by place and clearance's gap range and lift, and how to read each reply and its numbers. Use when running edges or clearance with --max-gap or --lift, reading their answers, or naming one of their numbers in a need.
---

# The enclosure checks

`edges` comes with the enclosure toolset, beside the core's commands (the `using-the-bench` skill), and `clearance --max-gap` is the core's. Each answers in plain text and numbers, with a second check by a different method, as every bench command does. `<command> --help` gives its arguments.

| Command | Answers |
|---|---|
| `edges FILE... [--with FILE]... [--min-radius R] [--box NAME=X0,Y0,Z0,X1,Y1,Z1]... [--facing NAME=DIR]...` | each outside edge of the parts named: sharp or rounded, how far a sharp one turns, and the least radius of the rounded ones, with where, overall, by group and by place. `--min-radius` fails a sharp edge or a tighter rounding |
| `clearance FILE... [--min-gap G] [--max-gap G2] [--lift D [--along AXIS]]` | overlap and gap, every pair. `--max-gap` fails a pair whose least gap is over it, `TOO LOOSE`. `--lift` moves the last file named off its seat before measuring |

## Reading `edges`

- **What it reads:** an edge a finger meets from outside every file named, the test finger being a ball 12 mm across. Name the closed case, base and lid in place, and the references inside it: each blocks the finger from the others' insides. A file under `references/`, or named with `--with`, blocks it and isn't read.
- **Sharp or rounded.** Where two faces meet at an angle, the edge is sharp, and the reply says how far it turns: 90° for a box's corner, 45° for each edge of a chamfer. Where a curved face meets its neighbours smoothly, it's a rounded edge, and its radius is the least it bends through. A sharp edge is radius 0.
- **Not read, and said:** inside corners, where the part's faces meet round the air; edges no finger reaches, inside the closed case or deep in an opening; edges a finger meets only flat-on, flush with another file, as a lid's seat with the base, or round a slot too narrow for it, or low in a tight corner.
- **Many sharp edges turning a few degrees each** are a rounding stored as flat strips. The reply says so: build it with `fillet` or an arc, and it reads as one rounded face.
- **By group:** each edge and rounding read is in one group, in the file's axes: `upright` where it runs within 30° of z, such as a case's vertical corners; else `top` where it faces more than 20° above level, such as the rim round a lid's top; `bottom` where it faces more than 20° below; and `side` for the rest, a level edge facing sideways. The reply gives each group's counts and least radius, with where.
- **By place:** `--box NAME=X0,Y0,Z0,X1,Y1,Z1` reads the edges with any point read inside the box, and `--facing NAME=DIR`, DIR one of `+x -x +y -y +z -z`, those facing within 60° of it, which are the edges round the face that faces it. A rounding's radius is its own least, wherever on it that is.
- **An edge round an opening in a side wall** is grouped by the way it faces: the opening's upright sides are `upright`, its top edge faces down and out, so `bottom`, and its foot `top`. Read an opening's edges with `--box` round it.
- **Its numbers:** `groups.NAME` and `selected.NAME`, each with `sharp`, `rounded`, `least_radius_mm` (0 where a sharp edge is in it, none where nothing is), `least_at` and `file`; `least_radius_mm`, 0 where any sharp edge is read, and `least_radius_at`; `least_rounded_mm` and `least_rounded_at`, the least of the rounded ones; `sharp_edges`, each with its file, where it runs from and to, and its turn; `rounded`, each rounded face with its least radius, where, and its group, sorted by radius, so a row's place in the list moves when the part changes; `edges`, the counts of each kind.
- **A rounding drawn at the request's radius passes:** it may read short of it by half a percent, or 0.005 mm.
- **If it fails,** each sharp edge and each tight rounding is named with where it is and on which file: round that edge, and run it again.

## Reading `clearance` with a gap range

- **`--max-gap G2` holds parts meant to mate from the loose side:** a pair whose least gap is over G2 is `TOO LOOSE`. With `--min-gap G1` too, the gap is held from both ends.
- **The gap both limits hold is `gap_mm`:** on STEP files OCCT's exact distance, and on STL files the mesh's. A gap drawn at a limit, such as a pocket 0.25 mm off a pane round its curved corners too, passes at 0.25. The mesh's figure, `gap_mesh_mm`, can read a gap on a curve a few thousandths short, as its chords cut into a hollow face: it's the second check, not the verdict.
- **A pair that touches has a least gap of 0,** which is never too loose: a lid seated on the base touches it, whatever the lip's play. The reply says so.
- **`--lift D` reads a lid's play on the printed lid.** It moves the last file named D mm along `--along` (z unless said, signed) before measuring, and its file stays where it is. D is more than G2 and G1, or it's refused: a lift short of the play would read as the gap. Where the least gap still reads D, the faces that seat are what's nearest: the reply says so, and the play is at least D.
- **Its numbers:** under `pairs`, each pair's `gap_mm`, `gap_by` (`occt` or `mesh`), `gap_mesh_mm` and `verdict`; `lift`, the file moved, the axis and the mm.

## Numbers a need can name

| Command | Number | Its limit |
|---|---|---|
| `edges` | `least_radius_mm` | `at_least`: the request's radius |
| `edges` | `groups.upright.least_radius_mm`, `groups.top.…`, `groups.bottom.…`, or `selected.NAME.least_radius_mm` | `at_least`: the request's radius for those edges |
| `clearance` | `pairs.0.gap_mm` | `at_least` and `at_most`: the request's play |

Never name a row of `rounded` or `sharp_edges` by its index: the list is sorted by radius, and a rebuild reorders it.

An `edges` need more often holds the radius with `--min-radius`, the request's figure, and a `clearance` need its range with `--min-gap` and `--max-gap`.
