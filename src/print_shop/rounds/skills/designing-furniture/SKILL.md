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
- **joint**: where two parts meet face to face. **Through**: held by a bought bolt that passes right through both parts, with a washer under its head and a nut on the far side. **Dovetail**: a tail on one part slid into a socket in the other, held by its own shape. **Bolted**: held by bought bolts into bought inserts. **Bonded**: glued or welded over the faces. **Touching**: held by nothing, so it can push and can't pull.
- **seat**: the face a thing bears on when it's home: the flat face round a hole's end that a washer or a nut bears on, and the face square to a dovetail's slide that stops its tail. **Pocket**: a recess at a hole's end whose flat walls hold a nut by its flats, so it can't turn. **Play**: the gap between each of the nut's flats and its pocket's wall.
- **tail**: the half of a dovetail that stands off its part, wider at its far face than at its **neck**, where it joins its part. **Socket**: the slot in the other part that the tail slides into. **Flank**: a face of the tail that runs along the slide. **Taper**: the tail narrowing toward the way it slides in, so it tightens as it goes home.
- **latch**: a flexing **tab** on one part whose hook snaps over a ledge on the other when the tail is home. Its **catch** is where the hook and the ledge overlap.
- **assembly file**: the JSON file that names the parts, where each is, how each is printed, the joints, and where it stands.

## The workflow

Copy this checklist into your notes and tick it off as you go:

