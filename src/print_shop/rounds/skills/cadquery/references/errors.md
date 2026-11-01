---
name: cadquery-errors
description: CadQuery 2.8's common errors and invalid shapes, what they usually mean, and what to change
---

# Errors and what they usually mean

From the errors CadQuery 2.8.0 raises, and its
[Class reference](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/classreference.rst).

| Error | Usually | Change |
|---|---|---|
| `'Workplane' object has no attribute ...`, `'Solid' object has no attribute ...` | a method of one kind of object called on the other (`cadquery` skill, "Two kinds of object") | `lookup` the name: it says which classes have it. `.val()` takes the shape from a Workplane |
| `type object 'Face' has no attribute ...`, `module 'cadquery' has no attribute ...` | a name that isn't CadQuery's, or is on another class | `lookup` the fragment, or `docs` for how it's done |
| `translate() takes 2 positional arguments ...` | three numbers where it takes one tuple or vector | `.translate((x, y, z))` |
| `object of type 'Solid' has no len()` | a shape where a list was meant: `Shape.solids()` returns a shape | `.Solids()` is the list of solids |
| `ValueError: ... must have at least one solid on the stack to union` | the other side of a `union` or `cut` holds no solid: a wire, a failed sweep, a compound of shells | check `.solids().vals()` on it; make it a solid first |
| `ValueError: No pending wires present` | an `extrude`, `revolve` or `sweep` with no closed wire pending: an open `polyline` (end it with `.close()`), or a wire put on the stack with `add()` and not made pending | `.close()` the outline, or `.add(wire).toPending()` before the operation |
| `StdFail_NotDone`, `Null TopoDS_Shape` | OCCT couldn't do a boolean, fillet or chamfer | a fillet larger than the edge allows; solids that only touch: overlap them by a little, or try a smaller radius |
| `Standard_ConstructionError`, `gp_...` | an impossible geometry: a zero length, parallel vectors, a circle of radius 0 | check the numbers that reached the call |
| a selector that picks nothing, then an `IndexError` or a `ValueError` | `faces(">Z")` on the wrong object, or a type that isn't there | print `len(wp.faces(...).vals())`; see `cadquery-selectors` |
| the part is in the wrong place | a centred `box` or `rect`, a workplane's origin, or `"XZ"`'s normal being -Y | `inspect` the STEP and compare its bounding box with the spec |

```python
import cadquery as cq

open_outline = cq.Workplane("XY").polyline([(0, 0), (10, 0), (0, 10)])
try:
    open_outline.extrude(2)
except ValueError as exc:
    assert "No pending wires" in str(exc)
else:
    raise AssertionError("an open polyline extruded")
assert len(cq.Workplane("XY").box(1, 1, 1).val().Solids()) == 1
```

## An invalid shape
- `part.val().isValid()` is `False` after a boolean: often two surfaces that coincide
  or only touch. Overlap them by a little, or build the feature longer and cut it back.
- A sweep that comes out as a shell, not a solid: the profile wasn't closed, or it
  twisted. Close the wire, and sweep along a helix with `isFrenet=True`
  (`cadquery-threads-helices`).
