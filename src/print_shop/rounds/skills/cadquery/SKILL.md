---
name: cadquery
description: how to build a part in CadQuery 2.8 from a spec: sketch, then features, then checks, the two kinds of object, and which reference to read for what
---

# Building a part in CadQuery 2.8

Distilled from CadQuery's own documentation at the version this container runs,
v2.8.0 ([Quickstart](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/quickstart.rst),
[Primer](https://github.com/CadQuery/cadquery/blob/v2.8.0/doc/primer.rst)). `docs`
searches that documentation; `lookup` gives the installed library's own signatures.
Use them before guessing a name.

## The approach

1. **Read the spec for its frame.** Where is the origin, which way is up, which face
   sits where? Write those numbers down as constants at the top of the script, and
   build every feature from them.
2. **Sketch, then extrude, revolve, loft or sweep** the base solid. Most parts are a
   `cq.Workplane` chain: a 2D sketch on a plane, then one operation that makes a
   solid (`extrude`, `revolve`, `loft`, `sweep`).
3. **Add features on selected faces:** holes, pockets, bosses, fillets and chamfers,
   each on a face or edge picked with a selector.
4. **Combine solids** when a feature is easier to make as its own solid.
5. **Export** with `cq.exporters.export(part, "part.step")`, one file per part.
6. **Check what you made** against the spec: its bounding box first (is it where the
   spec places it?), then the sizes and gaps the spec states.

## Two kinds of object

A `cq.Workplane` is a chain: each method returns a new Workplane, holding shapes. The
shapes themselves (`cq.Solid`, `cq.Compound`, `cq.Face`, `cq.Wire`, `cq.Edge`) are
another kind of object, with methods of their own. Many errors are a method of one
called on the other.

| | On a Workplane | On a shape |
|---|---|---|
| combine | `union`, `cut`, `intersect` | `fuse`, `cut`, `intersect` |
| move | `translate((x, y, z))`, one tuple | `translate(cq.Vector(x, y, z))`, `moved(...)` |
| check | not here | `isValid()`, `Volume()`, `BoundingBox()`, `distance(other)` |
| between them | `.val()` is its first shape, `.vals()` all of them | `cq.Workplane().add(shape)` |

```python
import cadquery as cq

part = cq.Workplane("XY").box(10, 10, 5)
solid = part.val()
assert solid.isValid() and abs(solid.Volume() - 500) < 1e-6
assert not hasattr(part, "isValid") and not hasattr(part, "fuse")
back = cq.Workplane().add(solid.fuse(cq.Solid.makeBox(1, 1, 1)))
```

## Which reference to read

| Read | When |
|---|---|
| `cadquery-sketch-extrude` | making the base solid: workplanes, 2D shapes, extrude, revolve, loft, holes, arrays |
| `cadquery-selectors` | picking a face or an edge to work on: `faces(">Z")`, `edges("\|Z")` and the rest |
| `cadquery-threads-helices` | a thread, a spring, a helix, or any profile swept along a path |
| `cadquery-assemblies-fits` | several parts that fit together, each in its own file, placed as assembled |
| `cadquery-errors` | an exception or an invalid shape: what it usually means and what to change |

## Habits that save turns

- **Name things with the API as it is.** If you're unsure of a name or what it
  takes, `lookup` it: it says which classes have it.
- **One script, run often.** Write the whole part, run it, read the error, change
  one thing with `edit`.
- **Keep a shape valid.** After a boolean, `part.val().isValid()` should be `True`; a
  part that isn't valid won't be measured.
- **Units are millimetres**, angles in degrees unless an argument says radians.
