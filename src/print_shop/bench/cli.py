"""The commands, inside the image. `scripts/bench.py` runs them in a container from the machine.

Exit codes: 0 the answer stands and nothing's wrong, 1 both methods agree that something's
wrong, 2 there's no answer to rely on, 3 the command itself was wrong.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

from .report import Report
from .shapes import NoAnswer


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(3, f"usage error: {message}\n{self.format_usage()}")

    def parse_args(self, args: Any = None, namespace: Any = None) -> Any:
        given = sys.argv[1:] if args is None else list(args)
        return super().parse_args(_coordinates_joined(given), namespace)


def _coordinates_joined(given: list[str]) -> list[str]:
    """`--close -5,0,2,4`, as `--close=-5,0,2,4`. To argparse a word starting with `-` that isn't
    a plain number is an option, so a point left of the origin would be a usage error."""
    joined: list[str] = []
    words = iter(given)
    for word in words:
        if word in ("--close", "--centre", "--hole"):
            value = next(words, None)
            joined.append(word if value is None else f"{word}={value}")
        else:
            joined.append(word)
    return joined


def _point(text: str) -> list[float]:
    values = [float(v) for v in text.split(",")]
    if len(values) != 3:
        raise argparse.ArgumentTypeError("three numbers: x,y,z")
    return values


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


#: The ways `edges --facing` takes, as `bench/edges.py` reads them.
FACINGS = ("+x", "-x", "+y", "-y", "+z", "-z")


def _named_facing(text: str) -> tuple[str, str]:
    name, way = _named(text)
    if way not in FACINGS:
        raise argparse.ArgumentTypeError(f"NAME=DIR, DIR one of {' '.join(FACINGS)}")
    return name, way


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
        "--along", choices=("x", "y", "z"), default="z", help="the axis --lift moves it along"
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
    rays.add_argument("--axis", choices=("x", "y", "z"), default="z")
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

    wall = sub.add_parser("wall", help="the least wall round one hole, along its whole length")
    wall.add_argument("file")
    wall.add_argument("--hole", type=_point, required=True, help="x,y,z: any point inside the hole")
    wall.add_argument("--axis", choices=("x", "y", "z"), default="z", help="the hole's axis")
    wall.add_argument("--min-wall", type=float, help="the least wall wanted, in mm: under is FAIL")
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


def gaps_given(args: Any, top: Parser) -> None:
    """`clearance`'s gap range, a least gap no larger than its most, and a lift past both: a lift
    short of a limit would read as the gap (mqucifer/print-shop#65)."""
    if args.command != "clearance":
        return
    if args.min_gap is not None and args.max_gap is not None and args.min_gap > args.max_gap:
        top.error(f"clearance: --min-gap {args.min_gap:g} is over --max-gap {args.max_gap:g}")
    if args.lift is None:
        return
    if len(args.files) < 2:
        top.error("clearance: --lift moves the last file named, so it takes two files or more")
    limits = [g for g in (args.min_gap, args.max_gap) if g is not None]
    if args.lift == 0 or any(abs(args.lift) <= g for g in limits):
        top.error(
            f"clearance: --lift {args.lift:g} is a gap it would read: lift by more than "
            f"{max(limits, default=0):g} mm, so the least gap is the play and not the lift"
        )


def selections_given(args: Any, top: Parser) -> None:
    """`edges`' selections and `clearance`'s places, each by a name of its own, and each box with
    room inside it."""
    if args.command not in ("edges", "clearance"):
        return
    names = [n for n, _ in args.boxes] + [n for n, _ in args.facings]
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
    report.save(out)
    print(report.text, end="")
    return report.exit_code
