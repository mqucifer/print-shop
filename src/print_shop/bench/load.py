"""`load`: joined parts under a weight (mqucifer/print-shop#156).

A side table is bigger than the bed, so it's printed as parts and joined, with bought bolts and
inserts where it takes load. `bend` reads one arm pushed aside. Nothing read several parts joined
together with a weight on them: whether each part takes it the way it's printed, what each joint
has to carry, and how far the top moves.

**The reading.** The assembly file (`assembly.py`) gives each part's file, where it is, its
material and the way its layers are stacked, the joints, and where it's held. The parts are put
in their places and must meet face to face: two that pass through each other, two that meet with
no joint named, and a joint whose parts don't meet are each said, and nothing is solved. The
parts are then one body whose faces that meet are shared, so gmsh meshes it with the same nodes
on both sides of each joint, and CalculiX solves it once, linear and static, through FreeCAD's
FEM workbench. `--weight` kg is shared equally between the places `--at` names, each spread over
the surface within `--over` mm of it, and pulls `--toward` -z unless said.

**Each part:** its worst strain pulled across its layers, which is what parts one printed layer
from the next, held to the material's limit across them; and its largest stretch any way, with
where it is and how far out of the layers' plane it points, held to the limit in their plane.
A part pushed together across its layers isn't pulled apart, so only a pull counts there.

**Each joint:** the force and the moment through the faces that meet, from the solve's own balance
at each node there: what the first part's elements carry, less what's put on it from outside
(`element_forces`), each element's nodes taken in FreeCAD's own order and checked
(mqucifer/print-shop#320). Every joint is solved as one piece, the faces tied all over, as a glued
joint is and as a bolted one is while its bolts hold. So a bolted joint is judged by what its bolts
then have to hold: the joint's pull shared between them, and its bending moment taken about the
face's edge on the pushed side, each bolt in proportion to its distance from that edge, against the
least of the bolt's proof load, the insert's pull-out where one is sourced, and the joint's own
figure. An insert pulls out of printed plastic long before a steel bolt gives, so where a bolt has
a pull to hold and neither its insert nor its joint has a figure, the answer is `UNREAD`, never
`OK` on the bolt alone. A bonded joint that carries anything is `UNREAD` too
(mqucifer/print-shop#170): glue is a second hold only, and no glue line has a figure here.

**A through joint** (mqucifer/print-shop#168, `load_through.py`) is a bolt right through both
parts, with a nut and washers, and no insert to give first. Its bolts' pulls are found the same
way, and it's judged by the worst of: the bolt against its proof load; the pressure under each
washer, or under the nut in its pocket, and where the shank leans on its hole's wall, against
what the plastic takes pressed that way to its layers; and the plastic round each hole, solved
again by itself under the bolt's pull and shear and read as strain, as any part is. The reply
names which is nearest its limit. It's never `OK` on the bolt alone. Where the joint declares its
bolt in a drop-in channel (mqucifer/print-shop#302), each washer bears on its wall less the
channel's cut, and the channel's open side carries nothing: a push toward it fails, and the
reply says so either way.

**A dovetail joint** (mqucifer/print-shop#170, `dovetail.py` and `load_dovetail.py`) is a tail
slid into a socket: a leg split for the bed. Its flanks and its seat only push, so what the
tied solve says the joint carries is put back on its faces as pushes alone. It's judged by the
worst of: a load that drives the tail out of its seat, which only its latch holds, never
friction or glue; a load its faces can't balance by pushing, which opens it; the pressure on
each face; the latch's catch and its tab, solved by itself under what it holds; and the tail's
neck and the socket's walls, each solved again by itself under the faces' pushes. Before
anything is solved it's taken apart the way it went together (`way_in.py`, mqucifer/print-shop#184
and mqucifer/print-shop#185): its tail out of its socket against `slides` past nothing but its
latch, and its latch's tab read by `bend` pushed as far as it has to go to get on. A joint that
can't be put together fails whatever the load, and nothing is solved. Its play is read as a
print takes the load (mqucifer/print-shop#188): the faces that face each other across a fit's
play close as the tail shifts and turns, and the latch's catch has its own.

**Lifted** (`--lift PART`, mqucifer/print-shop#170), the assembly is held by that part's
highest level and every other part hangs by its own weight: its solid volume at its material's
density. That's the case that pulls a leg's lower piece the way its tail slides out.

A touching joint is held by nothing: it fails where it's pulled apart, or where its push falls
outside the faces that meet, which tips it open; the friction it needs to stay put is a figure,
held to `--friction` where that's given.

**How far it moves:** each loaded place, along the load, and the most anywhere.

**A whole assembly** (mqucifer/print-shop#295). A latch can be named in its tab's part's own
axes and turns and moves with the part, and a part turned off the assembly's axes is cut round
its dovetail in its own. Where gmsh can't curve every element onto the parts' faces without
turning one inside out, as on a traced glass frame, the mesh is made again with straight sides,
and the reply says why. On that mesh a triangle is on a face where its corners are, as its
mid-side nodes are on its chords, off a curved face, so a part drawn in its print pose and placed
by its turns, as a design run places it, is told from the curved parts it meets
(mqucifer/print-shop#319). Where gmsh refuses the joined parts because its triangles on one face
cross another's, as where a cap sits in its counterbore with play round it and the two
cylinders' chords sag into each other, the pairs of faces that meet nowhere and lie closer than
their mesh's sag are meshed again finer there, and the reply says so; a pair too close for any
mesh is named, with its parts, its faces and how far apart they are (`close_faces.py`,
mqucifer/print-shop#346). Anything `load` can't read is `NO ANSWER`, naming the step, and the
part or the joint, where it stopped. CalculiX runs on one thread, as gmsh does, and each mesh's
and solve's files are deleted from the container's /tmp, which is memory, once read. Where the
solve still meets the container's memory, the reply says so from the container's own count, and
what of the mesh is finer than --mesh asks.

**Second methods.** The whole solve again on a mesh half as coarse again: each figure that's
judged, on both. The held faces' reaction, which CalculiX prints, against the weight put on. And
for a joint with every held face on one side of it, statics: what crosses it is the load beyond
it, whatever the mesh. Two readings of one figure on opposite sides of its limit are `DISAGREE`.

**A part as printed** (mqucifer/print-shop#337, `as_printed.py` and `infill.py`): a part the
assembly file gives a `print` is a solid skin, its walls and shells, round an infill core, each
core fused into the body so the mesh has a face where it meets its skin. A core's stiffness is
its pattern's lines as Orca lays them, held to the least share of that the published tests of
an infill alone reached, and its strain as a share of the solid's those tests took; written
for CalculiX as anisotropic constants and taken for each element in the nodes' balance. The
skin is read against the material's limits and each core against its own.

**What it can't read:** a part's walls and infill where it has no `print` (it's solid, so a
sparse part is less stiff and less strong than this), a part read solid's stiffness the other
way (one modulus every way), creep under a weight left on for months, a bolt's preload or a
joint that opens (it's the first, linear answer, with no contact), shear where an insert bears
on its plastic, a nut's thread, a bolt's length, a dovetail's glue and friction, the parts' own
weight under a `--weight`, buckling, and anything that isn't still: a knock, or someone sitting
down.
"""

from __future__ import annotations

import contextlib
import dataclasses
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import numpy as np

from . import (
    as_printed,
    cae,
    close_faces,
    dovetail,
    infill,
    load_dovetail,
    load_through,
    progress,
    way_in,
)
from .assembly import AXES, DIAMETER, Assembly, Held, Joint, PartAt, read
from .cae import ccx_one_thread, freecad, give_back, gmsh, tidy_mesh, tidy_solve
from .limits import over, said, under
from .report import NoAnswer, Report
from .through import channel_said, channel_saved, lean_off, lean_share, peak_said, peak_saved

#: Standard gravity, in m/s²: a kilogram's weight in newtons.
G = 9.80665
#: The elements across the thinnest part, unless `--mesh` says: second-order tetrahedra.
ACROSS = 2.0
#: The mesh is no finer than this unless `--mesh` says, in mm, and no finer than leaves about
#: `ELEMENTS` of them, which the container's 3 GiB solves in a minute or two.
MESH_LEAST = 0.5
ELEMENTS = 40_000
#: The second solve's mesh, as a multiple of the first's.
COARSER = 1.5
#: A coordinate is on a plane within this, in mm.
ON = 1e-6
#: A place `--at` names is on the surface within this, in mm.
ON_SURFACE = 0.5
#: Two parts pass through each other past this, in mm³.
OVERLAP = 1e-4
#: A joint carries nothing under this share of the load.
NOTHING = 1e-3
#: A push's point is on the faces that meet within this, in mm.
ON_FACE = 0.01
#: What setting a mesh up takes in `load`'s own process, in bytes (`_Model._set_up_fits`): each
#: node's place, 374 to 430 measured on three meshes in the image, at its peak 421, and in two
#: passes the rest of it, each element's faces and its neighbours, 735 at its peak on a mesh of
#: 646,197 elements (a design run's assembly on mqucifer/print-shop#376). Each held about a
#: tenth under the least; and, where a lower bound can follow, a half above the most (the
#: operator's ruling), as a step let through that doesn't fit costs the container.
SET_UP_NODE = 335
SET_UP_FACES = 660
SET_UP_NODE_HIGH = 650
SET_UP_FACES_HIGH = 1110
#: What FreeCAD's writer of CalculiX's input takes in `load`'s own process at its peak, and its
#: reader of CalculiX's results, in bytes a node, or an element, as if either alone: on two
#: meshes that solved in the image, of 356,359 and 531,542 elements, the writer 3,100 to 3,130 a
#: node, or 4,360 an element, and the reader 3,390 to 3,520, or 4,720 to 4,940 (a design run's
#: assembly on mqucifer/print-shop#376 was killed writing its input, its set-up checked). Both
#: meshes had the same nodes to an element, so each need is taken both ways: low, a tenth under
#: the less, and where a lower bound can follow, a half above the more (`_Model._solve_need`).
WRITE_NODE, WRITE_ELEMENT = 3100, 4360
WRITE_NODE_HIGH, WRITE_ELEMENT_HIGH = 3130, 4360
READ_NODE, READ_ELEMENT = 3390, 4720
READ_NODE_HIGH, READ_ELEMENT_HIGH = 3520, 4940
#: How a mesh whose elements' sides are straight is said where a step names it.
STRAIGHT = ", its elements straight-sided"
#: A face of a part read without its glass band is where the band met it where a point inside it
#: is this close to the band's solids, in mm: on a face the band was cut away by, its triangles'
#: middles are on the band's own face, or within its chord of it, `ON_BAND_CHORD`, where it's
#: curved (mqucifer/print-shop#375).
ON_BAND = 0.02
ON_BAND_CHORD = 0.005
#: CalculiX prints each node's movement to seven figures (`*NODE PRINT`, E13.6), where its .frd
#: file, which FreeCAD reads, has six (mqucifer/print-shop#320).
PRINTED = 7
#: It's given each load to nine figures (`_write`) and solves it directly to far closer than
#: seven: what's left at a node, past what the seven figures for each movement account for
#: (`figures`), as a share of the whole load.
LOADS = 1e-8
#: The elements whose stiffness is taken at once, for the balance (`_Model._imbalance`).
GROUP = 2048
#: The held faces' reaction is the weight within this share, or nothing is said: CalculiX's own
#: sum of the forces it solved for.
REACTION = 0.01


def _xyz(point: Any) -> str:
    return (
        "(" + ", ".join(f"{c + 0.0:g}" for c in np.round(np.asarray(point, dtype=float), 2)) + ")"
    )


def _list(values: Any, places: int = 4) -> list[float]:
    return [round(float(v), places) + 0.0 for v in values]


def bolt_pulls(
    force: np.ndarray,
    moment: np.ndarray,
    centre: np.ndarray,
    normal: np.ndarray,
    bolts: np.ndarray,
    outline: np.ndarray,
) -> list[dict[str, float]]:
    """What each bolt of a flat joint has to hold, in N. `force` and `moment` (about `centre`)
    are what the joint carries, a pull where the force is along `normal`. The pull is shared
    between the bolts. The bending moment, less what a push takes off it, is taken about the
    face's edge on the pushed side, the furthest of `outline` that way, each bolt in proportion
    to its distance from that edge. The shear is shared too."""
    count = len(bolts)
    along = float(force @ normal)
    shear = float(np.linalg.norm(force - along * normal)) / count
    pulls = np.full(count, max(along, 0.0) / count)
    bending = moment - (moment @ normal) * normal
    size = float(np.linalg.norm(bending))
    if size > 1e-9:
        # The side that's pulled is this way across the face from its centre.
        pulled = np.cross(normal, bending / size)
        edge = float(((outline - centre) @ pulled).min())
        levers = (bolts - centre) @ pulled - edge
        about_edge = size - max(-along, 0.0) * -edge
        if about_edge > 0 and float((levers**2).sum()) > 1e-12:
            pulls = pulls + about_edge * np.maximum(levers, 0.0) / float((levers**2).sum())
    return [{"pull_n": float(p), "shear_n": shear} for p in pulls]


#: A second-order tetrahedron's mid-side nodes, by the corners each lies between.
MIDS = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
#: Its four integration points, as CalculiX has them: three coordinates each, the first corner's
#: being what's left of 1.
_A, _B = 0.5854101966249685, 0.1381966011250105
POINTS = ((_B, _B, _B), (_A, _B, _B), (_B, _A, _B), (_B, _B, _A))


def element_forces(
    places: np.ndarray,
    moved: np.ndarray,
    modulus: Any,
    poisson: Any,
    tensor: np.ndarray | None = None,
) -> np.ndarray:
    """The force each second-order tetrahedron's stress puts on each of its ten nodes, in N:
    `places` and `moved` are each element's nodes, four corners and then `MIDS`, where they are
    and how far each moved, in mm. It's the element's own stiffness times its movement, the sum
    CalculiX balances at every node, so the forces on a part's nodes add up to what's put on
    it exactly, where the stress at a sharp inside corner, read off the surface, doesn't.
    `modulus` and `poisson` are the material's; where the elements differ, as a printed part's
    skin and core do (mqucifer/print-shop#337), `tensor` is each element's stiffness, C_ijkl."""
    lame, shear = _lame(modulus, poisson, len(places))
    corners = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    forces = np.zeros_like(places, dtype=float)
    for point in POINTS:
        share = np.array([1 - sum(point), *point])
        slopes = np.zeros((10, 3))
        for i in range(4):
            slopes[i] = (4 * share[i] - 1) * corners[i]
        for k, (i, j) in enumerate(MIDS, 4):
            slopes[k] = 4 * (share[i] * corners[j] + share[j] * corners[i])
        # How the element's own coordinates map to space, and each node's slope in space.
        mapped = np.einsum("ak,nai->nki", slopes, places)
        in_space = np.einsum("nik,ak->nai", np.linalg.inv(mapped), slopes)
        grown = np.einsum("nai,naj->nij", moved, in_space)
        strain = (grown + grown.transpose(0, 2, 1)) / 2
        if tensor is not None:
            stress = np.einsum("nijkl,nkl->nij", tensor, strain)
        else:
            stress = 2 * shear[:, None, None] * strain
            stress[:, range(3), range(3)] += (lame * np.trace(strain, axis1=1, axis2=2))[:, None]
        weight = np.abs(np.linalg.det(mapped)) / 24
        forces += weight[:, None, None] * np.einsum("nij,naj->nai", stress, in_space)
    return forces


#: A ten-node tetrahedron's own nodes, in its own coordinates: its corners, then `MIDS`.
_CORNERS = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
NODES = _CORNERS + tuple(
    tuple((a + b) / 2 for a, b in zip(_CORNERS[i], _CORNERS[j], strict=True)) for i, j in MIDS
)


def element_strains(
    places: np.ndarray, moved: np.ndarray, points: tuple[tuple[float, ...], ...] = POINTS
) -> tuple[np.ndarray, np.ndarray]:
    """Each second-order tetrahedron's own strain at `points` in its own coordinates, its four
    integration points unless given, from its ten nodes' movement as `element_forces` takes
    it, and where each point is: `[element, point, 3, 3]` and `[element, point, 3]`
    (mqucifer/print-shop#337). A printed part's core is read by its own elements' strain, at
    their integration points and their own nodes (`NODES`): CalculiX's strain at a node is the
    mean of its elements', and where a core meets its skin, 10 to 30 times as stiff, that's
    the core's diluted. An element's own at the node is never less than that mean's share."""
    corners = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    strain = np.zeros((len(places), len(points), 3, 3))
    at = np.zeros((len(places), len(points), 3))
    for k, point in enumerate(points):
        share = np.array([1 - sum(point), *point])
        slopes = np.zeros((10, 3))
        values = np.zeros(10)
        for i in range(4):
            slopes[i] = (4 * share[i] - 1) * corners[i]
            values[i] = share[i] * (2 * share[i] - 1)
        for m, (i, j) in enumerate(MIDS, 4):
            slopes[m] = 4 * (share[i] * corners[j] + share[j] * corners[i])
            values[m] = 4 * share[i] * share[j]
        mapped = np.einsum("ak,nai->nki", slopes, places)
        in_space = np.einsum("nik,ak->nai", np.linalg.inv(mapped), slopes)
        grown = np.einsum("nai,naj->nij", moved, in_space)
        strain[:, k] = (grown + grown.transpose(0, 2, 1)) / 2
        at[:, k] = np.einsum("a,nai->ni", values, places)
    return strain, at


def _lame(modulus: Any, poisson: Any, count: int) -> tuple[np.ndarray, np.ndarray]:
    """Lamé's first constant and the shear modulus, one for each of `count` elements."""
    modulus = np.broadcast_to(np.asarray(modulus, dtype=float), (count,))
    poisson = np.broadcast_to(np.asarray(poisson, dtype=float), (count,))
    lame = modulus * poisson / ((1 + poisson) * (1 - 2 * poisson))
    return lame, modulus / (2 * (1 + poisson))


def element_stiffness(
    places: np.ndarray, modulus: Any, poisson: Any, tensor: np.ndarray | None = None
) -> np.ndarray:
    """Each second-order tetrahedron's stiffness matrix, in N/mm, as `[element, node, axis,
    node, axis]`: the force on each of its ten nodes along each axis for a millimetre's
    movement of each along each, which `element_forces` is the sum of. At each integration
    point, for node a along i and node b along k, with each node's slope in space g, it's
    μ g_a·g_b where i is k, and μ g_ak g_bi + λ g_ai g_bk, weighted as the forces are."""
    lame, shear = _lame(modulus, poisson, len(places))
    lame, shear = lame[:, None, None, None, None], shear[:, None, None, None, None]
    corners = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    stiffness = np.zeros((len(places), 10, 3, 10, 3))
    for point in POINTS:
        share = np.array([1 - sum(point), *point])
        slopes = np.zeros((10, 3))
        for i in range(4):
            slopes[i] = (4 * share[i] - 1) * corners[i]
        for k, (i, j) in enumerate(MIDS, 4):
            slopes[k] = 4 * (share[i] * corners[j] + share[j] * corners[i])
        mapped = np.einsum("ak,nai->nki", slopes, places)
        g = np.einsum("nik,ak->nai", np.linalg.inv(mapped), slopes)
        weight = (np.abs(np.linalg.det(mapped)) / 24)[:, None, None, None, None]
        if tensor is not None:
            stiffness += weight * np.einsum("naj,nijkl,nbl->naibk", g, tensor, g)
            continue
        both = np.einsum("nac,nbc->nab", g, g)[:, :, None, :, None] * np.eye(3)[None, None, :, None]
        stiffness += weight * (
            shear * both
            + shear * np.einsum("nak,nbi->naibk", g, g)
            + lame * np.einsum("nai,nbk->naibk", g, g)
        )
    return stiffness


def figures(moved: np.ndarray, given: int = PRINTED) -> np.ndarray:
    """How far each movement read back can be from what CalculiX solved, in mm: half a unit in
    its last figure, written to `given` of them. Nothing where it's naught."""
    size = np.abs(moved)
    place = np.floor(np.log10(np.where(size > 0, size, 1.0)))
    return np.where(size > 0, 0.5 * 10.0 ** (place - (given - 1)), 0.0)


