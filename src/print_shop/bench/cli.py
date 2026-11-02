"""The commands, inside the image. `scripts/bench.py` runs them in a container from the machine.

Exit codes: 0 the answer stands and nothing's wrong, 1 both methods agree that something's
wrong, 2 there's no answer to rely on, 3 the command itself was wrong.
"""

from __future__ import annotations

import argparse
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


def parser() -> Parser:
    top = Parser(prog="bench", description=__doc__)
    top.add_argument("--out", default="bench-out", help="where reports are saved")
    sub = top.add_subparsers(dest="command", required=True, parser_class=Parser)

    info = sub.add_parser("info", help="a part's facts: size, volume two ways, flat heights")
    info.add_argument("file")

    clearance = sub.add_parser("clearance", help="overlap and gap between parts, every pair")
    clearance.add_argument("files", nargs="+", help="parts, or one file of several solids")
    clearance.add_argument("--min-gap", type=float, help="a gap under this in mm is TOO CLOSE")

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
        "--min-lip", type=float, default=0.4, help="the least lip after its play, in mm"
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
    params.add_argument(
        "--max-left",
        type=int,
        default=0,
        help="how many pieces may stay where they are and still read OK: over that is PARTIAL",
    )

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
    elif args.command == "pause":
        from .fabrication import run_pause

        run_pause(args, report)
    elif args.command == "params":
        from .params import run

        run(args, report)
    elif args.command == "render":
        from .render import VIEWS, run

        unknown = [v for v in args.views if v not in VIEWS]
        if unknown:
            raise NoAnswer(f"no view {unknown[0]}: they're {', '.join(VIEWS)}")
        args.out = args.out_renders
        run(args, report)


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(given)
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