```
- [ ] 1. The weight, where it sits, and what the request fixes written down
- [ ] 2. The load path drawn through the whole shape, before any split
- [ ] 3. The splits, each where the load is low, and each part's side on the bed
- [ ] 4. The joints, and the bought bolts, nuts and washers where they take load
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
- **A leg prints on its side, with its curve in the bed's plane.** A leg bends, and a bend pulls along its length, so its length lies in its layers. Standing on end, the same bend pulls one layer off the next.
- **A part too long for the bed, such as a leg, is split across its length and joined with a tapered dovetail.** The pieces lap, and the tail slides in along the part's length. The joints section says how.
- **Check each part against the bed as soon as it's split:** `fit-bed` says the side each lies on and whether it fits. A part longer than the bed is wide may still fit corner to corner.

### 4. The joints

- **A printed assembly is held mechanically, never by glue.** Glue isn't how printed parts are joined here: a glued seam between prints isn't trusted with a load, and `load` reads a `bonded` joint that carries any as `UNREAD`, never `OK`: no need passes on it. Don't choose `bonded` to hold printed parts together.
- **`through` where it takes load:** a bought bolt right through both parts, a washer under its head and a nut on the far side, never a printed thread. Name each from the bench's tables, such as `m5-8.8`, `m5-nut` and `m5-washer`, all one size, and give each bolt's place, a point on its axis. The head is on the first part the joint names and the nut on the second.
  - **Each part has a round or peaked hole along the bolt's axis,** no narrower than the bolt, such as 5.5 mm for M5, and the two holes meet end to end where the parts meet.
    - **Round:** the washer or nut bears on its whole ring, and the shank on the bolt's width times the hole's length.
    - **Peaked, where the hole lies level on the bed:** a round hole there needs support, and a peaked one none. It's the round with two flat faces tangent to it that rise to a point, no flatter than 45°, along the whole hole, the point up as printed. A flatter peak, or one cut short of its point, is refused. `load`, `fit-bed` and `rays` read it and say it's peaked. Its seat is the ring less the peak's cut, so it's pressed harder, and a shank leaning toward the point has only what's left of the round to bear on, about 3 tenths of it at 45°: `load` gives both areas.
    - **Or a drop-in channel, declared** (mqucifer/print-shop#302): a U with a round bottom, open on one side, running across the join, that the bolt drops into from that side, its head bearing on one part's wall and its nut on the other's. Use one where a bolt drops in across a join, in parts printed with the channel's open side up: it needs no support and no threading through. Declare it in the joint, `"channel": {"axis": "x", "open": "+z", "head_wall": "x=12", "nut_wall": "x=28"}`: the assembly's axis the bolt runs along, the way the channel is open, square to that, and the planes of the walls the head and the nut bear on, each on a washer, so no `pocket`. Undeclared, it's refused as a slot's end. What the bench reads on it:
      - **`fit-bed`:** its round against the bolt's shank, with its play; each wall where the channel ends, flat, square to the bolt and wide enough for the washer under the head and under the nut; the open side clear for the bolt to drop in; and the channel facing up as the part is laid. Laid any other way it needs support, and fails.
      - **`load`:** the bolt clamps the two walls, each washer on its ring less the channel's cut; the shank leans on the round only; and the open side carries nothing, so a push toward it fails. A channel carries no shear along its open way: put the joint where the load pulls along the bolt or pushes across it sideways.
  - **Each hole ends on a seat:** a flat face square to the bolt, wide enough for the whole washer. A washer that overhangs an edge fails.
  - **A nut in a pocket** needs no spanner behind it: a hex pocket, or a slot, at the hole's end in the second part, whose walls hold the nut by its flats. State its play in the joint, `"pocket": 0.2`. `fit-bed` checks the pocket against the nut: too tight and the nut doesn't go in, too wide and it turns. A nut in a pocket has no washer under it, so it bears on far less plastic than a washer does.
  - **Keep each hole away from the part's edges.** The plastic between a hole and an edge is what tears out, and `load` reads it. At least a washer's width of plastic round the hole is a start.
- **`dovetail` where a part is split for the bed:** a part split for the bed joins with a tapered dovetail. The tail's part is named first and the socket's second, with `slides`, the way the tail goes into its socket, toward its seat, in the assembly's axes: `{"between": ["leg-upper", "leg-lower"], "kind": "dovetail", "slides": "-z"}`.
  - **The taper points so the weight seats it.** The tail narrows the way it slides in, and it slides in the way the table's weight pushes it, so the weight drives it tighter. Pointed the other way, the weight drives it out, and `load` fails it.
  - **Give it a seat square to the slide,** such as the tail's tip on the socket's end, to take the weight. On its tapered flanks alone a tail is a wedge: it pushes the socket's walls apart with many times the weight.
  - **Glue channels are a second hold only.** Cut channels in the flanks for glue if you like: they're the parts' shape, and count as nothing. The Sponsor: "We can glue, it just can't be the primary hold." No field gives glue a figure, and a dovetail is never named `bonded`.
  - **A latch holds the lift.** Lifted by its top, a table hangs each lower piece from its dovetail, pulled the way the tail slides out, and the taper holds nothing that way. A printed click latch at the seat does: a tab on one part, its hook over a ledge on the other, within 1 mm. Name it in the joint: `"latch": {"on": "leg-upper", "arm": [[x0, y0, z0], [x1, y1, z1]], "root": "z=250"}`, the part the tab is on, a box round the tab, hook and all, with one side on its root, and the root's plane. The lift is a load case of its own: `load assembly.json --lift top`.
  - **A sideways pull on a taper also pushes the tail out.** Friction isn't counted, so a load that levers the tail off its socket squeezes it back up its taper. The weight has to seat it harder than that, or its latch has to hold it.
  - **Draw it home:** the tail where it sits once slid in, its flanks and its seat on the socket's, with no play. `load` reads the faces that meet, and a tail drawn loose meets fewer.
  - **Lay the tail's neck along its layers.** What pulls the tail off its socket pulls across its neck. A neck built up layer on layer from its part's face is pulled across its layers there.
  - **The latch has to get on too.** Its hook passes the other part on the way in, and its tab bends by the hook's depth. `clearance --slide` along the way out reads it held, and the harness reads its way on and its bend when the run says it's done.
- **Bolts hold a pull; they hold a bend by their distance from the edge the joint turns about.** Two bolts far apart across a joint hold a bending moment far better than one at its middle.
- **`bolted`, a bolt into a heat-set insert, only where a bolt can't pass through.** An insert pulls out of its plastic long before a steel bolt gives, and no pull-out figure for an insert in printed PETG has a source yet. So a bolted joint that has a pull to hold reads `UNREAD`, never `OK`, until the joint gives its own: `pull_n`, what each bolt holds in N, with its `source`. That figure comes from the request, an answer or a pull test. If there's none, ask with `questions`: never make one up.
- **Touching** only where the load always pushes the faces together, such as a top resting on a ledge: a touching joint that's pulled, or pushed off its edge, fails.
- **Parts meet face to face.** Two parts that pass through each other, or that meet with no joint named, are refused, saying where.

### 5. The parts and the assembly file

Each part is its own STEP file, one solid. Write the assembly file beside them, from the same named dimensions the parts are built from:

```json
{"parts": [
   {"name": "top", "file": "top.step", "at": [0, 0, 500], "material": "petg", "layers": "z"},
   {"name": "leg1", "file": "leg.step", "at": [10, 10, 0], "turn": [["z", 90]], "layers": "x"}],
 "joints": [
   {"between": ["top", "leg1"], "kind": "through", "bolt": "m5-8.8", "nut": "m5-nut",
    "washer": "m5-washer", "at": [[30, 30, 500]], "pocket": 0.2}],
 "held": [{"plane": "z=0", "how": "standing"}]}
