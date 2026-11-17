---
name: designing-furniture
description: Designs printed furniture that carries a weight and is bigger than the printer's bed, such as a side table, a shelf or a stand, as parts split along the load's path and joined where the load is low, each laid on the bed so its layers suit its load, through the bench's load and bed checks. Use when the request's kind is furniture, or it asks for a table, shelf, stand, stool or bracket that holds a weight, or for a part too big for the bed to be split and joined, and before settling needs for a weight, a joint or a part's fit on the bed.
---

# Designing furniture

Printed furniture is bigger than the bed and carries a weight. So it's printed as parts and joined, and two things decide whether it holds: where it's split, and which way each part's layers lie. This skill is the workflow, the terms and the exact checks. The design itself is yours.

## The terms

Each is used in one sense only, here and in every bench reply.

- **load path**: the way a weight travels through the parts to the floor: down a leg, along a rail, across a top to its supports.
- **layers**: a printed part is a stack of layers, and it's far weaker pulled across them than along them: PETG takes about 1% strain across its layers and about 5% in their plane.
- **across the layers**: a pull square to the layers, which parts one from the next. **Along the layers**: any pull in their plane.
- **split**: where one shape is cut into two parts so each fits the bed.
- **joint**: where two parts meet face to face. **Bonded**: glued or welded over the faces. **Bolted**: held by bought bolts into bought inserts. **Touching**: held by nothing, so it can push and can't pull.
- **assembly file**: the JSON file that names the parts, where each is, how each is printed, the joints, and where it stands.

## The workflow

Copy this checklist into your notes and tick it off as you go:

```
- [ ] 1. The weight, where it sits, and what the request fixes written down
- [ ] 2. The load path drawn through the whole shape, before any split
- [ ] 3. The splits, each where the load is low, and each part's side on the bed
- [ ] 4. The joints, and the bought bolts and inserts where they take load
- [ ] 5. The parts built, each its own file where it sits, and the assembly file
- [ ] 6. Each bench check: run it, fix, run it again, until it passes
- [ ] 7. `ask` or `done`
```

### 1. The weight

Write down the weight the request gives and where it sits. If the request names something to hold and gives no weight, ask with `questions`: there's no default, and a need's `--weight` is quoted from the request.

### 2. The load path

Before splitting anything, say in a line or two how the weight reaches the floor through the whole shape, and where it bends most: the middle of a span, the top of a leg, the root of a bracket.

### 3. Splitting along the load path

- **Split along the load's path, not across it.** A cut that the load runs beside leaves each part carrying its share whole. A cut the load has to cross makes the joint carry all of it. A top that spans between legs is split over a support, not at mid-span.
- **Put each joint where the load is low.** A beam held at both ends bends most at its middle and least near its ends. A cantilever bends most at its root. Read where the strain is low from a `load` of the whole shape as one part, and put the split there.
- **Lay each part so its layers run along its pull.** A leg that's pushed straight down can be printed standing: a push across the layers parts nothing. A rail, an arm or a top that bends has a pull along its length on one face, so its length lies flat on the bed. A part printed standing that bends at its foot is pulled across its layers there, and that's where it snaps.
- **Check each part against the bed as soon as it's split:** `fit-bed` says the side each lies on and whether it fits. A part longer than the bed is wide may still fit corner to corner.

### 4. The joints

- **Bolted where it takes load:** a bought bolt into a bought insert, never a printed thread. Name both from the bench's table, such as `m4-8.8` into `m4-heat-set`, and give each bolt's place, a point on its axis.
- **Bolts hold a pull; they hold a bend by their distance from the edge the joint turns about.** Two bolts far apart across a joint hold a bending moment far better than one at its middle.
- **An insert pulls out of its plastic long before a steel bolt gives.** No pull-out figure for an insert in printed PETG has a source yet, so `load` judges the bolt alone and says so. Where a request, an answer or a pull test gives the figure, put it on the joint as `pull_n` with its `source`.
- **Bonded** for faces glued all over. **Touching** only where the load always pushes the faces together, such as a top resting on a ledge: a touching joint that's pulled, or pushed off its edge, fails.
- **Parts meet face to face.** Two parts that pass through each other, or that meet with no joint named, are refused, saying where.

### 5. The parts and the assembly file

Each part is its own STEP file, one solid. Write the assembly file beside them, from the same named dimensions the parts are built from:

```json
{"parts": [
   {"name": "top", "file": "top.step", "at": [0, 0, 500], "material": "petg", "layers": "z"},
   {"name": "leg1", "file": "leg.step", "at": [10, 10, 0], "turn": [["z", 90]], "layers": "x"}],
 "joints": [
   {"between": ["top", "leg1"], "kind": "bolted", "bolt": "m4-8.8", "insert": "m4-heat-set",
    "at": [[30, 30, 500]]}],
 "held": [{"plane": "z=0", "how": "standing"}]}
```

- `at` moves a part to where it sits, after each `turn` about the origin's axes, in degrees. A part drawn where it sits needs neither.
- `layers` is the axis of the part's own file that points up on the bed. It turns with the part, so a leg drawn lying along x and printed that way has `"layers": "z"` however it's turned in the assembly.
- `held` is where it stands, `standing` on a plane, free to slide, or `fixed` to it, as a bracket is to a wall. With none it stands on its lowest level.

### 6. Each bench check

| The request asks | `measures` | Its check |
|---|---|---|
| it holds W kg, sitting at a place | `load` | `load assembly.json --weight W --at X,Y,Z` |
| it holds W kg shared between places | `load` | `load assembly.json --weight W --at X1,Y1,Z1 --at X2,Y2,Z2` |
| the top moves no more than D under the weight | `load` | the same command, `number` `moves_mm`, `at_most` D |
| a bracket holds W kg pulling sideways | `load` | `load assembly.json --weight W --at X,Y,Z --toward -y`, with its wall face `fixed` |
| each part fits the bed | `bed-fit` | `fit-bed assembly.json` |
| which way a part prints | a bed need | `{"by": "bed", "file": "leg.step", "side": "-z"}`, the side `fit-bed` names |

Run them in this order: `fit-bed` first, as a part that doesn't fit is split again and the joints move; then `load`.

1. Read the verdict and the numbers before anything else.
2. If it fails, fix the design: where a split is, which way a part lies, how thick it is where the reply says it's pulled, how many bolts a joint has and how far apart. Never change the weight, the assembly's joints or a part's `layers` to pass: the file says what will be printed and joined.
3. Run the same check again, and `fit-bed` again after any change to a part.

**Reading `load`.** Each part's line gives its worst pull across its layers and where, held to 1% for PETG, and its largest stretch any way, with how far out of the layers' plane it points. `FAIL` across the layers is the print's direction or the part's thickness at that place. Each joint's line gives the force and the bending moment it carries, and a bolted joint's worst bolt against what it holds. `moves_mm` is how far the loaded place moves. `DISAGREE` means the reading changes with the mesh, most often at a sharp inside corner: round the corner, where a real part cracks first anyway.

**What `load` can't read.** It reads every part as solid: a part printed with thin walls and sparse infill is weaker and bends more, so print load-bearing parts with thick walls and say so at `ask`. It doesn't read a weight left on for months, a knock, the parts' own weight, or a joint that works loose. Say what was read and what wasn't.

### 7. Asking and done

`ask` draws a sheet: give each part its colour, and say for each which side lies on the bed and why its layers suit its load. Name each joint's bolts and inserts as things to buy. `done` names every printed part in `part`, and gives each one's side in `on_bed`.
