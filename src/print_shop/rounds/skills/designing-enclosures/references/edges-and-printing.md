---
name: softening-edges
description: Rounds an enclosure's edges and corners in CadQuery, in the order that keeps fillets building, as curves and never as strips, and says each part's print orientation and what rounding its bed edges costs. Use when softening a case's or lid's edges, when a fillet fails, or when choosing how each part lies on the bed.
---

# Softening edges, and how each part prints

## The order to fillet in

Build the part's whole shape first, every box, cut and boss, then round it. A fillet on an edge that a later cut runs through is cut away, and a fillet across a later union leaves a step.

1. **The outline's upright edges** first, the largest radius: `.edges("|Z").fillet(R)`.
2. **The top and bottom edges** next, the request's radius: `.faces(">Z").edges().fillet(R)`. With the upright edges already round, the corners blend into one smooth face each.
3. **Each opening's outside edge,** where a finger meets it: select it by where it is, such as `.faces(">X").edges("not |X")`.
4. **Inside corners** last, if at all: a fillet at a boss's foot or a wall's root stiffens it, and nothing reads it as an edge.

A fillet as large as the faces it joins fails: the radius has to be less than half the thinnest face it meets. Where a fillet fails, give that edge a smaller radius, or round the edges in a different order; `cadquery-errors` has the messages. Never fall back to a chamfer where the request asks rounded: `edges` reads a chamfer's two edges as sharp.

Keep the wall even under a rounded outside: cut the inside with a box rounded `R - WALL` at its upright edges, so the wall's thickness is the same round the corner. Don't `shell` a block that's rounded all over: in CadQuery 2.8 it fails quietly and leaves a surface that isn't closed.

## Build roundings as curves

Every rounding is a curve: `fillet` on the solid, `offset2D(r, kind="arc")` on an outline, or a `Sketch` with `.vertices().fillet(r)`. Never a list of points round a corner through `polyline`: it's stored as flat strips, one face for each segment, `info` counts them, and `edges` reads each strip's edge as sharp, turning a few degrees. `cadquery-sketch-extrude` has the same rule for any part.

```python
import cadquery as cq

L, W, H, WALL, R = 100.0, 70.0, 30.0, 2.0, 3.0  # named once
base = cq.Workplane("XY").box(L, W, H, centered=(True, True, False))
base = base.edges("|Z").fillet(R).faces("<Z").edges().fillet(R / 2)  # outline, then the bottom
inside = cq.Workplane("XY").workplane(offset=WALL).rect(L - 2 * WALL, W - 2 * WALL)
base = base.cut(inside.extrude(H).edges("|Z").fillet(R - WALL))  # an even wall
assert len(base.solids().vals()) == 1
assert {f.geomType() for f in base.val().Faces()} <= {"PLANE", "CYLINDER", "TORUS", "SPHERE", "BSPLINE"}
```

## Print orientation

Say each part's orientation at `ask` and `done`, where `done`'s `on_bed` gives each file its side. Where the request says which way a part prints, that's a bed need on its file, not a stop: `done` holds `on_bed` to it (`bench`, "How a need names a check"). Changing a part for its orientation or supports is a later pass, not the design (mqucifer/print-shop#9).

| Part | Lies | Why |
|---|---|---|
| base | on its floor, opening up | its walls and bosses stand with no overhang |
| lid | its outside face down, lip up | its outside face prints flat on the bed |
| a clip | with its arm level, where the part lets it lie level; hanging from a part printed flat, upright (`sizing-walls-and-lids`) | level, it bends along the layers ([E1]) |

- **An overhang prints clean up to 45°** from upright, and to 60° on some settings ([E3] [E2]). A fillet's top half is such an overhang; a hole through an upright wall is bridged at its top.
- **A rounded edge on the bed** starts as a very steep overhang, and a chamfer prints cleaner there ([E2]); a 45° chamfer or a radius on every bed edge is the usual practice ([E3]). When the request asks every edge rounded, round the bed edges too, and say at the ask which edges lie on the bed and that their lowest layers may droop: the Sponsor decides.

## Sources

- [E1]: https://www.hubs.com/knowledge-base/how-design-snap-fit-joints-3d-printing/ ("avoid snap-fit cantilevers built vertically in the Z direction")
- [E2]: https://help.prusa3d.com/article/modeling-with-3d-printing-in-mind_164135 ("If oriented towards the print bed, fillets create a very steep overhang"; "use chamfer instead if a perfect part finish is a priority"; overhangs "between 45 and 60 degrees")
- [E3]: https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/ ("Include a 45° degree chamfer or radius on all edges of an FDM part touching the build plate"; overhangs "up to 45°")
