"""Each printed file's downward faces, read at a stop by the print loop's own overhang reading
(mqucifer/print-shop#147).

A request said its channel prints open side up with no supports. The run was given a lesson that a
catch's underside must slope, and used it; it still built the catch with a flat 0.6 mm underside,
argued it was bridgeable, and nothing in the needs or the stops read it. A design run's lid had a
flat 1.5 mm ledge at the top of its print, which the Sponsor caught by eye. The print side already
reads this: `print_shop.setup.features.overhang_to_support` is the area of a seated mesh that
would need support, as the orientation step weighs each pose. So the stops read every file to be
printed with that function, not a new bench check (the Sponsor, 2026-11-15: "We need to use tools
at our disposal").

**How it's read.** The bench lays each file on the side its bed need or `done`'s `on_bed` names,
`-z` where neither does, and meshes it, by the refinement pass's own helper (`bench/refine.py`'s
`mesh`), in a container. The harness then reads that mesh with `features.overhang_to_support`
and `features.overhang_places`: the area that would need support, and each stretch of height
it's in, turned back into the file's own coordinates. A round or chamfer rising from the plate
is left out, as the print loop leaves it out, and said.

**What it holds.** It's a reading, shown beside `thin` at each stop, for every file. Where the
request, or a need on the file, says it prints with no supports, any area to support is a check
that doesn't pass: at `done` the round isn't met, and at an `ask` it's listed with the checks that
don't pass. Nothing is refused: `ask` stays open, and a person may still take the part.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from print_shop.bench.refine import MARK, ON_BED
from print_shop.rounds import judge
from print_shop.rounds.box import Box
from print_shop.rounds.needs import BED, Need
from print_shop.setup import features

#: The check's name, as the ledger's `standing` rows and a stop's record keep it.
DOWNWARD = "downward"
HEADING = (
    "Each file to be printed, as it lies on the bed: its downward faces, by the print loop's own "
    "overhang reading"
)
#: An area to support under this, in mm², is none: the print loop says an overhang to the mm².
LEAST_MM2 = 0.5
#: The most stretches of height said one by one.
SHOWN = 4
#: Words that say a part prints with no supports, read in the request and in a need's own words.
NO_SUPPORTS = re.compile(
    r"\b(?:no|without(?: any)?|zero|free of|not? (?:need|use|require|want)\w*(?: any)?)\s+"
    r"(?:\w+\s+)?supports?\b(?!\s+(?:for|from|of)\b)"
    r"|\bsupport[- ]?(?:free|less)\b|\bsupports? (?:off|disabled)\b",
    re.IGNORECASE,
)


def said_no_supports(text: str) -> str:
    """The line of a text that says the part prints with no supports, or nothing."""
    for line in re.split(r"(?<=[.!?])\s+|\n", text):
        if NO_SUPPORTS.search(line):
            return line.strip(" -*\t")
    return ""


def unsupported(request: str, needs: list[Need], files: list[str]) -> dict[str, str]:
    """Each file that's to print with no supports, with the words that say so: every file where
    the request says it, and the files a need names where the need does, or every file where it
    names none."""
    found: dict[str, str] = {}
    by_request = said_no_supports(request)
    if by_request:
        found = {name: f'the request says "{by_request}"' for name in files}
    for need in needs:
        words = said_no_supports(" \n".join([need.what, need.quote, need.look_for]))
        if not words:
            continue
        named = [f for f in [*need.files(), need.file] if f in files] or files
        for name in named:
            found.setdefault(name, f'the need `{need.id}` says "{words}"')
    return found


def sides(needs: list[Need], on_bed: dict[str, str] | None = None) -> dict[str, str]:
    """The side each file lies on: `done`'s `on_bed` where it's given, a bed need's otherwise."""
    found = {n.file: n.side for n in needs if n.by == BED and n.file and n.side}
    return {**found, **(on_bed or {})}


