"""Study a part's settings for strength, filament and time, and recommend one set for the gate.

mqucifer/print-shop#339. On the Mac, with Orca installed and the bench's second image built
(`scripts/bench.py build cae`):

    uv run python scripts/settings_study.py run PART.stl --assembly A.json --part NAME \\
        --nozzle 0.4 --filament NAME [--turn RX,RY,RZ] [--context PATH] \\
        (--case 'NAME=LOAD ARGS' ... | --needs RUN/record/needs.json [--need ID ...]) \\
        [--out DIR] [--ledger PATH] [--max-candidates N] [--max-hours H]
    uv run python scripts/settings_study.py lessons [STUDY]
    uv run python scripts/settings_study.py accept ID --by "reviewer: WHO"
    uv run python scripts/settings_study.py edit ID --by "reviewer: WHO" [--text T] \\
        [--keys K,K] --why W
    uv run python scripts/settings_study.py reject ID --by "reviewer: WHO" --why W

`PART.stl` is the part in its own file's axes, the same solid as its STEP file in the assembly
file, which `load` reads (`docs/bench.md`, "The assembly file"); `--part` names it there. The
assembly is what `load` reads the part's load cases on: the part alone with what holds and loads
it, read in one pass. `--turn` is the pose it prints in, which must stand the assembly's
`layers` axis up. Each `--case` is a name and `load`'s words after the assembly file, as
`'standing=--weight 30 --at 10,10,700'` or `'lifted=--lift top'`; or `--needs` takes each
`load` need of a design run's `record/needs.json` (only `--need`'s, where given), with its own
figure and limit, the run's assembly word replaced by `--assembly`.

The print loop's model (`setup.model.setup_llm`, LiteLLM :4000) studies it with tools: it reads
the profile's defaults first, then candidates of its own, each sliced through Orca and read by
`load` under every case, and recommends one (`docs/settings-study.md`). Nothing is printed,
armed or sent to the queue. It writes, in `--out` (a new folder under the system's temporary
one by default): `study.md`, the table and recommendation for the gate; `study.json`; and a
folder for each candidate with its Orca project, G-code and `load` replies. The ledger (the
Mac's, unless `--ledger`) keeps the study and its lessons as candidates for a reviewer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import trimesh

from print_shop.bench.assembly import read as read_assembly
from print_shop.bench.report import NoAnswer
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import LLMConfig
from print_shop.ingest.orientation import AXIS_ALIGNED, candidates, write_rotated
from print_shop.ledger import Ledger, ledger_path
from print_shop.rounds.lessons import Refused
from print_shop.runtime import bed_type, job_profiles
from print_shop.setup.context import read_context
from print_shop.setup.features import measure
from print_shop.setup.model import setup_llm
from print_shop.slicer.regions import Pose
from print_shop.slicer.store import NoChoice
from print_shop.study import lessons
from print_shop.study.candidate import Shaper
from print_shop.study.loop import StudyLoop, StudyPart, llm_chat, run
from print_shop.study.reading import BenchLoad, Case, OrcaSlicing, cases_from_needs, defaults
from print_shop.study.table import as_plain, table


def _case(text: str) -> Case:
    name, sep, words = text.partition("=")
    if not sep or not name.strip() or not words.strip():
        raise argparse.ArgumentTypeError(f"a case is NAME=LOAD ARGS, as 'standing=--weight 30 "
                                         f"--at 10,10,700': not {text!r}")
    return Case(name.strip(), tuple(shlex.split(words)))


def _turn(text: str) -> tuple[float, float, float]:
    try:
        values = tuple(float(v) for v in text.split(","))
    except ValueError:
        values = ()
    if len(values) != 3:
        raise argparse.ArgumentTypeError("--turn is three angles, RX,RY,RZ, in degrees")
    return values[0], values[1], values[2]


def study(args: argparse.Namespace) -> int:
    stl: Path = args.stl.resolve()
    assembly_path: Path = args.assembly.resolve()
    try:
        assembly = read_assembly(str(assembly_path))
        placed = assembly.part(args.part)
    except (NoAnswer, StopIteration, KeyError) as exc:
        print(f"--assembly: {exc or f'no part {args.part}'}", file=sys.stderr)
        return 2
    cases = list(args.case or [])
    if args.needs:
        cases += cases_from_needs(args.needs, args.need or [])
    if not cases:
        print("no load cases: give --case, or --needs with load needs", file=sys.stderr)
        return 2
    mesh = trimesh.load(stl, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        print(f"{stl.name} isn't one mesh", file=sys.stderr)
        return 2
    turn = args.turn
    if Pose.of(mesh, turn).layers() != placed.layers:
        print(f"--turn {','.join(f'{v:g}' for v in turn)} stands the part's own "
              f"{Pose.of(mesh, turn).layers() or 'no axis'} up, but {assembly_path.name} gives "
              f"its layers along {placed.layers}: give the pose it prints in", file=sys.stderr)
        return 2
    out: Path = (args.out or Path(tempfile.mkdtemp(prefix="settings-study-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = load_env(SETTINGS_PATH)
    try:
        # The profiles a job slices with, on the printer's plate (`runtime.bed_type`): the
        # process's own Cool Plate won't take PETG.
        profiles = job_profiles(None, out, bed=bed_type(env), nozzle_mm=args.nozzle,
                                filament=args.filament)
    except (NoChoice, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    context = read_context(stl, args.context) if args.context else None
    poses = candidates(stl)
    kept = next(p for p in poses if p.rotation_deg == turn) if turn in AXIS_ALIGNED else None
    if kept is None:
        print("--turn is one of the poses the print loop turns parts by: "
              + "; ".join(",".join(f"{v:g}" for v in p) for p in AXIS_ALIGNED), file=sys.stderr)
        return 2
    posed = write_rotated(stl, turn, out / "posed" / stl.name)
    features = measure(posed, kept.bed_contact_area_mm2)
    keys = lessons.keys(features, assembly, args.part, context)
    process = json.loads(Path(profiles.process).read_text())
    config = setup_llm(LLMConfig.from_settings(), env)
    loop = StudyLoop(chat=llm_chat(config), model=config.model,
                     max_candidates=args.max_candidates, max_seconds=args.max_hours * 3600)
    meshes = out / "meshes"
    started = datetime.now(UTC).isoformat(timespec="seconds")
    where = args.ledger or ledger_path()
    # Open only to read and to record: a study runs for hours, beside the shop's own jobs.
    with Ledger(where) as ledger:
        kept_lessons = [f"lesson {r['id']}: {r['text']}" for r in lessons.for_part(ledger, keys)]
        studied_before = lessons.earlier(ledger, keys)
    part = StudyPart(
        name=args.part, stl=stl, turn=turn, cases=tuple(cases),
        shaper=Shaper(mesh, meshes, context, assembly, args.part,
                      (turn, *(p for p in AXIS_ALIGNED if p != turn))),
        slicing=OrcaSlicing(stl, profiles, meshes),
        loading=BenchLoad(assembly_path, args.part),
        defaults=defaults(profiles, stl, turn), material=placed.material,
        workdir=out / "candidates", keys=keys,
        poses=(kept, *(p for p in poses if p.rotation_deg != turn)),
        lessons=tuple(kept_lessons), earlier=tuple(studied_before),
        wall_sequence=str(process.get("wall_sequence", "")), filament=args.filament,
    )
    print(f"studying {args.part} under {len(cases)} load cases, in {out}", flush=True)
    result = run(loop, part)
    names = [c.name for c in cases]
    (out / "study.md").write_text(table(result, args.part, names))
    (out / "study.json").write_text(json.dumps(as_plain(result, args.part), indent=1))
    with Ledger(where) as ledger:
        study_id, made = lessons.record(
            ledger, part=args.part, stl_name=stl.name,
            stl_sha256=hashlib.sha256(stl.read_bytes()).hexdigest(), features=features,
            part_keys=keys, filament=args.filament,
            cases=[{"name": c.name, "args": list(c.args), "number": c.number,
                    "at_most": c.at_most, "at_least": c.at_least} for c in cases],
            result=result, started_at=started)
    print((out / "study.md").read_text())
    print(f"the ledger keeps it as {study_id}, with {len(made)} lessons for a reviewer "
          f"(settings_study.py lessons {study_id})")
    print(f"written in {out}: study.md, study.json, candidates/")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="study a part's settings")
    go.add_argument("stl", type=Path, help="the part, in its own file's axes")
    go.add_argument("--assembly", type=Path, required=True,
                    help="the assembly file load reads the part's load cases on")
    go.add_argument("--part", required=True, help="the part's name in the assembly file")
    go.add_argument("--nozzle", type=float, required=True, help="the nozzle, in mm (0.4)")
    go.add_argument("--filament", required=True, help="a variety, or a tuning's name")
    go.add_argument("--turn", type=_turn, default=AXIS_ALIGNED[0], metavar="RX,RY,RZ",
                    help="the pose it prints in, in degrees about x, then y, then z")
    go.add_argument("--context", type=Path, help="a sidecar or design run's folder, for its fits")
    go.add_argument("--case", type=_case, action="append", metavar="NAME=ARGS",
                    help="a load case: its name and load's words after the assembly file")
    go.add_argument("--needs", type=Path, help="a design run's record/needs.json")
    go.add_argument("--need", action="append", metavar="ID", help="only these needs")
    go.add_argument("--out", type=Path, help="where the study is written")
    go.add_argument("--ledger", type=Path, help="the ledger: the Mac's unless given")
    go.add_argument("--max-candidates", type=int, default=10)
    go.add_argument("--max-hours", type=float, default=6.0)
    listing = sub.add_parser("lessons", help="every study lesson, or one study's")
    listing.add_argument("study", nargs="?")
    listing.add_argument("--ledger", type=Path)
    for name in ("accept", "edit", "reject"):
        step = sub.add_parser(name, help=f"{name} a study lesson")
        step.add_argument("id", type=int)
        step.add_argument("--by", required=True, help='"reviewer: WHO"')
        step.add_argument("--ledger", type=Path)
        if name != "accept":
            step.add_argument("--why", required=True)
        if name == "edit":
            step.add_argument("--text")
            step.add_argument("--keys", help="traits and joints, comma-separated")
    args = parser.parse_args(argv)
    if args.command == "run":
        return study(args)
    with Ledger(args.ledger or ledger_path()) as ledger:
        try:
            if args.command == "lessons":
                print(lessons.listing(ledger, args.study), end="")
            elif args.command == "accept":
                lessons.accept(ledger, args.id, args.by)
            elif args.command == "reject":
                lessons.reject(ledger, args.id, args.by, args.why)
            else:
                lessons.edit(ledger, args.id, args.by, text=args.text,
                             keyed=args.keys.split(",") if args.keys else None, why=args.why)
        except Refused as exc:
            print(exc, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