def misordered(wired: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Which elements' nodes aren't in `MIDS`' order (mqucifer/print-shop#320), as a flag for
    each row of `wired`, each element's ten nodes as rows of `points`. Each element's nodes are
    in order where its ten are distinct; each of its mid-side nodes is its side's, as every
    element with that side has it, and of no other side or corner; and each lies on its own
    side's curve: in the middle half of the side along its chord, as a second-order side has
    to be not to fold back on itself, and no further off the chord than half its length, as a
    side bent round no more than a half circle is. That last says only that each node is near
    a side: a neighbouring side's midpoint lies within those bounds too, so it doesn't tell two
    sides of one element apart. Where another element has the side, its shared node does; and
    for any element that carries load, the balance does (`_Model._balanced`)."""
    if wired.ndim != 2 or wired.shape[1] != 4 + len(MIDS):
        return np.ones(len(wired), dtype=bool)
    ends = np.array(MIDS)
    corners, mids = wired[:, :4], wired[:, 4:]
    ordered = np.sort(wired, axis=1)
    wrong = (ordered[:, 1:] == ordered[:, :-1]).any(axis=1)
    # Each side by its two corners, and the one mid-side node every element gives it.
    first, second = corners[:, ends[:, 0]], corners[:, ends[:, 1]]
    sides = np.stack([np.minimum(first, second), np.maximum(first, second)], axis=2)
    _, side = np.unique(sides.reshape(-1, 2), axis=0, return_inverse=True)
    side = side.reshape(mids.shape)
    pairs = np.unique(np.stack([side.ravel(), mids.ravel()], axis=1), axis=0)
    shared_side = np.bincount(pairs[:, 0], minlength=side.max() + 1) > 1
    nodes, count = np.unique(pairs[:, 1], return_counts=True)
    two_sides = nodes[count > 1]
    wrong |= shared_side[side].any(axis=1)
    wrong |= np.isin(mids, two_sides).any(axis=1)
    wrong |= np.isin(mids, corners).any(axis=1)
    # Each on its own side's curve.
    start, end = points[first], points[second]
    chord = end - start
    length = np.linalg.norm(chord, axis=2)
    off = points[mids] - (start + end) / 2
    along = np.einsum("nsk,nsk->ns", off, chord) / np.where(length > 0, length, 1.0)
    aside = np.sqrt(np.maximum(np.einsum("nsk,nsk->ns", off, off) - along**2, 0.0))
    wrong |= ~((np.abs(along) < length / 4) & (aside <= length / 2)).all(axis=1)
    return np.asarray(wrong, dtype=bool)


def beyond(
    assembly: Assembly,
    joint: Joint,
    loads: list[dict[str, Any]],
    held: set[str],
    centre: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """What crosses a joint by statics, as the force and moment the second part puts on the
    first: the load on the side of it that nothing holds. None where both sides are held, or
    the parts are joined round it another way."""
    first, second = joint.between
    reached = {first}
    grown = True
    while grown:
        grown = False
        for other in assembly.joints:
            if other is joint:
                continue
            a, b = other.between
            for near, far in ((a, b), (b, a)):
                if near in reached and far not in reached:
                    reached.add(far)
                    grown = True
    if second in reached:
        return None
    near_held, far_held = bool(held & reached), bool(held - reached)
    if near_held == far_held:
        return None
    # The side nothing holds is in balance under its loads and the joint alone.
    free_is_far = near_held
    force, moment = np.zeros(3), np.zeros(3)
    for load in loads:
        if (load["part"] in reached) != free_is_far:
            push = np.asarray(load["force"])
            force += push
            moment += np.cross(np.asarray(load["middle"]) - centre, push)
    return (force, moment) if free_is_far else (-force, -moment)


@dataclasses.dataclass
class Band:
    """What a part's glass band took from it, where the part is read without it
    (mqucifer/print-shop#375): the band's solids where the assembly puts them, the volume of the
    part it took, in mm³, and the whole part's volume and middle, where the assembly puts it. A
    lift keeps the band's weight, put on the faces where it met the rest of the part."""

    solids: list[Any]
    volume: float
    whole_volume: float
    whole_middle: np.ndarray


def _placed(assembly: Assembly, without_bands: bool = False) -> list[Any]:
    """Each part's one solid, where the assembly puts it: with `without_bands`, less its glass
    band where it names one (mqucifer/print-shop#295), and what the band took kept in `_bands`
    (mqucifer/print-shop#375)."""
    folder = Path(assembly.path).parent
    found = []
    _bands.clear()
    for part in assembly.parts:
        with step(f"placing the part {part.name} by its turn and its at"):
            found.append(_one_placed(part, folder, without_bands, _bands))
    for i, first in enumerate(found):
        for j in range(i + 1, len(found)):
            if not first.BoundBox.intersect(found[j].BoundBox):
                continue
            shared = first.common(found[j])
            if shared.Volume > OVERLAP:
                box = shared.BoundBox
                names = f"{assembly.parts[i].name} and {assembly.parts[j].name}"
                raise NoAnswer(
                    f"{names} pass through each other where the assembly puts them, by "
                    f"{shared.Volume:.3f} mm³, from {_xyz((box.XMin, box.YMin, box.ZMin))} to "
                    f"{_xyz((box.XMax, box.YMax, box.ZMax))}: parts meet face to face. Move one "
                    "or cut it back, and `clearance` reads the pair"
                )
    return found


def _one_placed(
    part: PartAt, folder: Path, without_bands: bool, bands: dict[str, Band] | None = None
) -> Any:
    """One part's one solid, turned by its `turn` and moved by its `at`; and, given `bands`,
    what its glass band took from it there, where it's left out."""
    import Part

    solids = [s for s in Part.read(str(folder / part.file)).Solids if s.Volume > 1e-6]
    if len(solids) != 1:
        raise NoAnswer(
            f"the part {part.name}'s file, {part.file}, holds {len(solids)} solids: a part is "
            "one solid, and each other piece is a part of its own"
        )
    solid = solids[0].copy()
    if without_bands and part.band is not None:
        band = [s for s in Part.read(str(folder / part.band)).Solids if s.Volume > 1e-6]
        left = [s for s in solid.cut(Part.makeCompound(band)).Solids if s.Volume > 1e-6]
        if len(left) != 1:
            raise NoAnswer(
                f"the part {part.name} with its band, {part.band}, left out is "
                f"{len(left)} solids: a part read without its band is one solid still"
            )
        if bands is not None:
            whole = Part.Vertex(solid.CenterOfMass)
            middle = _moved(whole, part).Point
            bands[part.name] = Band(
                solids=[_moved(s, part) for s in band],
                volume=float(solid.Volume - left[0].Volume),
                whole_volume=float(solid.Volume),
                whole_middle=np.array([middle.x, middle.y, middle.z]),
            )
        solid = left[0]
    return _moved(solid, part)


def on_band(face: Any, band: list[Any]) -> bool:
    """Whether a face of a part read without its glass band lies on the band's surface, where
    the band met the rest of the part (mqucifer/print-shop#375): the middle of the largest of
    its own triangles is within `ON_BAND` of one of the band's solids. A face that only meets
    the band along an edge is off it there."""
    import FreeCAD as App
    import Part

    box = App.BoundBox(face.BoundBox)
    box.enlarge(ON_BAND)
    near = [s for s in band if s.BoundBox.intersect(box)]
    if not near:
        return False
    points, triangles = face.tessellate(ON_BAND_CHORD)
    if not triangles:
        return False
    corners = np.array([[[p.x, p.y, p.z] for p in (points[i] for i in t)] for t in triangles])
    areas = np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    )
    middle = Part.Vertex(App.Vector(*corners[int(areas.argmax())].mean(axis=0)))
    return any(s.distToShape(middle)[0] <= ON_BAND for s in near)


def _moved(solid: Any, part: PartAt) -> Any:
    """A shape in a part's own axes, turned by its `turn` and moved by its `at`, in place."""
    import FreeCAD as App

    for axis, degrees in part.turn:
        way = [0.0, 0.0, 0.0]
        way[AXES.index(axis)] = 1.0
        solid.rotate(App.Vector(0, 0, 0), App.Vector(*way), degrees)
    solid.translate(App.Vector(*part.at))
    return solid


def _frame(part: PartAt) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A part's layers' way and the two ways of their plane its `infill_angle` is measured
    between, in the assembly's axes: with its layers along its own axis a, from its own axis
    a+1 toward a+2 (`printed.own_way`)."""
    matrix = part.matrix()
    layers = AXES.index(part.layers)
    return part.layers_way(), matrix[:, (layers + 1) % 3], matrix[:, (layers + 2) % 3]


#: CalculiX's order of an anisotropic material's 21 constants, D_ijkl (`*ELASTIC, TYPE=ANISO`).
ANISO = ("1111", "1122", "2222", "1133", "2233", "3333", "1112", "2212", "3312", "1212", "1113",
         "2213", "3313", "1213", "1313", "1123", "2223", "3323", "1223", "1323", "2323")


def _aniso(tensor: np.ndarray) -> str:
    """A stiffness, C_ijkl in MPa, as CalculiX's anisotropic constants, eight to a line."""
    values = [float(tensor[tuple(int(c) - 1 for c in key)]) for key in ANISO]
    lines = [", ".join(f"{v:.9g}" for v in values[i : i + 8]) for i in range(0, 21, 8)]
    return "*ELASTIC, TYPE=ANISO\n" + "\n".join(lines) + "\n"


def _cores(assembly: Assembly, without_bands: bool = False) -> dict[str, list[Any]]:
    """Each printed part's cells (`as_printed.cells`, mqucifer/print-shop#337), made in its own
    axes and placed with it: by part, its cells, each with its core's solids where the assembly
    puts them. Where its walls and shells fill a cell, the cell has no core, and is solid."""
    folder = Path(assembly.path).parent
    found: dict[str, list[Any]] = {}
    for part in assembly.parts:
        if part.printed is None:
            continue
        with step(f"making the part {part.name}'s skin and core from its print settings"):
            own = _one_placed(dataclasses.replace(part, turn=(), at=(0.0, 0.0, 0.0)), folder,
                              without_bands)
            cells = as_printed.cells(own, part.printed, AXES.index(part.layers), folder,
                                     part.name)
            found[part.name] = [
                (cell, [_moved(s.copy(), part) for s in cell.core]) for cell in cells
            ]
    return found


def _span(box: Any, axis: int) -> tuple[float, float]:
    return ((box.XMin, box.XMax), (box.YMin, box.YMax), (box.ZMin, box.ZMax))[axis]


def _flat(shape: Any, axis: int, at: float) -> bool:
    low, high = _span(shape.BoundBox, axis)
    return abs(low - at) < ON and abs(high - at) < ON


class _Model:
    """The joined parts in one FreeCAD document, meshed and solved once."""

    def __init__(
        self,
        assembly: Assembly,
        placed: list[Any],
        args: Any,
        size: float,
        straight: bool = False,
        two_pass: bool = False,
        spread: bool = True,
        cores: dict[str, list[Any]] | None = None,
    ) -> None:
        self.assembly, self.placed, self.args, self.size = assembly, placed, args, size
        #: Each printed part's cells and their cores, placed (`_cores`, mqucifer/print-shop#337):
        #: none for a part read solid.
        self.cores = cores or {}
        #: Each core's solid in the joined shape, by its index there, with its part's name, its
        #: cell and the cell's figures (`infill.core`): filled by `join`.
        self.core_solids: list[tuple[int, str, Any, Any]] = []
        #: Each core's stiffness, C_ijkl in MPa, in the assembly's axes.
        self.core_tensors: list[np.ndarray] = []
        #: Which core each element is in, and each element's stiffness, in FreeCAD's order,
        #: where the parts aren't all one material: filled by `solve`.
        self.element_core: np.ndarray | None = None
        #: A printed part's skin's nodes, and each of its cores', as rows (mqucifer/print-shop#337):
        #: none for a part read solid, and in a pass that reads none as printed.
        self.skin_rows: dict[str, np.ndarray] = {}
        self.core_rows: dict[int, np.ndarray] = {}
        #: Each material's stiffness, C_ijkl, and which each element is, by its row there.
        self.element_tensor: np.ndarray | None = None
        self.element_kind: np.ndarray | None = None
        #: Whether its second-order elements are straight-sided (mqucifer/print-shop#295).
        self.straight = straight
        self._surface_nodes: list[tuple[int, ...]] | None = None
        #: Each element's nodes as rows, once read and checked (`_wired`).
        self._wiring: np.ndarray | None = None
        #: Each node's movement as CalculiX printed it to seven figures, by row, in one pass
        #: (`_printed`), for the elements' balance (mqucifer/print-shop#320).
        self.printed: np.ndarray | None = None
        #: Whether, where CalculiX's direct solver meets the container's memory, it's solved
        #: again by its iterative one and corrected (`_iterated`): in two passes only
        #: (mqucifer/print-shop#301). None, or what the direct solver met, once it has.
        self.two_pass = two_pass
        #: Whether gmsh spreads a small element's size from the faces' edges across them, as
        #: it does unless told: the first of two passes isn't, so its bores' squares are
        #: meshed at --mesh and no finer round them (mqucifer/print-shop#301).
        self.spread = spread
        self.iterated: str | None = None
        #: The pairs of faces closer than the mesh's sag, meshed finer there
        #: (`close_faces`, mqucifer/print-shop#346): filled by `solve`. And how many faces curve
        #: tighter than gmsh's sizing would mesh at `close_faces.LEAST`, the least element then.
        self.closes: list[close_faces.Close] = []
        self.tight = 0
        #: What each close pair's face is meshed at, as a share of the size that meshes it: the
        #: finer of a solve's two meshes `1 / COARSER` of it, and the coarser one, given the
        #: finer's pairs, all of it (`_solved`'s `coarser_of`), so that on those faces too the
        #: coarser mesh is coarser and its second check reads them.
        self.local_scale = 1 / COARSER
        #: Each node's axes held, as CalculiX is told them, by `_add_to`.
        self.constrained: dict[int, set[int]] = {}
        #: The solids each part's own weight is taken from, where they aren't `placed`: in
        #: the first of two passes, the parts as they are, not with their bores squared.
        self.weighed: list[Any] = placed
        self.toward = np.zeros(3)
        self.toward[AXES.index(args.toward[1])] = 1.0 if args.toward[0] == "+" else -1.0

    # The body

    def join(self, doc: Any) -> None:
        """The parts as one body with the faces that meet shared, and each joint's faces."""
        placed = self.placed
        whole = fused(placed)
        self.feature = doc.addObject("Part::Feature", "Parts")
        self.feature.Shape = whole
        self.shape = shape = self.feature.Shape
        #: The joined shape itself, as the fuse made it, its faces in `shape`'s order: what
        #: FreeCAD caches of its faces can be let go, where the document's copy can't be changed.
        self.whole = whole
        if len(shape.Solids) != len(placed):
            raise NoAnswer(
                f"joined, the {len(placed)} parts are {len(shape.Solids)} solids: two of them "
                "pass through each other, or one is in pieces"
            )
        self.solids = []
        for part, solid in zip(self.assembly.parts, placed, strict=True):
            centre = solid.CenterOfMass
            near = min(shape.Solids, key=lambda s: s.CenterOfMass.distanceToPoint(centre))
            if abs(near.Volume - solid.Volume) > 1e-3 * solid.Volume + 1e-3:
                raise NoAnswer(f"joined, the part {part.name} isn't the solid it was")
            self.solids.append(near)
        if self.cores:
            self._cored()
            shape = self.shape
        names = [p.name for p in self.assembly.parts]
        self.met: dict[frozenset[str], list[Any]] = {}
        faces = [solid.Faces for solid in self.solids]
        indexed = [Faces(each) for each in faces]
        for i, first in enumerate(faces):
            for j in range(i + 1, len(self.solids)):
                theirs = indexed[j]
                shared = [f for f in first if theirs.has(f)]
                if shared:
                    self.met[frozenset((names[i], names[j]))] = shared
        named = {frozenset(j.between): j for j in self.assembly.joints}
        for pair, faces in self.met.items():
            if pair not in named:
                a, b = sorted(pair)
                centre = faces[0].CenterOfMass
                raise NoAnswer(
                    f"{a} and {b} meet over {sum(f.Area for f in faces):.1f} mm², round "
                    f"{_xyz((centre.x, centre.y, centre.z))}, and the assembly names no joint "
                    "between them: say whether it's bonded, bolted or touching"
                )
        for pair, joint in named.items():
            if pair not in self.met:
                a, b = (placed[names.index(n)] for n in joint.between)
                raise NoAnswer(
                    f"the joint {joint.name} is between parts that don't meet: they're "
                    f"{a.distToShape(b)[0]:.3f} mm apart where the assembly puts them. Parts "
                    "meet face to face"
                )
        self.joint_faces = {j.name: self.met[frozenset(j.between)] for j in self.assembly.joints}
        for joint in self.assembly.joints:
            if joint.kind == "dovetail":
                tail = self.solids[names.index(joint.between[0])]
                load_dovetail.check(joint, self.joint_faces[joint.name], tail)
        self.between = [f for faces in self.met.values() for f in faces]

    def _cored(self) -> None:
        """Each printed part as its skin round its cores, one body still (mqucifer/print-shop#337):
        the cores are fused into the joined parts, so the mesh has a face where each skin meets
        its core, and every element is the skin's or a core's, never half of each. Each part's
        solid is then its skin, which has every face the part has: the faces it meets other
        parts by, is held by and is loaded on."""
        given = [(name, k, cell, solid)
                 for name, cells in self.cores.items()
                 for k, (cell, solids) in enumerate(cells)
                 for solid in solids]  # fmt: skip
        if not given:
            return
        whole = self.shape.generalFuse([solid for *_, solid in given])[0]
        self.feature.Shape = whole
        self.shape = self.feature.Shape
        self.whole = whole
        solids = self.shape.Solids
        if len(solids) != len(whole.Solids):
            raise NoAnswer("the parts with their cores fused in couldn't be read back")

        def within(piece: Any, of: Any) -> bool:
            """Whether most of `piece` is in `of`: the fuse's pieces don't overlap."""
            return bool(of.BoundBox.intersect(piece.BoundBox)
                        and of.common(piece).Volume > 0.5 * piece.Volume)

        cores: dict[int, tuple[str, Any]] = {}
        for index, piece in enumerate(solids):
            found = next(((name, cell) for name, _, cell, solid in given if within(piece, solid)),
                         None)
            if found is not None:
                cores[index] = found
        names = [p.name for p in self.assembly.parts]
        skins: dict[str, list[int]] = {}
        for index, solid in enumerate(solids):
            if index in cores:
                continue
            owner = next(
                (n for n, part in zip(names, self.solids, strict=True) if within(solid, part)),
                None,
            )
            if owner is None:
                raise NoAnswer("a piece of the parts with their cores fused in is in none of them")
            skins.setdefault(owner, []).append(index)
        for k, name in enumerate(names):
            mine = skins.get(name, [])
            if len(mine) != 1:
                raise NoAnswer(
                    f"the part {name} with its cores fused in is {len(mine)} skins: a printed "
                    "part's walls and shells are one piece round its core"
                )
            self.solids[k] = solids[mine[0]]
        self.core_solids = [
            (index, name, cell, infill.core(cell.settings, self.assembly.part(name).material))
            for index, (name, cell) in sorted(cores.items())
        ]
        self.core_tensors = []
        for _, name, _, figures in self.core_solids:
            part = self.assembly.part(name)
            self.core_tensors.append(figures.stiffness(*_frame(part)))

    def _pieces(self, k: int, solid: Any) -> list[Any]:
        """The part `k`'s solids in the joined shape: its own, and a printed part's cores."""
        if not self.core_solids:
            return [solid]
        name = self.assembly.parts[k].name
        cores = [self.shape.Solids[i] for i, owner, *_ in self.core_solids if owner == name]
        return [solid, *cores]

    def _face_name(self, face: Any) -> str:
        # Numbered from the joined shape as it is now: a printed part's cores are fused in after
        # the parts are joined (`_cored`).
        if getattr(self, "_numbered", None) is not self.shape:
            self._numbered = self.shape
            self._numbers: dict[int, list[tuple[int, Any]]] = {}
            for number, other in enumerate(self.shape.Faces, 1):
                self._numbers.setdefault(other.hashCode(), []).append((number, other))
        # The first of the joined parts' faces it's the same as, as by asking each in turn.
        for number, other in self._numbers.get(face.hashCode(), ()):
            if face.isSame(other):
                return f"Face{number}"
        raise NoAnswer("a face of the joined parts couldn't be found again")

    def hold(self) -> None:
        """The faces each held plane names: fixed, or standing on it."""
        held = list(self.assembly.held)
        names = [p.name for p in self.assembly.parts]
        if self.args.lift is not None:
            # Lifted by one part: held by that part's highest level, and by nothing else.
            axis = int(np.abs(self.toward).argmax())
            span = _span(self.solids[names.index(self.args.lift)].BoundBox, axis)
            top = span[0 if self.toward[axis] > 0 else 1]
            held = [Held(AXES[axis], top, "fixed", self.args.lift)]
        elif not held:
            low = self.shape.BoundBox.ZMin
            held = [Held("z", low, "standing")]
        #: The planes it's held by, as read: two passes hold each part by them alone.
        self.planes = held
        self.fixed: list[Any] = []
        self.standing: dict[int, list[Any]] = {}
        self.held_parts: set[str] = set()
        self.held_said = []
        between = Faces(self.between)
        for one in held:
            axis = AXES.index(one.axis)
            faces = []
            for name, solid in zip(names, self.solids, strict=True):
                if one.part is not None and name != one.part:
                    continue
                for face in solid.Faces:
                    if _flat(face, axis, one.at) and not between.has(face):
                        faces.append(face)
                        self.held_parts.add(name)
            if not faces and self.args.lift is not None:
                raise NoAnswer(
                    f"--lift {one.part}: {one.part} has no flat face at its highest level, "
                    f"{one.axis}={one.at:g}, to be lifted by"
                )
            if not faces:
                box = self.shape.BoundBox
                raise NoAnswer(
                    f"no flat face of {one.part or 'the assembly'} lies in the held plane "
                    f"{one.axis}={one.at:g}: it runs from {_span(box, axis)[0]:g} to "
                    f"{_span(box, axis)[1]:g} along {one.axis}"
                )
            if one.how == "fixed":
                self.fixed += faces
            else:
                self.standing.setdefault(axis, []).extend(faces)
            self.held_said.append(
                f"{one.how} {'to' if one.how == 'fixed' else 'on'} {one.axis}={one.at:g}, "
                f"{len(faces)} face(s), {sum(f.Area for f in faces):.1f} mm²"
            )
        if not self.fixed:
            across = [a for a in range(3) if a not in self.standing and abs(self.toward[a]) > ON]
            if across:
                raise NoAnswer(
                    f"the load is --toward {self.args.toward}, along what the assembly stands on: "
                    "standing holds it square to its plane only, and it would slide. Hold a "
                    "plane `fixed` in the assembly's `held`"
                )

    # The solve

    def solve(self, doc: Any) -> None:
        import ObjectsFem
        from femmesh.gmshtools import GmshTools
        from femtools import ccxtools

        analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
        solver.AnalysisType = "static"
        solver.GeometricalNonlinearity = "linear"
        analysis.addObject(solver)
        # One material for the body: every part's so far. The assembly file is refused before
        # this if two parts' differ in stiffness.
        material = self.assembly.parts[0].material
        self.modulus = material.modulus_mpa
        solid = ObjectsFem.makeMaterialSolid(doc, "Material")
        values = solid.Material
        values.update(
            Name="Printed",
            YoungsModulus=f"{material.modulus_mpa:g} MPa",
            PoissonRatio=f"{material.poisson:g}",
            Density="1270 kg/m^3",
        )
        solid.Material = values
        analysis.addObject(solid)
        if self.fixed:
            fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
            fixed.References = [(self.feature, tuple(self._face_name(f) for f in self.fixed))]
            analysis.addObject(fixed)
        for axis, faces in self.standing.items():
            stand = ObjectsFem.makeConstraintDisplacement(doc, f"Standing{AXES[axis]}")
            stand.References = [(self.feature, tuple(self._face_name(f) for f in faces))]
            for other, name in enumerate(AXES):
                setattr(stand, f"{name}Free", other != axis)
            setattr(stand, f"{AXES[axis]}Displacement", 0.0)
            analysis.addObject(stand)
        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = self.feature
        mesh.CharacteristicLengthMax = self.size
        mesh.ElementOrder = "2nd"
        if self.straight:
            # Each element's mid-side nodes halfway along its straight sides, so none is
            # turned inside out: a curved face is followed by its elements' chords
            # (mqucifer/print-shop#295).
            mesh.SecondOrderLinear = True
            mesh.HighOrderOptimize = "None"
        else:
            # A second-order element's curved side, on a bolt's hole far smaller than the
            # mesh, can turn it inside out, and CalculiX then solves nothing: gmsh straightens
            # those (mqucifer/print-shop#168). A part with flat sides only is meshed as it was.
            mesh.HighOrderOptimize = "Optimization"
        analysis.addObject(mesh)
        doc.recompute()
        began = time.time()
        # Where the curved try found faces close, the straight one is meshed finer there from
        # the start (mqucifer/print-shop#346).
        kills = oom_kills()
        tools = self._gmsh(
            GmshTools, mesh, close_faces.fields(self.closes, self.size, self.local_scale)
        )
        self._mesh_met(mesh, kills, tools)
        said = None if mesh.FemMesh.VolumeCount else _gmsh_said(tools)
        for _ in range(RETRIES):
            if said is None or not CROSSED.search(said):
                break
            # gmsh's triangles on one face cross another's (mqucifer/print-shop#346): where two
            # faces that meet nowhere lie closer than their sag, it's meshed again finer there.
            try:
                with step("finding faces closer than the mesh's sag, where gmsh refused them"):
                    sizes, more = self._close(int(mesh.MeshSizeFromCurvature), said)
            except NoAnswer as stopped:
                if stopped.__cause__ is None:
                    raise  # its own refusal, which says gmsh's words
                # The search itself failed: what gmsh said is said with it.
                raise NoAnswer(f"{stopped}; gmsh had refused the mesh: {said[:300]}") from stopped
            if not more:
                break
            kills = oom_kills()
            tools = self._gmsh(GmshTools, mesh, sizes)
            self._mesh_met(mesh, kills, tools)
            said = None if mesh.FemMesh.VolumeCount else _gmsh_said(tools)
        words = said
        if said is not None:
            if self.closes:
                words = f"{words}; meshed again {self._finer_words()}, and refused again"
            if CROSSED.search(said):
                words = f"{words}{self._near_said(said)}"
        self.mesh_s = round(time.time() - began, 2)
        self.fem = fem = mesh.FemMesh
        if words is not None:
            # FreeCAD keeps no mesh where gmsh ends with an error, and says nothing of it: its
            # own words are in what it printed.
            if not self.straight:
                raise _Curved(
                    f"gmsh made no curved mesh of the joined parts: {words[:900]}",
                    self.closes,
                    self.tight,
                )
            raise _Unread(
                "gmsh made no mesh of the joined parts, with its elements' sides curved onto "
                f"the parts' faces or straight: {words[:900]}"
            )
        self._set_up_fits(fem)
        with step(
            f"setting the mesh up in load's own process: {fem.VolumeCount} elements, "
            f"{fem.NodeCount} nodes"
        ):
            self.nodes = {n: np.array([p.x, p.y, p.z]) for n, p in fem.Nodes.items()}
            if self.two_pass and len(self.solids) > 1:
                # Each part has elements of its own, before anything is solved: a mesh that
                # doesn't part them is refused at once in two passes (mqucifer/print-shop#301),
                # which then leave time for a glass band's lower bound.
                order = sorted(self.nodes)
                row = {n: i for i, n in enumerate(order)}
                corners = np.array(
                    [[row[n] for n in fem.getElementNodes(e)[:4]] for e in fem.Volumes]
                )
                try:
                    self._owned(corners, row, np.array([self.nodes[n] for n in order]))
                except _Unparted as unparted:
                    raise self._unparted_read(unparted) from unparted
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        # On disk, where /tmp is the container's memory (`cae.scratch`, mqucifer/print-shop#376).
        fea.setup_working_dir(cae.scratch("load-"))
        fea.setup_ccx()
        wrong = self._prerequisites(fea.check_prerequisites())
        if wrong:
            raise NoAnswer(f"the solve couldn't be set up: {wrong[:300]}")
        began = time.time()
        # FreeCAD's writer, in this process, held to the room first (mqucifer/print-shop#376).
        self._fits(fem, self._write_need(fem), "to write CalculiX's input with its loads")
        with step(
            f"writing CalculiX's input: {fem.VolumeCount} elements, {fem.NodeCount} nodes"
        ):
            fea.write_inp_file()
            if self.core_solids:
                self._stiffnesses(fea.inp_file_name)
            self._add_to(fea.inp_file_name)
        kills = oom_kills()
        #: Each of CalculiX's tries, and how long it ran, in s, for the reply where they meet
        #: the memory (the operator, mqucifer/print-shop#376).
        self.tries: list[tuple[str, float]] = []
        with step("CalculiX's solve of the joined parts"):
            solving = time.time()
            _ccx(fea, self.size)
            self.tries.append(("direct", time.time() - solving))
        self._results_back(fea)
        self.solve_s = round(time.time() - began, 2)
        results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
        if "nonpositive jacobian" in str(getattr(fea, "ccx_stdout", "")):
            tidy_solve(fea)
            where = self._inverted_said(str(fea.ccx_stdout))
            if not self.straight:
                raise _Curved(
                    "CalculiX found an element of the curved mesh, at most "
                    f"{self.size:g} mm, turned inside out{where}",
                    self.closes,
                    self.tight,
                )
            raise NoAnswer(
                f"CalculiX solved nothing: an element of the mesh, at most {self.size:g} mm, is "
                f"turned inside out{where}. One with a curved side on a small hole or a tight "
                "round can be: give a finer --mesh"
            )
        # Two passes, and a glass band's read, which nothing follows (the operator, on a design
        # run's assembly on mqucifer/print-shop#376), are solved again iteratively where the
        # direct solver met the memory.
        if (not results or not results[0].NodeStrainXX) and (self.two_pass or bool(_bands)):
            full = memory_met(kills)
            if full is not None:
                self._iterated(fea, analysis, full)
                tidy_solve(fea)
                self.solve_s = round(time.time() - began, 2)
                self._balance()
                return
        if not results or not results[0].NodeStrainXX:
            tidy_solve(fea)
            full = memory_met(kills)
            if full is not None:
                raise self._too_big(full)
            raise NoAnswer(
                f"CalculiX gave no result for the {fem.VolumeCount} elements, at a mesh of "
                f"{self.size:g} mm: it may have run out of the container's memory. Give a "
                "coarser --mesh"
            )
        self.row, self.strain, self.stress, self.moved = _arrays(results[0])
        self.points = np.array([self.nodes[n] for n in results[0].NodeNumbers])
        self.reaction, self.held_force = _reaction(os.path.splitext(fea.inp_file_name)[0] + ".dat")
        if not self.two_pass:
            self.printed = _printed(os.path.splitext(fea.inp_file_name)[0] + ".dat", self.row)
        tidy_solve(fea)
        self._balance()

    def _unparted_read(self, unparted: _Unparted) -> NoAnswer:
        """What two passes say of a mesh that gives parts no element of their own: the whole
        unread, for `load --parts` to read each glass band left out as the one pass's two
        passes do (mqucifer/print-shop#375), only where it's the leads' doing, each such part
        banded or meeting a banded part, and none of them turned. A turned part's placement
        (mqucifer/print-shop#334), or the faces two parts with no band meet by not found on the
        mesh (mqucifer/print-shop#319), is said as it is, as one pass says it (the operator's
        review)."""
        parts = {p.name: p for p in self.assembly.parts}
        banded = {name for name, p in parts.items() if p.band is not None}
        by_band = all(
            name in banded or any(name in pair and pair & banded for pair in self.met)
            for name in unparted.empty
        )
        if by_band and not any(parts[name].turn for name in unparted.empty):
            return _Unread(str(unparted))
        return unparted

    def _set_up_fits(self, fem: Any) -> None:
        """Whether the mesh, read back, can be set up in `load`'s own process
        (mqucifer/print-shop#376): each node's place, and in two passes each element's faces,
        to find each part's own. Its need, by the mesh's counts, at the least measured per node
        and per element (`SET_UP_NODE`, `SET_UP_FACES`), is held against the container's room
        (`cae.room`). Where it won't fit, the whole is unread for memory before anything is
        allocated, where the kernel would have stopped `load`; off the container, nothing's
        asked."""
        faces = self.two_pass and len(self.solids) > 1
        high = self._falls_back()
        node, face = (
            (SET_UP_NODE_HIGH, SET_UP_FACES_HIGH) if high else (SET_UP_NODE, SET_UP_FACES)
        )
        need = fem.NodeCount * node + (fem.VolumeCount * face if faces else 0)
        give_back()
        left = cae.room(files=not high)
        if left is None or left >= need:
            return
        _model_unread(
            self,
            f"the mesh of the joined parts at a mesh of at most {self.size:g} mm"
            f"{STRAIGHT if self.straight else ''}, {fem.VolumeCount:,} elements and "
            f"{fem.NodeCount:,} nodes, takes about {need / 2**30:.2f} GiB to set up in load's "
            f"own process, and the container's memory has {max(left, 0) / 2**30:.2f} GiB left, "
            "by its own count: so it isn't set up"
            + (", as held high, where a lower bound can follow, it may not fit" if high else "")
        )

    def _write_need(self, fem: Any) -> float:
        return _solve_need(
            fem, self._falls_back(), (WRITE_NODE, WRITE_ELEMENT),
            (WRITE_NODE_HIGH, WRITE_ELEMENT_HIGH),
        )  # fmt: skip

    def _read_need(self, fem: Any) -> float:
        return _solve_need(
            fem, self._falls_back(), (READ_NODE, READ_ELEMENT),
            (READ_NODE_HIGH, READ_ELEMENT_HIGH),
        )  # fmt: skip

    def _fits(self, fem: Any, need: float, what: str) -> None:
        """Whether a step of `load`'s own process, `what`, its `need` in bytes, fits the
        container's room (mqucifer/print-shop#376): where it won't, the whole is unread for
        memory, before anything's begun. FreeCAD's own code aborts where it meets the memory,
        so this is asked first. Off the container, nothing's asked."""
        high = self._falls_back()
        give_back()
        left = cae.room(files=not high)
        if left is None or left >= need:
            return
        _model_unread(
            self,
            f"the mesh of the joined parts at a mesh of at most {self.size:g} mm"
            f"{STRAIGHT if self.straight else ''}, {fem.VolumeCount:,} elements and "
            f"{fem.NodeCount:,} nodes, takes about {need / 2**30:.2f} GiB {what} in load's own "
            f"process, and the container's memory has {max(left, 0) / 2**30:.2f} GiB left, by "
            "its own count: so it isn't done"
            + (", as held high, where a lower bound can follow, it may not fit" if high else "")
        )

    def _results_back(self, fea: Any) -> None:
        """CalculiX's results read back into FreeCAD, in this process: where it left a results
        file, its need held to the room first (`_fits`), as a step of its own with the mesh's
        counts. Where it left none, as where it was stopped, FreeCAD's reader finds none, as
        before."""
        fem = self.fem
        if not os.path.exists(os.path.splitext(fea.inp_file_name)[0] + ".frd"):
            fea.load_results()
            return
        self._fits(fem, self._read_need(fem), "to read CalculiX's results back")
        with step(
            f"reading CalculiX's results back: {fem.VolumeCount} elements, {fem.NodeCount} nodes"
        ):
            fea.load_results()

    def _falls_back(self) -> bool:
        """Whether a lower bound can follow where the whole is unread: a part names its glass
        band, and this isn't the band's own read (`_bands`). A mesh's needs are held high then,
        and low anywhere else, where the try is all there is (the operator's ruling)."""
        return any(p.band is not None for p in self.assembly.parts) and not _bands

    def _mesh_met(self, mesh: Any, kills: int | None, tools: Any = None) -> None:
        """Where gmsh made no mesh and the kernel stopped a process in the container for memory
        while it ran, `kills` its count before (mqucifer/print-shop#375), or where its mesh
        won't fit read back (`cae.gmsh`, `tools.memory`, mqucifer/print-shop#376): the whole is
        unread, for the container's memory, as where CalculiX meets it, never meshed again
        straight, which takes as much. gmsh is the first to go (`cae.gmsh`), so this read is
        left to say so, and to read in two passes or each glass band left out, where it can."""
        if mesh.FemMesh.VolumeCount:
            return
        refused = getattr(tools, "memory", None)
        if refused is not None:
            need, left, high = refused
            _model_unread(
                self,
                f"gmsh meshed the joined parts at a mesh of at most {self.size:g} mm"
                f"{STRAIGHT if self.straight else ''}, and its mesh takes about "
                f"{need / 2**30:.2f} GiB read back, where the container's memory has "
                f"{max(left, 0) / 2**30:.2f} GiB left, by its own count: so it isn't read back"
                + (", as held high, where a lower bound can follow, it may not fit" if high else "")
            )
        full = memory_met(kills)
        if full is None:
            return
        _model_unread(
            self,
            f"gmsh made no mesh of the joined parts at a mesh of at most {self.size:g} mm"
            f"{STRAIGHT if self.straight else ''}: it met the container's memory, "
            f"{full / 2**30:g} GiB, meshing them, and the kernel stopped it there, as the "
            "container's own count of what it stopped for memory says"
        )

    def _gmsh(self, gmsh_tools: Any, mesh: Any, sizes: str) -> Any:
        """gmsh's mesh, as FreeCAD makes it, with a pass that isn't spread told so, and `sizes`,
        gmsh's local sizes where faces are close, where there are any; its files deleted once
        read, and FreeCAD's tools, which keep what gmsh said."""
        tools = gmsh_tools(mesh)
        if not self.spread or sizes:
            written = tools.write_geo

            def write_geo() -> None:
                written()
                path = Path(tools.temp_file_geo)
                text = path.read_text()
                if "// meshing" not in text:
                    raise NoAnswer("FreeCAD's gmsh input has no `// meshing` to set its sizes by")
                if sizes and "Background Field" in text:
                    raise NoAnswer(
                        "FreeCAD's gmsh input has a background field of its own, and the local "
                        "sizes where faces are close can't be set beside it"
                    )
                given = ("" if self.spread else "Mesh.MeshSizeExtendFromBoundary = 0;\n") + sizes
                path.write_text(text.replace("// meshing", given + "// meshing", 1))

            tools.write_geo = write_geo
        # First to go, so a mesh that meets the container's memory stops gmsh, and this read
        # goes on to say so (mqucifer/print-shop#375).
        gmsh(tools, first=True, high=self._falls_back())
        tidy_mesh(tools)
        return tools

    def _close(self, per_turn: int, words: str) -> tuple[str, bool]:
        """Where gmsh's triangles crossed, the pairs of faces that meet nowhere and lie closer
        than their mesh's sag (`close_faces`): every such pair of two parts' faces, and any
        near where they crossed; gmsh's local sizes for them and the pairs found before, and
        whether any is new. A pair that would need elements finer than `close_faces.LEAST` is
        the parts' to fix: where one is within the mesh of where gmsh's triangles crossed, it's
        said, with its parts, its faces and how far apart they are, and nothing is meshed;
        elsewhere, it's left as gmsh meshed it."""
        point = _crossed_at(words)
        tight: list[int] = []
        found = close_faces.found(
            self.whole, self.size, per_turn, _measured(self.whole), self._face_owners(), point,
            tight,
        )  # fmt: skip
        self.tight = len(tight)
        refused = close_faces.too_close(found)
        faces = self.whole.Faces
        here = [
            c for c in refused
            if point is not None and min(
                faces[n - 1].distToShape(point)[0] for n in (c.first, c.second)
            ) < self.size
        ]  # fmt: skip
        if here or (refused and point is None):
            refused = here or refused
            raise NoAnswer(
                f"{len(refused)} pair(s) of the parts' faces are too close together to mesh: "
                "each pair meets nowhere, and gmsh's triangles on one cross the other's unless "
                f"finer than {close_faces.LEAST:g} mm there, as gmsh found ({words[:300]}). "
                + close_faces.said(faces, refused, self._owners, lambda f: _xyz(f.CenterOfMass))
                + ". Draw each pair meeting face to face, or further apart"
            )
        before = {(c.first, c.second) for c in self.closes}
        new = [c for c in found if c not in refused and (c.first, c.second) not in before]
        self.closes += new
        return close_faces.fields(self.closes, self.size, self.local_scale), bool(new)

    def _finer_words(self) -> str:
        finer = self.finer_said()
        return _finer_said({"finer": finer}).removeprefix(", ") if finer else ""

    def _near_said(self, words: str) -> str:
        """The faces nearest where gmsh says its triangles cross, in words, for a design run to
        find them by: their parts, their kinds and where they are."""
        point = _crossed_at(words)
        if point is None:
            return ""
        near = self._faces_near(point)
        return f"; the faces nearest where gmsh's triangles cross: {near}" if near else ""

    def _faces_near(self, point: Any) -> str:
        """The `NEAREST` faces nearest a vertex, in words: each one's parts, kind and where it
        is, how far from it, and whether it was meshed finer or is narrow, under a tenth of the
        mesh across."""
        finer = {n for c in self.closes for n in c.faces}
        near = []
        for number, face in enumerate(self.whole.Faces, 1):
            box = face.BoundBox
            box.enlarge(self.size)
            if box.isInside(point.Point):
                near.append((float(face.distToShape(point)[0]), number, face))
            close_faces._let_go(face)
        near.sort(key=lambda row: row[0])
        said = []
        for d, number, f in near[:NEAREST]:
            length = sum(e.Length for e in f.Edges)
            thin = length > 0 and 2 * f.Area / length < self.size / 10
            flags = [w for w, yes in (("meshed finer", number in finer), ("narrow", thin)) if yes]
            said.append(
                f"{'/'.join(self._owners(f))}'s {close_faces.kind_of(f)} face"
                + (f" of radius {f.Surface.Radius:g} mm" if hasattr(f.Surface, "Radius") else "")
                + f" round {_xyz(f.CenterOfMass)}, {d:.4f} mm from it"
                + (f" ({', '.join(flags)})" if flags else "")
            )
            close_faces._let_go(f)
        return ", ".join(said)

    def _inverted_said(self, said: str) -> str:
        """Where CalculiX found elements turned inside out (mqucifer/print-shop#346), in words:
        how many, and for the first, its part, where it is and the faces nearest it, so a
        design run, or `load`, can tell a face meshed finer from a sliver the fuse left."""
        numbers = list(dict.fromkeys(int(n) for n in INVERTED.findall(said)))
        if not numbers or getattr(self, "fem", None) is None:
            return ""
        try:
            return self._placed_said(numbers)
        except Exception:  # noqa: BLE001 - placing it is only words: the try goes on as before
            return f": {len(numbers)} element(s)"

    def _placed_said(self, numbers: list[int]) -> str:
        import FreeCAD as App
        import Part

        corners = [self.nodes[n] for n in self.fem.getElementNodes(numbers[0])[:4]]
        middle = App.Vector(*np.mean(corners, axis=0))
        parts = [p.name for p, solid in zip(self.assembly.parts, self.solids, strict=True)
                 if solid.isInside(middle, 1e-6, True)] or ["none of the parts"]  # fmt: skip
        longest = max(float(np.linalg.norm(a - b)) for a in corners for b in corners)
        near = self._faces_near(Part.Vertex(middle))
        return (
            f": {len(numbers)} element(s), the first in {'/'.join(parts)}, round "
            f"{_xyz(middle)}, its longest side {longest:.4f} mm"
            + (f"; the faces nearest it: {near}" if near else "")
        )

    def _face_owners(self) -> list[frozenset[int]]:
        """Each face of the joined shape's parts, by their number from 0, in the shape's order:
        two where they meet, and none for a core's."""
        if getattr(self, "_owners_by_face", None) is None:
            owned: list[set[int]] = [set() for _ in self.shape.Faces]
            for k, solid in enumerate(self.solids):
                for face in solid.Faces:
                    owned[int(self._face_name(face).removeprefix("Face")) - 1].add(k)
                close_faces._let_go(solid)
            self._owners_by_face = [frozenset(o) for o in owned]
        return self._owners_by_face

    def _owners(self, face: Any) -> list[str]:
        """The parts a face of the joined shape is a face of: two where they meet."""
        number = int(self._face_name(face).removeprefix("Face"))
        names = [self.assembly.parts[k].name for k in sorted(self._face_owners()[number - 1])]
        return names or ["a core"]

    def finer_said(self) -> dict[str, Any] | None:
        """What's meshed finer where faces are close, for the reply: none where nothing is."""
        if not self.closes:
            return None
        faces = {n for c in self.closes for n in c.faces}
        return {
            "pairs": len(self.closes),
            "faces": len(faces),
            "closest_mm": round(min(c.apart for c in self.closes), 4),
            "tight_faces": self.tight,
            "least_mm": close_faces.LEAST,
            "finest_mm": round(
                close_faces.stepped(min(c.finest for c in self.closes) * self.local_scale), 4
            ),
        }

    def _stiffnesses(self, path: str) -> None:
        """Each core's elements, and its own stiffness, written into FreeCAD's CalculiX input
        (mqucifer/print-shop#337): FreeCAD writes the one material for every element, as for
        any assembly, and each core's elements are then a set of their own, with CalculiX's
        anisotropic constants in the assembly's axes, and every other element the material's.
        FreeCAD's own sets, one material to several solids, left elements out of every one."""
        from .infill import isotropic

        kinds = self._core_elements()
        text = Path(path).read_text()
        sections = list(re.finditer(r"^\*SOLID SECTION,\s*ELSET=(\S+),\s*MATERIAL=(\S+)\s*$",
                                    text, re.M))
        if len(sections) != 1:
            raise NoAnswer(
                f"FreeCAD's CalculiX input has {len(sections)} solid sections, not the one for "
                "its one material, to write each core's own into"
            )
        order = list(self.fem.Volumes)

        def elements(name: str, which: list[int]) -> str:
            rows = [which[i : i + 8] for i in range(0, len(which), 8)]
            return f"*ELSET,ELSET={name}\n" + "".join(
                ", ".join(map(str, r)) + "\n" for r in rows
            )

        material = sections[0].group(2)
        written = elements("SKINS", [e for e, k in zip(order, kinds, strict=True) if k < 0])
        lines = f"*SOLID SECTION, ELSET=SKINS, MATERIAL={material}\n"
        for number, tensor in enumerate(self.core_tensors):
            mine = [e for e, k in zip(order, kinds, strict=True) if k == number]
            if not mine:
                _, name, cell, _ = self.core_solids[number]
                raise NoAnswer(
                    f"the part {name}'s core in {cell.name} has no element of the mesh of its "
                    f"own, at a mesh of at most {self.size:g} mm: give a finer --mesh"
                )
            written += elements(f"CORE{number}", mine)
            written += f"*MATERIAL, NAME=CORE{number}\n" + _aniso(tensor)
            lines += f"*SOLID SECTION, ELSET=CORE{number}, MATERIAL=CORE{number}\n"
        start, end = sections[0].span()
        text = text[:start] + written + lines.rstrip("\n") + text[end:]
        Path(path).write_text(text)
        material_of = self.assembly.parts[0].material
        self.element_core = kinds
        self.element_tensor = np.array(
            [isotropic(material_of.modulus_mpa, material_of.poisson), *self.core_tensors]
        )
        self.element_kind = kinds + 1

    def _core_elements(self) -> np.ndarray:
        """Which core each element is in, by its place in `core_solids`, or -1 for a skin or a
        part read solid, in FreeCAD's order of the elements: the elements reached from one
        another without crossing a core's faces are one piece, and which core that is, if any,
        is asked of the solids once, at one element's middle (as `_owned` parts the parts)."""
        import FreeCAD as App

        cores = [(number, self.shape.Solids[index])
                 for number, (index, *_) in enumerate(self.core_solids)]  # fmt: skip
        walls = {
            tuple(sorted(nodes[:3]))
            for _, solid in cores
            for face in solid.Faces
            for nodes in self._on_face(face)
        }
        order = list(self.fem.Volumes)
        corners = [tuple(self.fem.getElementNodes(e)[:4]) for e in order]
        beside: dict[tuple[int, ...], list[int]] = {}
        for element, four in enumerate(corners):
            for skip in range(4):
                face = tuple(sorted(four[:skip] + four[skip + 1 :]))
                if face not in walls:
                    beside.setdefault(face, []).append(element)
        next_to: list[list[int]] = [[] for _ in order]
        for pair in beside.values():
            if len(pair) == 2:
                next_to[pair[0]].append(pair[1])
                next_to[pair[1]].append(pair[0])
        kinds = np.full(len(order), -2, dtype=int)
        for start in range(len(order)):
            if kinds[start] != -2:
                continue
            middle = App.Vector(*np.mean([self.nodes[n] for n in corners[start]], axis=0))
            kind = next(
                (number for number, solid in cores
                 if solid.BoundBox.isInside(middle) and solid.isInside(middle, 1e-6, False)),
                -1,
            )  # fmt: skip
            kinds[start] = kind
            todo = [start]
            while todo:
                for other in next_to[todo.pop()]:
                    if kinds[other] == -2:
                        kinds[other] = kind
                        todo.append(other)
        return kinds

    def _stiff(self, rows: slice | None = None) -> tuple[Any, Any, np.ndarray | None]:
        """The elements' stiffness for `element_forces`, of `rows`: the material's modulus and
        Poisson's ratio where every element is it; else each element's own, C_ijkl."""
        material = self.assembly.parts[0].material
        if self.element_tensor is None or self.element_kind is None:
            return material.modulus_mpa, material.poisson, None
        kinds = self.element_kind if rows is None else self.element_kind[rows]
        return material.modulus_mpa, material.poisson, self.element_tensor[kinds]

    def _iterated(self, fea: Any, analysis: Any, limit: int) -> None:
        """Solved by CalculiX's iterative solver, where its direct one met the container's
        memory (mqucifer/print-shop#301), and corrected: the iterative solver stops at a fixed
        residual, which moves a strain by a percent or so, so what's left unbalanced at each
        node, the load less what the elements carry (`element_forces`), is solved for again and
        added, until it's under `RESIDUAL` of the load. Each solve is linear, so the movement,
        the strain, the stress and the held faces' reaction add."""
        path = Path(fea.inp_file_name)
        text = path.read_text()
        static = re.search(r"^\*STATIC\s*$", text, re.M)
        if static is None:
            raise NoAnswer("FreeCAD's CalculiX input has no *STATIC step to solve iteratively")
        text = text[: static.start()] + ITERATIVE + text[static.end() :]
        loaded = sum(float(np.linalg.norm(p)) for p in self.pushed.values())
        runs: list[float] = []
        for run in range(1 + CORRECTIONS):
            job = path if run == 0 else path.with_name(f"{path.stem}_{run}.inp")
            job.write_text(text)
            fea.inp_file_name = str(job)
            solving = time.time()
            ccx_one_thread(fea)
            self.tries.append(("iterative", time.time() - solving))
            self._results_back(fea)
            results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
            if not results or not results[-1].NodeStrainXX:
                raise self._too_big(limit)
            row, strain, stress, moved = _arrays(results[-1])
            reaction, held = _reaction(str(job.with_suffix(".dat")))
            if run == 0:
                self.row, self.strain, self.stress, self.moved = row, strain, stress, moved
                self.points = np.array([self.nodes[n] for n in results[-1].NodeNumbers])
                self.reaction, self.held_force = reaction, held
            else:
                # The same mesh, its nodes taken in the first solve's order.
                same = [row[n] for n in self.row]
                self.strain += strain[same]
                self.stress += stress[same]
                self.moved += moved[same]
                self.reaction = self.reaction + reaction
                for node, force in held.items():
                    self.held_force[node] = self.held_force.get(node, 0) + force
            for result in results:
                analysis.Document.removeObject(result.Name)
            left = self._unbalanced()
            runs.append(float(np.abs(left).sum()) / loaded if loaded else 0.0)
            if runs[-1] <= RESIDUAL:
                break
            text = _cload(text, left, self.row)
        self.residuals = runs
        self.iterated = (
            f"CalculiX's direct solver met the container's memory, {limit / 2**30:g} GiB, as the "
            "container's own count of what it stopped for memory says, so it's solved by its "
            f"iterative solver and corrected {len(runs) - 1} time(s), each time for what was "
            f"left unbalanced, to {runs[-1]:.1e} of the load"
        )
        if runs[-1] > RESIDUAL:
            raise NoAnswer(
                f"{self.iterated}: more than the {RESIDUAL:g} it's held to, so nothing is read"
            )

    def _wired(self) -> np.ndarray:
        """Each element's nodes as rows, its corners first and then `MIDS`' mid-side nodes,
        in FreeCAD's own order (SMESH's for a ten-node tetrahedron), in one pass as in two
        (mqucifer/print-shop#320). The one pass found each mid-side node by where it lies, and
        on a small curved element took one side's for another's, so that element's forces went
        twice to one node and never to its sixth. The order is checked (`misordered`): where
        it can't be read, nothing is read from it."""
        if self._wiring is not None:
            return self._wiring
        given = [self.fem.getElementNodes(e) for e in self.fem.Volumes]
        corners = np.array([[self.row[n] for n in nodes[:4]] for nodes in given])
        short = [k for k, nodes in enumerate(given) if len(nodes) != 4 + len(MIDS)]
        if short:
            raise NoAnswer(
                f"the mesh's elements can't be read in their order: {len(short)} element(s) "
                f"of {self._parts_of(corners, short)} haven't ten nodes, at a mesh of at most "
                f"{self.size:g} mm{STRAIGHT if self.straight else ''}, so no force on a joint is "
                "read from them, and nothing is read"
            )
        wired = np.array([[self.row[n] for n in nodes] for nodes in given])
        wrong = np.flatnonzero(misordered(wired, self.points))
        if wrong.size:
            raise NoAnswer(
                f"the mesh's elements can't be read in FreeCAD's order: {wrong.size} "
                f"element(s) of {self._parts_of(corners, wrong)} haven't six mid-side nodes of "
                "their own, each on its own side, at a mesh of at most "
                f"{self.size:g} mm{STRAIGHT if self.straight else ''}, so no force on a joint is "
                "read from them, and nothing is read"
            )
        self._wiring = wired
        return wired

    def _parts_of(self, corners: np.ndarray, elements: Any) -> str:
        """The parts some of the mesh's elements are in, named: each part is what's reached from
        one of its elements without crossing a joint (`_owned`), which takes each element's
        corners alone, so it's told whatever its mid-side nodes are."""
        owned = self._owned(corners, self.row, self.points)
        names = [p.name for p, mine in zip(self.assembly.parts, owned, strict=True)
                 if mine[elements].any()]  # fmt: skip
        return ("the part " if len(names) == 1 else "the parts ") + ", ".join(names)

    def _unbalanced(self) -> np.ndarray:
        """What's left unbalanced at each node, as rows: the load put on it less what the
        elements carry there, on each axis CalculiX isn't told to hold."""
        wired = self._wired()
        forces = element_forces(self.points[wired], self.moved[wired], *self._stiff())
        return self._left(wired, forces)

    def _left(self, wired: np.ndarray, forces: np.ndarray) -> np.ndarray:
        """What's left unbalanced at each node, as rows, of the elements' `forces`."""
        left = np.zeros((len(self.points), 3))
        np.add.at(left, wired, -forces)
        for node, push in self.pushed.items():
            left[self.row[node]] += push
        for node, axes in self.constrained.items():
            left[self.row[node], sorted(axes)] = 0.0
        return left

    def _imbalance(self, wired: np.ndarray, moved: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """What's left unbalanced at each node on each axis not held, in N, with each node
        `moved` as CalculiX printed it (`_printed`), and the most the print's own figures can
        leave there: each element's stiffness (`element_stiffness`), each term taken as its
        size, times how far each of its nodes' movements can be from what CalculiX solved
        (`figures`), summed over the elements at the node; and `LOADS` of the whole load.
        Element by element, in groups of `GROUP`, so it fits the memory."""
        apart = figures(moved)
        left = np.zeros((len(self.points), 3))
        allowed = np.zeros((len(self.points), 3))
        for start in range(0, len(wired), GROUP):
            rows = wired[start : start + GROUP]
            places = self.points[rows]
            these = self._stiff(slice(start, start + GROUP))
            forces = element_forces(places, moved[rows], *these)
            np.add.at(left, rows, -forces)
            stiff = element_stiffness(places, *these)
            np.add.at(allowed, rows, np.einsum("naibk,nbk->nai", np.abs(stiff), apart[rows]))
        for node, push in self.pushed.items():
            left[self.row[node]] += push
        for node, axes in self.constrained.items():
            left[self.row[node], sorted(axes)] = 0.0
        allowed += LOADS * sum(float(np.linalg.norm(p)) for p in self.pushed.values())
        return np.abs(left), allowed

    def _balanced(self, wired: np.ndarray, owned: np.ndarray) -> None:
        """The one pass's elements balance as CalculiX solved them (mqucifer/print-shop#320): at
        every node not held, what they carry, with each node moved as CalculiX printed it to
        seven figures, is the load put on it, within what those figures can leave
        (`_imbalance`). An element whose nodes aren't in the order CalculiX took them carries
        other forces, and leaves the rest at its nodes: wherever that's more than the figures
        account for, nothing is read, and the parts are named."""
        if self.printed is None:
            raise NoAnswer("CalculiX printed no node's movement to hold the elements' balance to")
        left, allowed = self._imbalance(wired, self.printed)
        over = np.flatnonzero((left > allowed).any(axis=1))
        if not over.size:
            return
        elements = np.flatnonzero(np.isin(wired, over).any(axis=1))
        names = [p.name for p, mine in zip(self.assembly.parts, owned, strict=True)
                 if mine[elements].any()]  # fmt: skip
        worst = float((left[over] / allowed[over]).max())
        raise NoAnswer(
            "the mesh's elements don't balance as CalculiX solved them: at "
            f"{over.size} node(s) of {'the part ' if len(names) == 1 else 'the parts '}"
            f"{', '.join(names)}, what they carry is further from the load than the seven "
            f"figures CalculiX prints each movement to can make it, up to {worst:.3g} times as "
            "far, "
            f"at a mesh of at most {self.size:g} mm{STRAIGHT if self.straight else ''}: an "
            "element's nodes there aren't in the order CalculiX solved them, so no force on a "
            "joint is read from them, and nothing is read"
        )

    def _prerequisites(self, wrong: str) -> str:
        """What FreeCAD says is missing before it solves."""
        return wrong

    def _too_big(self, limit: int) -> _Unread:
        """Why the solve met the container's memory (mqucifer/print-shop#295), led by what to
        do: the mesh's size, against what its size alone would give, the rest being finer round
        the parts' small features, which gmsh sizes by their own curvature and not by --mesh."""
        fem = self.fem
        # About 8.5 tetrahedra fill a cube of their own edge, as `_mesh_size` takes it.
        alone = 8.5 * float(self.shape.Volume) / self.size**3
        finer = fem.VolumeCount - alone
        evidence = (
            f"CalculiX gave no result for the {fem.VolumeCount:,} elements ({fem.NodeCount:,} "
            f"nodes) at a mesh of at most {self.size:g} mm: it met the container's memory, "
            f"{limit / 2**30:g} GiB, and the kernel stopped it there, as the container's own "
            "count of what it stopped for memory says. "
            f"The mesh's size alone would give about {alone:,.0f} elements over the parts' "
            f"{self.shape.Volume / 1000:,.0f} cm³"
            + (
                f", and the other {finer:,.0f}, {100 * finer / fem.VolumeCount:.0f}%, are "
                "finer, round the parts' small features, such as bolts' bores and rounds, "
                "which gmsh sizes by their own curvature and not by --mesh: a coarser --mesh "
                "leaves most of those as they are"
                if finer > alone
                else ""
            )
        )
        parts = getattr(getattr(self, "assembly", None), "parts", [])
        banded = any(getattr(p, "band", None) is not None for p in parts)
        to_do = (
            _banded_to_do()
            if banded
            else "name each glass frame's `band` in the assembly file, and it's read as a lower "
            "bound with its band left out, or read fewer of the parts at once"
            if finer > alone
            else "give a coarser --mesh, or read fewer of the parts at once"
        )
        tries = getattr(self, "tries", [])
        if tries:
            evidence += (
                "; CalculiX's "
                + " and its ".join(f"{kind} solver ran {seconds:.0f} s" if k == 0 else
                                   f"{kind} one {seconds:.0f} s"
                                   for k, (kind, seconds) in enumerate(tries))
                + (", each stopped there" if len(tries) > 1 else ", stopped there")
            )  # fmt: skip
        return _Unread(
            f"nothing of the assembly is read, as it's past what the container's memory solves: "
            f"{to_do}. {evidence}. Every part, joint and bolt of it is left unread",
            evidence,
            memory=True,
        )

    def _balance(self) -> None:
        """The force each part's elements put on each node, and what's put on each part's
        nodes from outside: its loads and its held faces' reaction."""
        names = [p.name for p in self.assembly.parts]
        wired = self._wired()
        # A printed part's skin is thin, flat elements, so stiff that the .frd's six figures of
        # each movement leave up to a newton at a node on the planted beam's halves: its forces
        # are read from the seven CalculiX prints, where it has them (mqucifer/print-shop#337).
        # A part read solid is read as it was.
        moved = self.printed if self.core_solids and self.printed is not None else self.moved
        forces = element_forces(self.points[wired], moved[wired], *self._stiff())
        owned = self._owned(wired, self.row, self.points)
        # Solved iteratively, the elements' balance is what the corrections hold to `RESIDUAL`
        # (`_iterated`), from the same wiring, checked as it's read (`_wired`).
        if not self.two_pass and self.iterated is None:
            self._balanced(wired, owned)
        self.part_rows = {name: np.unique(wired[owned[k]]) for k, name in enumerate(names)}
        if self.element_core is not None:
            for k, name in enumerate(names):
                if name in self.cores:
                    self.skin_rows[name] = np.unique(wired[owned[k] & (self.element_core < 0)])
            for number in range(len(self.core_solids)):
                self.core_rows[number] = np.unique(wired[self.element_core == number])
        self.within = {}
        for k, name in enumerate(names):
            total = np.zeros((len(self.points), 3))
            np.add.at(total, wired[owned[k]], forces[owned[k]])
            self.within[name] = total
        # What's put on each part from outside, node by node.
        self.outside = {name: np.zeros((len(self.points), 3)) for name in names}
        for load in self.loads:
            for node, push in load["pushes"].items():
                self.outside[load["part"]][self.row[node]] += push
        for name, solid in zip(names, self.solids, strict=True):
            own = Faces(solid.Faces)
            mine = [f for f in [*self.fixed, *sum(self.standing.values(), [])] if own.has(f)]
            # The held faces' nodes as FreeCAD holds them, and as CalculiX sums their reaction.
            for node in {n for f in mine for n in self.fem.getNodesByFace(f)}:
                if node in self.held_force:
                    self.outside[name][self.row[node]] += self.held_force.pop(node)
        # A pin's reaction, on no held face of its own: it's nothing where the load is square.

    def tractions(self) -> dict[str, dict[str, np.ndarray]]:
        """What each part's neighbours put on it, for the second of two passes
        (mqucifer/print-shop#301): on each triangle of every face it meets another part by, the
        push per mm² there, and all of it as one force, and its moment about the origin. It's
        each node's own balance, what the part's elements carry less what's put on it from
        outside, shared between the triangles the node is a corner or a side of; so what its
        neighbours put on a part balances its loads and its held faces' reaction exactly."""
        found: dict[str, dict[str, np.ndarray]] = {}
        for name in [p.name for p in self.assembly.parts]:
            faces = [f for pair, met in self.met.items() if name in pair for f in met]
            triangles = [t for f in faces for t in self._triangles(f)]
            if not triangles:
                continue
            carried = self.within[name] - self.outside[name]
            count = np.zeros(len(self.points))
            for nodes, _, _ in triangles:
                np.add.at(count, [self.row[n] for n in nodes], 1)
            centres, pushes = [], []
            for nodes, area, centre in triangles:
                rows = [self.row[n] for n in nodes]
                centres.append(centre)
                pushes.append((carried[rows] / count[rows][:, None]).sum(axis=0) / area)
            met_at = np.flatnonzero(count)
            found[name] = {
                "centres": np.array(centres),
                "pushes": np.array(pushes),
                "force": carried[met_at].sum(axis=0),
                "moment": np.cross(self.points[met_at], carried[met_at]).sum(axis=0),
            }
        return found

    def _owned(self, wired: np.ndarray, row: dict[int, int], points: np.ndarray) -> np.ndarray:
        """Which part each element is in, as a row of flags for each part: `wired` is each
        element's nodes as rows of `points`, and `row` each node's. Elements that share
        a face are in one part, but for a face that's one of a joint's triangles: so each part
        is what's reached from one of its elements without crossing a joint, and which part
        that is, is asked of the solid once, at one element's middle."""
        import FreeCAD as App

        joined = {
            tuple(sorted(row[n] for n in nodes[:3]))
            for face in self.between
            for nodes, _, _ in self._triangles(face)
        }
        beside: dict[tuple[int, ...], list[int]] = {}
        for element, corners in enumerate(wired[:, :4].tolist()):
            for skip in range(4):
                face = tuple(sorted(corners[:skip] + corners[skip + 1 :]))
                if face not in joined:
                    beside.setdefault(face, []).append(element)
        next_to: list[list[int]] = [[] for _ in range(len(wired))]
        for pair in beside.values():
            if len(pair) == 2:
                next_to[pair[0]].append(pair[1])
                next_to[pair[1]].append(pair[0])
        owned = np.zeros((len(self.solids), len(wired)), dtype=bool)
        seen = np.zeros(len(wired), dtype=bool)
        for start in range(len(wired)):
            if seen[start]:
                continue
            reached, todo = [start], [start]
            seen[start] = True
            while todo:
                for other in next_to[todo.pop()]:
                    if not seen[other]:
                        seen[other] = True
                        reached.append(other)
                        todo.append(other)
            middle = App.Vector(*points[wired[start, :4]].mean(axis=0))
            # A printed part is its skin and its cores (mqucifer/print-shop#337).
            part = next(
                (k for k, s in enumerate(self.solids)
                 if any(piece.isInside(middle, 1e-6, False) for piece in self._pieces(k, s))),
                None,
            )  # fmt: skip
            if part is None:
                raise NoAnswer("an element of the mesh is in none of the parts")
            owned[part, reached] = True
        parts = self.assembly.parts
        empty = [p.name for p, mine in zip(parts, owned, strict=True) if not mine.any()]
        if empty:
            # Each part is reached whole from any of its elements, so a part with none was
            # reached from another's, across faces where they meet that weren't found on the
            # mesh (mqucifer/print-shop#319).
            meets = sorted({n for pair in self.met if pair & set(empty) for n in pair} - set(empty))
            # And how each is placed, where it's turned (mqucifer/print-shop#334).
            turned = "".join(
                f"; {p.name} is placed by "
                + ", then ".join(f"{degrees:g}° about {axis}" for axis, degrees in p.turn)
                for p in parts
                if p.name in empty and p.turn
            )
            raise _Unparted(
                f"the mesh gives {', '.join(empty)} no element of its own, at a mesh of at most "
                f"{self.size:g} mm{STRAIGHT if self.straight else ''}: the mesh's triangles on "
                f"the faces where it meets {', '.join(meets) or 'the other parts'} weren't "
                f"found, so it isn't parted from them, and nothing of it is read{turned}",
                empty,
            )
        return owned

    def _triangles(self, face: Any) -> list[tuple[tuple[int, ...], float, np.ndarray]]:
        """The mesh's triangles on a face: each one's nodes, corners first, its area and its
        centre."""
        found = []
        for nodes in self._on_face(face):
            a, b, c = (self.nodes[n] for n in nodes[:3])
            area = 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
            found.append((nodes, area, (a + b + c) / 3))
        return found

    def _on_face(self, face: Any) -> list[tuple[int, ...]]:
        """The nodes of each of the mesh's triangles on a face, corners first.

        FreeCAD's own `getFacesByFace` takes a triangle whose every node is on the face. With
        its elements' sides straight, a triangle's mid-side nodes are halfway along its chords,
        off a curved face, so on one it finds none: a curved face where two parts meet was
        crossed as if it weren't there, the parts it parts were taken for one, and the part
        left with no element of its own stopped the reading with "attempt to get argmax of an
        empty sequence" (mqucifer/print-shop#319). Its corners are on the face still, so there
        a triangle is one whose corners are, and that lies on it (`lies_on`): not one of the
        face beside it whose corners are all on the edge they share. So too on a flat face along
        a curved edge, where a mid-side node is on the edge's chord, just past the face. A mesh
        whose sides are curved is read as it was.

        Either way a node is on the face within the tolerance the joined parts hold it to
        (`as_joined`, mqucifer/print-shop#334), not the face's own alone."""
        face = as_joined(face)
        if not self.straight:
            return [self.fem.getElementNodes(e) for e in self.fem.getFacesByFace(face)]
        import FreeCAD as App

        on = set(self.fem.getNodesByFace(face))
        surface = face.Surface
        found = []
        for nodes in self._surface():
            if not on.issuperset(nodes[:3]):
                continue
            corners = np.array([self.nodes[n] for n in nodes[:3]])
            u, v = surface.parameter(App.Vector(*corners.mean(axis=0)))
            bend = max(abs(surface.curvature(u, v, kind)) for kind in ("Max", "Min"))
            nearest, normal = np.array(surface.value(u, v)), np.array(surface.normal(u, v))
            if lies_on(corners, nearest, normal, bend):
                found.append(nodes)
        return found

    def _surface(self) -> list[tuple[int, ...]]:
        """Every triangle of the mesh's surface, as its nodes, read from the mesh once."""
        if self._surface_nodes is None:
            self._surface_nodes = [self.fem.getElementNodes(e) for e in self.fem.Faces]
        return self._surface_nodes

    def _face_nodes(self, face: Any) -> list[int]:
        """The mesh's nodes on a face where two parts meet: on a straight-sided mesh, with its
        triangles' mid-side nodes, which are off a curved face, or past a curved edge of a flat
        one (`_on_face`); within the tolerance the joined parts hold it to (`as_joined`). A held
        face's are FreeCAD's own, as its constraint holds those."""
        found = self.fem.getNodesByFace(as_joined(face))
        if not self.straight:
            return list(found)
        return sorted({*found, *(n for nodes in self._on_face(face) for n in nodes)})

    def _patches(self) -> None:
        """Each place `--at` names as the triangles of the surface within `--over` of it, and
        the force on each of their nodes."""

        each = self.args.weight * G / len(self.args.at)
        self._put([(tuple(float(c) for c in place), each) for place in self.args.at])

    def _put(self, places: list[tuple[tuple[float, ...], float]]) -> None:
        """Each place's weight, `each` N, spread over the surface within `--over` of it."""
        import FreeCAD as App
        import Part

        names = [p.name for p in self.assembly.parts]
        between = Faces(self.between)
        outer = [f for f in self.shape.Faces if not between.has(f)]
        self.loads: list[dict[str, Any]] = []
        self.pushed: dict[int, np.ndarray] = {}
        for place, each in places:
            point = np.asarray(place, dtype=float)
            vertex = Part.Vertex(App.Vector(*place))
            near = [(f.distToShape(vertex)[0], f) for f in outer]
            least = min(d for d, _ in near)
            if least > ON_SURFACE:
                box = self.shape.BoundBox
                raise NoAnswer(
                    f"--at {_xyz(point)} is {least:.3f} mm from the assembly's surface, which "
                    f"runs from {_xyz((box.XMin, box.YMin, box.ZMin))} to "
                    f"{_xyz((box.XMax, box.YMax, box.ZMax))}: a weight sits on a surface"
                )
            faces = [f for d, f in near if d <= ON_SURFACE]
            triangles = [t for f in faces for t in self._triangles(f)]
            if not triangles:
                raise NoAnswer(
                    f"--at {_xyz(point)}: the mesh, at most {self.size:g} mm, has no triangle on "
                    "the faces it's on, so no weight can be put there"
                )
            within = [t for t in triangles if np.linalg.norm(t[2] - point) <= self.args.over]
            if not within:
                within = [min(triangles, key=lambda t: float(np.linalg.norm(t[2] - point)))]
            area = sum(t[1] for t in within)
            patch: set[int] = set()
            pushes: dict[int, np.ndarray] = {}
            for nodes, size, _ in within:
                # A uniform push on a second-order triangle is a third on each mid-side node.
                for node in nodes[3:]:
                    pushes[node] = pushes.get(node, 0) + each * size / area / 3 * self.toward
                patch.update(nodes)
            for node, push in pushes.items():
                self.pushed[node] = self.pushed.get(node, 0) + push
            part = next(
                n for n, s in zip(names, self.solids, strict=True) if Faces(s.Faces).has(faces[0])
            )
            self.loads.append(
                {
                    "at": point,
                    "part": part,
                    "force": each * self.toward,
                    "patch_mm2": area,
                    "nodes": sorted(patch),
                    "pushes": pushes,
                    # Where the spread weight acts: a patch cut off by an edge isn't round.
                    "middle": sum(self.nodes[n] * (p @ self.toward) for n, p in pushes.items())
                    / each,
                }
            )

    def _weights(self) -> None:
        """Each part's own weight but the lifted one's (mqucifer/print-shop#170): its solid
        volume at its material's density, pulling `--toward`, put on every node of its own
        elements. A uniform pull on a second-order tetrahedron is a fifth of it on each
        mid-side node, less a twentieth on each corner."""
        order = sorted(self.nodes)
        row = {n: i for i, n in enumerate(order)}
        points = np.array([self.nodes[n] for n in order])
        wired = np.array([[row[n] for n in self.fem.getElementNodes(e)] for e in self.fem.Volumes])
        owned = self._owned(wired, row, points)
        corners = points[wired[:, :4]]
        volume = np.abs(np.linalg.det(corners[:, 1:] - corners[:, :1])) / 6
        self.loads = []
        self.pushed = {}
        for k, (part, solid) in enumerate(zip(self.assembly.parts, self.weighed, strict=True)):
            if part.name == self.args.lift:
                continue
            grams = solid.Volume * part.material.density_g_cm3 / 1000
            newtons = grams / 1000 * G
            mine = owned[k]
            # The mesh's own volume differs from the solid's by its chords on a curved face.
            each = volume[mine] * newtons / float(volume[mine].sum())
            shared = np.zeros(len(points))
            np.add.at(shared, wired[mine, :4], -each[:, None] / 20)
            np.add.at(shared, wired[mine, 4:], each[:, None] / 5)
            rows = np.unique(wired[mine])
            pushes = {order[r]: shared[r] * self.toward for r in rows if shared[r] != 0}
            centre = solid.CenterOfMass
            middle = np.array([centre.x, centre.y, centre.z])
            whole = float(solid.Volume)
            kept: dict[str, Any] = {}
            band = _bands.get(part.name)
            if band is not None:
                # Read without its glass band, it hangs by its whole weight still
                # (mqucifer/print-shop#375): the band's is put where the band met it.
                band_pushes, area, band_grams = self._band_weight(k, part, band)
                for node, push in band_pushes.items():
                    pushes[node] = pushes.get(node, 0) + push
                grams += band_grams
                newtons = grams / 1000 * G
                middle, whole = band.whole_middle, band.whole_volume
                kept = {"band_grams": band_grams, "band_mm2": area}
            for node, push in pushes.items():
                self.pushed[node] = self.pushed.get(node, 0) + push
            self.loads.append(
                {
                    "at": middle,
                    "part": part.name,
                    "force": newtons * self.toward,
                    "patch_mm2": None,
                    "nodes": [order[r] for r in rows],
                    "pushes": pushes,
                    "middle": middle,
                    "volume_mm3": whole,
                    "grams": grams,
                    **kept,
                }
            )

    def _band_weight(
        self, k: int, part: PartAt, band: Band
    ) -> tuple[dict[int, np.ndarray], float, float]:
        """A glass band's weight, where its part is read without it under a lift
        (mqucifer/print-shop#375): the volume it took at the part's density, pulling `--toward`,
        spread over the faces where it met the rest of the part (`on_band`), each of their
        triangles taking its share by its area, a third of it on each mid-side node, as a
        weight on a place is (`_put`). Its stiffness is left out, never made up. Each push by
        node, the faces' area in mm², and the band's grams."""
        grams = band.volume * part.material.density_g_cm3 / 1000
        newtons = grams / 1000 * G
        between = Faces(self.between)
        with step(f"finding the faces where the part {part.name}'s glass band met it"):
            faces = [
                f for f in self.solids[k].Faces if not between.has(f) and on_band(f, band.solids)
            ]
        triangles = [t for f in faces for t in self._triangles(f)]
        if not triangles:
            raise NoAnswer(
                f"the part {part.name} with its glass band, {part.band}, left out has no face of "
                f"the mesh where the band met it, so the band's {grams:.2f} g has nowhere to "
                "hang from it, and nothing is read: a band is what the part is cut by"
            )
        area = sum(t[1] for t in triangles)
        pushes: dict[int, np.ndarray] = {}
        for nodes, size, _ in triangles:
            for node in nodes[3:]:
                pushes[node] = pushes.get(node, 0) + newtons * size / area / 3 * self.toward
        return pushes, float(area), float(grams)

    def _add_to(self, path: str) -> None:
        """The loads, the pins that stop a standing assembly sliding, and the held faces'
        reaction asked for, written into the input FreeCAD made."""
        if self.args.lift is not None:
            self._weights()
        else:
            self._patches()
        self._write(path)

    def _pins(self, held: list[int]) -> tuple[str, list[int]]:
        """What stops the body moving that its held faces don't, as CalculiX's lines, and the
        nodes they hold beside its held faces' own."""
        if self.fixed:
            return "", []
        # Standing holds it square to each plane only. Two nodes stop the rest: one held
        # along the other axes, and the furthest from it held from turning about it. With
        # a load square to the plane they carry nothing.
        free = [a for a in range(3) if a not in self.standing]
        first = held[0]
        last = max(held, key=lambda n: float(np.linalg.norm(self.nodes[n] - self.nodes[first])))
        lines = "*BOUNDARY\n" + "".join(f"{first},{a + 1},{a + 1}\n" for a in free)
        self.constrained.setdefault(first, set()).update(free)
        if len(free) == 2:
            apart = np.abs(self.nodes[last] - self.nodes[first])
            steer = min(free, key=lambda a: float(apart[a]))
            lines += f"{last},{steer + 1},{steer + 1}\n"
            self.constrained.setdefault(last, set()).add(steer)
        return lines, []

    def _write(self, path: str) -> None:
        held = sorted(
            {
                n
                for f in [*self.fixed, *sum(self.standing.values(), [])]
                # As FreeCAD holds them: its own constraint's nodes.
                for n in self.fem.getNodesByFace(f)
            }
        )
        for face in self.fixed:
            for node in self.fem.getNodesByFace(face):
                self.constrained.setdefault(node, set()).update(range(3))
        for axis, faces in self.standing.items():
            for face in faces:
                for node in self.fem.getNodesByFace(face):
                    self.constrained.setdefault(node, set()).add(axis)
        step = "*CLOAD\n" + "".join(
            f"{node},{axis + 1},{push[axis]:.9g}\n"
            for node, push in sorted(self.pushed.items())
            for axis in range(3)
            if abs(push[axis]) > 0
        )
        pins, more = self._pins(held)
        step += pins
        held = sorted({*held, *more})
        step += "*NODE PRINT, NSET=HELDALL, TOTALS=YES\nRF\n"
        if not self.two_pass:
            # Every node's movement to seven figures, for the elements' balance (`_balanced`).
            step += "*NODE PRINT, NSET=Nall\nU\n"
        text = Path(path).read_text()
        start, outputs = text.find("\n*STEP"), text.find("** Outputs")
        if start < 0 or outputs < 0:
            raise NoAnswer("FreeCAD's CalculiX input has no step to put the load in")
        outputs = text.rfind("\n***", 0, outputs) + 1
        rows = [held[i : i + 12] for i in range(0, len(held), 12)]
        sets = "*NSET, NSET=HELDALL\n" + "".join(",".join(map(str, r)) + ",\n" for r in rows)
        text = text[: start + 1] + sets + text[start + 1 : outputs] + step + text[outputs:]
        Path(path).write_text(text)

    # The readings

    def parts_read(self, mesh: str) -> dict[str, Any]:
        found = {}
        for part in self.assembly.parts:
            with step(f"reading the part {part.name}, at {mesh}"):
                found[part.name] = self._part_read(part)
        return found

    def _part_read(self, part: PartAt) -> dict[str, Any]:
        """One part's worst pull across its layers and largest stretch any way. A printed part
        (mqucifer/print-shop#337) is read so on its skin, against the material's own limits,
        and each of its cores against its own (`infill.Core.read`), as `cores`."""
        if part.name not in self.skin_rows:
            return self._read_rows(part, self.part_rows[part.name])
        found = self._read_rows(part, self.skin_rows[part.name])
        found["printed"] = True
        found["skin_nodes"] = found.pop("nodes")
        found["nodes"] = len(self.part_rows[part.name])
        way, first, second = _frame(part)
        cores = []
        for number, (_, name, cell, figures) in enumerate(self.core_solids):
            if name != part.name:
                continue
            assert self.element_core is not None
            mine = self._wired()[self.element_core == number]
            if not len(mine):
                raise NoAnswer(
                    f"the part {part.name}'s core in {cell.name} has no element of the mesh "
                    f"of its own, at a mesh of at most {self.size:g} mm: give a finer --mesh"
                )
            # Its own elements' strain, at their integration points, from each node's movement
            # to CalculiX's seven figures where it printed them, never averaged with its skin's.
            moved = self.printed if self.printed is not None else self.moved
            strain, at = element_strains(self.points[mine], moved[mine], POINTS + NODES)
            read = figures.read(strain.reshape(-1, 3, 3), way, first, second)
            for key in ("across_at", "stretch_at"):
                read[key] = _list(at.reshape(-1, 3)[read.pop(key)], 3)
            # And CalculiX's own, the mean at each node of every element there, for what it's
            # worth beside it: never more than the core's own at its nodes.
            rows = self.core_rows.get(number, np.array([], dtype=int))
            averaged = np.einsum("i,nij,j->n", way, self.strain[rows], way) if rows.size else [0]
            read["averaged_across_layers_pct"] = 100 * max(float(np.max(averaged)), 0.0)
            cores.append({"cell": cell.name, "elements": len(mine), **figures.said(), **read})
        found["cores"] = cores
        return found

    def _read_rows(self, part: PartAt, rows: np.ndarray) -> dict[str, Any]:
        """The worst pull across its layers and largest stretch any way, of a part's `rows`."""
        way = part.layers_way()
        strain = self.strain[rows]
        across = np.einsum("i,nij,j->n", way, strain, way)
        values, vectors = np.linalg.eigh(strain)
        pick = np.abs(values).argmax(axis=1)
        stretch = np.abs(values).max(axis=1)
        worst, most = int(across.argmax()), int(stretch.argmax())
        along = vectors[most][:, pick[most]]
        return {
            "material": part.material.name,
            "layers": _list(way),
            "across_layers_pct": 100 * max(float(across[worst]), 0.0),
            "across_at": _list(self.points[rows[worst]], 3),
            "across_limit_pct": part.material.across_pct,
            "stretch_pct": 100 * float(stretch[most]),
            "stretch_is": "pull" if values[most][pick[most]] > 0 else "push",
            "stretch_at": _list(self.points[rows[most]], 3),
            "stretch_from_layers_deg": math.degrees(
                math.asin(min(1.0, abs(float(along @ way))))
            ),
            "stretch_limit_pct": part.material.along_pct,
            "nodes": len(rows),
        }

    def joint_read(self, joint: Joint) -> dict[str, Any]:
        """What a joint carries, as the force and the moment the second part puts on the first:
        the nodes' own balance on the faces that meet (`element_forces`)."""
        import FreeCAD as App

        names = [p.name for p in self.assembly.parts]
        first = self.solids[names.index(joint.between[0])]
        pieces = []
        of_face = []
        for index, face in enumerate(self.joint_faces[joint.name]):
            triangles = self._triangles(face)
            if not triangles:
                continue
            for nodes, area, centre in triangles:
                a, b, c = (self.nodes[n] for n in nodes[:3])
                normal = np.cross(b - a, c - a)
                normal /= np.linalg.norm(normal)
                # Out of the first part, into the second.
                inside = centre - 1e-3 * normal
                if not first.isInside(App.Vector(*inside), 1e-6, False):
                    normal = -normal
                pieces.append((nodes, area, centre, normal))
                of_face.append(index)
        if not pieces:
            raise NoAnswer(
                f"the mesh has no triangle on the joint {joint.name}: give a finer --mesh"
            )
        total = sum(p[1] for p in pieces)
        centre = np.sum([p[1] * p[2] for p in pieces], axis=0) / total
        mean = np.sum([p[1] * p[3] for p in pieces], axis=0)
        flat = float(np.linalg.norm(mean)) > (1 - 1e-6) * total
        mean = mean / np.linalg.norm(mean) if np.linalg.norm(mean) > 1e-9 else np.zeros(3)
        # By the surface's stress, roughly: how much of the faces is pulled, and how much pushed.
        pull, push = 0.0, 0.0
        for nodes, area, _, normal in pieces:
            for node in nodes[3:]:
                square = float(normal @ self.stress[self.row[node]] @ normal) * area / 3
                pull, push = pull + max(square, 0.0), push + max(-square, 0.0)
        # What the second part puts on the first: at each node of the faces that meet, the
        # force the first part's own elements carry, less what's put on it there from outside.
        rows = sorted(
            {self.row[n] for f in self.joint_faces[joint.name] for n in self._face_nodes(f)}
        )
        name = joint.between[0]
        carried = self.within[name][rows] - self.outside[name][rows]
        force = carried.sum(axis=0)
        moment = np.cross(self.points[rows] - centre, carried).sum(axis=0)
        outline = np.array(
            [
                self.nodes[n]
                for f in self.joint_faces[joint.name]
                for n in self._face_nodes(f)
            ]
        )
        return {
            "kind": joint.kind,
            "at": centre,
            "normal": mean,
            "flat": flat,
            "area_mm2": total,
            "force": force,
            "moment": moment,
            "pulled_n": pull,
            "pushed_n": push,
            "outline": outline,
            "faces": list(self.joint_faces[joint.name]),
            # Each triangle of the faces: its centre, its way out of the first part, its area
            # and which face it's on.
            "pieces": (
                np.array([p[2] for p in pieces]),
                np.array([p[3] for p in pieces]),
                np.array([p[1] for p in pieces]),
                np.array(of_face),
            ),
        }

    def loads_read(self) -> list[dict[str, Any]]:
        found = []
        for load in self.loads:
            moved = self.moved[[self.row[n] for n in load["nodes"] if n in self.row]].mean(axis=0)
            # A part's own weight says its volume and its mass too.
            # And where it hangs without its glass band, the band's weight it carries still
            # (mqucifer/print-shop#375).
            own: dict[str, Any] = {
                k: load[k] for k in ("volume_mm3", "grams", "band_grams", "band_mm2") if k in load
            }
            found.append(
                {
                    "at": _list(load["at"], 3),
                    "part": load["part"],
                    "force_n": _list(load["force"]),
                    "patch_mm2": load["patch_mm2"],
                    "middle": _list(load["middle"], 3),
                    "moved": _list(moved),
                    "moves_mm": float(moved @ self.toward),
                    **own,
                }
            )
        return found


def _reaction(path: str) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """The held nodes' force as CalculiX printed it: the total, and each node's."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise NoAnswer("CalculiX wrote no table of the held faces' reaction") from exc
    total = re.search(r"total force.*?HELDALL.*?\n\s*\n\s*(\S+)\s+(\S+)\s+(\S+)", text, re.S)
    each = re.search(r"\n\s*forces \(fx,fy,fz\).*?HELDALL.*?\n\s*\n(.*?)\n\s*\n", text, re.S)
    if not total or not each:
        raise NoAnswer("CalculiX's table has no reaction for the held faces")
    nodes = {}
    for line in each.group(1).splitlines():
        words = line.split()
        nodes[int(words[0])] = np.array([float(v) for v in words[1:4]])
    return np.array([float(v) for v in total.groups()]), nodes


def _printed(path: str, row: dict[int, int]) -> np.ndarray:
    """Each node's movement as CalculiX printed it to seven figures, by `row`."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise NoAnswer("CalculiX wrote no table of the nodes' movements") from exc
    table = r"displacements \(vx,vy,vz\) for set NALL.*?\n\s*\n(.*?)(?:\n\s*\n|\Z)"
    found = re.search(table, text, re.S)
    if not found:
        raise NoAnswer("CalculiX's table has no movement for the nodes")
    moved = np.full((len(row), 3), np.nan)
    for line in found.group(1).splitlines():
        words = line.split()
        if int(words[0]) in row:
            moved[row[int(words[0])]] = [float(v) for v in words[1:4]]
    if np.isnan(moved).any():
        raise NoAnswer("CalculiX's table of the nodes' movements leaves some out")
    return moved


def _arrays(result: Any) -> tuple[dict[int, int], np.ndarray, np.ndarray, np.ndarray]:
    """A CalculiX result as each node's row, and its strain, stress and movement by row."""
    row = {n: i for i, n in enumerate(result.NodeNumbers)}
    count = len(result.NodeNumbers)
    strain, stress = np.zeros((count, 3, 3)), np.zeros((count, 3, 3))
    for (i, j), name in {
        (0, 0): "XX", (1, 1): "YY", (2, 2): "ZZ", (0, 1): "XY", (0, 2): "XZ", (1, 2): "YZ"
    }.items():  # fmt: skip
        strain[:, i, j] = strain[:, j, i] = getattr(result, f"NodeStrain{name}")
        stress[:, i, j] = stress[:, j, i] = getattr(result, f"NodeStress{name}")
    moved = np.array([[v.x, v.y, v.z] for v in result.DisplacementVectors])
    return row, strain, stress, moved


#: CalculiX's iterative solver, for a mesh its direct one can't hold (mqucifer/print-shop#301).
ITERATIVE = "*STATIC, SOLVER=ITERATIVE CHOLESKY"
#: It's corrected at most this many times, until what's left unbalanced, summed over the nodes,
#: is under this share of the load summed the same way.
CORRECTIONS = 4
RESIDUAL = 1e-7


def _cload(text: str, left: np.ndarray, row: dict[int, int]) -> str:
    """A CalculiX input with its loads replaced by what's left unbalanced at each node."""
    start = text.find("*CLOAD\n")
    end = text.find("\n*", start + 1)
    if start < 0 or end < 0:
        raise NoAnswer("the CalculiX input has no *CLOAD to correct")
    lines = "".join(
        f"{node},{axis + 1},{left[r, axis]:.12g}\n"
        for node, r in sorted(row.items())
        for axis in range(3)
        if left[r, axis] != 0
    )
    return text[:start] + "*CLOAD\n" + lines + text[end + 1 :]


#: The container's own cgroup, where the kernel counts what it stopped for memory.
CGROUP = Path("/sys/fs/cgroup")


def oom_kills(cgroup: Path = CGROUP) -> int | None:
    """How many processes the kernel has stopped in the container for memory: `oom_kill` in
    its cgroup's `memory.events`. None where the kernel keeps no count."""
    try:
        text = (cgroup / "memory.events").read_text()
    except OSError:
        return None
    found = re.search(r"^oom_kill (\d+)$", text, re.M)
    return int(found.group(1)) if found else None


def memory_met(before: int | None, cgroup: Path = CGROUP) -> int | None:
    """The container's memory limit, in bytes, where the kernel stopped a process there for
    memory since `before`, its `oom_kill` count then (mqucifer/print-shop#295): so a solve is
    said to have met the limit only where something was stopped for it while it ran, and not
    for what the container held at the most, which counts files read since it started. None
    where nothing was, or the kernel keeps no count or limit."""
    after = oom_kills(cgroup)
    if before is None or after is None or after <= before:
        return None
    try:
        most = (cgroup / "memory.max").read_text().strip()
    except OSError:
        return None
    return int(most) if most.isdigit() else None


def _mesh_size(placed: list[Any], given: float | None) -> float:
    if given is not None:
        return float(given)
    thin = min(min(s.BoundBox.XLength, s.BoundBox.YLength, s.BoundBox.ZLength) for s in placed)
    volume = sum(s.Volume for s in placed)
    # About 8.5 tetrahedra fill a cube of their own edge.
    budget = (8.5 * volume / ELEMENTS) ** (1 / 3)
    return float(round(max(MESH_LEAST, budget, thin / ACROSS), 3))


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """The workbench prints as it goes, and a reply's first line is its verdict."""
    said_before = os.dup(1)
    sys.stdout.flush()
    with open(os.devnull, "w") as nowhere:
        os.dup2(nowhere.fileno(), 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(said_before, 1)
        os.close(said_before)


#: A straight-sided triangle lies on a face where its middle is within this, in mm, beyond its
#: longest side's sag, and its normal within 60° of the face's (`lies_on`).
ON_SAG, ON_NORMAL = 1e-4, 0.5


def as_joined(face: Any) -> Any:
    """A face where two parts meet, as the joined parts hold it (mqucifer/print-shop#334): within
    the greatest tolerance of the face, its edges and its corners. FreeCAD finds a node on a face
    within the face's own tolerance only, but where two parts' faces meet a little more than
    OCC's 1e-7 mm apart, as a part's do when its turns place it a hair off its neighbours', the
    fuse shares them and holds their edges to more than that: gmsh's nodes along those edges
    then lie off the face by more than its own tolerance, the mesh's triangles there weren't
    found, and the part was taken for one with its neighbours. A face its edges and corners hold
    no looser is itself."""
    import Part

    held = max([face.Tolerance, *(e.Tolerance for e in face.Edges),
                *(v.Tolerance for v in face.Vertexes)])  # fmt: skip
    if held <= face.Tolerance:
        return face
    found = face.copy()
    found.fixTolerance(held, Part.Face)
    return found


def lies_on(corners: np.ndarray, nearest: np.ndarray, normal: np.ndarray, bend: float) -> bool:
    """Whether a straight-sided triangle whose corners are all on a face lies on it
    (mqucifer/print-shop#319): its middle is within its longest side's sag of the face's surface,
    `nearest` the point of it nearest the middle, and its normal within 60° of the surface's
    there, `normal`, `bend` its greatest curvature there. A triangle across the face beside it,
    as on a peg's end whose corners are all on the round edge it shares with its side, lies
    square to that side however near its middle is."""
    a, b, c = (np.asarray(p, dtype=float) for p in corners)
    across = np.cross(b - a, c - a)
    size, way = float(np.linalg.norm(across)), float(np.linalg.norm(normal))
    if size <= 1e-12 or way <= 1e-12:
        return False
    longest = max(float(np.linalg.norm(q - p)) for p, q in ((a, b), (b, c), (c, a)))
    sag = longest**2 * abs(bend) / 8
    off = float(np.linalg.norm((a + b + c) / 3 - np.asarray(nearest, dtype=float)))
    return off <= sag + ON_SAG and abs(float(across @ normal)) >= ON_NORMAL * size * way


@contextlib.contextmanager
def step(what: str) -> Iterator[None]:
    """What `load` was doing, said with anything it couldn't do there (mqucifer/print-shop#319):
    a reason it gives itself is said as it is, and anything else as `NoAnswer` naming the step,
    and the part or the joint, where it stopped. Never a traceback's last line alone. And said
    as it begins, with the container's memory, so a command killed for memory says where it was
    (`progress`, mqucifer/print-shop#336). As it ends, what FreeCAD cached of the faces it read
    is let go (`forget`), and what was freed is given back to the kernel, for the steps after it
    and the solvers they start (`cae.give_back`)."""
    try:
        with progress.step(what):
            yield
    except NoAnswer:
        raise
    except MemoryError as met:
        # Python's own allocation failed: the whole unread for memory (mqucifer/print-shop#376),
        # only where the container's room is short of it (the operator's review).
        if not _short_of(met):
            raise NoAnswer(f"load stopped {what}: MemoryError: {met}"[:500]) from met
        evidence = f"load's own process met the container's memory, {what}"
        raise _Unread(
            f"nothing of the assembly is read, as {evidence}. Every part, joint and bolt of it is "
            "left unread",
            evidence,
            memory=True,
        ) from met
    except Exception as exc:  # noqa: BLE001 - whatever stops a step is said with the step
        raise NoAnswer(f"load stopped {what}: {type(exc).__name__}: {exc}"[:500]) from exc
    finally:
        forget()
        give_back()


#: numpy's words for what it couldn't allocate, and each unit's bytes.
ASKED = re.compile(r"Unable to allocate ([\d.]+) (B|KiB|MiB|GiB|TiB|PiB|EiB)\b")
UNITS = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "PiB": 2**50,
         "EiB": 2**60}  # fmt: skip


def _short_of(met: MemoryError) -> bool:
    """Whether a `MemoryError` was the container's memory: off the container, never; where
    numpy says what it asked for, where that's within the container's limit and past its room;
    and where it doesn't say, where the room is gone. An array no container could hold is a
    plain error (the operator's review of mqucifer/print-shop#376)."""
    left = cae.room()
    if left is None:
        return False
    found = ASKED.search(str(met))
    if found is None:
        return left <= 0
    asked = float(found.group(1)) * UNITS[found.group(2)]
    most = cae.memory_max()
    return most is not None and asked <= most and asked > left


#: gmsh's lines that only sum up its errors.
_SUMMED = re.compile(
    r"^(-+|Mesh generation error summary|\d+ (warning|error)s?|Check the full log.*)$"
)


#: gmsh's words where its surface mesh crosses itself (mqucifer/print-shop#346).
CROSSED = re.compile(r"intersect", re.I)
#: How many of the faces nearest where gmsh's triangles cross are named.
NEAREST = 4
#: CalculiX's words for each element it finds turned inside out, with its number.
INVERTED = re.compile(r"determinant in element\s+(\d+)")
#: How many times the parts are meshed again finer where gmsh's triangles cross, each time
#: where it says they cross again.
RETRIES = 4


def _gmsh_said(tools: Any) -> str:
    """gmsh's own error lines, from what FreeCAD's tools kept of what it printed."""
    said = getattr(tools, "said", None)
    if said is None:
        said = bytes(tools.process.readAllStandardOutput()).decode(errors="replace")
        said += bytes(tools.process.readAllStandardError()).decode(errors="replace")
    return "; ".join(_gmsh_errors(said)) or "it gave no reason"


def _crossed_at(words: str) -> Any:
    """Where gmsh says its triangles cross, as a vertex: none where it gives no point."""
    found = re.search(
        r"(?:intersection|at point) \(([-\d.e+]+), ?([-\d.e+]+), ?([-\d.e+]+)\)", words
    )
    if found is None:
        return None
    import FreeCAD as App
    import Part

    return Part.Vertex(App.Vector(*(float(v) for v in found.groups())))


def _gmsh_errors(said: str) -> list[str]:
    """gmsh's own error lines in what it printed, each once, less its summary of them."""
    found = []
    for line in said.splitlines():
        if line.startswith("Error"):
            text = line.split(":", 1)[-1].strip()
            if text and not _SUMMED.match(text) and text not in found:
                found.append(text)
    return found


class _Unread(NoAnswer):
    """The whole assembly can't be read: it meets the container's memory, or no mesh of it
    solves. A part that names its glass band is read again without it (mqucifer/print-shop#295).
    `evidence` is what it met, without what to do."""

    def __init__(self, said: str, evidence: str | None = None, memory: bool = False) -> None:
        super().__init__(said)
        self.evidence = said if evidence is None else evidence
        #: Whether it met the container's memory, which two passes read within
        #: (mqucifer/print-shop#301).
        self.memory = memory


class _Unparted(NoAnswer):
    """A mesh that gives parts no element of their own (mqucifer/print-shop#319): `empty`, their
    names."""

    def __init__(self, said: str, empty: list[str]) -> None:
        super().__init__(said)
        self.empty = empty


def _solve_need(
    fem: Any, falls_back: bool, low: tuple[int, int], high: tuple[int, int]
) -> float:
    """A step's need by the mesh's counts, from what it took a node and an element, each as if
    alone (`WRITE_NODE` and the rest): where a lower bound can follow, a half above the more of
    the two; anywhere else, a tenth under the less (mqucifer/print-shop#376)."""
    nodes, elements = int(fem.NodeCount), int(fem.VolumeCount)
    if falls_back:
        return 1.5 * max(nodes * high[0], elements * high[1])
    return 0.9 * min(nodes * low[0], elements * low[1])


def _banded_to_do() -> str:
    """What to do where the whole is past the memory and its glass bands are named
    (mqucifer/print-shop#376): never to name them."""
    if _bands:
        return (
            "with each glass band left out already, it's past the container's memory still: "
            "read fewer of the parts at once"
        )
    return (
        "each glass frame's band is named, and it's read next with its band left out, as a "
        "lower bound"
    )


def _model_unread(model: Any, evidence: str) -> NoReturn:
    """`_memory_unread`, for a model: banded where any of its parts names its glass band."""
    parts = getattr(getattr(model, "assembly", None), "parts", [])
    _memory_unread(evidence, any(getattr(p, "band", None) is not None for p in parts))


def _memory_unread(evidence: str, banded: bool = False) -> NoReturn:
    """The whole unread for the container's memory, `evidence` what it met
    (mqucifer/print-shop#375, mqucifer/print-shop#376), with what to do: `banded` where its
    glass bands are named."""
    to_do = (
        _banded_to_do()
        if banded
        else "name each glass frame's `band` in the assembly file, and it's read as a lower "
        "bound with its band left out, or give a coarser --mesh, or read fewer of the parts at "
        "once"
    )
    raise _Unread(
        f"nothing of the assembly is read, as its mesh is past what the container's memory "
        f"holds: {to_do}. {evidence}. Every part, joint and bolt of it is left unread",
        evidence,
        memory=True,
    )


class _Slow(_Unread):
    """The one pass wouldn't end inside the container's time, where two passes, under the hour
    the host offered, can read the assembly (mqucifer/print-shop#331). `whole` is how long the
    one pass's whole took at the mesh, in s, where it was read: two passes' clock takes it as
    what a whole takes at the most."""

    def __init__(self, said: str, whole: float) -> None:
        super().__init__(said)
        self.whole = whole


#: How long before the container's limit the one pass stops to go on to two passes, in s: the
#: hand-off to the host, from the stop to the host reading `load_parts.TAKEN`, as the one pass
#: takes up the hour before anything is tidied (mqucifer/print-shop#331). It's the container's
#: start after the host's clock began, and the line's way out: 0.16 to 0.28 s over nine runs of
#: the planted stool on the Mac, stopped in CalculiX's solve (`proofs/ux-331/handoff.py`), and
#: this is about seven times that. A one pass that would have ended in its last 2 s goes to two
#: passes; one whose hand-off took longer is stopped by the host, which names `--parts`.
ONE_PASS_MARGIN = 2.0


class _OnePass:
    """The one pass's clock, where two passes could read the assembly under the hour the host
    offered (mqucifer/print-shop#331). Every step it can stop at is asked before it starts, as
    two passes' clock is, never interrupted, but for CalculiX, a process of its own:
    - before a reading round a bolt, at the longest so far (`check`);
    - before the coarser mesh of the whole, at what the whole at the mesh took before its solve
      and after it, which is no less than the coarser's (`check_coarser`);
    - CalculiX's solve of the whole is given what's left, less what the whole's reading after its
      solve took, and stopped there (`_ccx`).
    Each way the one pass takes up the hour at once and goes on to two passes, before the host
    would stop it, and says why. Past what it can foresee, as the whole's first mesh, or a
    reading round the first bolt, running past the limit, the host stops the command and names
    `--parts`."""

    def __init__(self, started: float, limit: float) -> None:
        self.started = started
        self.limit = limit
        self.longest: dict[str, float] = {}
        #: When the whole at the mesh began, and its CalculiX solve began and ended, where it has.
        self.whole_began: float | None = None
        self.ccx_began: float | None = None
        self.ccx_ended: float | None = None
        #: What the whole at the mesh took before its solve and after it, in s.
        self.before = 0.0
        self.after = 0.0

    def time(self, kind: str, seconds: float) -> None:
        self.longest[kind] = max(self.longest.get(kind, 0.0), seconds)

    def left(self) -> float:
        return self.limit - ONE_PASS_MARGIN - (time.time() - self.started)

    def check(self, kind: str, what: str) -> None:
        if self.left() < self.longest.get(kind, 0.0):
            raise self.stop(f"before {what}, which would end past it")

    def whole_read(self) -> None:
        """The whole at the mesh is read: what it took in all, and before and after its solve."""
        assert self.whole_began is not None, "a whole is timed from when it began"
        now = time.time()
        self.time("whole", now - self.whole_began)
        if self.ccx_began is not None and self.ccx_ended is not None:
            self.before = self.ccx_began - self.whole_began
            self.after = now - self.ccx_ended

    def check_coarser(self, size: float) -> None:
        if self.left() < self.before + self.after:
            raise self.stop(
                f"before the whole's coarser mesh, at {size:g} mm, whose meshing and reading "
                f"would end past it, as the mesh's took {self.before + self.after:.0f} s"
            )

    def stop(self, where: str) -> _Slow:
        """Why the one pass stops, where it stops: the hour is taken up at once, before
        anything is tidied, so the host waits for two passes."""
        from . import load_parts

        load_parts.take_longer()
        now = time.time()
        spent = now - self.started
        # The whole's time for two passes' clock: what it took, or, stopped in it, what it had
        # taken by then.
        whole = self.longest.get("whole", 0.0)
        if not whole and self.whole_began is not None:
            whole = now - self.whole_began
        return _Slow(
            f"the one pass wouldn't end inside the container's {self.limit:g} s: it had run "
            f"{spent:.0f} s, {where}",
            whole,
        )


def _one_pass(
    assembly: Assembly, started: float, two_pass: str | None, without_bands: bool
) -> None:
    """Where this is the one pass, the host offered two passes the hour, and the assembly can go
    to them, more than one part, the one pass runs on `_OnePass`'s clock, and the host is told
    that two passes could read it (`load_parts.ABLE`), so that if it stops the one pass itself it
    names `--parts` (mqucifer/print-shop#331). Otherwise nothing changes: two passes and a glass
    band's read keep their own clocks; a single part, a script, or a limit the caller gave, keeps
    its own, and is stopped there as before."""
    from . import load_parts

    if two_pass is not None or without_bands:
        return
    given = os.environ.get(load_parts.TIMEOUT, "")
    if len(assembly.parts) < 2 or not os.environ.get(load_parts.OFFER, "").isdigit():
        return
    if not given.replace(".", "", 1).isdigit():
        return
    print(load_parts.ABLE, file=sys.stderr, flush=True)
    _clock["now"] = _OnePass(started, float(given))


def _on_one_pass() -> _OnePass | None:
    clock = _clock["now"]
    return clock if isinstance(clock, _OnePass) else None


def _ccx(fea: Any, size: float) -> None:
    """CalculiX's solve of the whole, as `ccx_one_thread` runs it. On the one pass's clock, it's
    given what's left, less what the whole's reading after its solve took, and where it's stopped
    there the one pass goes on to two passes (mqucifer/print-shop#331): the hour first, then the
    solve's files tidied. Anywhere else, as before."""
    clock = _on_one_pass()
    if clock is None:
        ccx_one_thread(fea)
        return
    clock.ccx_began = time.time()
    try:
        ccx_one_thread(fea, timeout=clock.left() - clock.after)
    except subprocess.TimeoutExpired:
        late = clock.stop(f"in CalculiX's solve of the whole at {size:g} mm")
        tidy_solve(fea)
        raise late from None
    clock.ccx_ended = time.time()


#: The shapes one `load` keeps while it reads, as the parts as placed and joined
#: (mqucifer/print-shop#336). FreeCAD caches what it reads of each face of a shape on the shape:
#: on a design run's whole table, reading the faces of its 37 parts once, as the bolts' ways
#: do, took 1.6 GiB, held until the parts were.
_kept: list[Any] = []
#: Of them, the parts as placed, kept for the whole read whatever else is let go.
_placed_now: list[Any] = []
#: What each part's glass band took from it, where the parts are placed without their bands, by
#: the part's name (`_placed`, mqucifer/print-shop#375).
_bands: dict[str, Band] = {}
#: Which of CalculiX's solvers read the last read's mesh and its coarser one (`_run`).
_solvers: list[str] = []


def keep(shapes: list[Any]) -> None:
    """Shapes this `load` keeps while it reads, whose faces' cache `forget` lets go."""
    _kept.extend(shapes)


def forget() -> None:
    """What FreeCAD cached of the kept shapes' faces, let go: it's read again as it's asked."""
    close_faces._let_go(*_kept)


#: The parts joined, by the shapes joined, while one `load` runs (mqucifer/print-shop#336): each
#: key the shapes' own ids, and each entry the shapes, held so no id is used again, and the join.
_joined: dict[tuple[int, ...], tuple[list[Any], Any]] = {}


def fused(placed: list[Any]) -> Any:
    """The parts as one body, the faces that meet shared: OCC's general fuse of them. The same
    shapes join to the same body, so one `load` joins each set of them once, and reads each
    mesh of it from that: on a design run's whole table each join took 1,000 to 1,200 s of the
    hour, and two passes join the parts as placed twice, for the way in and the second pass,
    and with their bores squared twice, at the mesh and the coarser one. Six plates of 2,310
    faces each took 340 s to join, and 9 s again."""
    if len(placed) == 1:
        return placed[0]
    key = tuple(id(s) for s in placed)
    if key not in _joined:
        _joined[key] = (list(placed), placed[0].generalFuse(placed[1:])[0])
        keep([_joined[key][1]])
    return _joined[key][1]


#: What's measured of each joined shape's faces where gmsh's triangles crossed
#: (`close_faces.found`, mqucifer/print-shop#346), by the shape's hash: each entry the shape,
#: held so no hash is another's, and how far apart its pairs of faces are.
_apart: dict[int, tuple[Any, dict[tuple[int, int], float]]] = {}


def _measured(shape: Any) -> dict[tuple[int, int], float]:
    """How far apart `shape`'s pairs of faces were measured, so far, while this `load` runs:
    the same parts joined are asked of at two meshes, curved and straight, and on the made-up
    traced glass frame of `test_cae.py`, 2,400 faces, measuring them took 50 s. The shape is
    kept as the joins are (`keep`), so what FreeCAD caches of its faces is let go as each step
    ends."""
    key = shape.hashCode()
    held = _apart.get(key)
    if held is None or not held[0].isSame(shape):
        held = _apart[key] = (shape, {})
        keep([shape])
    return held[1]


def unjoined(placed: list[Any]) -> None:
    """The join of `placed` let go, once nothing more is read from it."""
    let_go = _joined.pop(tuple(id(s) for s in placed), None)
    if let_go is not None:
        for key, (shape, _) in list(_apart.items()):
            if shape.isSame(let_go[1]):
                del _apart[key]
                _kept[:] = [s for s in _kept if s is not shape]
        # A part the squaring left as it was is a part as placed still, and stays kept.
        gone = [t for t in (*let_go[0], let_go[1]) if all(t is not b for b in _placed_now)]
        _kept[:] = [s for s in _kept if all(s is not t for t in gone)]


class Faces:
    """Faces by their hash, so whether a face is one of them is asked, by `isSame`, of the few
    with its hash and not of every one (mqucifer/print-shop#336). OCC's hash of a shape is its
    own and its place's, as `isSame` compares them, so the answer is the same: on a design run's
    whole table, of 37 parts and up to 2,565 faces each, asking every face of each part of
    every face of each other is some 250 million asks, at 2.6 µs each in the image."""

    def __init__(self, faces: list[Any]) -> None:
        self.by: dict[int, list[Any]] = {}
        for face in faces:
            self.by.setdefault(face.hashCode(), []).append(face)

    def has(self, face: Any) -> bool:
        return any(face.isSame(f) for f in self.by.get(face.hashCode(), ()))


class _Curved(NoAnswer):
    """The mesh with its elements' sides curved onto the parts' faces has an element turned
    inside out, or none at all: it's meshed again with them straight. With the pairs of faces
    the curved try found close (mqucifer/print-shop#346), so the straight one starts from them:
    the same parts at the same mesh, and gmsh's triangles cross the same way."""

    def __init__(
        self, said: str, closes: list[close_faces.Close] | None = None, tight: int = 0
    ) -> None:
        super().__init__(said)
        self.closes = list(closes or [])
        self.tight = tight


def curved_said(why: str) -> str:
    """Why the curved try didn't stand, in a few words, for the step that follows it: what met
    it, never a figure or a name."""
    if why.startswith("CalculiX"):
        return "CalculiX found an element turned inside out"
    if "made no curved mesh" in why:
        return "gmsh made no curved mesh" + (
            ", with faces meshed finer where they're close" if "meshed again" in why else ""
        )
    return "it didn't stand"


def _solved(
    assembly: Assembly,
    placed: list[Any],
    args: Any,
    size: float,
    two_pass: bool = False,
    weighed: list[Any] | None = None,
    spread: bool = True,
    cores: dict[str, list[Any]] | None = None,
    coarser_of: tuple[list[Any], int] | None = None,
) -> dict[str, Any]:
    """One solve at one mesh, and everything read from it. Where gmsh can't curve every
    element's sides onto the parts' faces without turning one inside out, as on a traced
    glass frame's many small curves and sharp corners (mqucifer/print-shop#295), it's meshed
    again with them straight, and `straight` says why: every mesh that solves curved is as it
    was. Given `coarser_of`, the finer mesh's close pairs and tight faces, it's that mesh's
    coarser one: meshed on the same pairs from the start, at their own size where the finer
    is meshed finer still (`_Model.local_scale`, mqucifer/print-shop#346)."""
    import FreeCAD as App

    # One thread, so the same parts are the same mesh, and the same figures, every time.
    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    def model(straight: bool) -> _Model:
        made = _Model(assembly, placed, args, size, straight, two_pass, spread, cores)
        if weighed is not None:
            made.weighed = weighed
        coarser(made, coarser_of)
        return made

    try:
        doc = App.newDocument("Load")
        with _quiet():
            return _read_solved(assembly, model(False), doc, size)
    except _Curved as curved:
        why, closes, tight = str(curved), curved.closes, curved.tight
    # Meshed again outside the `except`, so what the curved try held, its model and its mesh,
    # which its traceback keeps, is let go first (mqucifer/print-shop#336): held through the
    # straight try, a design run's whole table had 0.8 GiB more in use there.
    give_back()
    with step(f"meshing again straight-sided, as the curved mesh didn't stand: {curved_said(why)}"):
        pass
    doc = App.newDocument("Load")
    straight = model(True)
    straight.closes, straight.tight = closes, tight
    try:
        with _quiet():
            found = _read_solved(assembly, straight, doc, size)
    except NoAnswer as stopped:
        # What turned the curved try away is said too, as the reply would have said it.
        stopped.args = (f"{stopped}; before it, the curved mesh: {why[:600]}", *stopped.args[1:])
        raise
    found["straight"] = why
    return found


def coarser(model: _Model, of: tuple[list[Any], int] | None) -> None:
    """A model made the coarser mesh of a finer one's close pairs and tight faces, `of`, where
    one's given: meshed on them from the start, each at the size that meshes it."""
    if of is not None:
        model.closes, model.tight, model.local_scale = list(of[0]), of[1], 1.0


def _read_solved(assembly: Assembly, model: _Model, doc: Any, size: float) -> dict[str, Any]:
    import FreeCAD as App

    mesh = f"a mesh of at most {size:g} mm" + (STRAIGHT if model.straight else "")
    try:
        with step("joining the parts"):
            model.join(doc)
        with step("finding the faces the assembly is held by"):
            model.hold()
        with step(f"meshing and solving the joined parts, at {mesh}"):
            model.solve(doc)
        joints = {}
        for joint in assembly.joints:
            with step(f"reading the joint {joint.name}, at {mesh}"):
                joints[joint.name] = model.joint_read(joint)
        with step(f"reading the loaded places, at {mesh}"):
            loads = model.loads_read()
        parts = model.parts_read(mesh)
        far = int(np.linalg.norm(model.moved, axis=1).argmax())
        return {
            "mesh_mm": size,
            "parts": parts,
            "joints": joints,
            "loads": loads,
            "raw_loads": model.loads,
            "held_parts": model.held_parts,
            "held": model.held_said,
            "reaction": model.reaction,
            "toward": model.toward,
            "most_moves_mm": float(np.linalg.norm(model.moved[far])),
            "most_moves_at": _list(model.points[far], 3),
            "nodes": model.fem.NodeCount,
            "elements": model.fem.VolumeCount,
            "mesh_s": model.mesh_s,
            "solve_s": model.solve_s,
            "straight": None,
            "finer": model.finer_said(),
            "closes": (model.closes, model.tight),
            "iterated": model.iterated,
            "planes": model.planes,
            **({"tractions": model.tractions()} if model.two_pass else {}),
        }
    finally:
        App.closeDocument(doc.Name)


def _straight_said(solved: dict[str, Any]) -> str:
    """Where a mesh's elements are straight-sided, why (mqucifer/print-shop#295); and where
    it's finer where faces are close (mqucifer/print-shop#346)."""
    return _finer_said(solved) + _only_straight_said(solved)


def _finer_said(solved: dict[str, Any]) -> str:
    finer = solved.get("finer")
    if not finer:
        return ""
    return (
        f", finer, to at most {finer['finest_mm']:g} mm, on {finer['faces']} face(s) where "
        f"{finer['pairs']} pair(s) of faces that meet nowhere lie closer than its triangles' "
        f"sag, the closest {finer['closest_mm']:g} mm apart, and nowhere finer than "
        f"{finer['least_mm']:g} mm"
        + (
            f": {finer['tight_faces']} face(s) curve tighter than a print's round, and are "
            "meshed at that"
            if finer.get("tight_faces")
            else ""
        )
    )


def _only_straight_said(solved: dict[str, Any]) -> str:
    if not solved.get("straight"):
        return ""
    return (
        f", its elements second-order with straight sides, mid-side nodes halfway along them, "
        "so each curved face is read as their chords: "
        f"{solved['straight']}"
    )


def _bolts(joint: Joint, read: dict[str, Any], force: np.ndarray, moment: np.ndarray) -> list[Any]:
    normal = read["normal"]
    bolts = np.array(joint.at, dtype=float)
    # Each bolt where its axis crosses the faces' plane.
    bolts = bolts - np.outer((bolts - read["at"]) @ normal, normal)
    pulls = bolt_pulls(force, moment, read["at"], normal, bolts, read["outline"])
    return [{"at": _list(b, 3), **p} for b, p in zip(bolts, pulls, strict=True)]


def run(args: Any, report: Report) -> None:
    """The assembly read whole; or, where it meets the container's memory, in two passes
    (mqucifer/print-shop#301), and where it wouldn't end inside the container's time, with two
    passes offered the hour (mqucifer/print-shop#331); or, where neither reads it, and parts of
    it name their glass band, read again with each band left out, as a lower bound
    (mqucifer/print-shop#295). The reply leads with that, names the parts and says why."""
    _clock["now"] = None
    # The container's memory said as it goes, for the host to say where it was killed
    # (mqucifer/print-shop#336).
    progress.watch()
    # A killed command's mesh and solve files, left on disk, go (mqucifer/print-shop#376).
    cae.clear_stale()
    try:
        _read(args, report)
    finally:
        cae.clear_own()
        _clock["now"] = None
        _joined.clear()
        _kept.clear()
        _placed_now.clear()
        _bands.clear()
        progress.stop()


def _read(args: Any, report: Report) -> None:
    if getattr(args, "parts", False):
        _parts_read(args, report)
        return
    began = time.time()
    try:
        _run(args, report)
        return
    except _Unread as whole:
        unread = whole
    # The one pass's clock, where it had one, is done with.
    _clock["now"] = None
    tried = time.time() - began
    failed: NoAnswer | None = None
    banded = [p.name for p in read(args.assembly).parts if p.band is not None]
    # Where the one pass wouldn't end inside the container's time, it goes on to two passes as
    # it does on memory, under the hour (mqucifer/print-shop#331).
    slow = isinstance(unread, _Slow)
    if unread.memory or slow:
        # Read in two passes, inside the container's limits as they are
        # (mqucifer/print-shop#301), before any part is read without its band. Where a part
        # names one, they're given the container's time less what the one pass's try took,
        # held back for the band's lower bound, which reads a mesh smaller than that try's:
        # so the lower bound is never lost to two passes that can't end in time.
        from . import load_parts
        from .load_parts import OutOfTime

        # Two passes run under the hour the host offered (mqucifer/print-shop#325), and so does
        # what follows them here: taken up before anything's tidied. It counts from the
        # command's start, so the one pass's try is in it. A one pass that ran out of time took
        # it up as it stopped (`_OnePass.stop`), and this does nothing more.
        load_parts.take_longer()
        _give_back()
        report.again()
        try:
            _run(
                args,
                report,
                two_pass=(
                    unread.evidence
                    if slow
                    else "the whole, read at once, met the container's memory"
                ),
                reserve=load_parts.RESERVE * tried if banded else 0.0,
                # A try that ran out of time was mostly not the whole: what its whole took.
                tried=unread.whole if isinstance(unread, _Slow) else tried,
            )
            key = "one_pass_late" if slow else "whole_met"
            report.numbers["two_passes"][key] = unread.evidence
            return
        except OutOfTime as late:
            if not banded:
                raise
            failed = late
        except NoAnswer as again:
            failed = again
    if not banded:
        raise failed or unread
    _band_read(
        args,
        report,
        banded,
        unread.evidence + (f"; and in two passes, {failed}" if failed else ""),
        (
            "its one pass wouldn't end inside the container's time, and two passes were tried "
            "first"
            if slow
            else "its whole met the container's memory, and two passes were tried first"
            if unread.memory
            else "its whole couldn't be read"
        ),
        tried,
        "its read, which a mesh no larger than the one pass's try takes",
    )


def _parts_read(args: Any, report: Report) -> None:
    """`load --parts`: two passes, asked for (mqucifer/print-shop#301). Where they can't read
    the whole, by the container's time or its memory, or where no mesh of it reads, and parts of
    it name their glass band, each is read again with its band left out, as the one pass's two
    passes are followed (mqucifer/print-shop#375). With no one pass's try to time the band's
    read by, two passes are given the container's time less `load_parts.RESERVE` times a one
    pass's limit, `load_parts.ONE_PASS`, held back for it. Anything else two passes say, they
    say: a whole that reads is read whole."""
    from . import load_parts

    try:
        banded = [p.name for p in read(args.assembly).parts if p.band is not None]
    except NoAnswer:
        # Said by `_run`, after what it says of the command's own words.
        banded = []
    began = time.time()
    try:
        _run(
            args,
            report,
            two_pass="asked for with --parts",
            reserve=load_parts.RESERVE * load_parts.ONE_PASS if banded else 0.0,
        )
        return
    except (load_parts.OutOfTime, _Unread) as stopped:
        if not banded:
            raise
        met = stopped.evidence if isinstance(stopped, _Unread) else str(stopped)
        memory = isinstance(stopped, _Unread) and stopped.memory
        late = isinstance(stopped, load_parts.OutOfTime)
    _clock["now"] = None
    _give_back()
    _band_read(
        args,
        report,
        banded,
        f"read in two passes, as --parts asks: {met}",
        "two passes, asked for with --parts, "
        + (
            "met the container's time"
            if late
            else "met the container's memory"
            if memory
            else "couldn't read its whole"
        ),
        load_parts.ONE_PASS,
        "its read, which a one pass's limit is given for",
        tried_s=time.time() - began,
    )


def _band_read(
    args: Any,
    report: Report,
    banded: list[str],
    why: str,
    done: str,
    tried: float,
    what: str,
    tried_s: float | None = None,
) -> None:
    """Each part that names its glass band read again with it left out, where the whole can't
    be read (mqucifer/print-shop#295), `why`, said first, with `done`, what was tried. The read
    has the container's time left: where that's less than `tried`, what the band's read is
    taken to take, s, it says so. Under a weight it's a lower bound (`BOUND`). Lifted, each band's
    weight is kept, hung from where it met its part (`_Model._band_weight`), the movement taken
    together is an upper bound, never a part's strain (`LIFT_BOUND`, mqucifer/print-shop#375),
    and a joint's and its bolts' share is the full weight's only where statics fixes it: every
    other share is said not to be a bound (the operator's review). Either way the verdict's
    line says the band is unread. `tried_s` is what's said the try took, `tried` unless
    given."""
    report.again()
    from . import load_parts

    clock = load_parts.Clock(report.started, label="the glass band's lower bound")
    clock.done.append(done)
    clock.time("band", tried)
    clock.check("band", what)
    _clock["now"] = clock
    band_began = time.time()
    names = ", ".join(banded)
    try:
        _run(args, report, without_bands=True)
    except load_parts.OutOfTime:
        raise  # it says it's the band's lower bound's own clock
    except NoAnswer as stopped:
        # The bound itself unread, said as it is, never as if it were the whole
        # (mqucifer/print-shop#376).
        raise NoAnswer(
            f"the glass band's lower bound couldn't be read either: the whole couldn't be read "
            f"({why}), and with {names}'s glass band left out, {stopped}"
        ) from stopped
    lifted = args.lift is not None
    joints = report.numbers.get("joints", {})
    fixed = sorted(n for n, j in joints.items() if "statics_force_n" in j)
    loose = sorted(n for n, j in joints.items() if "statics_force_n" not in j)
    if lifted:
        report.facts.insert(
            0,
            f"read as a bound: {names} read with its glass band's stiffness left out and its "
            "weight kept, the leads and lips its `band` names hung from the faces where they "
            f"met the rest of it, as the whole couldn't be read: {why}. The band's stiffness is "
            f"never made up, and the band itself is unread. {LIFT_BOUND}"
            + (
                f". Statics fixes {', '.join(fixed)}: each carries the load beyond it, which "
                "hangs from it alone, the full weight's as with the band in, and so its bolts "
                "share the same"
                if fixed
                else ""
            )
            + (
                f". {' and '.join(loose)} can carry more or less than with the band in, as the "
                "band's stiffness moves load between the ways it goes round a loop, and so can "
                "their bolts: no share of theirs is a bound"
                if loose
                else ""
            ),
        )
        report.why = (
            f"read as a bound, {names} with its glass band's stiffness left out and its weight "
            f"kept, the band unread: {report.why}"
        )
    else:
        report.facts.insert(
            0,
            f"read as a lower bound: {names} read with its glass band left out, the leads and "
            "lips its `band` names, as the whole couldn't be read: "
            f"{why}. The rest of each is read with no stiffness for its band, as it would be "
            "with the band gone, never with a stiffness made up for it, and the band itself is "
            f"unread. {BOUND}",
        )
        report.why = (
            f"read as a lower bound, {names} with its glass band left out, the band unread: "
            f"{report.why}"
        )
    report.numbers["lower_bound"] = {
        "parts": banded,
        "why": why,
        # How long the try took, and the band's read (mqucifer/print-shop#301).
        "tried_s": round(tried if tried_s is None else tried_s, 1),
        "band_s": round(time.time() - band_began, 1),
        # And which of CalculiX's solvers read it, at the mesh and the coarser one: the
        # iterative where the direct met the memory (mqucifer/print-shop#376).
        "solvers": dict(zip(("mesh", "coarser"), _solvers, strict=False)),
        # Lifted, each band's weight kept, in g (mqucifer/print-shop#375), and the joints whose
        # load statics fixes, and those whose share isn't a bound (the operator's review).
        **(
            {
                "weight_kept_g": {
                    one["part"]: one["band_grams"]
                    for one in report.numbers.get("loads", [])
                    if "band_grams" in one
                },
                "statics_fixed": fixed,
                "shares_not_bound": loose,
            }
            if lifted
            else {}
        ),
    }


#: Which figures read without a band are a bound, and which aren't (mqucifer/print-shop#295).
BOUND = (
    "Of what's read, how far the loaded places move along the load, taken together, is a "
    "bound: with less in it the assembly gives more under the same weight, never less. Each "
    "part's strain, and each joint's and bolt's share of the load, can be more or less than "
    "with the band in, and isn't one"
)
#: And lifted, with each band's weight kept (mqucifer/print-shop#375).
LIFT_BOUND = (
    "Lifted, every part hangs by its whole weight still, the band's in it, with less of the "
    "part to carry it: how far what hangs moves along its weight, taken together, is an upper "
    "bound under the full weight, the safe side. Each part's strain isn't one: a banded part's "
    "is read on the rest of it alone, and its band, where its worst may be, is unread; and any "
    "part's, at a corner where parts meet, reads by the mesh there, which differs with the band "
    "left out"
)


def _run(
    args: Any,
    report: Report,
    without_bands: bool = False,
    two_pass: str | None = None,
    reserve: float = 0.0,
    tried: float = 0.0,
) -> None:
    freecad("load")
    # Each read places its parts afresh: what an earlier one joined isn't read again.
    _joined.clear()
    _kept.clear()
    _placed_now.clear()
    args.lift = getattr(args, "lift", None)
    if args.lift is not None and (args.weight is not None or args.at):
        raise NoAnswer(
            "--lift hangs every other part from the one named, by its own weight: it takes no "
            "--weight and no --at. A weight on it is a load case of its own"
        )
    if args.lift is None and (args.weight is None or not args.at):
        raise NoAnswer(
            "load takes --weight KG and --at X,Y,Z, the weight and a place it sits on; or "
            "--lift PART, the assembly lifted by that part with every other part's own weight "
            "hanging from it"
        )
    if args.lift is None and args.weight <= 0:
        raise NoAnswer("--weight is the load in kg, more than 0")
    if args.over <= 0:
        raise NoAnswer("--over is how far round each place the weight is spread, more than 0 mm")
    assembly = read(args.assembly)
    if args.lift is not None and args.lift not in [p.name for p in assembly.parts]:
        raise NoAnswer(
            f"--lift {args.lift}: the parts are {', '.join(p.name for p in assembly.parts)}"
        )
    stiff = {(p.material.modulus_mpa, p.material.poisson) for p in assembly.parts}
    if len(stiff) > 1:
        raise NoAnswer("parts of different stiffness aren't solved together yet: one material")
    # The one pass, timed where two passes could go on from it (mqucifer/print-shop#331).
    _one_pass(assembly, report.started, two_pass, without_bands)
    placed = _placed(assembly, without_bands)
    keep(placed)
    _placed_now[:] = placed
    # Each through-bolt's way through both parts, before anything is solved.
    with step("finding each through joint's bolts' way through its parts"):
        ways = load_through.ways(assembly, placed)
    # And each dovetail's latch as what it catches on, and the faces of each that don't touch,
    # and would close on each other across their play as the tail shifts and turns
    # (mqucifer/print-shop#188).
    catches, plays = {}, {}
    for j in assembly.joints:
        if j.kind == "dovetail":
            with step(f"reading the dovetail joint {j.name}'s latch and its play"):
                catches[j.name] = load_dovetail.catch(assembly, j, placed)
                plays[j.name] = load_dovetail.play(assembly, j, placed)
    # And each dovetail taken apart the way it went together: its tail out of its socket past
    # nothing but its latch, and its latch's tab bent as far as it has to be to let it in
    # (mqucifer/print-shop#184 and mqucifer/print-shop#185). A joint that can't be put together
    # fails whatever the load, and nothing is solved.
    # The parts are joined first, as the solve joins them, so what the join refuses, as a face
    # that leans against the way the file says a tail slides in, is said before this.
    if any(j.kind == "dovetail" for j in assembly.joints):
        import FreeCAD as App

        doc = App.newDocument("Joined")
        try:
            with _quiet(), step("joining the parts"):
                _Model(assembly, placed, args, 1.0).join(doc)
        finally:
            App.closeDocument(doc.Name)
    ways_in = {}
    for j in assembly.joints:
        if j.kind == "dovetail":
            with step(f"taking the dovetail joint {j.name} apart the way it went together"):
                ways_in[j.name] = way_in.read(assembly, j, placed)
    if ways_in:
        report.numbers["ways_in"] = {
            name: {k: v for k, v in found.items() if k not in ("fails", "apart")}
            for name, found in ways_in.items()
        }
        for name, found in ways_in.items():
            for text in way_in.said(found, name):
                report.line(text)
            for text in way_in.second(found, name):
                report.second(text)
        apart_in = [t for found in ways_in.values() for t in found["apart"]]
        fails_in = [t for found in ways_in.values() for t in found["fails"]]
        if apart_in:
            report.verdict(
                "DISAGREE", "; and ".join(apart_in) + ": read both, and don't pick one. Nothing "
                "is solved until the way in has one answer"
            )
            return
        if fails_in:
            report.verdict(
                "FAIL", "; and ".join(fails_in) + ". Nothing is solved: a joint that can't be "
                "put together carries nothing"
            )
            return
    size = _mesh_size(placed, args.mesh)
    printed = [p.name for p in assembly.parts if p.printed is not None]
    if printed and two_pass is not None:
        raise NoAnswer(
            f"{', '.join(printed)} {'is' if len(printed) == 1 else 'are'} read as printed, a "
            "skin round an infill core, in one pass only so far, and this is read in two "
            f"({two_pass}): read the printed part(s) on their own, or leave out their `print` "
            "to read them solid"
        )
    cores = _cores(assembly, without_bands) if printed else None
    if two_pass is not None:
        # Two passes (mqucifer/print-shop#301): the first is the whole, read as the one pass
        # reads it but for its bolts' bores, and the parts are read in the second.
        # Here, as it builds on this module's own model.
        from . import load_parts

        clock = load_parts.Clock(report.started, reserve)
        # The one pass's try, where there was one, is what a whole takes at the most: its mesh
        # had every bore round.
        clock.time("whole", tried)
        _clock["now"] = clock
        fine, coarse, firsts = load_parts.first(assembly, placed, args, size, clock)
        reads, rough, seconds = load_parts.second(
            assembly, placed, args, size, fine, coarse, clock
        )
        fine = {
            **fine,
            "whole_parts": fine["parts"],
            "parts": {n: r["part"] for n, r in reads.items()},
        }
        coarse = {**coarse, "parts": {n: r["part"] for n, r in rough.items()}}
    else:
        one = _on_one_pass()
        if one is not None:
            one.whole_began = time.time()
        fine = _solved(assembly, placed, args, size, cores=cores)
        if one is not None:
            one.whole_read()
            one.check_coarser(round(size * COARSER, 3))
        coarse = _solved(
            assembly, placed, args, round(size * COARSER, 3), cores=cores, coarser_of=fine["closes"]
        )
    # Which of CalculiX's solvers read each mesh, for a glass band's read (mqucifer/print-shop#376).
    _solvers[:] = [
        "iterative, corrected" if one.get("iterated") else "direct" for one in (fine, coarse)
    ]
    force = float(sum(np.linalg.norm(one["force"]) for one in fine["raw_loads"]))
    meshed = (
        f"a mesh of at most {size:g} mm ({fine['nodes']} nodes, {fine['elements']} elements, "
        f"meshed in {fine['mesh_s']} s and solved in {fine['solve_s']} s)"
        + _straight_said(fine)
        + (f"; {fine['iterated']}" if fine.get("iterated") else "")
        if two_pass is None
        else "in two passes, each one's meshes below"
    )
    start = (
        f"the assembly: {len(assembly.parts)} part(s) and {len(assembly.joints)} joint(s) from "
        f"{args.assembly}, "
    )
    if args.lift is not None:
        args.weight = force / G
        dense = sorted(
            {
                f"{p.material.name} at {p.material.density_g_cm3:g} g/cm³, from "
                f"{p.material.density_source}"
                for p in assembly.parts
                if p.name != args.lift
            }
        )
        report.line(
            f"{start}lifted by {args.lift}: {'; '.join(fine['held'])}, and held by nothing "
            f"else, the assembly's own `held` set aside. Every other part hangs by its own "
            f"weight, --toward {args.toward}: its solid volume, {'; '.join(dense)}, "
            f"{1000 * args.weight:.2f} g and {force:.4f} N in all; {meshed}"
        )
        report.numbers["lift"] = args.lift
    else:
        report.line(
            f"{start}{'; '.join(fine['held'])}; {args.weight:g} kg, {force:.2f} N, "
            f"--toward {args.toward}, shared between {len(args.at)} place(s), each over the "
            f"surface within {args.over:g} mm of it; {meshed}"
        )
    if two_pass is not None:
        load_parts.pass_lines(report, firsts, seconds, two_pass)
        report.numbers["two_passes"] = {"why": two_pass, "first": firsts, "second": seconds}
    off = float(np.linalg.norm(fine["reaction"] + force * fine["toward"]))
    if off > REACTION * force:
        raise NoAnswer(
            f"the held faces' reaction, {_xyz(fine['reaction'])} N as CalculiX sums it, isn't "
            f"the {force:.4g} N put on, by {off:.3f} N: the load didn't go on as asked"
        )
    report.numbers["weight_kg"] = args.weight
    report.numbers["force_n"] = force
    report.numbers["toward"] = args.toward
    report.numbers["mesh_mm"] = size
    report.numbers["coarse_mesh_mm"] = coarse["mesh_mm"]
    report.numbers["nodes"] = fine["nodes"]
    report.numbers["elements"] = fine["elements"]
    report.numbers["mesh_s"] = fine["mesh_s"]
    report.numbers["solve_s"] = fine["solve_s"]
    report.numbers["reaction_n"] = _list(fine["reaction"])
    report.numbers["held"] = fine["held"]
    fails: list[str] = []
    apart: list[str] = []
    lines, seconds_said = len(report.facts), len(report.second_check)
    _parts(
        report, fine, coarse, fails, apart, 0.0 if two_pass is None else load_parts.BAND
    )
    if two_pass is not None:
        _tag(report, lines, seconds_said, "pass 2, on its own: ", ("the part ",))
        lines, seconds_said = len(report.facts), len(report.second_check)
    _joints(assembly, args, report, fine, coarse, fails, apart, (ways, placed, catches, plays))
    unread = [n for n, j in report.numbers["joints"].items() if j.get("insert_unread")]
    glued = [n for n, j in report.numbers["joints"].items() if j.get("glue_unread")]
    report.numbers["loads"] = fine["loads"]
    for one, other in zip(fine["loads"], coarse["loads"], strict=True):
        one["coarse_moves_mm"] = other["moves_mm"]
        if two_pass is not None:
            # Read on the whole, without its bolts' bores meshed round: taken on to a finer
            # mesh, so it's no less than one pass reads (mqucifer/print-shop#301).
            one["mesh_moves_mm"] = one["moves_mm"]
            one["moves_mm"] = load_parts.finer(one["moves_mm"], other["moves_mm"])
        if one["patch_mm2"] is None:
            report.line(
                f"the part {one['part']} hangs by its own weight: {one['volume_mm3']:.1f} mm³ "
                f"solid is {one['grams']:.2f} g, {np.linalg.norm(one['force_n']):.4f} N, at its "
                f"middle, {_xyz(one['middle'])}"
                + (
                    f", {one['band_grams']:.2f} g of it its glass band's, left out of its "
                    f"stiffness and hung from the {one['band_mm2']:.1f} mm² where the band met "
                    "the rest of it"
                    if "band_grams" in one
                    else ""
                )
                + f"; it moves {one['moves_mm']:.4f} mm {args.toward}"
            )
            continue
        report.line(
            f"the place at {_xyz(one['at'])}, on {one['part']}: it moves {one['moves_mm']:.3f} mm "
            f"{args.toward} under its {np.linalg.norm(one['force_n']):.2f} N, spread over "
            f"{one['patch_mm2']:.1f} mm² whose middle is {_xyz(one['middle'])}"
            + (
                f" ({one['mesh_moves_mm']:.3f} mm on the mesh and {one['coarse_moves_mm']:.3f} "
                "on the coarser one)"
                if "mesh_moves_mm" in one
                else ""
            )
        )
    report.numbers["moves_mm"] = moves_most = max(one["moves_mm"] for one in fine["loads"])
    if two_pass is not None:
        fine["most_moves_mm"] = load_parts.finer(fine["most_moves_mm"], coarse["most_moves_mm"])
        report.line(load_parts.FINER_SAID)
    report.numbers["most_moves_mm"] = fine["most_moves_mm"]
    report.numbers["most_moves_at"] = fine["most_moves_at"]
    if two_pass is not None:
        load_parts.checks(report, fine, coarse, reads, rough, force, apart)
    report.line(
        f"the most any point moves is {fine['most_moves_mm']:.3f} mm, at "
        f"{_xyz(fine['most_moves_at'])}, as most_moves_mm; the loaded place that moves most, "
        f"{moves_most:.3f} mm, is moves_mm. "
        + (
            "Both are in proportion to one over the material's stiffness, "
            f"{assembly.parts[0].material.modulus_mpa:g} MPa, a solid part's"
            if not cores
            else f"The skins are the material's own stiffness, "
            f"{assembly.parts[0].material.modulus_mpa:g} MPa, and each core its infill's, below"
        )
    )
    moves = ", ".join(
        f"{o['moves_mm']:.3f} mm at {_xyz(o['at'])} ({_share(o['moves_mm'], f['moves_mm'])} apart)"
        for f, o in zip(fine["loads"], coarse["loads"], strict=True)
    )
    report.second(
        f"the same solve on a mesh of at most {coarse['mesh_mm']:g} mm ({coarse['nodes']} nodes"
        f"{_straight_said(coarse)}"
        + (f"; {coarse['iterated']}" if two_pass is None and coarse.get("iterated") else "")
        + f"): the loaded places move {moves}"
    )
    report.second(
        f"the held faces' reaction, as CalculiX sums it: {_xyz(fine['reaction'])} N, against the "
        f"{force:.4g} N put on --toward {args.toward}, {off:.4f} N apart"
    )
    if two_pass is not None:
        _tag(
            report, lines, seconds_said, "pass 1, the whole: ",
            ("the joint ", "its ", "the place at ", "the part ", "the most any point",
             "the same solve", "the held faces'"),
        )  # fmt: skip
    sources = sorted({p.material.source for p in assembly.parts})
    report.line("the limits are from " + "; ".join(sources))
    if cores:
        _as_printed(assembly, report, cores)
    solid = [p.name for p in assembly.parts if p.printed is None]
    if args.lift is not None:
        report.note(
            ("each part is read as solid, with one stiffness every way, and" if not cores else
             f"{', '.join(solid) or 'no part'} is read as solid, with one stiffness every way, "
             "and every printed part as its skin round its core; each")
            + " hangs at its solid "
            "weight, which is more than a printed part's with its walls and infill: creep, a "
            f"knock, and the weight of anything bought that hangs with them aren't in it, and "
            f"nor is {args.lift}'s own weight, which is in the hand that lifts it"
        )
    elif cores:
        report.note(
            f"{', '.join(solid) or 'no part'} {'is' if len(solid) == 1 else 'are'} read as "
            "solid, with one stiffness every way, and every printed part as its skin round its "
            "core, and every joint as one piece while it holds: creep, a joint that opens and "
            "the parts' own weight aren't in it"
        )
    else:
        report.note(
            "each part is read as solid, with one stiffness every way, and every joint as one "
            "piece while it holds: walls and infill, creep, a joint that opens and the parts' "
            "own weight aren't in it"
        )
    if apart:
        report.verdict(
            "DISAGREE",
            "; and ".join(apart) + ": read both, and don't pick one. A sharp inside corner has "
            "no one figure in any mesh: round it, or give a finer --mesh",
        )
    elif fails:
        report.verdict("FAIL", "; and ".join(fails))
    elif unread or glued:
        why = []
        if unread:
            inserts = sorted(
                {j.insert.name for j in assembly.joints if j.name in unread and j.insert}
            )
            why.append(
                f"the bolted joint(s) {', '.join(unread)} have a pull to hold, "
                f"{max(report.numbers['joints'][n]['bolt_pull_n'] for n in unread):.1f} N a bolt "
                f"at most, and the insert {', '.join(inserts)} has no pull-out figure: in "
                "printed plastic the insert gives long before the bolt, so the bolt's own "
                "figure doesn't say it holds. Give each such joint its `pull_n`, what each "
                "bolt holds in N, and its `source`, from the request or a pull test"
            )
        if glued:
            most = max(np.linalg.norm(report.numbers["joints"][n]["force_n"]) for n in glued)
            why.append(
                f"the bonded joint(s) {', '.join(glued)} carry load, {most:.2f} N at most, and "
                "glue is a second hold only: no glue line in printed plastic has a figure "
                "here, so nothing says it holds. Join the parts by something that's read, a "
                "`through` bolt or a `dovetail`, and keep glue for its channels"
            )
        report.verdict(
            "UNREAD", "; and ".join(why) + ". Everything else read is within its limit"
        )
    else:
        # Every part's, and where it's printed its skin's and each core's
        # (mqucifer/print-shop#337).
        across = []
        for name, one in fine["parts"].items():
            across.append((name + ("'s skin" if one.get("printed") else ""), one))
            across += [(f"{name}'s core in {c['cell']}", c) for c in one.get("cores", [])]
        worst = max(across, key=lambda p: p[1]["across_layers_pct"])
        under = (
            f"lifted by {args.lift}, with {1000 * args.weight:.1f} g hanging from it,"
            if args.lift is not None
            else f"under {args.weight:g} kg"
        )
        report.verdict(
            "OK",
            f"{under} every part is within its material's limits and every "
            f"joint holds: the most across any part's layers is "
            f"{worst[1]['across_layers_pct']:.3f}%, in {worst[0]}, and "
            + ("what hangs" if args.lift is not None else "the loaded place")
            + f" moves {moves_most:.3f} mm",
        )


def _as_printed(assembly: Assembly, report: Report, cores: dict[str, list[Any]]) -> None:
    """What a read as printed assumed (mqucifer/print-shop#337): each printed part's cells, its
    walls and shells, its cores' figures and their source, that no print has checked them, and
    what of it is still read solid."""
    found: dict[str, Any] = {}
    for part in assembly.parts:
        if part.name not in cores:
            continue
        cells = []
        for cell, solids in cores[part.name]:
            figures = infill.core(cell.settings, part.material)
            cells.append({
                "cell": cell.name,
                "settings": cell.settings.given(),
                "walls_mm": round(cell.walls_mm, 3),
                "top_mm": round(cell.top_mm, 3),
                "bottom_mm": round(cell.bottom_mm, 3),
                "volume_mm3": round(cell.volume_mm3, 1),
                "core_mm3": round(cell.core_mm3, 1),
                "core_box": cell.core_box,
                "cut": cell.cut,
                **({"cut_planes": cell.check.planes, "cut_planes_read": cell.check.read,
                    "cut_short_walls_pct": round(100 * cell.check.short, 2),
                    "cut_over_walls_pct": round(100 * cell.check.over, 2)}
                   if cell.check is not None else {}),
                **figures.said(),
            })
            report.line(
                f"the part {part.name} as printed, {cell.name}: {cell.settings.said()}; so walls "
                f"{cell.walls_mm:.2f} mm thick, as many of Orca's beads' spacing, its classic "
                f"walls' and Arachne's alike, {cell.top_mm:.2f} mm of shells under each top "
                f"face and {cell.bottom_mm:.2f} mm over each bottom one, round a core of "
                f"{cell.core_mm3:.0f} mm³ of its {cell.volume_mm3:.0f}"
                + ("" if solids else ": its walls and shells fill it, so it's solid")
                + (f"; its walls cut by {cell.cut}, and held to each layer's own outline "
                   f"offset in by them, as Orca slices it, at {cell.check.read} of its layers' "
                   f"planes: its core short of it by {100 * cell.check.short:.1f}% of its walls "
                   f"at most, within {100 * as_printed.SHORT:.0f}%, and over it by "
                   f"{100 * cell.check.over:.1f}%, within {100 * as_printed.AGREE:.0f}%"
                   if cell.check is not None else "")
            )
        found[part.name] = cells
        joined = [j.name for j in assembly.joints
                  if part.name in j.between and j.kind in ("through", "dovetail")]
        if joined:
            report.note(
                f"what's solved again round {', '.join(joined)}, the plastic round each bolt, a "
                f"dovetail's neck and walls and its latch's tab, is read solid in {part.name}, "
                "not as printed: stronger than a printed part's, so read it with a margin"
            )
    report.numbers["printed"] = found
    patterns = sorted({c["pattern"] for cells in found.values() for c in cells})
    reach = "; ".join(
        f"{p} from {infill.checked_from(p):g}%, "
        f"set by {infill.set_by(infill.TESTED_AS[p])['source']}"
        for p in patterns
    )
    report.line(
        "each core's figures: its lines as Orca 2.4.2 lays them, each set stiff along its run, "
        "walled through the layers where a pattern lays the same lines each layer, and bending "
        "between them (Ashby 2006, The properties of foams and lattices); held to the least "
        "share of that any published test of an infill alone reached, Ben hadj Hassine et al. "
        "2024, Polymers 16:1562 (PLA with no walls or shells, 40 to 60%), and Alabdli et al. "
        "2026, Polymers 18:2299 (PETG grid, 20 to 60%), its strain as a share of the solid's "
        "those tests took, at the least; and under each published test of a skin round a core "
        "as an upper bound (`infill.BOUNDS`, docs/bench.md). Checked by them: "
        f"{reach}; below that no test checks them, and none goes past {infill.MOST:g}%"
    )
    report.note(
        "the infill's figures are published tests of PLA, ABS and PETG, as shares of their "
        "own plastic's, taken for this PETG: none has been checked against a print from this "
        "printer yet, which the printed coupons will do (mqucifer/print-shop#340). A printed "
        "part hangs at its solid weight, more than its own"
    )


def _give_back() -> None:
    """What the solve that met the container's memory held, given back to the kernel
    (mqucifer/print-shop#301), as every step's is (`cae.give_back`)."""
    give_back()


#: Two passes' clock (mqucifer/print-shop#301); or the one pass's, where it could go on to two
#: passes (`_OnePass`, mqucifer/print-shop#331); or None, where nothing stops early.
_clock: dict[str, Any] = {"now": None}


def _in_time(kind: str, what: str) -> None:
    """In two passes, or a one pass that could go on to them, stop before a step that wouldn't
    end inside the container's time."""
    clock = _clock["now"]
    if clock is not None:
        clock.check(kind, what)


def _timed(kind: str, seconds: float) -> None:
    clock = _clock["now"]
    if clock is not None:
        clock.time(kind, seconds)


def _tag(report: Report, lines: int, seconds: int, tag: str, starts: tuple[str, ...]) -> None:
    """Which pass each figure is from, put before each line since `lines` and each second
    check since `seconds` that starts with one of `starts` (mqucifer/print-shop#301)."""
    report.facts[lines:] = [
        tag + f if f.startswith(starts) else f for f in report.facts[lines:]
    ]
    report.second_check[seconds:] = [
        tag + f if f.startswith(starts) else f for f in report.second_check[seconds:]
    ]


def _near(value: float, limit: float, band: float) -> bool:
    """Under `limit`, but within `band` of it as a share of it, the band's edge in it: read
    `band` low, it would be at the limit or over it (mqucifer/print-shop#301)."""
    return not over(value, limit) and not under(value / (1 - band), limit)


def _share(value: float, against: float) -> str:
    return f"{100 * abs(value - against) / abs(against):.1f}%" if abs(against) > 1e-12 else "n/a"


def _parts(
    report: Report,
    fine: dict[str, Any],
    coarse: dict[str, Any],
    fails: list[str],
    apart: list[str],
    band: float = 0.0,
) -> None:
    """Each part against its material: pulled across its layers, and stretched any way. In
    two passes (mqucifer/print-shop#301), a strain within `band` of its share below its limit
    is either side of it."""
    report.numbers["parts"] = fine["parts"]
    for name, one in fine["parts"].items():
        other = coarse["parts"][name]
        one["coarse_across_layers_pct"] = other["across_layers_pct"]
        one["coarse_stretch_pct"] = other["stretch_pct"]
        way = "(" + ", ".join(f"{v:g}" for v in np.round(one["layers"], 3)) + ")"
        lies = one["stretch_from_layers_deg"]
        skin = "'s skin, its walls and shells," if one.get("printed") else ""
        report.line(
            f"the part {name}{skin} ({one['material']}, its layers stacked along {way}): pulled "
            f"across its layers by {one['across_layers_pct']:.3f}% at most, at "
            f"{_xyz(one['across_at'])}, as parts.{name}.across_layers_pct, against "
            f"{one['across_limit_pct']:g}%; its largest stretch any way is a {one['stretch_is']} "
            f"of {one['stretch_pct']:.3f}%, at {_xyz(one['stretch_at'])}, {lies:.0f}° out of "
            f"its layers' plane ({'across them' if lies > 45 else 'along them'}), against "
            f"{one['stretch_limit_pct']:g}%"
        )
        report.second(
            f"the part {name}{skin} on the coarser mesh: {other['across_layers_pct']:.3f}% "
            f"across its layers "
            f"({_share(other['across_layers_pct'], one['across_layers_pct'])} apart) and "
            f"{other['stretch_pct']:.3f}% any way "
            f"({_share(other['stretch_pct'], one['stretch_pct'])} apart)"
        )
        for key, limit_key, what in (
            ("across_layers_pct", "across_limit_pct", "pulled across its layers"),
            ("stretch_pct", "stretch_limit_pct", "stretched"),
        ):
            value, limit = one[key], one[limit_key]
            where = one["across_at" if key == "across_layers_pct" else "stretch_at"]
            if over(value, limit) != over(other[key], limit):
                apart.append(
                    f"the part {name} is {what} by {said(value, limit)}% on the mesh and "
                    f"{said(other[key], limit)}% on the coarser one, either side of its "
                    f"{limit:g}% limit"
                )
            elif band and _near(value, limit, band):
                apart.append(
                    f"pass 2 reads the part {name} {what} by {said(value, limit)}%, within the "
                    f"{100 * band:g}% two passes are held to of its {limit:g}% limit: read it "
                    "in one pass, or give it more margin"
                )
            elif over(value, limit):
                fails.append(
                    f"the part {name}{skin} is {what} by {said(value, limit, 2)}% at "
                    f"{_xyz(where)}, over its material's {limit:g}% "
                    + (
                        "across its layers: lay it on the bed so its layers run along this pull, "
                        "or make it thicker there"
                        if key == "across_layers_pct"
                        else "in its layers' plane"
                    )
                )
        for core, rough in zip(one.get("cores", []), other.get("cores", []), strict=True):
            _core(report, name, core, rough, fails, apart)


def _core(
    report: Report,
    name: str,
    core: dict[str, Any],
    rough: dict[str, Any],
    fails: list[str],
    apart: list[str],
) -> None:
    """A printed part's core (mqucifer/print-shop#337): pulled across its layers, and its
    stretch any way against the limit for the way it points, against its lines and its
    layers (`infill.Core.read`), on both meshes."""
    core["coarse_across_layers_pct"] = rough["across_layers_pct"]
    core["coarse_stretch_pct"] = rough["stretch_pct"]
    where = f"the part {name}'s core in {core['cell']}"
    report.line(
        f"{where} ({core['said']}): pulled across its layers by "
        f"{core['across_layers_pct']:.3f}% at most, at {_xyz(core['across_at'])}, against "
        f"{core['across_limit_pct']:g}%; its stretch nearest its limit is a {core['stretch_is']} "
        f"of {core['stretch_pct']:.3f}% at {_xyz(core['stretch_at'])}, "
        f"{core['stretch_from_layers_deg']:.0f}° out of its layers' plane and "
        f"{core['stretch_from_lines_deg']:.0f}° off its lines, against "
        f"{core['stretch_limit_pct']:.3g}%"
    )
    report.second(
        f"{where} on the coarser mesh: {rough['across_layers_pct']:.3f}% across its layers "
        f"({_share(rough['across_layers_pct'], core['across_layers_pct'])} apart) and "
        f"{rough['stretch_pct']:.3f}% against {rough['stretch_limit_pct']:.3g}% that way"
    )
    for key, limit_key, what, at in (
        ("across_layers_pct", "across_limit_pct", "pulled across its layers", "across_at"),
        ("stretch_pct", "stretch_limit_pct", "stretched", "stretch_at"),
    ):
        value, limit = core[key], core[limit_key]
        if over(value, limit) != over(rough[key], rough[limit_key]):
            apart.append(
                f"{where} is {what} by {said(value, limit)}% of {limit:.3g}% on the mesh and "
                f"{said(rough[key], rough[limit_key])}% of {rough[limit_key]:.3g}% on the "
                "coarser one, either side of its limit"
            )
        elif over(value, limit):
            fails.append(
                f"{where} is {what} by {said(value, limit, 2)}% at {_xyz(core[at])}, over its "
                f"infill's {limit:.3g}% that way ({core['said']}): give it more walls or "
                "denser infill there, or turn its lines along the stretch"
            )


def _joints(
    assembly: Assembly,
    args: Any,
    report: Report,
    fine: dict[str, Any],
    coarse: dict[str, Any],
    fails: list[str],
    apart: list[str],
    through: tuple[
        dict[str, list[dict[str, Any]]], list[Any], dict[str, Any], dict[str, list[dict[str, Any]]]
    ],
) -> None:
    """Each joint's force, and whether what holds it can."""
    saved: dict[str, Any] = {}
    for joint in assembly.joints:
        with step(
            f"judging the joint {joint.name}, {joint.kind}, between {' and '.join(joint.between)}"
        ):
            saved[joint.name] = _joint(assembly, joint, args, report, fine, coarse, fails, apart,
                                       through)  # fmt: skip
    report.numbers["joints"] = saved


def _joint(
    assembly: Assembly,
    joint: Joint,
    args: Any,
    report: Report,
    fine: dict[str, Any],
    coarse: dict[str, Any],
    fails: list[str],
    apart: list[str],
    through: tuple[
        dict[str, list[dict[str, Any]]], list[Any], dict[str, Any], dict[str, list[dict[str, Any]]]
    ],
) -> dict[str, Any]:
    """One joint's force, and whether what holds it can: its saved figures."""
    force = report.numbers["force_n"]
    read, other = fine["joints"][joint.name], coarse["joints"][joint.name]
    normal = read["normal"]
    along = float(read["force"] @ normal)
    shear = float(np.linalg.norm(read["force"] - along * normal))
    bending = read["moment"] - (read["moment"] @ normal) * normal
    one: dict[str, Any] = {
        "kind": joint.kind,
        "at": _list(read["at"], 3),
        "area_mm2": read["area_mm2"],
        "force_n": _list(read["force"]),
        "moment_nmm": _list(read["moment"]),
        "pull_n": along,
        "shear_n": shear,
        "bending_nmm": float(np.linalg.norm(bending)),
        "twist_nmm": float(read["moment"] @ normal),
        "pulled_part_n": read["pulled_n"],
        "pushed_part_n": read["pushed_n"],
        "coarse_force_n": _list(other["force"]),
        "coarse_moment_nmm": _list(other["moment"]),
    }
    report.line(
        f"the joint {joint.name}, {joint.kind}, over {read['area_mm2']:.1f} mm² round "
        f"{_xyz(read['at'])}: it carries {np.linalg.norm(read['force']):.2f} N, "
        f"{abs(along):.2f} N of it a {'pull' if along > 0 else 'push'} square to the faces "
        f"and {shear:.2f} N along them, with a bending moment of "
        f"{one['bending_nmm']:.1f} N·mm and a twist of {abs(one['twist_nmm']):.1f} N·mm, as "
        f"joints.{joint.name}"
    )
    statics = beyond(assembly, joint, fine["raw_loads"], fine["held_parts"], read["at"])
    if statics is not None:
        one["statics_force_n"], one["statics_moment_nmm"] = _list(statics[0]), _list(statics[1])
        gap = float(np.linalg.norm(read["force"] - statics[0]))
        turn = float(np.linalg.norm(read["moment"] - statics[1]))
        report.second(
            f"the joint {joint.name} by statics, the load beyond it: "
            f"{np.linalg.norm(statics[0]):.2f} N and {np.linalg.norm(statics[1]):.1f} N·mm, "
            f"{gap:.2f} N and {turn:.1f} N·mm from the solve's; on the coarser mesh the "
            f"solve gives {np.linalg.norm(other['force']):.2f} N and "
            f"{np.linalg.norm(other['moment']):.1f} N·mm"
        )
    else:
        report.second(
            f"the joint {joint.name} on the coarser mesh: "
            f"{np.linalg.norm(other['force']):.2f} N and "
            f"{np.linalg.norm(other['moment']):.1f} N·mm (statics alone doesn't give it: the "
            "parts are held, or joined, on both sides of it)"
        )
    if joint.kind == "bolted":
        _bolted(joint, report, one, read, other, statics, fails, apart)
    elif joint.kind in ("through", "dovetail"):
        ways, placed, catches, plays = through
        solids = [placed[[p.name for p in assembly.parts].index(n)] for n in joint.between]
        if joint.kind == "dovetail":
            _dovetail(
                assembly, joint, report, one, read, other, statics, fails, apart,
                catches[joint.name], solids, plays[joint.name],
            )  # fmt: skip
            return one
        _through(
            assembly, joint, report, one, read, other, statics, fails, apart,
            ways[joint.name], solids,
        )  # fmt: skip
    elif joint.kind == "touching":
        _touching(joint, args, report, one, read, force, fails)
    elif joint.kind == "bonded":
        # Glue is a second hold only, and no glue line in printed plastic has a sourced
        # figure: a glued joint that carries anything isn't read (the operator's ruling on
        # mqucifer/print-shop#171), as an insert with no figure isn't.
        size = float(np.ptp(read["outline"], axis=0).max()) or 1.0
        carries = max(np.linalg.norm(read["force"]), np.linalg.norm(read["moment"]) / size)
        one["glue_unread"] = bool(carries > NOTHING * force)
        report.line(
            "  it's bonded, and nothing here says what a glue line in printed plastic "
            "holds: "
            + (
                "what it carries isn't read against anything, as joints."
                f"{joint.name}.glue_unread"
                if one["glue_unread"]
                else "it carries nothing to speak of under this load"
            )
        )
    return one


def _bolted(
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    other: dict[str, Any],
    statics: tuple[np.ndarray, np.ndarray] | None,
    fails: list[str],
    apart: list[str],
) -> None:
    if not read["flat"]:
        raise NoAnswer(
            f"the bolted joint {joint.name}'s faces aren't in one plane: a bolt's pull is read "
            "square to one flat face. Give each flat face's joint to its own pair of parts"
        )
    holds = joint.holds()
    assert holds is not None and joint.bolt is not None and joint.insert is not None
    limit, whose = holds
    bolts = _bolts(joint, read, read["force"], read["moment"])
    worst = max(b["pull_n"] for b in bolts)
    one.update(bolts=bolts, bolt_pull_n=worst, holds_n=limit, holds_from=whose)
    report.line(
        f"its {len(bolts)} bolt(s), {joint.bolt.name} into {joint.insert.name}: the most one has "
        f"to hold is a pull of {worst:.1f} N, with {bolts[0]['shear_n']:.1f} N across it, as "
        f"joints.{joint.name}.bolt_pull_n, against {limit:.0f} N ({whose})"
    )
    # In printed plastic the insert pulls out long before a steel bolt gives, so a bolt that
    # has anything to hold, with no figure for its insert, isn't read (the operator's ruling on
    # mqucifer/print-shop#160).
    one["insert_unread"] = (
        joint.insert.pull_n is None
        and joint.pull_n is None
        and worst > NOTHING * report.numbers["force_n"]
    )
    ways = {"on the coarser mesh": _bolts(joint, other, other["force"], other["moment"])}
    if statics is not None:
        ways["by statics"] = _bolts(joint, read, *statics)
    for how, theirs in ways.items():
        again = max(b["pull_n"] for b in theirs)
        one[f"bolt_pull_{how.split()[-1]}_n"] = again
        report.second(f"the joint {joint.name}'s worst bolt {how}: a pull of {again:.1f} N")
        if over(again, limit) != over(worst, limit):
            apart.append(
                f"the joint {joint.name}'s worst bolt holds {said(worst, limit, 1)} N by the "
                f"solve and {said(again, limit, 1)} N {how}, either side of its {limit:.0f} N"
            )
    if over(worst, limit):
        fails.append(
            f"the joint {joint.name}'s worst bolt has to hold {said(worst, limit, 1)} N, over "
            f"its {limit:.0f} N ({whose}): more bolts, further from the edge it turns about, "
            "or a joint where the load is lower"
        )


def _through(
    assembly: Assembly,
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    other: dict[str, Any],
    statics: tuple[np.ndarray, np.ndarray] | None,
    fails: list[str],
    apart: list[str],
    ways: list[dict[str, Any]],
    solids: list[Any],
) -> None:
    """A bolt right through both parts (mqucifer/print-shop#168): the bolt against its proof
    load, the pressure under each washer or the nut and on each hole's wall, and the plastic
    round each hole, solved again by itself. The joint's answer is the worst of them, named."""
    assert joint.bolt is not None and joint.nut is not None and joint.washer is not None
    name = joint.name
    if not read["flat"]:
        raise NoAnswer(
            f"the through joint {name}'s faces aren't in one plane: a bolt's pull is read "
            "square to one flat face. Give each flat face's joint to its own pair of parts"
        )
    normal = read["normal"]
    for way in ways:
        if abs(abs(float(way["axis"] @ normal)) - 1) > 1e-6:
            raise NoAnswer(
                f"the through joint {name}'s bolt at {_xyz(way['sides'][0]['seat'])} isn't "
                "square to the faces that meet: its pull is read square to them"
            )
    limit = joint.bolt.pull_n
    whose = f"the bolt {joint.bolt.name}: {joint.bolt.source}"
    bolts = _bolts(joint, read, read["force"], read["moment"])
    count = len(bolts)
    # What the second part's shank puts on the first part's hole, and the other way on its own.
    across = (read["force"] - float(read["force"] @ normal) * normal) / count
    shank = DIAMETER[joint.bolt.thread]
    parts = [assembly.part(n) for n in joint.between]
    force = report.numbers["force_n"]
    #: Every reading as its share of its limit, to name the nearest.
    shares: list[tuple[float, str]] = []
    worst = max(b["pull_n"] for b in bolts)
    shares.append((worst / limit, f"the bolt, at {worst:.1f} N of its {limit:.0f} N"))
    one.update(bolts=bolts, bolt_pull_n=worst, holds_n=limit, holds_from=whose)
    seat = "in a pocket, at a play of " + f"{joint.pocket:g} mm" if joint.pocket is not None else ""
    report.line(
        f"its {count} through-bolt(s), {joint.bolt.name} with the nut {joint.nut.name}"
        + (f" {seat}" if seat else "")
        + f" and the washer {joint.washer.name}: the most one has to hold is a pull of "
        f"{worst:.1f} N, with {bolts[0]['shear_n']:.1f} N across it, as "
        f"joints.{name}.bolt_pull_n, against {limit:.0f} N ({whose})"
    )
    if over(worst, limit):
        fails.append(
            f"the joint {name}'s worst bolt has to hold {said(worst, limit, 1)} N, over its "
            f"{limit:.0f} N ({whose}): more bolts, further from the edge it turns about, or a "
            "larger bolt"
        )
    again = {"on the coarser mesh": _bolts(joint, other, other["force"], other["moment"])}
    if statics is not None:
        again["by statics"] = _bolts(joint, read, *statics)
    for how, theirs in again.items():
        pull = max(b["pull_n"] for b in theirs)
        one[f"bolt_pull_{how.split()[-1]}_n"] = pull
        report.second(f"the joint {name}'s worst bolt {how}: a pull of {pull:.1f} N")
        if over(pull, limit) != over(worst, limit):
            apart.append(
                f"the joint {name}'s worst bolt holds {said(worst, limit, 1)} N by the solve "
                f"and {said(pull, limit, 1)} N {how}, either side of its {limit:.0f} N"
            )
    press_source = ""
    lifted: list[str] = []
    for index, (bolt, way) in enumerate(zip(bolts, ways, strict=True)):
        ends = []
        for k, (side, part, solid) in enumerate(zip(way["sides"], parts, solids, strict=True)):
            layers = part.layers_way()
            where = f"the joint {name}'s bolt at {_xyz(side['seat'])}, in {part.name}"
            press_source = part.material.press_source
            # Under the washer, or the nut.
            most, pressed = part.material.press(way["axis"], layers)
            pressure = bolt["pull_n"] / side["bears_mm2"]
            end: dict[str, Any] = {
                "part": part.name,
                "under": "nut" if side["on_nut"] else "washer",
                "seat": _list(side["seat"], 3),
                "bears_mm2": side["bears_mm2"],
                "ring_mm2": side["ring_mm2"],
                "seat_mm2": side["seat_mm2"],
                "pressure_mpa": pressure,
                "pressure_limit_mpa": most,
                "pressed": pressed,
                "hole_mm": side["hole_mm"],
                "hole_length_mm": side["hole_length_mm"],
            }
            hole = side["hole"]
            peaked = hole["peak"] is not None
            if peaked:
                end["peak"] = peak_saved(hole["peak"])
            gap = hole.get("channel")
            if gap is not None:
                end["channel"] = channel_saved(gap)
            shares.append(
                (pressure / most, f"the pressure under {side['under']} on {part.name}, at "
                 f"{pressure:.2f} MPa of {most:g} MPa")
            )  # fmt: skip
            report.line(
                f"  {side['under']} bears on {side['bears_mm2']:.2f} mm² of {part.name} round a "
                f"{'channel' if gap is not None else 'hole'} {side['hole_mm']:.2f} mm across and "
                f"{side['hole_length_mm']:.2f} mm long: "
                f"{pressure:.3f} MPa under the bolt's {bolt['pull_n']:.1f} N, pressed {pressed}, "
                f"against {most:g} MPa, as joints.{name}.bolts.{index}.ends.{k}.pressure_mpa"
            )
            if peaked:
                report.line(
                    f"  the bore in {part.name} is {peak_said(hole)}, as "
                    f"joints.{name}.bolts.{index}.ends.{k}.peak: its seat is the ring less the "
                    f"peak's cut, {side['seat_mm2']:.2f} of {side['ring_mm2']:.2f} mm²"
                )
                report.second(
                    f"the seat at {where}, by arithmetic, the ring less the peak's cut: "
                    f"{side['seat_mm2']:.3f} mm²; by the part's own faces, "
                    f"{side['bears_mm2']:.3f}"
                )
                if side["bears_mm2"] * load_through.SEATED > side["seat_mm2"]:
                    apart.append(
                        f"{where}: {side['under']} bears on {side['bears_mm2']:.3f} mm² by the "
                        f"part's faces, more than the {side['seat_mm2']:.3f} mm² its ring has "
                        "less the peak's cut"
                    )
            if gap is not None:
                assert joint.channel is not None
                report.line(
                    f"  the bolt lies in {part.name} in {channel_said(hole)}, as "
                    f"joints.{name}.bolts.{index}.ends.{k}.channel, and {side['under']} bears "
                    f"on {joint.channel.wall_said('nut' if k else 'head')}: its seat is the "
                    f"ring less the channel's cut, {side['seat_mm2']:.2f} of "
                    f"{side['ring_mm2']:.2f} mm²"
                )
                report.second(
                    f"the seat at {where}, by arithmetic, the ring less the channel's cut: "
                    f"{side['seat_mm2']:.3f} mm²; by the part's own faces, "
                    f"{side['bears_mm2']:.3f}"
                )
                if side["bears_mm2"] * load_through.SEATED > side["seat_mm2"]:
                    apart.append(
                        f"{where}: {side['under']} bears on {side['bears_mm2']:.3f} mm² by the "
                        f"part's faces, more than the {side['seat_mm2']:.3f} mm² its ring has "
                        "less the channel's cut"
                    )
            if not side["seated"]:
                less = f", and {side['seat_mm2']:.1f} mm² less the peak's cut" if peaked else ""
                if gap is not None:
                    assert joint.channel is not None
                    less = (
                        f", and {side['seat_mm2']:.1f} mm² less the channel's cut, on "
                        f"{joint.channel.wall_said('nut' if k else 'head')}"
                    )
                fails.append(
                    f"{where}: {side['under']} sits on {side['bears_mm2']:.1f} mm² of the part, "
                    f"of its ring's {side['ring_mm2']:.1f} mm²{less}: it overhangs an edge or a "
                    "step. Move the bolt in, or make its seat flat and wide enough"
                )
            if over(pressure, most):
                fails.append(
                    f"{where}: the plastic under {side['under']} is pressed by "
                    f"{said(pressure, most, 2)} MPa, {pressed}, over its {most:g} MPa: a "
                    "wider washer, more bolts, or a larger bolt"
                )
            for how, theirs in again.items():
                second = theirs[index]["pull_n"] / side["bears_mm2"]
                if over(second, most) != over(pressure, most):
                    apart.append(
                        f"{where}: the pressure under {side['under']} is "
                        f"{said(pressure, most, 2)} MPa by the solve and "
                        f"{said(second, most, 2)} MPa {how}, either side of its {most:g} MPa"
                    )
            # Where the shank leans on the hole's wall.
            push = across if k == 0 else -across
            if gap is not None:
                # A channel's open side carries nothing (mqucifer/print-shop#302): what pushes
                # the shank toward it lifts the part off the bolt, and only the rest bears.
                lifts = max(float(push @ gap["toward"]), 0.0)
                end["toward_open_n"] = lifts
                report.line(
                    f"  the channel's open side in {part.name} carries nothing: the shank is "
                    + (
                        f"pushed toward it by {lifts:.1f} N, as "
                        f"joints.{name}.bolts.{index}.ends.{k}.toward_open_n"
                        if lifts > NOTHING * force
                        else "not pushed toward it"
                    )
                )
                if lifts > NOTHING * force:
                    lifted.append(part.name)
                    fails.append(
                        f"{where}: the shank is pushed toward the channel's open side by "
                        f"{lifts:.1f} N, and nothing there holds it: the part comes off the "
                        "bolt that way. Turn the channel's open side away from the push, or hold "
                        "the parts that way by something else"
                    )
                push = push - lifts * np.asarray(gap["toward"], dtype=float)
            leans = float(np.linalg.norm(push))
            if leans > NOTHING * force:
                wall_most, wall_pressed = part.material.press(push / leans, layers)
                share_there = lean_share(hole, push / leans)
                on_wall = shank * side["hole_length_mm"] * share_there
                wall = leans / on_wall
                end.update(
                    wall_pressure_mpa=wall,
                    wall_bears_mm2=on_wall,
                    wall_limit_mpa=wall_most,
                    wall_pressed=wall_pressed,
                )
                shares.append(
                    (wall / wall_most, f"the shank on the hole's wall in {part.name}, at "
                     f"{wall:.2f} MPa of {wall_most:g} MPa")
                )  # fmt: skip
                report.line(
                    f"  the shank leans on {part.name}'s hole with {leans:.1f} N over its "
                    f"{shank:g} mm by {side['hole_length_mm']:.2f} mm"
                    + (
                        f", of which the {'peak' if peaked else 'channel'} leaves "
                        f"{100 * share_there:.1f}% for a lean "
                        f"{math.degrees(lean_off(hole, push)):.0f}° from its "
                        f"{'point' if peaked else 'open side'}, "
                        f"{on_wall:.2f} mm² as joints.{name}.bolts.{index}.ends.{k}.wall_bears_mm2"
                        if peaked or gap is not None
                        else ""
                    )
                    + f": {wall:.3f} MPa, pressed {wall_pressed}, against {wall_most:g} MPa"
                )
                if over(wall, wall_most):
                    fails.append(
                        f"{where}: the shank presses the hole's wall by "
                        f"{said(wall, wall_most, 2)} MPa, {wall_pressed}, over its "
                        f"{wall_most:g} MPa: a thicker part there, more bolts, or a larger bolt"
                    )
            # The plastic round the hole, solved by itself under this bolt.
            if bolt["pull_n"] > NOTHING * force or leans > NOTHING * force:
                _in_time("bolt", f"the reading round {where}")
                began = time.time()
                with _quiet():
                    solves = [
                        load_through.round_bolt(
                            solid, part, side, way["axis"], bolt["pull_n"], push, coarser
                        )
                        for coarser in (1.0, COARSER)
                    ]  # fmt: skip
                _timed("bolt", time.time() - began)
                fine, rough = solves
                end["round"] = {**fine, "coarse": rough}
                if "wall_share" in fine and leans > NOTHING * force:
                    by_mesh = shank * side["hole_length_mm"] * fine["wall_share"]
                    report.second(
                        f"what the shank leans on at {where}, by the mesh's triangles on what's "
                        f"left of the round: {by_mesh:.2f} mm², against {on_wall:.2f} by "
                        "arithmetic"
                    )
                    if over(leans / by_mesh, wall_most) != over(wall, wall_most):
                        apart.append(
                            f"{where}: the shank presses the hole's wall by "
                            f"{said(wall, wall_most, 2)} MPa by arithmetic and "
                            f"{said(leans / by_mesh, wall_most, 2)} MPa by the mesh's area, "
                            f"either side of its {wall_most:g} MPa"
                        )
                if leans > NOTHING * force:
                    # The wall that took the shear, beside the stretch the pressure is read on
                    # (mqucifer/print-shop#245).
                    end["sheared_mm2"] = fine["sheared_mm2"]
                    report.line(
                        f"  the solve round it puts the shear on {fine['sheared_mm2']:.2f} mm² "
                        f"of {part.name}'s round wall, the half leant on"
                        + (
                            (", less the peak," if peaked else ", the channel's round,")
                            if share_there < 1
                            else ""
                        )
                        + " of the "
                        f"{side['hole_length_mm']:.2f} mm from where the parts meet to the "
                        f"seat, and none past them, as "
                        f"joints.{name}.bolts.{index}.ends.{k}.sheared_mm2"
                    )
                report.line(
                    f"  the plastic round it in {part.name}, cut off {fine['held_mm']:.1f} mm "
                    f"from the bolt's axis and held there, under the bolt's pull and shear "
                    f"({fine['elements']} elements of at most {fine['mesh_mm']:g} mm): pulled "
                    f"across its layers by {fine['across_layers_pct']:.3f}% at most, at "
                    f"{_xyz(fine['across_at'])}, against {part.material.across_pct:g}%; its "
                    f"largest stretch any way is a {fine['stretch_is']} of "
                    f"{fine['stretch_pct']:.3f}%, at {_xyz(fine['stretch_at'])}, against "
                    f"{part.material.along_pct:g}%"
                )
                report.second(
                    f"the plastic round {where} on a mesh of at most {rough['mesh_mm']:g} mm: "
                    f"{rough['across_layers_pct']:.3f}% across its layers "
                    f"({_share(rough['across_layers_pct'], fine['across_layers_pct'])} apart) "
                    f"and {rough['stretch_pct']:.3f}% any way "
                    f"({_share(rough['stretch_pct'], fine['stretch_pct'])} apart)"
                )
                for key, most_pct, what, at in (
                    ("across_layers_pct", part.material.across_pct, "pulled across its layers",
                     "across_at"),
                    ("stretch_pct", part.material.along_pct, "stretched", "stretch_at"),
                ):  # fmt: skip
                    value = fine[key]
                    shares.append(
                        (value / most_pct, f"the plastic round the hole in {part.name}, {what} "
                         f"by {value:.3f}% of {most_pct:g}%")
                    )  # fmt: skip
                    if over(value, most_pct) != over(rough[key], most_pct):
                        apart.append(
                            f"{where}: the plastic round the hole is {what} by "
                            f"{said(value, most_pct)}% on the mesh and "
                            f"{said(rough[key], most_pct)}% on the coarser one, either side of "
                            f"its {most_pct:g}% limit"
                        )
                    elif over(value, most_pct):
                        fails.append(
                            f"{where}: the plastic round the hole is {what} by "
                            f"{said(value, most_pct, 2)}% at {_xyz(fine[at])}, over its "
                            f"material's {most_pct:g}%: it tears out there. Move the bolt "
                            "further from the edge, make the part thicker round it, or lay it "
                            "so its layers run along this pull"
                        )
            ends.append(end)
        bolt["ends"] = ends
    share, nearest = max(shares)
    one.update(gives_first=nearest, nearest_share=share)
    report.line(
        f"  of the bolt, the washers, the nut and the plastic, the nearest its limit is "
        f"{nearest}: {100 * share:.1f}% of it, as joints.{name}.nearest_share"
    )
    if lifted:
        # Nothing holds a channel's open side, so that gives first, whatever the rest is at.
        one["gives_first"] = f"the shank pushed toward the channel's open side in {lifted[0]}"
        report.line(
            f"  what gives first is the shank pushed toward the channel's open side in "
            f"{' and '.join(lifted)}, which holds nothing, as joints.{name}.gives_first"
        )
    if press_source:
        report.line(f"  what the plastic takes pressed is from {press_source}")
    report.note(
        f"the through joint {name} is read with its bolts snug and no more: a bolt tightened "
        "hard presses its washers on the plastic by its own preload, which isn't in this, and "
        "PETG creeps under it. The pressure is taken as even under each washer"
    )


#: A dovetail's faces said one by one, the most pushed first: the rest are counted.
FACES_SAID = 6
#: A latch that holds more than this many times what draws its tail out is said to be levered.
LEVER = 1.5


def _pressed(parts: list[PartAt], way: np.ndarray) -> tuple[float, str, str]:
    """The least pressure two parts that meet take pushed along `way`: the figure, which way
    that is to that part's layers, and the part."""
    found = [(*part.material.press(way, part.layers_way()), part.name) for part in parts]
    return min(found)


def _dovetail(
    assembly: Assembly,
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    other: dict[str, Any],
    statics: tuple[np.ndarray, np.ndarray] | None,
    fails: list[str],
    apart: list[str],
    caught: dict[str, Any] | None,
    solids: list[Any],
    played: list[dict[str, Any]],
) -> None:
    """A tapered dovetail (mqucifer/print-shop#170): which way the load drives its tail, its
    flanks and its seat as pushes alone, its latch against what's left to hold, and the tail's
    neck and the socket's walls solved again by themselves. The joint's answer is the worst of
    them, named. Glue is counted nowhere.

    **Play** (mqucifer/print-shop#188): the faces that don't touch where the file puts them and
    face each other across a fit's play (`played`, from `load_dovetail.play`) are faces too, a
    gap apart: the tail shifts and turns as one body until its play closes, and the faces that
    then touch carry the load. The latch's catch has its own play. The latch's tab is the
    latch's, and none of its faces bears for the tail."""
    name = joint.name
    tail, socket = joint.between
    parts = [assembly.part(n) for n in joint.between]
    slides = joint.slides_way()
    total = report.numbers["force_n"]
    assert joint.slides is not None
    out = ("-" if joint.slides[0] == "+" else "+") + joint.slides[1]

    def driven(force: np.ndarray) -> float:
        """How hard the load drives the tail along its way in: what the socket puts on the
        tail is the other way."""
        return -float(force @ slides)

    def faced(pieces: Any) -> tuple[Any, ...]:
        """The joint's faces in pieces: those that touch, less what's in the latch's box, and
        those with play, each piece with its gap and which face it's on."""
        centres, normals, areas, of_face = pieces
        keep = np.ones(len(areas), dtype=bool)
        if joint.latch is not None:
            latch = joint.latch
            keep = np.array([not latch.inside(c) for c in centres], dtype=bool)
        touching = int(of_face.max()) + 1 if len(of_face) else 0
        every = [centres[keep]], [normals[keep]], [areas[keep]], [of_face[keep]]
        gaps = [np.zeros(int(keep.sum()))]
        for k, patch in enumerate(played):
            count = len(patch["areas"])
            for into, more in zip((*every, gaps), (
                patch["centres"], np.tile(patch["normal"], (count, 1)), patch["areas"],
                np.full(count, touching + k), np.full(count, patch["gap"]),
            ), strict=True):  # fmt: skip
                into.append(more)
        return (*(np.concatenate(e) for e in every), np.concatenate(gaps))

    def bedded(pieces: Any, force: np.ndarray, moment: np.ndarray) -> tuple[Any, bool, Any]:
        """The faces' pushes that balance what the joint carries: by its flanks and its seat
        alone, and with its latch where those leave something over."""
        centres, normals, areas, _, gaps = pieces
        plain = dovetail.bed(centres, normals, areas, force, moment, read["at"], gaps, stiff)
        if plain["holds"] or caught is None:
            return plain, False, areas
        more = load_dovetail.catch_patches(caught, slides)
        every = (
            np.vstack([centres, more[0]]),
            np.vstack([normals, more[1]]),
            np.concatenate([areas, more[2]]),
        )
        clear = np.concatenate([gaps, more[3]])
        found = dovetail.bed(*every, force, moment, read["at"], clear, stiff)
        return found, True, every[2]

    arms = np.linalg.norm(read["pieces"][0] - read["at"], axis=1).max()
    if max(np.linalg.norm(read["force"]), np.linalg.norm(read["moment"]) / arms) <= (
        NOTHING * total
    ):
        one.update(slides=joint.slides, drive_n=driven(read["force"]), opens=False, latched=False)
        report.line(f"the dovetail joint {name} carries nothing to speak of under this load")
        return
    drive = driven(read["force"])
    leaving = drive < -NOTHING * total
    one.update(slides=joint.slides, drive_n=drive)
    if leaving:
        way = f"out of its seat, {out}, the way it would leave, by {-drive:.2f} N"
    elif drive > NOTHING * total:
        way = f"into its seat by {drive:.2f} N"
    else:
        way = "neither in nor out, to speak of"
    report.line(
        f"its tail, on {tail}, slides {joint.slides} into its socket, in {socket}, toward its "
        f"seat: this load drives it {way}, as joints.{name}.drive_n (more than 0 is into its "
        "seat)"
    )
    pieces = faced(read["pieces"])
    # How hard a face pushes back for each mm the tail presses into it: what decides whether
    # a face with play closes.
    stiff = dovetail.stiffness(parts[0].material.modulus_mpa, pieces[0], read["at"])
    seconds = {"on the coarser mesh": (faced(other["pieces"]), other["force"], other["moment"])}
    if statics is not None:
        seconds["by statics"] = (pieces, *statics)
    for how, (_, force, _) in seconds.items():
        again = driven(force)
        if (again < -NOTHING * total) != leaving:
            apart.append(
                f"the dovetail joint {name}'s tail is driven {drive:.2f} N toward its seat by "
                f"the solve and {again:.2f} N {how}, either side of leaving it"
            )
    # Its faces, pushing only.
    result, latched, areas = bedded(pieces, read["force"], read["moment"])
    centres, normals, _, of_face, gaps = pieces
    count = len(of_face)
    push = result["pressure"] * areas
    toward = result["pressed_mm"][:count]
    faces: list[dict[str, Any]] = []
    for index in sorted(set(of_face.tolist())):
        mine = of_face == index
        normal = (normals[mine] * areas[:count][mine, None]).sum(axis=0)
        normal = normal / np.linalg.norm(normal)
        limit, pressed, weaker = _pressed(parts, normal)
        gap = float(gaps[mine].max())
        faces.append(
            {
                "kind": dovetail.kind(normal, slides),
                "at": _list((centres[mine] * areas[:count][mine, None]).sum(axis=0)
                            / areas[:count][mine].sum(), 3),
                "normal": _list(normal),
                "area_mm2": float(areas[:count][mine].sum()),
                "push_n": float(push[:count][mine].sum()),
                "bears_mm2": float(areas[:count][mine][push[:count][mine] > 0].sum()),
                "pressure_mpa": float(result["pressure"][:count][mine].max()),
                "pressure_limit_mpa": limit,
                "pressed": pressed,
                "pressed_part": weaker,
                # Its play as drawn, and how much of it the tail's move closes.
                "play_mm": gap,
                "closed_mm": min(max(float(toward[mine].max()), 0.0), gap),
            }
        )  # fmt: skip
    faces.sort(key=lambda f: -f["push_n"])
    latch_n = float(push[count:].sum()) if latched else 0.0
    turn = result["turn_rad"]
    moves = result["shift_mm"] + np.cross(turn, centres - read["at"])
    one.update(
        faces=faces,
        opens=not result["holds"],
        latched=latched,
        left_n=_list(result["left_n"]),
        left_nmm=_list(result["left_nmm"]),
        shift_mm=_list(result["shift_mm"]),
        turn_deg=_list(np.degrees(turn)),
        faces_move_mm=float(np.linalg.norm(moves, axis=1).max()),
    )
    pushing = [f for f in faces if f["push_n"] > NOTHING * total]
    clear = [f for f in faces if f["play_mm"] > 0]
    report.line(
        f"  its {len(faces)} face(s) only push: {sum(f['kind'] == 'seat' for f in faces)} "
        f"seat(s), square to the slide, and {sum(f['kind'] == 'flank' for f in faces)} "
        f"flank(s), along it"
        + (f", {len(clear)} of them clear of the socket by a fit's play, up to "
           f"{max(f['play_mm'] for f in clear):.3f} mm" if clear else "")
        + f". Under this load {len(pushing)} push"
        + (", and the rest have come away or carry nothing" if len(pushing) < len(faces) else "")
        + f", as joints.{name}.faces, the most pushed first"
    )  # fmt: skip
    for face in pushing[:FACES_SAID]:
        report.line(
            f"    a {face['kind']} at {_xyz(face['at'])}, facing {_xyz(face['normal'])} out of "
            f"{tail}"
            + (f", its {face['play_mm']:.3f} mm of play closed" if face["play_mm"] > 0 else "")
            + f": pushed by {face['push_n']:.2f} N over {face['bears_mm2']:.1f} of its "
            f"{face['area_mm2']:.1f} mm², {face['pressure_mpa']:.3f} MPa at most, against "
            f"{face['pressure_limit_mpa']:g} MPa pressed {face['pressed']} in "
            f"{face['pressed_part']}"
        )
    if len(pushing) > FACES_SAID:
        report.line(f"    and {len(pushing) - FACES_SAID} more, pushed less")
    if clear or latched and caught is not None and caught["gap_mm"] > 0:
        shut = [f for f in clear if f["push_n"] > NOTHING * total]
        report.line(
            "  it's read the way a print takes this load: the tail shifts and turns as one "
            "body until its play closes, and only the faces that then touch push. It shifts "
            f"{_xyz(result['shift_mm'])} mm and turns {np.degrees(np.linalg.norm(turn)):.2f}° "
            f"about the joint's middle, its faces moving {one['faces_move_mm']:.3f} mm at "
            f"most, as joints.{name}.shift_mm and turn_deg; {len(shut)} of the faces with play "
            "close"
            + (
                ", and of the rest the nearest to closing came "
                + ", ".join(
                    f"{f['closed_mm']:.3f} of its {f['play_mm']:.3f} mm"
                    for f in sorted((f for f in clear if f not in shut),
                                    key=lambda f: f["closed_mm"] - f["play_mm"])[-2:][::-1]
                )
                if len(shut) < len(clear) else ""
            )  # fmt: skip
        )
        if one["faces_move_mm"] > load_dovetail.PLAY:
            report.line(
                f"  its faces move {one['faces_move_mm']:.3f} mm at most, more than the "
                f"{load_dovetail.PLAY:g} mm of play read: faces further apart than that, which "
                "aren't read, may close first, and the turn is read as a small one"
            )
    for how, (again_pieces, force, moment) in seconds.items():
        again, again_latched, again_areas = bedded(again_pieces, force, moment)
        held = float((again["pressure"] * again_areas)[len(again_pieces[3]) :].sum())
        report.second(
            f"the dovetail joint {name}'s faces {how}: "
            + ("they balance what it carries" if again["holds"] else "they leave "
               f"{np.linalg.norm(again['left_n']):.2f} N of it held by nothing")
            + (f", its latch holding {held:.2f} N" if again_latched else "")
        )  # fmt: skip
        if again["holds"] != result["holds"]:
            apart.append(
                f"the dovetail joint {name}'s faces balance what it carries "
                + ("by the solve and not " if result["holds"] else "not by the solve and do ")
                + how
            )
    if not result["holds"]:
        left, turn = np.linalg.norm(result["left_n"]), np.linalg.norm(result["left_nmm"])
        along = float(result["left_n"] @ slides)
        one.update(gives_first="nothing holds what its faces can't push back on")
        if caught is None and leaving:
            fails.append(
                f"the dovetail joint {name}: this load drives the tail out of its seat by "
                f"{-drive:.2f} N, {out}, and it has no latch: only friction or glue would "
                "hold it, and neither is counted. Give it a latch at its seat, or point the "
                "taper so that this load seats it"
            )
        else:
            fails.append(
                f"the dovetail joint {name} opens: its flanks and its seat can only push, and "
                f"pushing they leave {left:.2f} N and {turn:.1f} N·mm of what it carries held "
                "by nothing"
                + (", its latch counted" if caught is not None else "")
                + (
                    f"; {along:.2f} N of that is along the way out, {out}, where a taper's "
                    "flanks squeeze the tail back out as they're pushed, and only a latch "
                    "would hold it: friction and glue aren't counted"
                    if along > NOTHING * total and caught is None
                    else ": a face there would have to pull"
                )
                + (
                    f". Faces up to {load_dovetail.PLAY:g} mm apart are counted as the tail's "
                    "play closes them, and none that would hold this does"
                )
            )
        report.line(
            f"  pushing alone, its faces leave {left:.2f} N, {_xyz(result['left_n'])}, and "
            f"{turn:.1f} N·mm of what the joint carries held by nothing, as "
            f"joints.{name}.left_n: the tail's neck and the socket's walls aren't read under "
            "a load the joint doesn't hold"
        )
        return
    #: Every reading as its share of its limit, to name the nearest.
    shares: list[tuple[float, str]] = []
    for face in pushing:
        most = face["pressure_limit_mpa"]
        where = f"the {face['kind']} at {_xyz(face['at'])}"
        shares.append(
            (face["pressure_mpa"] / most,
             f"the pressure on {where}, at {face['pressure_mpa']:.2f} MPa of {most:g} MPa")
        )  # fmt: skip
        if over(face["pressure_mpa"], most):
            fails.append(
                f"the dovetail joint {name}: {where} is pressed by "
                f"{said(face['pressure_mpa'], most, 2)} MPa, {face['pressed']} in "
                f"{face['pressed_part']}, over its {most:g} MPa: a longer or deeper tail, or a "
                "seat square to the slide to take the weight off the taper"
            )
    press_source = parts[0].material.press_source
    if caught is not None:
        drawn = -drive if leaving else 0.0
        _latch(assembly, joint, report, one, caught, (latch_n, drawn), solids, shares, fails,
               apart)  # fmt: skip
    carried = (read["force"], read["moment"], read["at"])
    for part, solid, role in zip(parts, solids, ("the tail's neck", "the socket's walls"),
                                 strict=True):  # fmt: skip
        with _quiet():
            fine, rough = (
                load_dovetail.around(
                    solid, part, joint, read["faces"], carried, caught if latched else None, by,
                    played, stiff,
                )  # fmt: skip
                for by in (1.0, COARSER)
            )
        key = "neck" if part.name == tail else "walls"
        one[key] = {**fine, "coarse": rough, "part": part.name}
        where = f"{role}, in {part.name}"
        report.line(
            f"  {where}, cut off {fine['held_mm']:.1f} mm from the joint and held there "
            f"({', '.join(fine['cut_on'])}), under the faces' pushes ({fine['elements']} "
            f"elements of at most {fine['mesh_mm']:g} mm): pulled across its layers by "
            f"{fine['across_layers_pct']:.3f}% at most, at {_xyz(fine['across_at'])}, against "
            f"{part.material.across_pct:g}%; its largest stretch any way is a "
            f"{fine['stretch_is']} of {fine['stretch_pct']:.3f}%, at "
            f"{_xyz(fine['stretch_at'])}, against {part.material.along_pct:g}%, as "
            f"joints.{name}.{key}"
        )
        report.second(
            f"the dovetail joint {name}: {where} on a mesh of at most {rough['mesh_mm']:g} mm: "
            f"{rough['across_layers_pct']:.3f}% across its layers "
            f"({_share(rough['across_layers_pct'], fine['across_layers_pct'])} apart) and "
            f"{rough['stretch_pct']:.3f}% any way "
            f"({_share(rough['stretch_pct'], fine['stretch_pct'])} apart)"
        )
        _strained(f"the dovetail joint {name}: {where}", where, fine, rough, part, shares, fails,
                  apart, "make it wider or deeper there, round its inside corners, or lay the "
                  "part so its layers run along this pull")  # fmt: skip
    share, nearest = max(shares) if shares else (0.0, "nothing: it carries nothing to speak of")
    one.update(gives_first=nearest, nearest_share=share)
    report.line(
        f"  of its faces, its latch, the tail's neck and the socket's walls, the nearest its "
        f"limit is {nearest}: {100 * share:.1f}% of it, as joints.{name}.nearest_share"
    )
    if press_source:
        report.line(f"  what the plastic takes pressed is from {press_source}")
    report.note(
        f"the dovetail joint {name} is read with no glue and no friction: each face pushes "
        "square to itself, in proportion to how far the tail is pressed into it, the tail "
        "taken as stiff, which is a reading of its balance and not a contact solve. Its play "
        "is read as a print takes it: the tail shifts and turns from where the file puts it "
        f"until faces up to {load_dovetail.PLAY:g} mm apart close, on its flat faces only; a "
        "curved face bears only where it's drawn touching. Its way in, and its latch's tab "
        "bent to let the tail by, are read before anything is solved, as ways_in"
    )


def _strained(
    where: str,
    short: str,
    fine: dict[str, Any],
    rough: dict[str, Any],
    part: PartAt,
    shares: list[tuple[float, str]],
    fails: list[str],
    apart: list[str],
    change: str,
) -> None:
    """A piece of a part solved by itself on two meshes, against its material's limits."""
    for key, most, what, at in (
        ("across_layers_pct", part.material.across_pct, "pulled across its layers", "across_at"),
        ("stretch_pct", part.material.along_pct, "stretched", "stretch_at"),
    ):
        value = fine[key]
        shares.append((value / most, f"{short}, {what} by {value:.3f}% of {most:g}%"))
        if over(value, most) != over(rough[key], most):
            apart.append(
                f"{where} is {what} by {said(value, most)}% on the mesh and "
                f"{said(rough[key], most)}% on the coarser one, either side of its {most:g}% "
                "limit"
            )
        elif over(value, most):
            fails.append(
                f"{where} is {what} by {said(value, most, 2)}% at {_xyz(fine[at])}, over its "
                f"material's {most:g}%: {change}"
            )


def _latch(
    assembly: Assembly,
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    caught: dict[str, Any],
    holding: tuple[float, float],
    solids: list[Any],
    shares: list[tuple[float, str]],
    fails: list[str],
    apart: list[str],
) -> None:
    """A dovetail's latch against what it has to hold, of what draws the tail out, `holding`:
    the pressure on its catch, and its tab, solved by itself with that pull on its hook."""
    name = joint.name
    holds, drawn = holding
    parts = [assembly.part(n) for n in joint.between]
    on = assembly.part(caught["on"])
    solid = solids[joint.between.index(caught["on"])]
    saved: dict[str, Any] = {
        "on": caught["on"],
        "catch_mm2": caught["area_mm2"],
        "catch_at": _list(caught["at"], 3),
        "play_mm": caught["gap_mm"],
        "holds_n": holds,
    }
    one["latch"] = saved
    report.line(
        f"  its latch, a tab on {caught['on']}, catches {caught['other']} over "
        f"{caught['area_mm2']:.2f} mm² round {_xyz(caught['at'])}, {caught['gap_mm']:.3f} mm "
        "clear of it along the slide, read from the two parts' own faces"
    )
    if holds <= NOTHING * report.numbers["force_n"]:
        report.line("  under this load its flanks and its seat hold it, and the latch nothing")
        return
    most, pressed, weaker = _pressed(parts, joint.slides_way())
    pressure = holds / caught["area_mm2"]
    saved.update(pressure_mpa=pressure, pressure_limit_mpa=most)
    shares.append(
        (pressure / most, f"the pressure on the latch's catch, at {pressure:.2f} MPa of "
         f"{most:g} MPa")
    )  # fmt: skip
    report.line(
        f"  the latch has {holds:.2f} N to hold, as joints.{name}.latch.holds_n: what draws "
        f"the tail out, and what its flanks add as they're pushed. On its catch that's "
        f"{pressure:.3f} MPa, against {most:g} MPa pressed {pressed} in {weaker}"
    )
    if drawn > 0 and holds > LEVER * drawn:
        # Why it's more than the pull (mqucifer/print-shop#188): the faces it turns on.
        report.line(
            f"  that's {holds / drawn:.1f} times the {drawn:.2f} N that draws the tail out: "
            "the tail turns on the faces that push beside the latch before any further off "
            "closes, and the latch and those faces pull against each other, as a lever's "
            "ends do"
        )
    if over(pressure, most):
        fails.append(
            f"the dovetail joint {name}'s latch: its catch is pressed by "
            f"{said(pressure, most, 2)} MPa, {pressed} in {weaker}, over its {most:g} MPa: a "
            "deeper or wider hook"
        )
    with _quiet():
        fine, rough = (load_dovetail.tab(solid, on, joint, caught, holds, by)
                       for by in (1.0, COARSER))  # fmt: skip
    saved["tab"] = {**fine, "coarse": rough}
    report.line(
        f"  its tab, {fine['thickness_mm']:.2f} mm thick at its root, held there with that "
        f"pull on its hook ({fine['elements']} elements of at most {fine['mesh_mm']:g} mm): "
        f"pulled across its layers by {fine['across_layers_pct']:.3f}% at most, at "
        f"{_xyz(fine['across_at'])}, against {on.material.across_pct:g}%; its largest stretch "
        f"any way is a {fine['stretch_is']} of {fine['stretch_pct']:.3f}%, at "
        f"{_xyz(fine['stretch_at'])}, against {on.material.along_pct:g}%, as "
        f"joints.{name}.latch.tab"
    )
    report.second(
        f"the dovetail joint {name}'s tab on a mesh of at most {rough['mesh_mm']:g} mm: "
        f"{rough['across_layers_pct']:.3f}% across its layers "
        f"({_share(rough['across_layers_pct'], fine['across_layers_pct'])} apart) and "
        f"{rough['stretch_pct']:.3f}% any way "
        f"({_share(rough['stretch_pct'], fine['stretch_pct'])} apart)"
    )
    _strained(f"the dovetail joint {name}'s tab", "the latch's tab", fine, rough, on, shares,
              fails, apart, "a thicker or wider tab, a hook nearer its line, or the part laid "
              "so the tab's length runs along its layers")  # fmt: skip


def _touching(
    joint: Joint,
    args: Any,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    force: float,
    fails: list[str],
) -> None:
    import FreeCAD as App
    import Part

    if not read["flat"]:
        report.note(
            f"the touching joint {joint.name}'s faces aren't in one plane, so it isn't judged: "
            f"the solve pulls on {read['pulled_n']:.2f} N of it and pushes on "
            f"{read['pushed_n']:.2f} N, and faces that only touch can't pull"
        )
        return
    along, shear = one["pull_n"], one["shear_n"]
    if abs(along) <= NOTHING * force and shear <= NOTHING * force:
        report.line(f"the joint {joint.name} carries nothing to speak of")
        return
    if along > NOTHING * force:
        fails.append(
            f"the touching joint {joint.name} is pulled apart by {along:.2f} N, and nothing "
            "holds it: bond it, bolt it, or put it where the load pushes it together"
        )
        return
    if along >= -NOTHING * force:
        fails.append(
            f"the touching joint {joint.name} carries {shear:.2f} N along its faces and "
            f"{one['bending_nmm']:.1f} N·mm of bending with no push to hold it: it slides or "
            "tips, as nothing holds it"
        )
        return
    normal = read["normal"]
    bending = read["moment"] - (read["moment"] @ normal) * normal
    point = read["at"] + np.cross(normal, bending) / along
    gap = min(f.distToShape(Part.Vertex(App.Vector(*point)))[0] for f in read["faces"])
    one["push_at"], one["push_off_faces_mm"] = _list(point, 3), gap
    needs = shear / -along
    one["friction_needed"] = needs
    report.line(
        f"it's pushed together by {-along:.2f} N, through {_xyz(point)}, "
        + ("on the faces that meet" if gap <= ON_FACE else f"{gap:.2f} mm off the faces that meet")
        + f"; to stay put along them it needs a friction of {needs:.3f}, as "
        f"joints.{joint.name}.friction_needed"
        + ("" if args.friction is not None else ", which has no limit unless --friction gives one")
    )
    if gap > ON_FACE:
        fails.append(
            f"the touching joint {joint.name}'s push passes {gap:.2f} mm outside the faces that "
            "meet, so it tips open: nothing there pulls it shut"
        )
    if args.friction is not None and over(needs, args.friction):
        fails.append(
            f"the touching joint {joint.name} needs a friction of {said(needs, args.friction)} to "
            f"stay put, over the {args.friction:g} given: it slides"
        )