def _in_the_file(low: Any, high: Any, side: str) -> list[list[float]]:
    """A box of the laid mesh, turned back to the file as it's stored."""
    axis, degrees = ON_BED[side]
    back = trimesh.transformations.rotation_matrix(  # type: ignore[no-untyped-call]
        np.radians(-degrees), axis
    )[:3, :3]
    corners = np.array(
        [[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
    )
    turned = corners @ back.T
    return [
        [round(float(v), 2) + 0.0 for v in turned.min(axis=0)],
        [round(float(v), 2) + 0.0 for v in turned.max(axis=0)],
    ]


def _point(p: list[float]) -> str:
    return "(" + ", ".join(f"{v:g}" for v in p) + ")"


def reading(name: str, side: str, mesh: trimesh.Trimesh, no_supports: str) -> judge.Standing:
    """One file's downward faces, from its mesh as it lies on the bed."""
    area, flare = features.overhang_to_support(mesh)
    places: list[dict[str, Any]] = [
        {
            "area_mm2": round(p.area_mm2, 2),
            "box": _in_the_file(p.low, p.high, side),
            "flat_mm2": round(p.flat_mm2, 2),
            "steepest_deg": round(p.steepest_deg, 1),
        }
        for p in features.overhang_places(mesh)
    ]
    numbers: dict[str, Any] = {
        "side": side,
        "to_support_mm2": round(area, 2),
        "rising_from_the_plate_mm2": round(flare, 2),
        "places": places,
        "no_supports": no_supports,
    }
    lying = f"lying on its {side} side"
    rising = (
        f" A round or chamfer rising from the plate, {flare:.1f} mm², is carried by the layers "
        "under it and isn't counted."
        if flare >= LEAST_MM2
        else ""
    )
    if area < LEAST_MM2:
        why = (
            f"{lying}, no face looks down past {features._OVERHANG_DEG:g}° off the plate: "
            f"nothing would need support.{rising}"
        )
        command = _command(name, side)
        return judge.Standing(DOWNWARD, name, command, True, False, "NONE", why, numbers)
    where = "; ".join(
        f"{p['area_mm2']:g} mm² from {_point(p['box'][0])} to {_point(p['box'][1])}"
        + (f", {p['flat_mm2']:g} mm² of it flat, a ceiling or a bridge" if p["flat_mm2"] else "")
        + f", {p['steepest_deg']:g}° past vertical at its worst"
        for p in places[:SHOWN]
    )
    more = f"; and {len(places) - SHOWN} more, in the record" if len(places) > SHOWN else ""
    why = (
        f"{lying}, {area:.1f} mm² looks down past {features._OVERHANG_DEG:g}° with nothing "
        f"under it, and would need support: {where}{more}, in the file's own coordinates.{rising}"
    )
    if no_supports:
        why += (
            f" And {no_supports}: slope each of these faces to {features._OVERHANG_DEG:g}° or "
            "steeper, as a chamfer under a ledge, or lay the file on another side"
        )
    return judge.Standing(
        DOWNWARD, name, _command(name, side), not no_supports, False, "NEEDS SUPPORT", why, numbers
    )


def _command(name: str, side: str) -> str:
    return f"downward faces of {name}, on its {side} side"


def _unread(name: str, side: str, why: str) -> judge.Standing:
    return judge.Standing(
        DOWNWARD, name, _command(name, side), False, True, "", f"it couldn't be read: {why}"
    )


def read(
    box: Box,
    files: list[str],
    laid: dict[str, str],
    where: Path,
    kept: Path,
    no_supports: dict[str, str] | None = None,
) -> list[judge.Standing]:
    """Each printed file's downward faces: laid on its side and meshed by the bench, in a
    container, then read here by the print loop's own function. A given file under `references/`
    isn't printed, so it isn't read."""
    mine = [name for name in files if not name.startswith("references/")]
    if not mine:
        return []
    # A folder of its own each time: one removed and made again under a container's mount isn't
    # there for the next container.
    scratch = kept / f"{DOWNWARD}-{uuid.uuid4().hex[:8]}"
    scratch.mkdir(parents=True)
    copies: dict[str, str] = {}
    for i, name in enumerate(mine):
        source = where / name
        if source.is_file():
            copies[name] = f"{i}__{Path(name).name}"
            shutil.copyfile(source, scratch / copies[name])
    request = {
        "request": "mesh",
        "files": list(copies.values()),
        "out": "laid",
        "on_bed": {copy: laid.get(name, "-z") for name, copy in copies.items()},
    }
    answer: dict[str, Any] = {}
    failed = ""
    if copies:
        ran = box.helper("refine", [json.dumps(request)], timeout=600, where=scratch)
        if MARK in ran.output:
            answer = json.loads(ran.output.split(MARK, 1)[1].splitlines()[0]).get("files") or {}
        else:
            failed = f"the bench didn't lay it on the bed (exit {ran.code})"
    found = []
    for name in mine:
        side = laid.get(name, "-z")
        row = answer.get(copies.get(name, ""), {})
        stl = scratch / str(row.get("stl") or "")
        if name not in copies:
            found.append(_unread(name, side, "the file isn't there"))
        elif failed or row.get("unread") or not stl.is_file():
            found.append(_unread(name, side, failed or str(row.get("unread") or "no mesh came")))
        else:
            mesh = trimesh.load_mesh(str(stl), force="mesh")
            found.append(reading(name, side, mesh, (no_supports or {}).get(name, "")))
    return found


def said(found: list[judge.Standing]) -> list[str]:
    """Each file's reading as a line, for a reply or a stop."""
    lines = []
    for one in found:
        mark = "can't tell" if one.unsure else "ok" if one.passed else "NEEDS SUPPORT"
        lines.append(f"- {one.file}, {mark}: {one.why}")
    return lines
