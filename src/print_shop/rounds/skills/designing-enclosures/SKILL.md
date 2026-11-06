---
name: designing-enclosures
description: Designs a printed enclosure, a case of a base and a lid round bought parts such as boards, a fan or a plug, from the references through each bench check, with softened edges. Use when the request's kind is enclosure, or it asks for a case, box, housing, lid, standoffs, a port or plug opening, vents, a fan mount or an LED window, and before settling needs for fit, a lid's play or edges.
---

# Designing enclosures

A printed enclosure is a case round parts that were bought, not printed: boards, a fan, a plug. It's usually two parts, a base and a lid, printed apart and closed by friction, snap clips or bought screws. This skill is the workflow, the terms and the exact checks. The design itself is yours.

## The terms

Each is used in one sense only, here and in every bench reply.

- **reference**: a bought part as the request's writer drew it, under `references/`: a board, a standoff, the fan, a plug's body. You never draw one, and its edges aren't read.
- **base**: the part the boards mount in. **lid**: the part that closes it.
- **seat**: where the lid rests on the base, usually the wall's top.
- **lip**: a rim on the lid that fits inside the base's walls and locates it.
- **play**: the gap all round between two parts meant to mate, such as the lip and the wall, on each face.
- **clearance**: the least gap between a printed part and a reference, or between two printed parts.
- **opening**: a hole through a wall for a plug, a fan's air, a vent or an LED's light.
- **boss**: a post on the base that a screw or a heat-set insert goes into. **standoff**: what holds a board off the floor, bought (a reference) or printed (a boss).
- **outside edge**: an edge a finger meets from outside the closed case. `edges` reads these.

## The workflow

Copy this checklist into your notes and tick it off as you go:

```
- [ ] 1. The references read, and what the request fixes written down
- [ ] 2. The parts, and how the lid closes
- [ ] 3. Every dimension named once, from the references
- [ ] 4. The needs, each with what it measures and the request's own limits
- [ ] 5. The base and the lid in 3D, a cut rendered after each build
- [ ] 6. Each bench check: run it, fix, run it again, until it passes
- [ ] 7. `ask` or `done`
```

If the request has `## Stops`, each stop goes where it names. Show it with `ask` and wait for the pick before going on.

### 1. The references

Run `info` on every file under `references/` and write down each one's bounds: the boards' outlines and heights, where each port, LED and hole is, the fan's frame and the plug's body. These are the only source of positions. Write down what the request fixes: which ports are used, which LEDs must show, how the lid closes, the edge radius, and each clearance. If the request names something to fit and gives no figure or no reference for it, ask with `questions`.

### 2. The parts and how the lid closes

Decide the parts before any geometry, and write them down: which part each reference mounts to, where the lid splits from the base, and how it closes. `references/walls-and-lids.md` (the skill `sizing-walls-and-lids`) has the three ways a lid closes, and what each needs. Where the request leaves the way open, take it to the plan stop.

### 3. Dimensions

Name each dimension once at the top of the script, and derive every other from the references' bounds and those: the wall, the floor, the clearance round the boards, the lip's play, the edge radius. The inside is the references' outline plus the clearance; the outside is the inside plus the wall. Print each part's bounds after each run.

### 4. The needs

Each bench need gives `measures`, what it's about, and its command has to measure that. Every limit is the request's, quoted. If the request gives no figure for something it asks, ask with `questions`: there's no default.

| The request asks | `measures` | Its check |
|---|---|---|
| the case clears a board, a fan or a plug by G | `fit` | `clearance base.step references/board.step --min-gap G` |
| the lid's play is from G1 to G2, so it isn't loose | `fit` | `clearance base.step lid-lifted.step --min-gap G1 --max-gap G2` |
| edges, corners and lid softened to R | `edges` | `edges base.step lid.step references/board.step --min-radius R` |
| a round opening of diameter D, such as the fan's | `hole` | `rays lid.step --centre X,Y,Z --d D` |
| the wall round a screw's boss | `wall` | `wall base.step --hole X,Y,Z --min-wall W` |
| each part printable | `printable` | `thin base.step` |
| how firmly the fan or the lid is held | an eye need | none measures it: say what to look for |

- **One pair to a `clearance` need,** the part and what it must clear. `clearance` reads every pair of the files named, and a part resting on another touches it, gap 0: a board on its standoffs, or the lid on its seat, reads `TOO CLOSE` under any `--min-gap`.
- **A lid's play is read with the lid lifted.** Seated, the lid touches its seat, so its least gap is 0 whatever the lip's play. Write a copy lifted off its seat along the way it closes by more than the largest play allowed, `lid-lifted.step`, from the same script. Its least gap to the base is then the lip's play: a lip too loose is `TOO LOOSE`, too tight `TOO CLOSE`.
- **A plug's body is drawn in place,** pushed fully into its port, as a reference. The opening clears the body, not the port: `clearance base.step references/plug.step --min-gap G`.
- **`edges` names every part of the closed case, and the references inside,** in place: each blocks a finger from the others' insides, so the inside of the closed case isn't read. A reference is never read.
- An `edges` need's limit is the request's radius, in its quote. "Softened" with no figure is a question to ask.

`references/checks.md` (the skill `checking-enclosures`) says what `edges` and `clearance --max-gap` answer and how to read them.

### 5. The base and the lid

Build each part from simple solids, joined, cut and then rounded: boxes and cylinders placed from the named dimensions, never a hand-traced list of points. Then:

- **Cut the inside, don't shell a rounded block.** In CadQuery 2.8, `shell` on a block whose every edge is rounded fails quietly, leaving a solid whose surface isn't closed (found building this repository's fixtures, `tests/bench_cases/make_fixtures.py`). Cut the inside away with a box, or with a rounded box `wall` smaller, so the wall stays even.
- **Round the edges last, outside first:** `references/edges-and-printing.md` (the skill `softening-edges`) has the order and the calls. Every rounding is a `fillet`, an arc or a rounded `Sketch`, never strips from points: `edges` reads strips as sharp edges.
- **Each part is its own file:** `base.step`, `lid.step`, and `lid-lifted.step` for the lid's check.
- **Render a cut after each build** of a part with a cavity, before reasoning about what it holds: `render base.step lid.step references/board.step --cut x=X` through the boards, and `--cut z=Z` through the lip. Read the cut's verdict, then its picture.

`references/openings.md` (the skill `cutting-openings`) has the plug's opening, the LED's window and the vents; `references/mounting.md` (the skill `mounting-boards`) the bosses, standoffs and screws.

### 6. Each bench check

Run each check at the need's own command. Then:

1. Read the verdict and the numbers before any picture.
2. If it fails, fix the design: a dimension, a radius, the lid's lip. Never change the check's limit, or its files, to pass.
3. Run the same check again.
4. Go on to the next check only when this one passes.

Run them in this order, as each depends on the one before:

1. `clearance` of each part against each reference it holds: the boards, the fan, the plug's body.
2. `clearance` of the base and the lifted lid, with the request's play.
3. `thin` on every part, one file at a time.
4. `rays` on each round opening, and `wall` on each boss.
5. `edges` on the closed case, with the references inside, at the request's radius. Rounding is last because a change to a wall or an opening changes its edges.

A change for one check can break another, as a thicker wall changes the inside. After a fix, run the earlier checks again.

### 7. Asking and done

`ask` draws a sheet: give each part its colour, the references grey, and add a cut through the boards and one through the lid's lip (`cuts`). Say what each picture shows as drawn. Say each part's print orientation and where its bed edges are (`softening-edges`). `done` names every printed part in `part`: the base, the lid, and any other printed part, not the lifted copy.
