---
name: cadquery-selectors
description: CadQuery 2.8's string selectors for faces, edges and vertices: direction, parallel, perpendicular, type, nth, and combining them
---

# Selectors

From CadQuery's [Selectors](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/selectors.rst)
page, at v2.8.0. A selector filters what `.faces()`, `.edges()`, `.vertices()` or
`.solids()` pick from the objects on the stack.

| Selector | Picks | Example |
|---|---|---|
| `>Z`, `<Z` | the farthest along +Z, or along -Z | `faces(">Z")`: the top face |
| `\|Z` | parallel to Z: edges along Z, and faces whose normal is along Z | `edges("\|Z")`: the vertical edges; `faces("\|Z")`: the top and bottom |
| `#Z` | perpendicular to Z: faces whose normal is across Z | `faces("#Z")`: the side faces |
| `+Z`, `-Z` | faces whose normal points along +Z, or -Z | `faces("-Z")` |
| `%CIRCLE`, `%LINE`, `%PLANE`, `%CYLINDER` | by geometry type | `edges("%CIRCLE")`: the round edges |
| `>Z[n]`, `<Z[n]` | the nth of the faces square to Z, counted from 0, in order along +Z | `faces(">Z[-2]")`: the second from the top; `faces(">Z[1]")`: the second from the bottom |

```python
import cadquery as cq

box = cq.Workplane("XY").box(10, 10, 10)
assert len(box.faces("|Z").vals()) == 2  # top and bottom
assert len(box.faces("#Z").vals()) == 4  # the sides
assert len(box.edges("|Z").vals()) == 4  # the vertical edges

# A stepped part: faces square to Z at z = 0, 5 and 10.
steps = cq.Workplane("XY").box(10, 10, 5, centered=(True, True, False))
steps = steps.faces(">Z").workplane().rect(4, 4).extrude(5)
assert abs(steps.faces(">Z[-2]").val().Center().z - 5) < 1e-6  # second from the top
assert abs(steps.faces(">Z[1]").val().Center().z - 5) < 1e-6  # second from the bottom
assert abs(steps.faces(">Z[0]").val().Center().z - 0) < 1e-6
```

- **Combine** with `and`, `or`, `not` and brackets: `edges("|Z and >Y")`,
  `faces(">Z or <Z")`, `edges("not(<X or >X)")`.
- Any direction works: `>X`, `<Y`, or a vector, `">(1,1,0)"`.
- A selector that picks nothing raises later, at the operation that needs something
  selected (see `cadquery-errors`). Check with `len(wp.faces(">Z").vals())`.
- `.tag("base")` names a stage of the chain, and `.faces(">Z", tag="base")` or
  `.workplaneFromTagged("base")` goes back to it.
