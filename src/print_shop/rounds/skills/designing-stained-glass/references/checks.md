---
name: checking-stained-glass
description: Explains what the stained-glass bench commands answer (capture, panes, light and thickness) and how to read each reply, its numbers and its pane numbers. Use when running one of them, reading its answer, or naming one of their numbers in a need.
---

# The stained-glass checks

These come with the stained-glass toolset, beside the core's (the `bench` skill). Each answers in plain text and numbers, with a second check by a different method, as every bench command does. `<command> --help` gives its arguments.

| Command | Answers |
|---|---|
| `capture FRAME --pocket-z Z --clearance C --pane-thickness T [--pane-floor-z F] [--min-lip M] [--layer L] [--panes N]` | whether each pane is held by shape on both faces, wherever its play lets it sit: the lip after play, how thick it is, where it's thin, and whether the pane can slide or swing out. `--panes` fails on any other count |
| `panes FRAME --pocket-z Z [--lip-z Z] --clearance C [--pane-thickness T]` | a pane from each opening, written to `panes/FRAME/`, its fit, and the edge with no lip |
| `light FRAME PANE... [--with FILE]... [--min-open P]` | the share of each pane open straight through the frame, in percent, and the narrowest band of leading a person sees between two open pieces. `--with` adds another part of the frame, such as a cap printed apart |
| `thickness PANE... [--max T] [--min T]` | each pane's thickness straight through, its thickest and thinnest |

## Reading them

- **`capture`'s pane numbers are its own,** largest opening first, not your files'. Each pane is said with its centre, its size and, where a pane file beside the frame has its outline, that file's name. Its "pane 1" isn't `pane_1.step` unless it says so.
- **`capture`'s lips.** Each face of each pane, under `panes`, has `least_lip_mm`, the lip before play, with the pane in the middle of its pocket, and `least_lip_with_play_mm`, after it: the lip before play less the play. `--min-lip` holds the one after play. The top-level `least_lip_mm` is the least before play of any pane.
- **A pane too thick for its pocket, or a height that misses it,** is `capture`'s `FAIL`, saying whether the pane meets the ceiling, the floor or frame standing in its place.
- **An opening with an island of frame inside it,** joined to the rest only above or below, holds a pane with a hole round the island. `capture`, `pause`, `panes` and `render --section` say "opening N has 1 island(s) of frame inside it". An island closer to the opening's edge than twice the play leaves the pane going round it open.
- **`light` reads straight along z,** as the piece hangs flat to the light: each pane's footprint less the frame's shadow, cut at every layer and at each level face. A floor under a pane reads 0%. Its numbers: `least_open_pct` and `least_open_file`, each pane's `open_pct` under `panes`, and `narrowest_band_mm`.
- **`thickness`** reads each pane's height, and rays through it. A pane thicker in one place than the rest reads as both: `thickest_mm` and `thinnest_mm`.
- **No limit, no verdict on it.** `light` with no `--min-open`, and `thickness` with no `--max`, are readings and pass. A need on them gives the request's limit, and a need with none is refused.
- **When a reading and your design disagree,** cut a section at the height in question (`render FRAME --section Z`), and read its verdict before its picture.

## Numbers a need can name

| Command | Number | Its limit |
|---|---|---|
| `light` | `least_open_pct` | `at_least`: the request's share open, in percent |
| `thickness` | `thickest_mm` | `at_most`: the request's thickest pane |

A `capture` need holds the lip after play with `--min-lip`, the request's figure, rather than a number: its lips are each pane's own, under `panes`.
