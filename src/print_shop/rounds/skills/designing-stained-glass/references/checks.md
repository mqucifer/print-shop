---
name: checking-stained-glass
description: Explains what the stained-glass bench commands answer (capture, panes, light and thickness) and how to read each reply, its numbers and its pane numbers. Use when running one of them, reading its answer, or naming one of their numbers in a need.
---

# The stained-glass checks

These come with the stained-glass toolset, beside the core's (the `bench` skill). Each answers in plain text and numbers, with a second check by a different method, as every bench command does. `<command> --help` gives its arguments.

| Command | Answers |
|---|---|
| `capture FRAME --pocket-z Z --clearance C --pane-thickness T [--pane-floor-z F] [--min-lip M \| --full-rim R --least-rim R2 --min-visible P] [--layer L] [--panes N]` | whether each pane is held by shape on both faces, wherever its play lets it sit: its lip, how thick it is, where it's thin, and whether the pane can slide or swing out. `--panes` fails on any other count. With `--full-rim`, each pane's rim is sized to keep P% of it visible |
| `panes FRAME --pocket-z Z [--lip-z Z] --clearance C [--pane-thickness T]` | a pane from each opening, written to `panes/FRAME/`, its fit, and the edge with no lip |
| `light FRAME PANE... [--with FILE]... [--min-open P]` | the share of each pane open straight through the frame, in percent, and the narrowest band of leading a person sees between two open pieces. `--with` adds another part of the frame, such as a cap printed apart |
| `thickness PANE... [--max T] [--min T]` | each pane's thickness straight through, its thickest and thinnest |

## Reading them

- **`capture`'s pane numbers are its own,** largest opening first, not your files'. Each pane is said with its centre, its size and, where a pane file beside the frame has its outline, that file's name. Its "pane 1" isn't `pane_1.step` unless it says so.
- **`capture`'s lips.** Each face of each pane, under `panes`, has `least_lip_mm`, the lip after play: how far it covers the pane in the middle of its pocket. `least_lip_with_play_mm` takes the play off again, for the pane slid to one side, and that's what `--min-lip` holds. The top-level `least_lip_mm` is the least of any pane.
- **A rim sized to the visible share.** `--full-rim R --least-rim R2 --min-visible P` go together, all from the request, with no default, the rims before play. Each pane needs the full rim if P% of it stays visible with it; if not, the widest rim that keeps P%; and if even the smallest rim can't, it's to be removed. A pane passes when its rim is at least the rim it needs all round and at least P% of it is visible on the frame as built. Each pane's line says its share, the share wanted, the rim it needs and the rim it has, and a failing pane says "set this pane's rim to X mm before play" or "remove this pane". Its numbers are under each pane's `rim`.
- **Why a pane isn't held.** When a face lets the pane out, or holds it by less than the limit along half its edge or more, `capture` says what the frame has beyond that face, read from the pane's face to the frame's end, stretch by stretch with where on the edge: covered by only so much; nothing at the edge, with the frame starting so far in; a hole as wide as the pocket, so the pocket's cut runs on through that face; a hole wider than the pocket, with the frame starting so far out; or no frame there at all. It's a measurement by sections, checked by rays: if your own check says otherwise, find which step in your script made what it reads, before changing the frame. Each face's `over` has every stretch.
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
