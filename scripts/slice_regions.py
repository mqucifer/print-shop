"""Slice a part with regions of their own settings, through Orca's modifiers, and read each back.

mqucifer/print-shop#338. On the Mac, with Orca installed:

    uv run python scripts/slice_regions.py PART.stl --nozzle 0.4 --filament NAME \\
        [--print PRINT.json] [--turn RX,RY,RZ] [--out DIR]
        [--joints '{"infill": 60, "walls": 4}' --margin 3 [--context PATH]
         [--assembly A.json --part NAME]]

`--print` is a part's `print`, as an assembly file gives it (`bench/printed.py`): the part's own
settings and its regions, in the part's own file's axes. `--joints` adds a region of those
settings round each of the part's joints, `--margin` mm round each: its fits, from its context
(a sidecar or design run, as `print_job.py --context` reads it), and its bolt holes and latch,
from the run's assembly file, where `--part` names it there (`slicer/joints.py`).

The part is posed by `--turn` (degrees about x, then y, then z, as the print loop turns it), and
sliced by Orca with its regions as Orca's own modifiers, and again without them. What Orca built
in each region is read from the layers and said, with the time and grams the regions add. The
profiles are print-shop's own (`profiles/`), for the nozzle and filament named: nothing is read
from the inventory, and nothing is sent to the printer.

It writes, in `--out` (a new folder under the system's temporary one by default): the project
Orca sliced (`regions.3mf`), both G-codes, and `print.json`, the part's `print` whole, every
setting resolved from the profile it was sliced with, as `load` takes it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from print_shop.bench.assembly import read as read_assembly
from print_shop.bench.printed import Printed, Settings, read
from print_shop.bench.report import NoAnswer
from print_shop.ingest.orientation import read_part
from print_shop.setup.context import read_context
from print_shop.slicer.joints import round_joints
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.regions import slice_regions
from print_shop.slicer.store import STORE_DIR, NoChoice, Store, choose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stl", type=Path, help="the part, in its own file's axes")
    parser.add_argument("--nozzle", type=float, required=True, help="the nozzle, in mm (0.4)")
    parser.add_argument("--filament", required=True, help="a variety, or a tuning's name")
    parser.add_argument("--print", dest="printed", type=Path, metavar="PRINT.json",
                        help="the part's settings and regions, as an assembly file's `print`")
    parser.add_argument("--turn", default="0,0,0", metavar="RX,RY,RZ",
                        help="the pose, in degrees about x, then y, then z")
    parser.add_argument("--joints", metavar="JSON",
                        help="settings for a region round each joint, as {\"infill\": 60}")
    parser.add_argument("--margin", type=float, default=3.0,
                        help="how far round each joint its region reaches, in mm")
    parser.add_argument("--context", type=Path, help="a sidecar or design run's folder")
    parser.add_argument("--assembly", type=Path, help="the run's assembly file")
    parser.add_argument("--part", help="the part's name in the assembly file")
    parser.add_argument("--out", type=Path, help="where the project and slices are written")
    args = parser.parse_args(argv)

    try:
        turn = tuple(float(v) for v in args.turn.split(","))
        if len(turn) != 3:
            raise ValueError
    except ValueError:
        parser.error("--turn is three angles, RX,RY,RZ, in degrees")
    base = args.printed.parent if args.printed else args.stl.parent
    try:
        printed = (read(json.loads(args.printed.read_text()), args.printed.name)
                   if args.printed else Printed())
    except (OSError, ValueError) as exc:
        print(f"--print: {exc}", file=sys.stderr)
        return 2
    notes: list[str] = []
    if args.joints:
        try:
            own = read({"regions": [{"name": "joint", "box": [[0, 0, 0], [1, 1, 1]],
                                     **json.loads(args.joints)}]}, "--joints").regions[0]
            context = read_context(args.stl, args.context) if args.context or not args.assembly \
                else None
            assembly = read_assembly(str(args.assembly)) if args.assembly else None
            if assembly is not None and not args.part:
                parser.error("--assembly needs --part: the part's name in it")
            mesh = read_part(args.stl)
            joints = round_joints(own.settings, args.margin, mesh, context, assembly, args.part)
        except (OSError, ValueError, NoAnswer, StopIteration) as exc:
            print(f"--joints: {exc}", file=sys.stderr)
            return 2
        printed = Printed(printed.settings, (*printed.regions, *joints.regions))
        notes += joints.notes
    if not printed.regions:
        print("no regions: give --print with regions, or --joints", file=sys.stderr)
        return 2

    try:
        choice = choose(Store(STORE_DIR), nozzle_mm=args.nozzle, filament=args.filament)
    except NoChoice as exc:
        print(exc, file=sys.stderr)
        return 2
    out = args.out or Path(tempfile.mkdtemp(prefix="slice-regions-"))
    composed = choice.compose(out / "profiles")
    profiles = SlicerProfiles(machine=composed["machine"], process=composed["process"],
                              filament=composed["filament"], used=choice.used(STORE_DIR))
    result = slice_regions(args.stl, printed, profiles, out, (turn[0], turn[1], turn[2]), base)
    (out / "print.json").write_text(json.dumps(result.for_load(), indent=2))
    for line in result.lines():
        print(line)
    for note in notes:
        print(f"note: {note}")
    print(f"written in {out}: regions.3mf, with/plate_1.gcode, without/plate_1.gcode, print.json")
    whole = Settings(**{k: v for k, v in result.for_load().items() if k != "regions"})
    if whole.missing():
        print(f"note: the profile doesn't give {', '.join(whole.missing())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
