"""A dovetail's faces, pushing only (mqucifer/print-shop#170), in plain Python and NumPy.

A leg taller than the bed is split, and joined by a tapered dovetail: a tail on one part that
slides into a socket in the other until it's home on its seat. `load` solves every joint as one
piece, its faces tied all over, and tied faces pull as well as they push. A dovetail's don't:
a flank or a seat pushes back on the tail, and comes away from it when it's pulled. So what the
joint carries, which the tied solve does give, is put back on the faces as pushes alone, here.

**Each face by the way the tail slides in** (`kind`). The tail goes in along `slides`, toward
its seat. A face of the tail whose way out of the tail is nearer that than square to it stops
the slide: a **seat**. Any other face runs along the slide, square to it or leaning into it as
a taper's does: a **flank**. A face that leans the other way, against the way in, would have
stopped the tail going in at all, so the joint isn't the one the file describes, and that's
said.

**The pushes** (`bed`). The tail is taken as stiff, bedded on its faces: it shifts and turns
a little as one body, and each bit of each face pushes back in proportion to how far the tail
has pressed into it there, and not at all where the tail has come away. That's a footing's
bearing pressure with no tension, on faces that lie every way. The shift and the turn are the
six numbers that balance what the joint carries, found by Newton's method on a sum that's
convex, so there's one answer. Friction isn't in it: a face pushes square to itself only,
which is the careful reading of a taper, where a wedge's push is its load over the taper's
slope.

**Play** (mqucifer/print-shop#188). A printed tail is a slip fit in its socket, its flanks a
few tenths of a mm clear, and the file draws it so. A face that's clear pushes back only once
the tail has moved that far into it: the tail shifts or turns until its play closes, and the
faces that then touch carry the load. Read with only the faces drawn touching, an off-centre
latch and the seat beside it levered against each other for many times the weight they held,
and a sideways push landed on whatever small faces were drawn touching. A weight down the slide
stays on the seat: the seat stops the tail within thousandths of a mm, far short of a flank's
play. That needs the faces' own stiffness, a block of the plastic as deep as the joint is wide.

**What nothing pushes back against** is left over, and that's how a dovetail fails: a load
that draws the tail back out the way it came in, or one a face would have to pull to carry.
A weak spring on the tail, `SOFT` of the faces' own stiffness, takes what the faces can't, so
there's always an answer, and what's in that spring is the part of the load that's left over.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

#: A face is a seat where its way out of the tail is nearer the way in than square to it.
SEAT = math.cos(math.radians(45))
#: A face leans against the way in past this.
AGAINST = 1e-6
#: The spring that takes what no face pushes back against, as a share of the faces' stiffness.
SOFT = 1e-9
#: The pushes balance the load within this share of it, or what's left is said.
LEFT = 1e-3
#: Newton's steps at most. A sum of this kind is settled in a handful.
STEPS = 200


def kind(normal: Any, slides: Any) -> str:
    """What a face of the tail is, by its way out of the tail, `normal`, and the way the tail
    slides in: `seat`, `flank`, or `against` where it faces the way out."""
    along = float(np.asarray(normal, dtype=float) @ np.asarray(slides, dtype=float))
    if along < -AGAINST:
        return "against"
    return "seat" if along > SEAT else "flank"


def stiffness(modulus: float, centres: np.ndarray, about: np.ndarray) -> float:
    """How hard a face pushes back for each mm the tail presses into it, in MPa/mm: the
    plastic's stiffness over the joint's own reach from `about`, as a block of it that deep
    pressed square. What it changes is only how far the faces that bear are pressed, a few
    thousandths of a mm here, which is what decides whether a face with play closes."""
    arms = np.asarray(centres, dtype=float).reshape(-1, 3) - np.asarray(about, dtype=float)
    reach = float(np.linalg.norm(arms, axis=1).max()) if len(arms) else 1.0
    return float(modulus) / (reach if reach > 0 else 1.0)


def bed(
    centres: np.ndarray,
    normals: np.ndarray,
    areas: np.ndarray,
    force: np.ndarray,
    moment: np.ndarray,
    about: np.ndarray,
    gaps: np.ndarray | None = None,
    stiff: float | None = None,
) -> dict[str, Any]:
    """The pushes on a tail's faces that balance what the joint carries. Each face is given in
    small flat pieces: its `centres`, its `normals`, out of the tail, and its `areas`, in mm
    and mm². `force` and `moment`, about `about`, are what the socket puts on the tail, in N
    and N·mm. It gives each piece's `pressure`, in MPa, never less than 0; `left_n` and
    `left_nmm`, the force and the moment about `about` that no push balances; and `holds`,
    whether that's nothing.

    **Play** (mqucifer/print-shop#188): `gaps` is how far each piece stands clear of what it
    would bear on, in mm along its normal, 0 where it touches. A piece pushes back only once
    the tail has moved that far into it, so the tail shifts and turns as one body until its
    play closes, and the faces that then touch carry the load. That needs the faces' own
    stiffness, `stiff` in MPa/mm (`stiffness`), and with it the move is in mm: `shift_mm`,
    the tail's shift, `turn_rad`, its turn about `about`, and `pressed_mm`, how far each piece
    has moved toward what it bears on. With no play, the pushes are the same whatever
    `stiff` is."""
    centres = np.asarray(centres, dtype=float).reshape(-1, 3)
    normals = np.asarray(normals, dtype=float).reshape(-1, 3)
    areas = np.asarray(areas, dtype=float).reshape(-1)
    gaps = np.zeros(len(areas)) if gaps is None else np.asarray(gaps, dtype=float).reshape(-1)
    if stiff is None:
        if (gaps > 0).any():
            raise ValueError("a face with play needs the faces' stiffness to say when it closes")
        stiff = 1.0
    force, moment = np.asarray(force, dtype=float), np.asarray(moment, dtype=float)
    arms = centres - np.asarray(about, dtype=float)
    reach = float(np.linalg.norm(arms, axis=1).max()) if len(arms) else 1.0
    reach = reach if reach > 0 else 1.0
    load = np.concatenate([force, moment / reach])
    size = float(np.linalg.norm(load))
    total = float(areas.sum())
    if size <= 0 or total <= 0:
        return {
            "pressure": np.zeros(len(areas)),
            "left_n": force.copy(),
            "left_nmm": moment.copy(),
            "holds": size <= 0,
            "shift_mm": np.zeros(3),
            "turn_rad": np.zeros(3),
            "pressed_mm": np.zeros(len(areas)),
        }
    # How far the tail presses into each piece, for a shift and a turn of one each.
    into = np.hstack([normals, np.cross(arms, normals) / reach])
    share = areas / total
    target = load / size
    # Each piece's play in the same measure as the move: a move of `moved` is
    # `moved · size / (stiff · total)` mm.
    scale = size / (stiff * total)
    clear = gaps / scale

    def value(moved: np.ndarray) -> float:
        pressed = np.maximum(into @ moved - clear, 0.0)
        return float(0.5 * share @ pressed**2 + 0.5 * SOFT * moved @ moved + target @ moved)

    moved = np.zeros(6)
    for _ in range(STEPS):
        pressed = into @ moved - clear
        on = pressed > 0
        slope = into.T @ (share * np.maximum(pressed, 0.0)) + SOFT * moved + target
        if float(np.linalg.norm(slope)) < 1e-13:
            break
        curve = (into[on] * share[on, None]).T @ into[on] + SOFT * np.eye(6)
        step = -np.linalg.solve(curve, slope)
        before, down, length = value(moved), float(slope @ step), 1.0
        while value(moved + length * step) > before + 1e-4 * length * down and length > 1e-12:
            length /= 2
        moved = moved + length * step
    pressure = np.maximum(into @ moved - clear, 0.0) * size / total
    left = -SOFT * moved * size
    left_n, left_nmm = left[:3], left[3:] * reach
    most = max(float(np.linalg.norm(force)), float(np.linalg.norm(moment)) / reach)
    holds = (
        float(np.linalg.norm(left_n)) <= LEFT * most
        and float(np.linalg.norm(left_nmm)) / reach <= LEFT * most
    )
    # The tail moves into the faces that push back on it: a point of it at `r` from `about`
    # moves `shift + turn × r`.
    shift, turn = moved[:3] * scale, moved[3:] * scale / reach
    return {
        "pressure": pressure,
        "left_n": left_n,
        "left_nmm": left_nmm,
        "holds": bool(holds),
        "shift_mm": shift,
        "turn_rad": turn,
        "pressed_mm": (into @ moved) * scale,
    }
