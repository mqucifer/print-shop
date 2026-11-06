---
name: checking-enclosures
description: Explains what the enclosure checks answer, the edges command and clearance's gap range, and how to read each reply and its numbers. Use when running edges or clearance with --max-gap, reading their answers, or naming one of their numbers in a need.
---

# The enclosure checks

`edges` comes with the enclosure toolset, beside the core's commands (the `using-the-bench` skill), and `clearance --max-gap` is the core's. Each answers in plain text and numbers, with a second check by a different method, as every bench command does. `<command> --help` gives its arguments.

| Command | Answers |
|---|---|
| `edges FILE... [--with FILE]... [--min-radius R]` | each outside edge of the parts named: sharp or rounded, how far a sharp one turns, and the least radius of the rounded ones, with where. `--min-radius` fails a sharp edge or a tighter rounding |
| `clearance FILE... [--min-gap G] [--max-gap G2]` | overlap and gap, every pair. `--max-gap` fails a pair whose least gap is over it, `TOO LOOSE` |

## Reading `edges`

- **What it reads:** an edge a finger meets from outside every file named, the test finger being a ball 12 mm across. Name the closed case, base and lid in place, and the references inside it: each blocks the finger from the others' insides. A file under `references/`, or named with `--with`, blocks it and isn't read.
- **Sharp or rounded.** Where two faces meet at an angle, the edge is sharp, and the reply says how far it turns: 90° for a box's corner, 45° for each edge of a chamfer. Where a curved face meets its neighbours smoothly, it's a rounded edge, and its radius is the least it bends through. A sharp edge is radius 0.
- **Not read, and said:** inside corners, where the part's faces meet round the air; edges no finger reaches, inside the closed case or deep in an opening; edges a finger meets only flat-on, flush with another file, as a lid's seat with the base, or round a slot too narrow for it, or low in a tight corner.
- **Many sharp edges turning a few degrees each** are a rounding stored as flat strips. The reply says so: build it with `fillet` or an arc, and it reads as one rounded face.
- **Its numbers:** `least_radius_mm`, 0 where any sharp edge is read, and `least_radius_at`; `least_rounded_mm` and `least_rounded_at`, the least of the rounded ones; `sharp_edges`, each with its file, where it runs from and to, and its turn; `rounded`, each rounded face with its least radius and where; `edges`, the counts of each kind.
- **A rounding drawn at the request's radius passes:** it may read short of it by half a percent, or 0.005 mm.
- **If it fails,** each sharp edge and each tight rounding is named with where it is and on which file: round that edge, and run it again.

## Reading `clearance` with a gap range

- **`--max-gap G2` holds parts meant to mate from the loose side:** a pair whose least gap is over G2 is `TOO LOOSE`. With `--min-gap G1` too, the gap is held from both ends. A gap drawn at G2 passes, within the mesh's chords.
- **A pair that touches has a least gap of 0,** which is never too loose: a lid seated on the base touches it, whatever the lip's play. The reply says so. Read a lid's play on a copy lifted off its seat by more than G2, so the least gap is the lip's play.
- **Its numbers:** under `pairs`, each pair's `gap_mesh_mm` and `verdict`, with OCCT's distance as the second check.

## Numbers a need can name

| Command | Number | Its limit |
|---|---|---|
| `edges` | `least_radius_mm` | `at_least`: the request's radius |
| `clearance` | `pairs.0.gap_mesh_mm` | `at_least` and `at_most`: the request's play |

An `edges` need more often holds the radius with `--min-radius`, the request's figure, and a `clearance` need its range with `--min-gap` and `--max-gap`.
