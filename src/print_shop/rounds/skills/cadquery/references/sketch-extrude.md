---
name: cadquery-sketch-extrude
description: CadQuery 2.8's workplanes, 2D shapes and the operations that make a solid: extrude, revolve, loft, holes and arrays
---

# Workplanes, sketches and solids

From CadQuery's [Workplane](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/workplane.rst),
[API reference](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/apireference.rst) and
[Examples](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/examples.rst), at v2.8.0.

## Workplanes
- `cq.Workplane("XY")` starts on the XY plane at the origin, its normal +Z. Use the
  axis names, `"XY"`, `"YZ"`, `"XZ"`: on `"XZ"` the normal is -Y, so `extrude(d)` goes
  towards -Y. (The other names, such as `"front"` and `"top"`, aren't what most CAD
  programs mean by them.)
- `.workplane(offset=d)` moves the plane along its normal; `.center(x, y)` moves its
  origin within it; `cq.Workplane("XY", origin=(x, y, z))` starts it anywhere.
- `.transformed(offset=(x, y, z), rotate=(rx, ry, rz))` moves and turns it.
- `.faces(">Z").workplane()` puts a new plane on the selected face. Its origin is the
  current origin projected onto the face, not the face's centre: for the centre, pass
  `centerOption="CenterOfBoundBox"` (see `cadquery-selectors`).

```python
import cadquery as cq

base = cq.Workplane("XY").box(20, 10, 4, centered=False)  # from (0, 0, 0) to (20, 10, 4)
on_top = base.faces(">Z").workplane()
assert on_top.plane.origin.toTuple() == (0.0, 0.0, 4.0)  # the origin, projected
centred = base.faces(">Z").workplane(centerOption="CenterOfBoundBox")
assert centred.plane.origin.toTuple() == (10.0, 5.0, 4.0)  # the face's centre
```

## 2D shapes
- `.rect(w, h)`, `.circle(r)`, `.ellipse(a, b)`, `.polygon(n, d)` (d is the diameter
  of the circle through its corners), `.slot2D(length, diameter)`.
- `.polyline(points).close()` for any outline: it must end in `.close()` before an
  `extrude`, or there's no closed wire to extrude. `.lineTo`, `.hLine`, `.vLine`,
  `.threePointArc` and `.spline` draw one segment at a time.
- Shapes are centred by default; `.rect(w, h, centered=False)` puts the corner at the
  origin.
- A second shape inside the first makes a hole when extruded:
  `.circle(10).circle(4).extrude(5)` is a tube.

```python
import cadquery as cq

tri = cq.Workplane("XY").polyline([(0, 0), (10, 0), (0, 10)]).close().extrude(2)
assert abs(tri.val().Volume() - 100) < 1e-6
```

## Making a solid
- `.extrude(d)`: along the plane's normal. `extrude(d, both=True)` goes both ways,
  `d` each side. A negative `d` goes the other way.
- `.revolve(angleDegrees, axisStart, axisEnd)`: round an axis in the plane's own
  coordinates.
- `.loft()`: through several wires on offset workplanes, e.g. a circle, then
  `.workplane(offset=10).rect(...)`.
- `.box(x, y, z)` and `.cylinder(height, radius)` are centred on the plane's origin;
  `centered=(True, True, False)` sits the box on the plane.
- From a shape rather than a workplane: `cq.Solid.makeBox`,
  `cq.Solid.makeCylinder(radius, height, pnt, dir)`, `cq.Solid.makeCone`, and
  `cq.Solid.extrudeLinear(face, vector)` for a face you made yourself.

## Features
- `.hole(diameter, depth=None)` through the solid from the selected face's plane;
  `.cboreHole(d, cboreDiameter, cboreDepth)` and `.cskHole(d, cskDiameter, cskAngle)`.
- `.pushPoints([(x, y), ...])`, `.rarray(xSpacing, ySpacing, xCount, yCount)` and
  `.polarArray(radius, startAngle, angle, count)` place the next shape or hole at
  each point.
- `.shell(thickness)` hollows the solid, opening the faces selected before it:
  `.faces(">Z").shell(-2)` is an open box with 2 mm walls inward.
- `.fillet(r)` and `.chamfer(d)` on selected edges: `.edges("|Z").fillet(2)`.
- `.union(other)`, `.cut(other)`, `.intersect(other)` combine them;
  `.translate((x, y, z))` (one tuple) and `.rotate(axisStart, axisEnd, degrees)` move
  them. `.mirror("YZ")` gives only the mirrored copy; `.mirror("YZ", union=True)`
  keeps both.

```python
import cadquery as cq

half = cq.Workplane("XY").box(5, 4, 2, centered=False)
assert abs(half.mirror("YZ").val().Volume() - 40) < 1e-6  # the copy alone
assert abs(half.mirror("YZ", union=True).val().Volume() - 80) < 1e-6
moved = half.translate((1, 2, 3))
assert moved.val().BoundingBox().xmin == 1.0
```

## Exporting
- `cq.exporters.export(workplane_or_shape, "part.step")`: the file type follows the
  name ([Import and export](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/importexport.rst)).
