---
name: cadquery-assemblies-fits
description: several parts that fit together in CadQuery 2.8: each part its own solid and file, placed as assembled, and checking the clearances between them
---

# Parts that fit together

From CadQuery's [Assemblies](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/assy.rst)
and [Class reference](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/classreference.rst),
at v2.8.0.

## One script, one file per part
- Build each part as its own solid, already where the spec places it in the
  assembly, and export each to the file the spec names:
  `cq.exporters.export(pin, "pin.step")`.
- Derive the parts from the same numbers: a hole's diameter is the pin's plus the
  clearance, so changing one changes both.
- `cq.Assembly` holds parts and constraints for a model to view or solve; it isn't
  needed to export parts that are already placed.

## Checking a fit before `done`
- `measure`'s `clearance FILE` gives the overlap and the gap between two of your
  STEP files. To check in a script instead, on the shapes (`.val()`):
- **Overlap:** `a.val().intersect(b.val()).Volume()` is the shared volume: 0 for a
  clearance fit, above 0 for a press fit.
- **Clearance:** `a.val().distance(b.val())` is the closest distance between them.
- **Where a part is:** `a.val().BoundingBox()` gives `xmin` ... `zmax`.
- A part resting on another touches it: distance 0, overlap 0.

```python
import cadquery as cq

pin = cq.Workplane("XY").circle(3).extrude(10)
hole = cq.Workplane("XY").rect(20, 20).circle(3.2).extrude(5)
assert abs(pin.val().distance(hole.val()) - 0.2) < 1e-6
assert pin.val().intersect(hole.val()).Volume() < 1e-9
```

## Clearances, in words
A clearance is the gap on each side, so a hole is the pin's diameter plus twice the
radial clearance. A press fit is the opposite: the pin larger than the hole. The spec
gives the numbers; when it doesn't, say what you chose.
