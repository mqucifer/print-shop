"""The commands, inside the image. `scripts/bench.py` runs them in a container from the machine.

Exit codes: 0 the answer stands and nothing's wrong, 1 both methods agree that something's
wrong, 2 there's no answer to rely on, 3 the command itself was wrong.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

from .report import NoAnswer, Report


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(3, f"usage error: {message}\n{self.format_usage()}")

    def parse_args(self, args: Any = None, namespace: Any = None) -> Any:
        given = sys.argv[1:] if args is None else list(args)
        split = _label_split(given)
        if split:
            self.error(split)
        return super().parse_args(_coordinates_joined(given), namespace)


#: What a file the bench reads ends with: a word after a `--look` that ends so is a file.
FILES = (".step", ".stp", ".stl", ".brep", ".3mf", ".obj", ".json")


def _label_split(given: list[str]) -> str:
    """A `--look` label of several words, unquoted, so the shell split it: `--look
    pane.step=blue:the cup` left `cup` over, and argparse said only "unrecognized arguments: cup"
    (mqucifer/print-shop#191). Said with the label as one word."""
    for k, word in enumerate(given):
        if word == "--look" and k + 1 < len(given):
            value, after = given[k + 1], k + 2
        elif word.startswith("--look="):
            value, after = word.removeprefix("--look="), k + 1
        else:
            continue
        name, _, look = value.partition("=")
        colour, colon, label = look.partition(":")
        rest = []
        while after < len(given) and not given[after].startswith("-"):
            if given[after].lower().endswith(FILES):
                break
            rest.append(given[after])
            after += 1
        if colon and rest:
            words = " ".join([label, *rest])
            one = re.sub(r"[^A-Za-z0-9_-]+", "-", words).strip("-") or "label"
            return (
                f"render: a --look label is one word: `--look {name}={colour}:{one}`, not "
                f"`{words}`, which the command line splits"
            )
    return ""


def _coordinates_joined(given: list[str]) -> list[str]:
    """`--close -5,0,2,4`, as `--close=-5,0,2,4`. To argparse a word starting with `-` that isn't
    a plain number is an option, so a point left of the origin would be a usage error. `--slide
    -z`, the way out as it's said, likewise (mqucifer/print-shop#76), and `fit-on --along -z`,
    `bend --toward -y` and `bend --arm -4,-17,...` (mqucifer/print-shop#142)."""
    joined: list[str] = []
    words = iter(given)
    for word in words:
        if word in JOINED:
            value = next(words, None)
            joined.append(word if value is None else f"{word}={value}")
        else:
            joined.append(word)
    return joined


#: Options whose value may start with `-`: a point, a way or a line left of or under the origin.
JOINED = (
    "--close", "--centre", "--hole", "--slide", "--along", "--toward", "--arm",
    "--at", "--line", "--aim", "--up", "--keep", "--keep-face",
)  # fmt: skip


def _point(text: str) -> list[float]:
    values = [float(v) for v in text.split(",")]
    if len(values) != 3:
        raise argparse.ArgumentTypeError("three numbers: x,y,z")
    return values


def _span(text: str) -> tuple[float, float]:
    """`0,2`: a stretch along an axis, from and to, in mm."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != 2 or not all(math.isfinite(v) for v in values) or values[0] >= values[1]:
        raise argparse.ArgumentTypeError("FROM,TO: two numbers, the first the lower")
    return values[0], values[1]


#: A name for a selection of edges, as a need's number path holds it: no dots.
NAME = re.compile(r"[A-Za-z0-9_-]+")


def _named(text: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep or not NAME.fullmatch(name):
        raise argparse.ArgumentTypeError(
            "NAME=..., the name in letters, digits, - and _, as a need's number names it"
        )
    return name, value


def _named_box(text: str) -> tuple[str, tuple[list[float], list[float]]]:
    """`corners=-30,-20,2,30,20,18`: a name, and the box's low and high corners."""
    name, value = _named(text)
    try:
        values = [float(v) for v in value.split(",")]
    except ValueError:
        values = []
    if len(values) != 6:
        raise argparse.ArgumentTypeError("NAME=X0,Y0,Z0,X1,Y1,Z1: six numbers, two corners")
    low = [min(a, b) for a, b in zip(values[:3], values[3:], strict=True)]
    high = [max(a, b) for a, b in zip(values[:3], values[3:], strict=True)]
    return name, (low, high)


def _fraction(text: str) -> float:
    """`0.4`: a share, more than 0 and less than 1."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not 0 < value < 1:
        raise argparse.ArgumentTypeError(
            f"a share of the part's volume, more than 0 and less than 1, such as 0.4: not {text!r}"
        )
    return value


def _box(text: str) -> tuple[list[float], list[float]]:
    """`-4,-17,10,4,-15,26`: a box's low and high corners."""
    return _named_box(f"box={text}")[1]


def _axis(text: str) -> str:
    """`z`: an axis, lower case and unsigned. On the runs `rays --axis Z` and `--axis +z` were
    refused by argparse's choices alone (mqucifer/print-shop#191), so the refusal says the form
    with one to copy, and that a way, where one is meant, is signed instead."""
    if text not in AXES:
        signed = ": a sign is a way's, as `--slide +z` takes it" if text[:1] in "+-" else ""
        raise argparse.ArgumentTypeError(
            f"an axis, lower case and unsigned, one of {' '.join(AXES)}, such as `z`: not "
            f"{text!r}{signed}"
        )
    return text


def _slide(text: str) -> str:
    """`+z`: the way a part moves out, signed and lower case. Unsigned, `z` could be either way,
    and the answer turns on it: a slide the wrong way reads NOT HELD (mqucifer/print-shop#191)."""
    if text not in FACINGS:
        raise argparse.ArgumentTypeError(
            f"the way the last file moves out, signed and lower case, one of {' '.join(FACINGS)}, "
            f"such as `--slide +z`: not {text!r}"
        )
    return text


def _way(text: str) -> str:
    """`+z`, or `z`, which is `+z`: a way along an axis."""
    way = text if text[:1] in "+-" else f"+{text}"
    if way not in FACINGS:
        raise argparse.ArgumentTypeError(f"one of {' '.join(FACINGS)}, or x y z for +x +y +z")
    return way


def _from(text: str) -> float | str:
    """`6`, a distance, or `clear`: as far out as the parts' boxes need to be apart."""
    if text.strip().lower() == "clear":
        return "clear"
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("a distance in mm, or `clear`") from None


def _plane(text: str) -> tuple[str, float]:
    """`z=3.75`: the axis a plane is square to, and where along it."""
    axis, sep, value = text.partition("=")
    try:
        at = float(value)
    except ValueError:
        at = math.nan
    if not sep or axis not in ("x", "y", "z") or not math.isfinite(at):
        raise argparse.ArgumentTypeError("AXIS=AT, AXIS one of x y z: z=3 is level at 3 mm")
    return axis, at


def _xy(text: str) -> tuple[float, float]:
    """`12.5,40`: a point on a piece map, in mm."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != 2 or not all(math.isfinite(v) for v in values):
        raise argparse.ArgumentTypeError("X,Y: two numbers, in the map's mm")
    return values[0], values[1]


def _line(text: str) -> list[tuple[float, float]]:
    """`10,0,12,8,10,16`: a line's points on a piece map, two or more."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) < 4 or len(values) % 2 or not all(math.isfinite(v) for v in values):
        raise argparse.ArgumentTypeError("x1,y1,x2,y2,...: two points or more, in the map's mm")
    return list(zip(values[::2], values[1::2], strict=True))


def _box_xy(text: str) -> tuple[float, float, float, float]:
    """`20,10,70,30`: two corners of a box on a piece map."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise argparse.ArgumentTypeError("X0,Y0,X1,Y1: two corners, in the map's mm")
    if values[0] == values[2] or values[1] == values[3]:
        raise argparse.ArgumentTypeError("X0,Y0,X1,Y1: two corners apart both ways")
    return values[0], values[1], values[2], values[3]


def _lips(text: str) -> tuple[float, float]:
    """`1.2,0.8`: the full lip and the least, before play."""
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) == 1:
        values = values * 2
    if len(values) != 2 or not 0 < values[1] <= values[0]:
        raise argparse.ArgumentTypeError(
            "FULL,LEAST: the lip every piece has, and the least a small piece may have, in mm "
            "before play, the least no more than the full"
        )
    return values[0], values[1]


#: An axis, as `rays --axis`, `wall --axis`, `clearance --along` and `bend --layers` take it.
AXES = ("x", "y", "z")
#: The ways `edges --facing` takes, as `bench/edges.py` reads them.
FACINGS = ("+x", "-x", "+y", "-y", "+z", "-z")


def _named_facing(text: str) -> tuple[str, str]:
    name, way = _named(text)
    if way not in FACINGS:
        raise argparse.ArgumentTypeError(f"NAME=DIR, DIR one of {' '.join(FACINGS)}")
    return name, way


#: `--placed`, as `clearance`, `fit-on` and `render` take it (mqucifer/print-shop#201).
PLACED = (
    "an assembly file: each file it names, as a part or in a part's `with`, is read where it "
    "sits, turned by the part's `turn` and moved by its `at`, and every other file where it is. "
    "A part built flat in its own frame is read so against the parts it meets. A file it names "
    "in several parts is read once for each, at each part's place, named `FILE as PART`"
)
#: `--part`, beside `--placed` (mqucifer/print-shop#343).
PART = (
    "with --placed: a part of the assembly to read a file as, where the assembly names that file "
    "in several parts. Give it once for each part, or several after it. A file any of whose "
    "parts is named is read as only those; a file none of whose parts is named is read as each "
    "of its parts"
)


def _placed_options(command: Any) -> None:
    command.add_argument("--placed", metavar="ASSEMBLY", help=PLACED)
    command.add_argument(
        "--part", dest="parts", action="extend", nargs="+", default=[], metavar="PART", help=PART
    )


def parser() -> Parser:
    top = Parser(prog="bench", description=__doc__)
    top.add_argument("--out", default="bench-out", help="where reports are saved")
    sub = top.add_subparsers(dest="command", required=True, parser_class=Parser)

    info = sub.add_parser("info", help="a part's facts: size, volume two ways, flat heights")
    info.add_argument("file")

    clearance = sub.add_parser("clearance", help="overlap and gap between parts, every pair")
    clearance.add_argument("files", nargs="+", help="parts, or one file of several solids")
    clearance.add_argument("--min-gap", type=float, help="a gap under this in mm is TOO CLOSE")
    clearance.add_argument(
        "--max-gap",
        type=float,
        help="a least gap over this in mm is TOO LOOSE, for parts meant to mate, such as a lid",
    )
    clearance.add_argument(
        "--lift",
        type=float,
        metavar="D",
        help="move the last file named D mm along --along before measuring, signed, its file left "
        "where it is: a seated lid lifted off its seat by more than the play, so the least gap is "
        "its lip's play, read on the printed lid",
    )
    clearance.add_argument(
        "--along", type=_axis, default="z", metavar="AXIS", help="the axis --lift moves it along"
    )
    clearance.add_argument(
        "--slide",
        type=_slide,
        metavar="DIR",
        help="slide the last file named along DIR, one of +x -x +y -y +z -z, its way out, until "
        "it's clear of every other part: an overlap where it lies, then the first part it meets "
        "and where, HELD within --hold, and everything on its path, which is what has to bend or "
        "be fitted afterwards on its way in (mqucifer/print-shop#76). A hold by a body joined to "
        "nothing else in its file is HELD BY A SEPARATE BODY (mqucifer/print-shop#122)",
    )
    clearance.add_argument(
        "--hold",
        type=float,
        metavar="D",
        help="with --slide, the part is HELD if it meets another within D mm (1 unless given)",
    )
    clearance.add_argument(
        "--box",
        dest="boxes",
        action="append",
        default=[],
        type=_named_box,
        metavar="NAME=X0,Y0,Z0,X1,Y1,Z1",
        help="a place: each pair's least gap between their surfaces inside this box, as "
        "pairs.N.places.NAME.gap_mm. Put it round the faces whose play it is, such as a pane's "
        "edge and its pocket's wall, and clear of the faces with another play. With a place "
        "named, --min-gap and --max-gap hold each place's gap",
    )
    _placed_options(clearance)
    clearance.add_argument(
        "--as",
        dest="moving_as",
        metavar="PART",
        help="with --placed and --slide or --lift: the part the last file named is moved as, "
        "where the assembly names its file in several parts. Its other parts stay where they "
        "sit, read as ones it could meet. Without it, such a file is moved as none of them",
    )
    clearance.add_argument(
        "--facing",
        dest="facings",
        action="append",
        default=[],
        type=_named_facing,
        metavar="NAME=DIR",
        help="a place on a STEP pair: the least gap between faces that face DIR, one of +x -x +y "
        "-y +z -z, within 60° all over, and the other part's faces facing back. DIR and its "
        "opposite read alike, as a gap has a face on each side",
    )

    readback = sub.add_parser("readback", help="volume before and after an operation")
    readback.add_argument("before")
    readback.add_argument("after")
    readback.add_argument("--cutter", help="the cutter: reads how much of it was in the body")
    readback.add_argument("--expect-delta", type=float, help="the change wanted, in mm³, signed")
    readback.add_argument("--tol", type=float, help="how far from it may pass, in mm³")
    readback.add_argument("--min-share", type=float, help="the least share of cutter in the body")

    rays = sub.add_parser("rays", help="rays from a cut feature's axis, against a section")
    rays.add_argument("file")
    rays.add_argument("--centre", type=_point, required=True, help="x,y,z on the feature's axis")
    rays.add_argument("--axis", type=_axis, default="z", metavar="AXIS")
    rays.add_argument("--d", type=float, help="the diameter wanted, for a round feature")
    rays.add_argument("--depth", type=float, help="how far it goes along the axis, signed")
    rays.add_argument("--tol", type=float, default=0.05, help="tolerance on those, in mm")
    rays.add_argument("--n", type=int, default=720, help="rays at each height")

    panes = sub.add_parser("panes", help="panes from a frame's openings, and their fit")
    panes.add_argument("frame")
    panes.add_argument("--pocket-z", type=float, required=True, help="a height inside the pockets")
    panes.add_argument("--lip-z", type=float, help="a height inside the lip above them")
    panes.add_argument("--clearance", type=float, required=True, help="play round a pane, in mm")
    panes.add_argument("--pane-thickness", type=float, help="in mm; else the pocket's height")
    panes.add_argument("--nozzle", type=float, default=0.4)
    panes.add_argument("--min-area", type=float, default=4.0, help="the smallest pane, in mm²")
    panes.add_argument("--panes-out", dest="out_panes", default="panes", help="where panes go")

    capture = sub.add_parser("capture", help="whether each pane is held by shape, on both faces")
    capture.add_argument("frame")
    capture.add_argument("--pocket-z", type=float, required=True, help="a height in the pockets")
    capture.add_argument("--clearance", type=float, required=True, help="play round a pane, in mm")
    capture.add_argument("--pane-thickness", type=float, required=True)
    capture.add_argument(
        "--pane-floor-z",
        type=float,
        help="where a pane's underside sits; else the pocket's level floor, or the part's",
    )
    capture.add_argument(
        "--min-lip",
        type=float,
        help="the least lip after its play, in mm, one rim everywhere: 0.4, a nozzle's width, "
        "unless you say",
    )
    capture.add_argument(
        "--full-rim",
        type=float,
        help="a rim sized to the visible share, from the request: the most rim each pane has, in "
        "mm before play, from the pocket's wall to the opening's edge",
    )
    capture.add_argument(
        "--least-rim",
        type=float,
        help="the least rim any pane may have to keep its share visible, in mm before play: a "
        "pane that needs less is to be removed",
    )
    capture.add_argument(
        "--min-visible",
        type=float,
        help="the least share of each pane, in percent, the frame covers on neither face",
    )
    capture.add_argument("--layer", type=float, default=0.2)
    capture.add_argument(
        "--panes", type=int, help="how many panes there must be: any other count is FAIL"
    )
    capture.add_argument(
        "--raised-faces",
        nargs="+",
        metavar="FILE",
        help="flush panes' files, where they sit in the frame (`pieces build --flush`): each "
        "raised face is read in its opening, with --face-play round it, and pushed --clearance "
        "any way with its body, and level with the frame's face",
    )
    capture.add_argument(
        "--face-play",
        type=float,
        help="with --raised-faces: the play round each raised face, in mm (--clearance unless "
        "given)",
    )

    thin = sub.add_parser("thin", help="features narrower than the nozzle, and tiny pieces")
    thin.add_argument("file")
    thin.add_argument("--nozzle", type=float, default=0.4)
    thin.add_argument("--layer", type=float, default=0.2)
    thin.add_argument("--min-body", type=float, default=1.0, help="the smallest body, in mm³")
    thin.add_argument(
        "--one-body", action="store_true", help="more than one connected body is FAIL"
    )
    thin.add_argument("--samples", type=int, default=4000, help="rays through the surface")
    thin.add_argument("--max-layers", type=int, default=400)
    thin.add_argument(
        "--wall-area",
        type=float,
        default=2.0,
        help="thin wall by rays alone, in mm², before it counts as a disagreement",
    )

    wall = sub.add_parser(
        "wall", help="the least wall round one hole, along its whole length or a stretch of it"
    )
    wall.add_argument("file")
    wall.add_argument("--hole", type=_point, required=True, help="x,y,z: any point inside the hole")
    wall.add_argument("--axis", type=_axis, default="z", metavar="AXIS", help="the hole's axis")
    wall.add_argument("--min-wall", type=float, help="the least wall wanted, in mm: under is FAIL")
    wall.add_argument(
        "--z",
        type=_span,
        metavar="FROM,TO",
        help="read only this stretch of the hole, along its axis (z, or --axis): a hole through a "
        "thin floor, say --z 0,2, reads the floor round it, not the hollow it opens into",
    )
    wall.add_argument("--layer", type=float, default=0.2, help="sections a layer apart")
    wall.add_argument("--n", type=int, default=360, help="rays at each height")

    edges = sub.add_parser(
        "edges", help="each outside edge of a part: sharp or rounded, and the least radius"
    )
    edges.add_argument(
        "files",
        nargs="+",
        help="the part's files, as assembled: each blocks a finger from the others' insides. "
        "A file under references/ blocks it and isn't read",
    )
    edges.add_argument(
        "--with",
        dest="with_files",
        action="append",
        default=[],
        metavar="FILE",
        help="another file in place, such as a bought fan: it blocks a finger, and isn't read",
    )
    edges.add_argument(
        "--min-radius",
        type=float,
        help="the least radius of every outside edge, in mm, from the request: a sharp edge or a "
        "tighter rounding is FAIL",
    )
    edges.add_argument(
        "--box",
        dest="boxes",
        action="append",
        default=[],
        type=_named_box,
        metavar="NAME=X0,Y0,Z0,X1,Y1,Z1",
        help="the edges with any point read in this box, their least radius as selected.NAME. "
        "Each edge is also in a group, upright, top, bottom or side, as groups.NAME",
    )
    edges.add_argument(
        "--facing",
        dest="facings",
        action="append",
        default=[],
        type=_named_facing,
        metavar="NAME=DIR",
        help="the edges facing within 60° of DIR, one of +x -x +y -y +z -z: those round the face "
        "that faces it, their least radius as selected.NAME",
    )

    light = sub.add_parser(
        "light", help="the share of each pane open straight through the frame, as light passes"
    )
    light.add_argument("frame")
    light.add_argument("panes", nargs="+", help="each pane's file")
    light.add_argument(
        "--with",
        dest="with_files",
        action="append",
        default=[],
        metavar="FILE",
        help="another part of the frame, such as a cap printed apart: its shadow counts too",
    )
    light.add_argument(
        "--min-open",
        type=float,
        help="the least share of each pane open, in percent, from the request: under is FAIL",
    )
    light.add_argument("--layer", type=float, default=0.2, help="the frame is cut a layer apart")
    light.add_argument("--rays", type=int, default=4000, help="rays through each pane's footprint")

    thickness = sub.add_parser("thickness", help="each pane's thickness, straight through")
    thickness.add_argument("panes", nargs="+", help="each pane's file")
    thickness.add_argument(
        "--max", type=float, help="the most a pane may be, in mm, from the request: over is FAIL"
    )
    thickness.add_argument(
        "--min", type=float, help="the least a pane may be, in mm, from the request: under is FAIL"
    )
    thickness.add_argument("--rays", type=int, default=2000, help="rays through each pane")

    pause = sub.add_parser("pause", help="the layer to pause after, to drop a pane in")
    pause.add_argument("frame")
    pause.add_argument("--pocket-z", type=float, required=True, help="a height inside the pockets")
    pause.add_argument("--pane-thickness", type=float, required=True)
    pause.add_argument("--layer", type=float, default=0.2)
    pause.add_argument("--first-layer", type=float, default=0.2)
    pause.add_argument("--max-gap", type=float, default=0.4, help="the most air over the pane")
    pause.add_argument("--min-room", type=float, help="the least air over the pane: under is FAIL")

    params = sub.add_parser("params", help="re-run a design's script with one parameter changed")
    params.add_argument("script", help="a Python script that writes STEP or STL files")
    params.add_argument(
        "--set", dest="change", required=True, help="NAME=VALUE, a top-level number"
    )
    params.add_argument("--timeout", type=int, default=150, help="seconds for each of its 3 runs")
    params.add_argument("--keep", help="a new folder to copy what the changed run wrote into")
    params.add_argument(
        "--max-left",
        type=int,
        default=0,
        help="how many pieces may stay where they are and still read OK: over that is PARTIAL",
    )

    layout = sub.add_parser(
        "layout",
        help="a flat layout of named pieces, in 2D: each piece, its least width and where, what "
        "an inset leaves of it, and a picture with each piece labelled",
    )
    layout.add_argument(
        "file",
        help='JSON: {"pieces": {NAME: OUTLINE}}, each outline GeoJSON, as '
        "shapely.geometry.mapping writes it, or WKT, in mm",
    )
    layout.add_argument(
        "--inset",
        dest="insets",
        action="append",
        type=float,
        default=[],
        metavar="D",
        help="what an inset of D mm leaves of each piece: its parts and its narrowest neck. "
        "Give it more than once for several. A negative D is an outset",
    )
    layout.add_argument(
        "--join",
        choices=("mitre", "round"),
        default="mitre",
        help="the inset's corners: mitre, as a layout's pockets are cut, or round, the true "
        "distance",
    )
    layout.add_argument(
        "--min-width", type=float, help="the least width wanted, in mm: a piece under it is FAIL"
    )
    layout.add_argument("--layout-out", dest="out_layout", default="layout")
    faces = sub.add_parser(
        "faces",
        help="a STEP part's flat faces, the way each faces, where and how large, and its round "
        "holes and bosses: centre, axis, radius and depth",
    )
    faces.add_argument("file")
    cut = sub.add_parser(
        "slice",
        help="a part cut by a plane: each separate piece's outline, box, area and holes",
    )
    cut.add_argument("file")
    cut.add_argument(
        "--at",
        action="append",
        required=True,
        type=_plane,
        metavar="AXIS=AT",
        help="the plane: z=3 is level at 3 mm, x=50 and y=40 upright. Give it more than once for "
        "several",
    )
    cut.add_argument(
        "--box",
        dest="boxes",
        action="append",
        default=[],
        type=_named_box,
        metavar="NAME=X0,Y0,Z0,X1,Y1,Z1",
        help="the section inside this box, at each plane through it: its pieces, their least "
        "width and size, and the plane's pieces wholly inside it, as cuts.N.places.NAME. So a "
        "need reads one wall or one tab whatever order the pieces come in",
    )
    fit = sub.add_parser(
        "fit-on",
        help="a part's way on: what it passes through at each step from out to seated, and how "
        "deep, which is how far something has to bend. Runs in the second image, with FreeCAD",
    )
    fit.add_argument("moving", help="the part that goes on, as a STEP, where it sits once on")
    fit.add_argument(
        "--onto",
        nargs="+",
        required=True,
        metavar="FILE",
        help="what it goes onto: each STEP file it could meet on its way",
    )
    _placed_options(fit)
    fit.add_argument(
        "--as",
        dest="moving_as",
        metavar="PART",
        help="with --placed: the part MOVING goes on as, where the assembly names its file in "
        "several parts. It's read there, and its other parts stay where they sit as ones it "
        "could meet, where its file is in --onto too",
    )
    fit.add_argument(
        "--along",
        required=True,
        type=_way,
        metavar="DIR",
        help="its way out, one of +x -x +y -y +z -z, as `clearance --slide` takes it (z is +z): "
        "it comes on against it. A lid lifted off upward is +z",
    )
    fit.add_argument(
        "--from",
        dest="start",
        required=True,
        type=_from,
        metavar="MM",
        help="how far out it starts, in mm: far enough that it's clear of everything. `clear` "
        "is as far as it takes for its box to be past every other file's along the way, and a "
        "step more",
    )
    fit.add_argument(
        "--step",
        type=float,
        default=0.25,
        help="how far it moves between readings, in mm (0.25 unless given): something thinner "
        "than this along the way, passing something as thin, can be stepped over",
    )
    bend = sub.add_parser(
        "bend",
        help="the strain an arm takes across its layers when it's pushed aside, against what its "
        "material takes, and how far its back moves. Runs in the second image, with FreeCAD's FEM",
    )
    bend.add_argument("file", help="the part with the arm, as a STEP")
    bend.add_argument(
        "--arm",
        required=True,
        type=_box,
        metavar="X0,Y0,Z0,X1,Y1,Z1",
        help="a box round the arm, hook and all, one side of it on the arm's root and clear of "
        "the wall beside it: the arm is the file's material inside it",
    )
    bend.add_argument(
        "--root",
        required=True,
        type=_plane,
        metavar="AXIS=AT",
        help="the plane the arm is held at, where it joins the rest: z=10 is level at 10 mm",
    )
    bend.add_argument(
        "--push",
        required=True,
        type=float,
        metavar="MM",
        help="how far the arm is pushed, in mm: `fit-on`'s depth where it meets the arm",
    )
    bend.add_argument(
        "--toward",
        required=True,
        type=_way,
        metavar="DIR",
        help="the way it's pushed, one of +x -x +y -y +z -z, across the arm",
    )
    bend.add_argument(
        "--at",
        choices=("far", "near", "both"),
        default="both",
        help="the edge pushed, of those along the arm's extreme on the side the push comes from, "
        "as a hook's face has: the furthest from the root, the nearest, or each in turn and the "
        "worse judged (both, unless given). The nearer edge is the shorter arm",
    )
    bend.add_argument(
        "--layers",
        type=_axis,
        metavar="AXIS",
        default="z",
        help="the axis the layers are stacked along, as it's printed (z unless given)",
    )
    bend.add_argument(
        "--material",
        choices=("petg",),
        default="petg",
        help="whose limit across its layers the strain is held to (petg unless given)",
    )
    bend.add_argument(
        "--limit",
        type=float,
        metavar="PCT",
        help="the most strain across the layers, in percent, in place of the material's",
    )
    bend.add_argument(
        "--mesh",
        type=float,
        metavar="MM",
        help="the largest element, in mm: half the arm's thickness at its root unless given",
    )
    load = sub.add_parser(
        "load",
        help="joined parts under a weight, or lifted by one of them: each part's worst strain "
        "against its layers, each joint's force, a through-bolt's washers, nut and the "
        "plastic round it, a dovetail's flanks, seat and latch, and how far the loaded place "
        "moves. Runs in the second image, with FreeCAD's FEM",
    )
    load.add_argument(
        "assembly",
        help="the assembly file, JSON: the parts' STEP files, their places, each one's material "
        "and the way its layers are stacked, the joints, and where it's held",
    )
    load.add_argument(
        "--weight",
        type=float,
        metavar="KG",
        help="the load, in kg: with --at, unless --lift is the load case",
    )
    load.add_argument(
        "--at",
        action="append",
        type=_point,
        metavar="X,Y,Z",
        help="a place on the assembly's surface the weight sits on. Give it more than once to "
        "share the weight equally between several",
    )
    load.add_argument(
        "--lift",
        metavar="PART",
        help="the assembly lifted by this part, in place of --weight and --at: it's held by "
        "that part's highest level, and every other part hangs by its own solid weight, "
        "--toward the way down",
    )
    load.add_argument(
        "--toward",
        type=_way,
        default="-z",
        metavar="DIR",
        help="the way the weight pulls, one of +x -x +y -y +z -z (-z, down, unless given)",
    )
    load.add_argument(
        "--over",
        type=float,
        default=10.0,
        metavar="MM",
        help="how far round each place the weight is spread over the surface, in mm (10 unless "
        "given): a cup's foot, not a point",
    )
    load.add_argument(
        "--friction",
        type=float,
        metavar="MU",
        help="the most friction a touching joint may need to stay put. There's no default: "
        "without it the friction needed is a figure, not a limit",
    )
    load.add_argument(
        "--mesh",
        type=float,
        metavar="MM",
        help="the largest element, in mm: half the thinnest part unless given, and no finer "
        "than the container solves in a minute or two",
    )
    load.add_argument(
        "--parts",
        action="store_true",
        help="read in two passes (mqucifer/print-shop#301): the whole, each bolt's bore stood "
        "in by a square one, for each joint's force and the movement; then each part on its "
        "own, its bores meshed, for its strain, under an hour. Without it, two passes run only "
        "where the whole meets the container's memory, or where an assembly's one pass wouldn't "
        "end inside its limit",
    )
    optimise = sub.add_parser(
        "optimise",
        help="where a part's load goes: BESO's topology optimisation on CalculiX, down to a "
        "target volume, as a reference for a design round and never a part. Runs in the second "
        "image",
    )
    optimise.add_argument(
        "assembly",
        help="an assembly file of one part, as `load` reads it: its STEP file, where it sits, "
        "its material, the way its layers are stacked, and where it's held",
    )
    optimise.add_argument("--weight", type=float, metavar="KG", help="the load, in kg")
    optimise.add_argument(
        "--at",
        action="append",
        type=_point,
        metavar="X,Y,Z",
        help="a place on the part's surface the weight sits on; more than once to share it",
    )
    optimise.add_argument(
        "--toward",
        type=_way,
        default="-z",
        metavar="DIR",
        help="the way the weight pulls, one of +x -x +y -y +z -z (-z, down, unless given)",
    )
    optimise.add_argument(
        "--over",
        type=float,
        default=10.0,
        metavar="MM",
        help="how far round each place the weight is spread over the surface, in mm (10 unless "
        "given)",
    )
    optimise.add_argument(
        "--volume",
        type=_fraction,
        required=True,
        metavar="FRACTION",
        help="the share of the part's volume to keep, more than 0 and less than 1, the regions "
        "kept solid in it",
    )
    optimise.add_argument(
        "--keep",
        action="append",
        type=_box,
        default=[],
        metavar="X0,Y0,Z0,X1,Y1,Z1",
        help="a box whose elements are kept solid: a boss, a pocket. More than once for several",
    )
    optimise.add_argument(
        "--keep-face",
        action="append",
        type=_point,
        default=[],
        metavar="X,Y,Z",
        help="the part's faces through this point are kept solid, an element deep: a face that "
        "shows, a seat. More than once for several",
    )
    optimise.add_argument(
        "--up",
        type=_way,
        metavar="DIR",
        help="the way up on the bed, along the part's layers: material is then kept only on "
        "material below it, so nothing hangs over air",
    )
    optimise.add_argument(
        "--mesh",
        type=float,
        metavar="MM",
        help="the largest element, in mm: about 12,000 elements in the part unless given",
    )
    optimise.add_argument("--optimised-out", dest="out_optimised", default="optimised")
    bed = sub.add_parser(
        "fit-bed",
        help="each part against the bed: the side it's laid on, its footprint turned to fit, "
        "its height, and whether it fits; and, for an assembly's through joints that state "
        "one, each nut's pocket against its nut",
    )
    bed.add_argument(
        "parts",
        nargs="+",
        metavar="PARTS",
        help="the parts, each a STEP file, or one assembly file, whose parts are each read on "
        "the side its `layers` names",
    )
    bed.add_argument(
        "--bed",
        type=_point,
        default=[256.0, 256.0, 256.0],
        metavar="X,Y,Z",
        help="the bed's width, depth and height, in mm (256,256,256 unless given)",
    )
    pieces = sub.add_parser(
        "pieces",
        help="a stained-glass panel as a piece map: traced from a picture, checked against "
        "glass-cutting rules, fixed piece by piece, and built into a frame and panes",
    )
    action = pieces.add_subparsers(dest="action", required=True, parser_class=Parser)
    trace = action.add_parser(
        "trace", help="a picture's pieces as a piece map: flat colours between dark lines"
    )
    trace.add_argument("image", help="the picture: PNG or JPEG")
    trace.add_argument(
        "--width", type=float, required=True, metavar="MM", help="the panel's width, in mm"
    )
    trace.add_argument(
        "--palette",
        metavar="NAME[=#rrggbb],...",
        help="the colours a piece may be, each a name the bench knows or NAME=#rrggbb: each "
        "piece takes the nearest. With none, each takes the nearest named colour to its own",
    )
    trace.add_argument(
        "--dark",
        type=int,
        default=40,
        metavar="N",
        help="a pixel is lead where every channel is under N, of 255 (40 unless given)",
    )
    trace.add_argument(
        "--whole",
        action="store_true",
        help="the whole picture is the panel: no frame is looked for or cut away. For a picture "
        "already cropped to its panel",
    )
    check = action.add_parser(
        "check", help="a piece map against the rules glass cutting follows, each a figure"
    )
    check.add_argument("map", help="the piece map, as `pieces trace` or a fix wrote it")
    for reads in (trace, check):
        reads.add_argument(
            "--min-width",
            type=float,
            metavar="MM",
            help="a piece under this at its widest is a sliver (3 unless given)",
        )
        reads.add_argument(
            "--min-area",
            type=float,
            metavar="MM2",
            help="a piece under this is too small to handle (25 unless given)",
        )
        reads.add_argument(
            "--min-neck",
            type=float,
            metavar="MM",
            help="a place under this straight across, in a piece wider than --min-width, is a "
            "thin neck (1.2 unless given)",
        )
        reads.add_argument(
            "--max-notch",
            type=float,
            metavar="R",
            help="a notch deeper than R times its mouth's width is a deep notch (1 unless given)",
        )
        reads.add_argument(
            "--max-colour-run",
            type=float,
            metavar="N",
            help="a colour that changes by more than N of 255 across one piece is a gradient "
            "(40 unless given)",
        )
        reads.add_argument(
            "--max-lead",
            type=float,
            metavar="MM",
            help="lead wider than this at its widest is a patch, not a line (three times the "
            "map's usual lead unless given)",
        )
        reads.add_argument(
            "--lip",
            type=_lips,
            metavar="FULL[,LEAST]",
            help="the lip `pieces build` will be given: each piece it leaves no one opening in "
            "is flagged, as the build would refuse it, with the line to split a waist along",
        )
        reads.add_argument(
            "--min-visible",
            type=float,
            metavar="P",
            help="with --lip, the build's --min-visible: each piece's lip sized to its pane's "
            "visible share",
        )
        reads.add_argument(
            "--play", type=float, metavar="C", help="with --lip, the build's play (0.25)"
        )
        reads.add_argument(
            "--fit", type=float, metavar="MM", help="with --lip, the build's --fit (0.1)"
        )
        reads.add_argument(
            "--flush",
            action="store_true",
            help="with --lip, the build's --flush: each piece whose raised face isn't one, or "
            "would touch the lead as its body moves in its pocket, is flagged",
        )
        reads.add_argument(
            "--face-play",
            type=float,
            metavar="MM",
            help="with --flush, the build's --face-play (--play unless given)",
        )
        reads.add_argument(
            "--min-lead",
            type=float,
            metavar="MM",
            help="lead narrower than this between two pieces is under a nozzle's width "
            "(0.4 unless given)",
        )
    merge = action.add_parser(
        "merge", help="piece A joins its neighbour B, and the line between them goes"
    )
    merge.add_argument("map")
    merge.add_argument("a", type=int, metavar="A", help="the piece that joins")
    merge.add_argument("b", type=int, metavar="B", help="the neighbour it joins: B's colour stays")
    split = action.add_parser(
        "split", help="a new lead line across a piece, from edge to edge: two pieces or more"
    )
    split.add_argument("map")
    split.add_argument("id", type=int, metavar="ID", help="the piece to cut")
    split.add_argument(
        "--line",
        type=_line,
        required=True,
        metavar="x1,y1,x2,y2,...",
        help="the line's points, in the map's mm: from one edge of the piece to another",
    )
    split.add_argument(
        "--lead", type=float, metavar="MM", help="the new line's width (the map's unless given)"
    )
    extend = action.add_parser(
        "extend", help="a dead-end line runs on to the next line or the panel's edge"
    )
    extend.add_argument("map")
    extend.add_argument(
        "--aim",
        type=_xy,
        metavar="X,Y",
        help="a point to run it toward, in place of straight on",
    )
    drop = action.add_parser(
        "drop-line", help="a line is taken out, and the glass on either side of it joins"
    )
    drop.add_argument("map")
    for ends in (extend, drop):
        ends.add_argument(
            "--at",
            type=_xy,
            required=True,
            metavar="X,Y",
            help="a point on the line, as `pieces check` says each dead end's",
        )
    widen = action.add_parser(
        "widen",
        help="the line between two pieces, where it's under a nozzle's width, becomes that "
        "wide: each piece's edge moves back by half of what's short, and the line keeps its path",
    )
    widen.add_argument("map")
    widen.add_argument("a", type=int, metavar="A", help="a piece the line lies beside")
    widen.add_argument("b", type=int, metavar="B", help="the piece on its other side")
    widen.add_argument(
        "--lead", type=float, metavar="MM", help="the line's least width (0.4 unless given)"
    )
    retrace = action.add_parser(
        "retrace",
        help="the picture traced again inside a box, at its own --dark and --palette, and "
        "spliced into the map: nothing outside the box changes, and each line that crosses "
        "its edge stays where it was and joins the line found inside",
    )
    retrace.add_argument("map")
    retrace.add_argument(
        "--box",
        type=_box_xy,
        required=True,
        metavar="X0,Y0,X1,Y1",
        help="two corners of the place to trace again, in the map's mm: it's moved in to the "
        "picture's pixels",
    )
    retrace.add_argument(
        "--dark",
        type=int,
        metavar="N",
        help="in the box a pixel is lead where every channel is under N, of 255 (the map's "
        "own trace's unless given)",
    )
    retrace.add_argument(
        "--palette",
        metavar="NAME[=#rrggbb],...",
        help="the colours a piece found wholly inside the box may be (the map's unless "
        "given). A piece that reaches out of the box keeps its colour",
    )
    fill = action.add_parser(
        "fill",
        help="a patch of lead becomes glass, shared among the pieces round it: each takes "
        "the part nearer to it than to any other, and a line is left between each two",
    )
    fill.add_argument("map")
    fill.add_argument(
        "--at",
        type=_xy,
        required=True,
        metavar="X,Y",
        help="a point in the patch, as `pieces check` says the lead's widest point",
    )
    fill.add_argument(
        "--lead",
        type=float,
        metavar="MM",
        help="the width of the lines left between the shares (the map's unless given)",
    )
    paint = action.add_parser(
        "paint",
        help="a piece takes another of the map's colours, or a new one with its RGB: how far "
        "it is from the picture's own colour in that piece",
    )
    paint.add_argument("map")
    paint.add_argument("id", type=int, metavar="ID", help="the piece to colour")
    paint.add_argument(
        "colour",
        metavar="COLOUR",
        help="one of the map's colours or one the bench has a name for, or NAME=#rrggbb for "
        "a new one",
    )
    move = action.add_parser(
        "move-line",
        help="the line between two neighbours drawn again along new points, as wide as it "
        "was: the glass on each side follows it, and the lines it meets stay joined",
    )
    move.add_argument("map")
    move.add_argument("a", type=int, metavar="A", help="a piece the line lies beside")
    move.add_argument("b", type=int, metavar="B", help="the piece on its other side")
    move.add_argument(
        "--line",
        type=_line,
        required=True,
        metavar="x1,y1,x2,y2,...",
        help="the new path's points, in the map's mm: from one end of the old line to the "
        "other, edge to edge of the two pieces together",
    )
    fixes = action.add_parser(
        "fix",
        help="a file of fixes (merge, split, extend, drop-line, widen, retrace, fill, paint, "
        "move-line) made in order in one call: each step's result, and the map up to the "
        "first step that's refused",
    )
    fixes.add_argument("map")
    fixes.add_argument(
        "steps",
        metavar="STEPS",
        help='JSON: a list of steps, or {"steps": [...]}, each {"fix": "merge", "a": A, "b": B}, '
        '{"fix": "split", "piece": ID, "line": [[x1, y1], [x2, y2]]}, {"fix": "extend", "at": '
        '[x, y]}, {"fix": "drop-line", "at": [x, y]}, {"fix": "widen", "a": A, "b": B}, '
        '{"fix": "retrace", "box": [x0, y0, x1, y1], "dark": N}, {"fix": "fill", "at": [x, y]}, '
        '{"fix": "paint", "piece": ID, "colour": NAME} or {"fix": "move-line", "a": A, "b": B, '
        '"line": [[x1, y1], [x2, y2]]}: `docs/pieces.md`',
    )
    for fix in (merge, split, extend, drop, widen, retrace, fill, paint, move, fixes):
        fix.add_argument(
            "--to",
            metavar="FILE",
            help="the new map's file: MAP's name with the next number unless given",
        )
    for reads in (check, retrace, paint, fixes):
        reads.add_argument(
            "--image",
            metavar="FILE",
            help="the picture the map was traced from, where it isn't where the map says: "
            "its colours are read from it",
        )
    build = action.add_parser(
        "build", help="the frame and a pane file for each colour, every curve built as a curve"
    )
    build.add_argument("map")
    build.add_argument(
        "--thickness", type=float, required=True, metavar="T", help="the frame's, in mm"
    )
    build.add_argument(
        "--pane", type=float, required=True, metavar="P", help="each pane's thickness, in mm"
    )
    build.add_argument(
        "--lip",
        type=_lips,
        required=True,
        metavar="FULL[,LEAST]",
        help="the lip over each pane's edge on both faces, in mm before play, on every piece: "
        "a piece whose opening it closes over at a waist is refused, with the line to split it "
        "along. LEAST is used with --min-visible",
    )
    build.add_argument(
        "--min-visible",
        type=float,
        metavar="P",
        help="size each piece's lip to its pane's visible share, as `capture --full-rim FULL "
        "--least-rim LEAST --min-visible P` reads it: FULL where the pane keeps P percent "
        "visible, else the widest lip that keeps it, never under LEAST. From the request: "
        "there's no default",
    )
    build.add_argument(
        "--play", type=float, required=True, metavar="C", help="the gap round each pane, in mm"
    )
    build.add_argument("--layer", type=float, default=0.2, help="the layer's height, in mm")
    build.add_argument(
        "--border",
        type=float,
        metavar="MM",
        help="the frame round the panel's edge, in mm (twice the map's lead unless given)",
    )
    build.add_argument(
        "--fit",
        type=float,
        default=0.1,
        metavar="MM",
        help="how far a built curve may be from the points of the map's outline, in mm (0.1 "
        "unless given)",
    )
    build.add_argument(
        "--build-out", dest="out_build", default=".", help="where the frame and panes go"
    )
    build.add_argument(
        "--seam",
        action="store_true",
        help="for a dome's slice: its two long edges as seams, each straight length bevelled "
        "by half the angle between the two slices there, set back half of --seam-gap, and "
        "with a groove in the inner face for a came strip's rib",
    )
    build.add_argument(
        "--seam-gap",
        type=float,
        metavar="MM",
        help="with --seam: how far apart two neighbours' faces stand, in mm (0.6 unless "
        "given). Nothing lies in it: a came strip holds from the inside",
    )
    build.add_argument(
        "--flush",
        action="store_true",
        help="each pane stepped, so its face is level with the lead's: its body in the pocket "
        "as now, and a raised face the shape of its opening, --face-play smaller all round, "
        "from the body down through the opening to the bed. For a panel that's a surface",
    )
    build.add_argument(
        "--face-play",
        type=float,
        metavar="MM",
        help="with --flush: the gap round each raised face, in mm (--play unless given)",
    )
    build.add_argument(
        "--lip-thick",
        type=float,
        metavar="MM",
        help="with --flush: the lip under the panes, and so each raised face's height, in mm, "
        "on a layer line and two layers or more (a flat pane's lower lip unless given)",
    )
    build.add_argument(
        "--body",
        type=float,
        metavar="MM",
        help="with --flush: each pane's body, in mm, two layers or more (--pane unless given)",
    )
    seam = action.add_parser(
        "seam",
        help="two neighbouring slices' built frames, bent by their folds and placed round the "
        "dome: how their faces meet, what they share, and the came strips and rings on them",
    )
    seam.add_argument(
        "maps", nargs=2, metavar="MAP", help="two neighbouring slices' maps, as `flatten` wrote"
    )
    seam.add_argument(
        "--frames",
        nargs=2,
        required=True,
        metavar="FRAME",
        help="each slice's frame, in the maps' order, as `pieces build --seam` wrote it",
    )
    seam.add_argument(
        "--gap",
        type=float,
        metavar="MM",
        help="the gap the seams were built for, `build`'s --seam-gap, in mm (0.6 unless given)",
    )
    seam.add_argument(
        "--came",
        metavar="FILE",
        help="the strips on this seam, as `pieces came` placed them: what lies over each "
        "slice's outer face and its inner, and how each holds both slices",
    )
    seam.add_argument(
        "--press",
        type=float,
        metavar="MM",
        help="the most a strip may press into a slice, in mm (0.1 unless given: a rib's "
        "snap)",
    )
    seam.add_argument(
        "--rings",
        nargs="+",
        metavar="FILE",
        help="the rings, as `pieces ring` placed them: each slice's hem and top edge is read "
        "in their grooves",
    )
    came = action.add_parser(
        "came",
        help="the came strips for a slice's seams, one for each straight length: a bar inside "
        "the dome with a rib into each slice's groove, at the angle between the slices there",
    )
    ring = action.add_parser(
        "ring",
        help="the rings that keep a dome's slices round: one at the hem and a seat and a cap "
        "at the top, each with a groove for every slice's edge",
    )
    for made in (came, ring):
        made.add_argument("map", help="a slice's map, as `pieces flatten` wrote it")
        made.add_argument(
            "--thickness", type=float, required=True, metavar="T", help="the frames', in mm"
        )
        made.add_argument(
            "--play", type=float, metavar="MM", help="the play round a slice's edge, in mm"
        )
        made.add_argument(
            "--bed", type=_xy, metavar="X,Y", help="the bed, in mm (256,256 unless given)"
        )
    came.add_argument(
        "--lip",
        type=float,
        required=True,
        metavar="MM",
        help="the least lip the frames were built with, in mm: a strip that reaches past the "
        "lead and that lip is over a pane's opening, and is refused",
    )
    came.add_argument(
        "--gap", type=float, metavar="MM", help="`build`'s --seam-gap, in mm (0.6 unless given)"
    )
    came.add_argument(
        "--grip",
        type=float,
        metavar="MM",
        help="how much tighter a rib is than its groove, a side, in mm (0.1 unless given, "
        "the Sponsor's PETG snap); with --hold h, how far each bead stands into a slice's face "
        "(0.05)",
    )
    came.add_argument(
        "--flange",
        type=_xy,
        metavar="REACH,THICK",
        help="with --hold h: how far a flange reaches from the cut's middle, and how thick it "
        "is, in mm (1.5,0.8 unless given)",
    )
    came.add_argument(
        "--hold",
        choices=("rib", "h"),
        default="rib",
        help="rib: a bar inside the dome with a rib into each slice's groove, nothing over the "
        "outer face (unless given). h: mqucifer/print-shop#166's H, with a flange over each "
        "slice's outer face too, kept to read against: FAIL, as it shows on the outside",
    )
    came.add_argument(
        "--bar",
        type=_xy,
        metavar="REACH,THICK",
        help="a rib strip's bar: how far it reaches from the cut's middle, and how thick it is "
        "at its edge, in mm (past the groove's far wall, and 0.8, unless given)",
    )
    came.add_argument(
        "--hem", type=float, metavar="MM", help="the first strip starts this far up the seam"
    )
    came.add_argument(
        "--top", type=float, metavar="MM", help="the last strip stops this far short of the top"
    )
    came.add_argument("--came-out", dest="out_came", default="came", help="where the strips go")
    ring.add_argument(
        "--border", type=float, metavar="MM", help="`build`'s --border (half the map's lead)"
    )
    ring.add_argument("--depth", type=float, metavar="MM", help="a groove's depth (4)")
    ring.add_argument("--wall", type=float, metavar="MM", help="a ring's walls (1.2)")
    ring.add_argument(
        "--hole",
        type=float,
        metavar="MM",
        help="the hole through the top ring's two parts, across, for the lamp's fitting: a "
        "stated figure until the fitting is chosen (10)",
    )
    ring.add_argument("--rings-out", dest="out_rings", default="rings", help="where they go")
    trace.add_argument(
        "--to", metavar="FILE", help="the map's file: IMAGE's name with .pieces.json unless given"
    )
    dome = action.add_parser(
        "dome",
        help="a dome shade's pieces from rules, as a piece map: one motif repeated round it, "
        "in bands from its top to its hem",
    )
    dome.add_argument(
        "--diameter", type=float, required=True, metavar="D", help="across at the hem, in mm"
    )
    dome.add_argument(
        "--height", type=float, required=True, metavar="H", help="from the hem to the top, in mm"
    )
    dome.add_argument(
        "--repeats", type=int, required=True, metavar="N", help="times the motif goes round"
    )
    dome.add_argument(
        "--bands",
        required=True,
        metavar="FILE",
        help="the bands from the top to the hem, as JSON: each its height down the dome, its "
        "pattern (grid, swag, jewels, strip, triangles or scallop) and its cells' colours",
    )
    dome.add_argument(
        "--top",
        type=float,
        metavar="MM",
        help="the opening at the top, across, in mm (a fifth of the diameter unless given)",
    )
    dome.add_argument("--to", metavar="FILE", help="the map's file: dome.pieces.json unless given")
    flatten = action.add_parser(
        "flatten",
        help="a dome's map cut along lead lines into slices, each laid flat as its own piece "
        "map, with the angle each fold bends through",
    )
    flatten.add_argument("map", help="the dome's piece map, as `pieces dome` wrote it")
    flatten.add_argument(
        "--slices",
        type=int,
        metavar="N",
        help="how many slices, cut evenly round from the map's x = 0 (the map's repeats "
        "unless given)",
    )
    flatten.add_argument(
        "--rows",
        type=int,
        metavar="R",
        help="how many flat rows a slice is: it folds at R - 1 of the lead lines that run all "
        "the way round, those that leave it nearest the dome (every such line unless given)",
    )
    flatten.add_argument(
        "--bed",
        type=_xy,
        metavar="X,Y",
        help="the bed each slice has to fit, in mm (256,256 unless given)",
    )
    flatten.add_argument(
        "--tolerance",
        type=float,
        default=0.01,
        metavar="MM",
        help="how far a slice bent back by its angles may be from the dome's own corners and "
        "flats, in mm (0.01 unless given)",
    )
    flatten.add_argument(
        "--max-off",
        type=float,
        metavar="MM",
        help="the furthest a flat row may stand from the curved dome, in mm: a reading with "
        "no limit unless given",
    )
    flatten.add_argument(
        "--to",
        metavar="NAME",
        help="the slices' files are NAME.slice-01.pieces.json and so on: MAP's name unless given",
    )
    pieces.add_argument("--pieces-out", dest="out_pieces", default="pieces", help=argparse.SUPPRESS)
    render = sub.add_parser("render", help="pictures from the standard views")
    render.add_argument("files", nargs="*")
    render.add_argument(
        "--variant",
        dest="variants",
        action="append",
        default=[],
        metavar="NAME=FILE[,FILE...]",
        help="one of several variants: each gets its own labelled row of views, on one sheet",
    )
    render.add_argument(
        "--placed",
        metavar="ASSEMBLY",
        help=PLACED + ". With variants, each row holding a placed file is drawn placed, and again "
        "after it with those files flat, as built",
    )
    render.add_argument(
        "--part", dest="parts", action="extend", nargs="+", default=[], metavar="PART", help=PART
    )
    render.add_argument("--views", nargs="*", default=["iso", "top", "front", "side"])
    render.add_argument("--section", nargs="*", type=float, default=[], help="z heights to cut")
    render.add_argument(
        "--close",
        action="append",
        default=[],
        metavar="X,Y,Z,R[,VIEW]",
        help="a close-up: R mm all round that point, looking along a view (top, unless named)",
    )
    render.add_argument(
        "--test-piece",
        dest="test_pieces",
        action="append",
        default=[],
        metavar="FILE",
        help="drawn see-through in grey, and marked as not the part",
    )
    render.add_argument(
        "--look",
        dest="looks",
        action="append",
        default=[],
        metavar="FILE=COLOUR[:LABEL]",
        help="a file's colour, by name or #rrggbb, and a short label: drawn, numbered and named in "
        "the legend. A file with none takes a colour no other file on the sheet has",
    )
    render.add_argument(
        "--cut",
        dest="cuts",
        action="append",
        default=[],
        metavar="AXIS=AT[,AXIS=FROM:TO]",
        help="a cut-through as a view: every file cut by a plane, each in its colour. z=3 is level "
        "at 3 mm; x=50 is upright through x=50, seen from +x; y=40 from the front. A window, "
        "such as x=50,y=20:40, draws only that stretch of the plane",
    )
    render.add_argument("--size", type=int, default=768)
    render.add_argument("--renders-out", dest="out_renders", default="renders")
    return top


def _dispatch(args: Any, report: Report) -> None:
    # Imported here so a usage error answers at once, with no CAD library to load.
    if args.command == "info":
        from .readback import run_info

        run_info(args, report)
    elif args.command == "clearance":
        from .clearance import run

        run(args, report)
    elif args.command == "readback":
        from .readback import run_readback

        run_readback(args, report)
    elif args.command == "rays":
        from .readback import run_rays

        run_rays(args, report)
    elif args.command == "panes":
        from .fabrication import run_panes

        args.out = args.out_panes
        run_panes(args, report)
    elif args.command == "capture":
        from .capture import run

        run(args, report)
    elif args.command == "thin":
        from .fabrication import run_thin

        run_thin(args, report)
    elif args.command == "wall":
        from .wall import run as run_wall

        run_wall(args, report)
    elif args.command == "edges":
        from .edges import run as run_edges

        run_edges(args, report)
    elif args.command == "light":
        from .glass import run_light

        run_light(args, report)
    elif args.command == "thickness":
        from .glass import run_thickness

        run_thickness(args, report)
    elif args.command == "pause":
        from .fabrication import run_pause

        run_pause(args, report)
    elif args.command == "params":
        from .params import run

        run(args, report)
    elif args.command == "layout":
        from .layout import run

        args.out = args.out_layout
        run(args, report)
    elif args.command == "faces":
        from .faces import run as run_faces

        run_faces(args, report)
    elif args.command == "slice":
        from .section import run as run_slice

        run_slice(args, report)
    elif args.command == "fit-on":
        from .fit_on import run as run_fit_on

        run_fit_on(args, report)
    elif args.command == "bend":
        from .bend import run as run_bend

        run_bend(args, report)
    elif args.command == "load":
        from .load import run as run_load

        run_load(args, report)
    elif args.command == "optimise":
        from .optimise import run as run_optimise

        run_optimise(args, report)
    elif args.command == "fit-bed":
        from .fit_bed import run as run_fit_bed

        run_fit_bed(args, report)
    elif args.command == "pieces":
        from .pieces import run as run_pieces

        args.out = args.out_pieces
        run_pieces(args, report)
    elif args.command == "render":
        from .render import VIEWS, run

        unknown = [v for v in args.views if v not in VIEWS]
        if unknown:
            raise NoAnswer(f"no view {unknown[0]}: they're {', '.join(VIEWS)}")
        args.out = args.out_renders
        run(args, report)


#: `capture`'s lip after play when nothing's said: a nozzle's width.
MIN_LIP = 0.4


def rims_given(args: Any, top: Parser) -> None:
    """`capture`'s rims: one, `--min-lip`, 0.4 mm unless given, or each pane's sized to the
    visible share, with all three figures from the request and no `--min-lip`."""
    if args.command != "capture":
        return
    by_share = (args.full_rim, args.least_rim, args.min_visible)
    if any(v is not None for v in by_share):
        if any(v is None for v in by_share):
            top.error(
                "capture: a rim sized to the visible share takes all three, from the request: "
                "--full-rim, --least-rim and --min-visible. There's no default for any of them"
            )
        if args.min_lip is not None:
            top.error("capture: --min-lip is one rim everywhere; --full-rim sizes each pane's")
        if not args.clearance < args.least_rim <= args.full_rim:
            top.error(
                "capture: --least-rim is more than the play and no more than --full-rim, both "
                "before play"
            )
        if not 0 < args.min_visible < 100:
            top.error("capture: --min-visible is a share in percent, between 0 and 100")
    elif args.min_lip is None:
        args.min_lip = MIN_LIP
    if args.face_play is not None and not args.raised_faces:
        top.error("capture: --face-play is the play round flush panes' faces: give --raised-faces")
    if args.face_play is None:
        args.face_play = args.clearance
    if not args.face_play > 0:
        top.error("capture: --face-play is the play round each raised face, in mm, over 0")


def gaps_given(args: Any, top: Parser) -> None:
    """`clearance`'s gap range, a least gap no larger than its most, and a lift past both: a lift
    short of a limit would read as the gap (mqucifer/print-shop#65)."""
    if args.command != "clearance":
        return
    if args.min_gap is not None and args.max_gap is not None and args.min_gap > args.max_gap:
        top.error(f"clearance: --min-gap {args.min_gap:g} is over --max-gap {args.max_gap:g}")
    slides(args, top)
    if args.lift is None:
        return
    # With --placed one file may be several parts: the bench reads which (mqucifer/print-shop#343).
    if len(args.files) < 2 and not args.placed:
        top.error("clearance: --lift moves the last file named, so it takes two files or more")
    limits = [g for g in (args.min_gap, args.max_gap) if g is not None]
    if args.lift == 0 or any(abs(args.lift) <= g for g in limits):
        top.error(
            f"clearance: --lift {args.lift:g} is a gap it would read: lift by more than "
            f"{max(limits, default=0):g} mm, so the least gap is the play and not the lift"
        )


#: How far a part may move before what it meets holds it, in mm, unless `--hold` says.
HOLD = 1.0


def slides(args: Any, top: Parser) -> None:
    """`clearance --slide`: two files or more, as it moves the last, and no gap range or lift, as
    it reads what holds a part, not its play (mqucifer/print-shop#76)."""
    if args.slide is None:
        if args.hold is not None:
            top.error("clearance: --hold is how far a --slide may go before it's held: add --slide")
        return
    if len(args.files) < 2 and not args.placed:
        top.error(
            "clearance: --slide moves the last file named past the others, so it takes two files "
            "or more: the part, then the bought part it holds"
        )
    given = [o for o, v in (("--min-gap", args.min_gap), ("--max-gap", args.max_gap),
                            ("--lift", args.lift)) if v is not None]  # fmt: skip
    if given:
        top.error(
            f"clearance: --slide reads what holds a part, and {' and '.join(given)} its play: "
            "run each in a call of its own"
        )
    if args.facings:
        top.error(
            "clearance: with --slide, a place names where a contact lies: give it as --box "
            "NAME=X0,Y0,Z0,X1,Y1,Z1, and read a gap facing a way in a call of its own"
        )
    if args.hold is None:
        args.hold = HOLD
    elif args.hold < 0:
        top.error(f"clearance: --hold {args.hold:g} is a distance, 0 or more")


def selections_given(args: Any, top: Parser) -> None:
    """`edges`' selections and `clearance`'s places, each by a name of its own, and each box with
    room inside it."""
    if args.command not in ("edges", "clearance", "slice"):
        return
    names = [n for n, _ in args.boxes] + [n for n, _ in getattr(args, "facings", [])]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        top.error(
            f"{args.command}: each --box and --facing has a name of its own: {', '.join(twice)}"
        )
    flat = [
        n for n, (low, high) in args.boxes if any(b <= a for a, b in zip(low, high, strict=True))
    ]
    if flat and args.command == "clearance":
        top.error(
            f"clearance: the box {flat[0]} has no room inside it: give two corners apart on x, y "
            "and z"
        )


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    top = parser()
    args = top.parse_args(given)
    rims_given(args, top)
    gaps_given(args, top)
    selections_given(args, top)
    out = Path(args.out)
    report = Report(args.command, given)
    try:
        _dispatch(args, report)
    except NoAnswer as exc:
        report.verdict("NO ANSWER", str(exc))
    except Exception as exc:  # noqa: BLE001 - whatever fails is said, and saved
        report.verdict("NO ANSWER", f"{type(exc).__name__}: {exc}"[:500])
    saved = report.save(out)
    print(report.text, end="")
    _written_down(given, saved)
    return report.exit_code


#: Where a command run from a script of the model's own says that it ran: a file `rounds/box.py`
#: names for each `run`, a line for each command, so the harness records it as a bench call
#: (mqucifer/print-shop#161). A `bench` tool call's container has none, and nor has a test's.
CALLS = "BENCH_CALLS"


def _written_down(given: list[str], saved: Path) -> None:
    """One line in the run's list of the bench commands its script ran: the words, and the
    answer's saved file. A script chains the bench's commands, as a map takes dozens of fixes,
    and none of those was a `bench` call: the ledger, the held checks and the stop sheets saw
    nothing of them."""
    journal = os.environ.get(CALLS)
    if not journal:
        return
    try:
        Path(journal).parent.mkdir(parents=True, exist_ok=True)
        with open(journal, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": given, "saved": os.path.abspath(saved)}) + "\n")
    except OSError:
        pass  # a script that took the folder away has an answer still, and no record of it
