---
name: cadquery-threads-helices
description: helices, threads and springs in CadQuery 2.8: a helix, sweeping a profile along it, and joining a thread to a core so the result is valid
---

# Helices, threads and sweeps

From CadQuery's [API reference](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/apireference.rst)
(`Wire.makeHelix`, `Workplane.sweep`, `Solid.sweep`) and its swept-helix example, at
v2.8.0. CadQuery has no thread function of its own: a thread is a profile swept along
a helix.

## The helix
- `cq.Wire.makeHelix(pitch, height, radius, center=Vector(0, 0, 0), dir=Vector(0, 0, 1),
  angle=360.0, lefthand=False)`: a right-hand helix by default, rising `pitch` per
  turn, `height` tall, starting at `center + (radius, 0, 0)`. It's a class method of
  `cq.Wire`: there's no `cq.makeHelix` or `Workplane.makeHelix`.
- `lefthand=True` turns it the other way.

## Sweeping a profile along it
- Draw the profile as a closed wire in a plane that holds the helix's start point,
  e.g. on `cq.Workplane("XZ")` at `x = radius`: on `"XZ"`, a point `(x, y)` is at
  X = x, Z = y.
- `.sweep(helix, isFrenet=True)` on that workplane: for a helix, `isFrenet=True` turns
  the profile with the helix, so it doesn't twist away from the axis.
- If the sweep comes out twisted or not solid, `auxSpine=` (a second helix, as a
  Workplane, guiding the profile's turn) is the other way to hold it.

```python
import cadquery as cq

helix = cq.Wire.makeHelix(pitch=2, height=6, radius=5)
profile = cq.Workplane("XZ").polyline([(4.8, -0.5), (5.8, 0.0), (4.8, 0.5)]).close()
thread = profile.sweep(helix, isFrenet=True)
assert thread.val().isValid() and thread.val().Volume() > 0
```

## A thread that stays valid
A swept thread joined straight onto a cylinder of the same length often gives an
invalid shape or a compound with no solid. What works:
1. Make the profile reach a little into the core (0.1 mm below the root), so the two
   overlap rather than touch.
2. Sweep along a helix longer than the thread, starting a pitch below it and ending a
   pitch above, and make the core cylinder just as long.
3. `core.union(thread)` on the Workplanes (or `core.val().fuse(thread.val())` on the
   shapes), then `.intersect()` it with a cylinder of the thread's real length, which
   cuts both ends square.
4. Join the head or anything else onto that, and check `part.val().isValid()`.

```python
import cadquery as cq

pitch, length, root = 2.0, 6.0, 4.8
helix = cq.Wire.makeHelix(pitch, length + 2 * pitch, 5, center=cq.Vector(0, 0, -pitch))
profile = (
    cq.Workplane("XZ")
    .polyline([(root - 0.1, -pitch - 0.5), (5.8, -pitch), (root - 0.1, -pitch + 0.5)])
    .close()
)
thread = profile.sweep(helix, isFrenet=True)
core = cq.Workplane("XY").workplane(offset=-pitch).circle(root).extrude(length + 2 * pitch)
trim = cq.Workplane("XY").circle(10).extrude(length)
part = core.union(thread).intersect(trim)
assert part.val().isValid()
assert abs(part.val().BoundingBox().zlen - length) < 1e-3
```

## An ISO metric thread's numbers
For a pitch P, ISO 68-1's basic profile has 60° flanks, a height H = 0.866 P, a flat
of P/8 at the crest and P/4 at the root, and a minor diameter of d - 1.0825 P. A nut's
thread is the same profile grown by the clearance it needs.

## Springs and coils
The same sweep with a circle for the profile: a circle on a plane square to the
helix at its start, swept with `isFrenet=True`.
