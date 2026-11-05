---
name: building-curves-from-a-layout
description: Builds a part's curves as true circles and arcs in CadQuery 2.8 from a 2D layout checked in Shapely, with the check that both are the same layout, and what a polygon costs where one is unavoidable. Use when a part laid out in Shapely has circles, arcs or rounded corners, before extruding any of it.
---

# From a checked 2D layout to arcs

Shapely is for laying a part out flat and checking it: whether it's in one piece, its areas,
its narrowest places. Its circles and round joins are polygons. CadQuery's are curves. So the
layout is a set of numbers, written once at the top of the script, and each is drawn twice: in
Shapely, to check, and in CadQuery, to build.

## The numbers, drawn twice

```python
import cadquery as cq
import shapely

W, H, CORNER, HOLE, T = 60.0, 40.0, 6.0, 4.0, 3.0  # the layout: once

# In 2D, to check.
plate_2d = shapely.box(-W / 2 + CORNER, -H / 2 + CORNER, W / 2 - CORNER, H / 2 - CORNER)
plate_2d = plate_2d.buffer(CORNER, quad_segs=64)
plate_2d = plate_2d.difference(shapely.Point(0, 0).buffer(HOLE, quad_segs=64))
assert plate_2d.geom_type == "Polygon" and plate_2d.interiors

# In 3D, to build, from the same numbers.
plate = (
    cq.Workplane("XY").rect(W - 2 * CORNER, H - 2 * CORNER).offset2D(CORNER, kind="arc")
    .extrude(T).faces(">Z").workplane().hole(2 * HOLE)
)
solid = plate.val()
assert abs(solid.Volume() / T - plate_2d.area) < 0.001 * plate_2d.area  # the same layout
assert len(solid.Faces()) == 11  # top, bottom, 4 sides, 4 corners and the hole: one face each
```

- **The check:** the solid's volume over its height is the layout's area, to 0.1%: Shapely's
  64 segments a quarter fall that much short of a circle at most.
- **What draws a curve:** `circle(r)`, `threePointArc(through, to)` and `radiusArc(to, r)`
  between two points, `offset2D(d, kind="arc")` to round every corner of an outline by `d`,
  `spline(points)` through points with no centre or radius, and a `cq.Sketch`.
- **An offset in 2D is the shape drawn again, further in.** Shapely's `buffer(-d)` of a circle
  of radius r is `circle(r - d)`; of a ring, `circle(r1 - d).circle(r0 + d)`; of a bar of width
  w, a bar `w + 2d` wide cut out. So one function of the inset draws every offset of a shape,
  and they always fit. `offset2D` on a wire of arcs and lines can fail with `Null TopoDS_Shape`
  on some wires (`cadquery-errors`); drawing again never does.
- **Straight sides are exact as polygons:** a shape with only straight sides can be built from
  its Shapely points with `polyline(points).close()`.

## Where only a polygon will do

A circle drawn with `quad_segs` segments a quarter is off by up to R·(1 − cos(45°/quad_segs)):

| R | 16 (Shapely's default) | 32 | 64 |
|---|---|---|---|
| 10 mm | 0.012 mm | 0.003 mm | 0.0008 mm |
| 40 mm | 0.048 mm | 0.012 mm | 0.003 mm |

Keep it under a tenth of the smallest gap the part depends on, such as a fit's play. Each
segment is a flat face in the file: a ring of 80 mm at 16 segments is 130 faces and about
500 kB, where two circles make 4 faces and about 10 kB, and a slicer can't fit arcs to it.
The bench's `info` counts flat faces under 1 mm across.

```python
import math

import cadquery as cq
import shapely

R, r, T = 40.0, 35.0, 3.0
ring_2d = shapely.Point(0, 0).buffer(R).difference(shapely.Point(0, 0).buffer(r))
off = R - shapely.Point(0, 0).distance(ring_2d.exterior)  # at the middle of a segment
assert abs(off - R * (1 - math.cos(math.radians(45 / 16)))) < 1e-9 and 0.048 < off < 0.049
faceted = cq.Workplane("XY").polyline(list(ring_2d.exterior.coords)[:-1]).close()
faceted = faceted.polyline(list(ring_2d.interiors[0].coords)[:-1]).close().extrude(T)
curved = cq.Workplane("XY").circle(R).circle(r).extrude(T)
assert len(faceted.val().Faces()) == 130 and len(curved.val().Faces()) == 4
```