```

- `at` moves a part to where it sits, after each `turn` about the origin's axes, in degrees. A part drawn where it sits needs neither.
- `layers` is the axis of the part's own file that points up on the bed. It turns with the part, so a leg drawn lying along x and printed that way has `"layers": "z"` however it's turned in the assembly.
- `held` is where it stands, `standing` on a plane, free to slide, or `fixed` to it, as a bracket is to a wall. With none it stands on its lowest level.
- A `dovetail` joint and a latch's `arm` and `root` are in the assembly's coordinates, where the parts sit, not a part's own file's.

### 6. Each bench check

| The request asks | `measures` | Its check |
|---|---|---|
| it holds W kg, sitting at a place | `load` | `load assembly.json --weight W --at X,Y,Z` |
| it holds W kg shared between places | `load` | `load assembly.json --weight W --at X1,Y1,Z1 --at X2,Y2,Z2` |
| the top moves no more than D under the weight | `load` | the same command, `number` `moves_mm`, `at_most` D |
| a bracket holds W kg pulling sideways | `load` | `load assembly.json --weight W --at X,Y,Z --toward -y`, with its wall face `fixed` |
| it can be lifted by its top, and nothing drops off | `lift` | `load assembly.json --lift top` |
| a dovetail's latch holds its tail in | `fit` | `clearance socket.step tail.step --slide <the tail's way out>`, verdict `HELD` |
| a dovetail's tail goes in freely until it's home | `fit` | `clearance socket.step tail-out.step --slide <its way in>`, the tail drawn D mm short of its seat: `number` `slide.first_mm`, `at_least` just under D |
| each part fits the bed, and each nut's pocket fits its nut | `bed-fit` | `fit-bed assembly.json` |
| which way a part prints | a bed need | `{"by": "bed", "file": "leg.step", "side": "-z"}`, the side `fit-bed` names |

Run them in this order: `fit-bed` first, as a part that doesn't fit is split again and the joints move; then `load`.

1. Read the verdict and the numbers before anything else.
2. If it fails, fix the design: where a split is, which way a part lies, how thick it is where the reply says it's pulled, how many bolts a joint has, how far apart, and how far each is from an edge. Never change the weight, the assembly's joints or a part's `layers` to pass: the file says what will be printed and joined.
3. Run the same check again, and `fit-bed` again after any change to a part.

**Reading `load`.** Each part's line gives its worst pull across its layers and where, held to 1% for PETG, and its largest stretch any way, with how far out of the layers' plane it points. `FAIL` across the layers is the print's direction or the part's thickness at that place. Each joint's line gives the force and the bending moment it carries, and its worst bolt against what it holds. A `through` joint's lines then give, for each bolt and each of the two parts: the pressure under its washer or its nut, and where its shank leans on its hole's wall, each against what the plastic takes pressed; and the plastic round the hole, read as strain, which is where a bolt near an edge tears out. Its last line names which of the bolt, the washers, the nut and the plastic is nearest its limit. A through joint is never `OK` on its bolt alone. A `dovetail` joint's lines give which way the load drives its tail, into its seat or out of it, and by what force; each face that pushes, and how hard, as its flanks and its seat only push; its latch against what it has to hold, where the load draws the tail out; and the tail's neck and the socket's walls, read as strain. A tail driven out with no latch fails, and so does a load its faces would have to pull to carry. `--lift PART` holds the assembly by that part's highest level and hangs every other part by its own solid weight. `moves_mm` is how far the loaded place moves. `UNREAD` means a bolted joint has no figure for its insert, or a `bonded` joint carries load: nothing read fails, and nothing says the joint holds. `DISAGREE` means the reading changes with the mesh, most often at a sharp inside corner: round the corner, where a real part cracks first anyway.

**What `load` can't read.** It reads every part as solid: a part printed with thin walls and sparse infill is weaker and bends more, so print load-bearing parts with thick walls and say so at `ask`. It doesn't read a weight left on for months, a knock, the parts' own weight, or a joint that works loose. A through-bolt is read snug and no more: tightened hard, it presses its washers on the plastic by itself, which isn't read. A dovetail is read with no glue and no friction, where it's drawn, home. Say what was read and what wasn't.

### 7. Asking and done

`ask` draws a sheet: give each part its colour, and say for each which side lies on the bed and why its layers suit its load. Name each joint's bolts, nuts, washers and inserts as things to buy. `done` names every printed part in `part`, and gives each one's side in `on_bed`.
