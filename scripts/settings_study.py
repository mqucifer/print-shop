"""Study a part's settings for strength, filament and time, and recommend one set for the gate.

mqucifer/print-shop#339. On the Mac, with Orca installed and the bench's second image built
(`scripts/bench.py build cae`):

    uv run python scripts/settings_study.py run PART.stl --assembly A.json --part NAME \\
        --nozzle 0.4 --filament NAME [--turn RX,RY,RZ|RX,RY,fit] [--context PATH] \\
        (--case 'NAME=LOAD ARGS' ... | --needs RUN/record/needs.json [--need ID ...] |
         --no-cases) [--limits FILE] [--out DIR] [--ledger PATH] [--max-candidates N] \\
        [--max-hours H] [--reserve-memory SIZE] [--reserve-cpus N] [--serial]
    uv run python scripts/settings_study.py queue RUN --by WHO [--file NAME]... \\
        [--case 'NAME=ARGS']... [--assembly FILE] [--limits FILE]
    uv run python scripts/settings_study.py queued --root ROOT
    uv run python scripts/settings_study.py work --root ROOT [--once] [--ledger PATH]
    uv run python scripts/settings_study.py lessons [STUDY]
    uv run python scripts/settings_study.py accept ID --by "reviewer: WHO"
    uv run python scripts/settings_study.py edit ID --by "reviewer: WHO" [--text T] \\
        [--keys K,K] --why W
    uv run python scripts/settings_study.py reject ID --by "reviewer: WHO" --why W

`PART.stl` is the part in its own file's axes, the same solid as its STEP file in the assembly
file, which `load` reads (`docs/bench.md`, "The assembly file"); `--part` names it there. The
assembly is what `load` reads the part's load cases on: the part alone with what holds and loads
it, read in one pass. `--turn` is the pose it prints in, which must stand the assembly's
`layers` axis up: its turn about z, the part's up axis, may be any angle, or `fit` for `fit-bed`'s
own turn, the least that fits the bed (mqucifer/print-shop#351). A pose that doesn't fit the bed
is refused before Orca runs, saying the turn that fits, or the least footprint where none does.
Each `--case` is a name and `load`'s words after the assembly file, as
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
import fcntl
import hashlib
import json
import math
import shlex
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import trimesh

from print_shop.bench.assembly import MATERIALS
from print_shop.bench.assembly import read as read_assembly
from print_shop.bench.budget import Budget, Slots, megabytes, others_mb, reserved
from print_shop.bench.host import memory_for
from print_shop.bench.job import running_here
from print_shop.bench.report import NoAnswer
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import LLMConfig
from print_shop.ingest.orientation import AXIS_ALIGNED, scored
from print_shop.ledger import Ledger, ledger_path
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.lessons import Refused
from print_shop.runtime import bed_type, job_profiles
from print_shop.setup.context import read_context
from print_shop.setup.model import setup_llm
from print_shop.slicer.regions import Bed, Pose, degrees_said, turned_to_fit
from print_shop.slicer.store import NoChoice
from print_shop.study import lessons
from print_shop.study import orders as study_orders
from print_shop.study.candidate import Shaper, study_poses, unfit
from print_shop.study.limits import Limits
from print_shop.study.loop import StudyLoop, StudyPart, StudyResult, llm_chat, run
from print_shop.study.reading import (
    BenchLoad,
    Case,
    OrcaSlicing,
    cases_from_needs,
    check_names,
    defaults,
)
from print_shop.study.table import as_plain, table


def _load_mb(case: Case) -> int:
    return megabytes(memory_for(["load", "-", *case.args]))


def _case(text: str) -> Case:
    name, sep, words = text.partition("=")
    if not sep or not name.strip() or not words.strip():
        raise argparse.ArgumentTypeError(f"a case is NAME=LOAD ARGS, as 'standing=--weight 30 "
                                         f"--at 10,10,700': not {text!r}")
    return Case(name.strip(), tuple(shlex.split(words)))


def _turn(text: str) -> tuple[float, float, float | None]:
    """Three angles in degrees, or two and `fit` for fit-bed's own turn about z (None)."""
    words = [w.strip() for w in text.split(",")]
    try:
        if len(words) == 3 and words[2] == "fit":
            return float(words[0]), float(words[1]), None
        values = tuple(float(v) for v in words)
    except ValueError:
        values = ()
    if len(values) != 3 or not all(math.isfinite(v) for v in values):
        raise argparse.ArgumentTypeError("--turn is three angles, RX,RY,RZ, in degrees, or "
                                         "RX,RY,fit for fit-bed's own turn about z")
    return values[0], values[1], values[2]


def study(args: argparse.Namespace) -> int:
    stl: Path = args.stl.resolve()
    part_name: str = args.part or stl.stem
    try:
        limits = Limits.read(args.limits) if args.limits else Limits()
    except ValueError as exc:
        print(f"--limits: {exc}", file=sys.stderr)
        return 2
    nozzle = args.nozzle or limits.nozzle
    filament = args.filament or limits.filament
    if nozzle is None or filament is None:
        print("give --nozzle and --filament, or the limits' nozzle and filament", file=sys.stderr)
        return 2
    assembly_path: Path | None = args.assembly.resolve() if args.assembly else None
    assembly, placed = None, None
    if assembly_path is not None:
        try:
            assembly = read_assembly(str(assembly_path))
            placed = assembly.part(part_name)
        except (NoAnswer, StopIteration, KeyError) as exc:
            print(f"--assembly: {exc or f'no part {part_name}'}", file=sys.stderr)
            return 2
    cases = list(args.case or [])
    if args.needs:
        cases += cases_from_needs(args.needs, args.need or [])
    if not cases and not args.no_cases:
        print("no load cases: give --case, or --needs with load needs, or --no-cases for a "
              "study of time, grams and finish only", file=sys.stderr)
        return 2
    if cases and assembly_path is None:
        print("load cases are read on an assembly: give --assembly", file=sys.stderr)
        return 2
    try:
        check_names(cases)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    # The material `published` reads its figures for: the assembly's, else the filament's kind.
    material = (placed.material if placed is not None
                else next((m for k, m in MATERIALS.items() if k in filament.lower()), None))
    mesh = trimesh.load(stl, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        print(f"{stl.name} isn't one mesh", file=sys.stderr)
        return 2
    out: Path = (args.out or Path(tempfile.mkdtemp(prefix="settings-study-"))).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = load_env(SETTINGS_PATH)
    try:
        # The profiles a job slices with, on the printer's plate (`runtime.bed_type`): the
        # process's own Cool Plate won't take PETG.
        profiles = job_profiles(None, out, bed=bed_type(env), nozzle_mm=nozzle,
                                filament=filament)
        bed = Bed.of(Path(profiles.machine))
    except (NoChoice, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    rx, ry, rz = args.turn
    # `fit`: fit-bed's own turn about the up axis, the least that fits (`bench.footprint`).
    turn = (turned_to_fit(mesh, (rx, ry), bed).turn if rz is None else (rx, ry, rz))
    layers = placed.layers if placed is not None else None
    if assembly_path is not None and Pose.of(mesh, turn).layers() != layers:
        print(f"--turn {degrees_said(turn)} stands the part's own "
              f"{Pose.of(mesh, turn).layers() or 'no axis'} up, but {assembly_path.name} gives "
              f"its layers along {layers}: give the pose it prints in", file=sys.stderr)
        return 2
    if Pose.of(mesh, turn).layers() is None:
        print(f"--turn {degrees_said(turn)} stands none of the part's own axes up",
              file=sys.stderr)
        return 2
    # Refused before Orca runs: a pose that doesn't fit the bed, saying what would.
    if why := unfit(mesh, turn, bed, layers):
        print(why, file=sys.stderr)
        return 2
    context = read_context(stl, args.context) if args.context else None
    offered = study_poses(mesh, turn, bed)
    poses = tuple(scored(mesh, t) for t in offered)
    kept = poses[0]
    # Its features laid, not turned about its up axis: its box on the plate isn't its own.
    features = lessons.measured(stl, turn, out / "laid", kept.bed_contact_area_mm2)
    keys = lessons.keys(features, assembly, part_name, context)
    process = json.loads(Path(profiles.process).read_text())
    try:
        # What the host runs at once: Docker's memory and CPUs, less what's kept for other
        # work, as the settings or the options state it (`bench.budget`).
        kept_mb, kept_cpus = reserved(env, args.reserve_memory, args.reserve_cpus)
        budget = Budget.serial() if args.serial else Budget.docker(kept_mb, kept_cpus)
        heaviest = max(_load_mb(c) for c in cases)
        if budget.at_once(heaviest) < 1:
            raise ValueError(f"the host's budget, {budget.said()}, holds no load of "
                             f"{heaviest} MiB: keep less for other work")
    except (ValueError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    config = setup_llm(LLMConfig.from_settings(), env)
    loop = StudyLoop(chat=llm_chat(config), model=config.model,
                     max_candidates=args.max_candidates, max_seconds=args.max_hours * 3600)
    meshes = out / "meshes"
    own = defaults(profiles, stl, turn)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    where = args.ledger or ledger_path()
    # Open only to read and to record: a study runs for hours, beside the shop's own jobs.
    with Ledger(where) as ledger:
        kept_lessons = [f"lesson {r['id']}: {r['text']}" for r in lessons.for_part(ledger, keys)]
        studied_before = lessons.earlier(ledger, keys)
    part = StudyPart(
        name=part_name, stl=stl, turn=turn, cases=tuple(cases),
        shaper=Shaper(mesh, meshes, context, assembly, part_name, offered,
                      line_width=float(own.line_width or 0.45),
                      layer_height=float(own.layer_height or 0.2)),
        slicing=OrcaSlicing(stl, profiles, meshes),
        loading=BenchLoad(assembly_path or stl, part_name),
        defaults=own, material=material, limits=limits,
        workdir=out / "candidates", keys=keys,
        poses=poses,
        lessons=tuple(kept_lessons), earlier=tuple(studied_before),
        wall_sequence=str(process.get("wall_sequence", "")), filament=filament,
        # Other processes' bench and rounds containers are counted as each read takes its slot.
        slots=Slots(budget, None if args.serial else (lambda: others_mb(running_here()))),
    )
    print(f"studying {part_name} turned {degrees_said(turn)} under {len(cases)} load cases, "
          f"in {out}, within {budget.said()}", flush=True)
    names = [c.name for c in cases]

    def landed(so_far: StudyResult) -> None:
        # The table as it fills in, each reading as it lands.
        (out / "study.md").write_text(table(so_far, part_name, names, where=out))

    result = run(loop, part, progress=landed)
    (out / "study.md").write_text(table(result, part_name, names, where=out))
    (out / "study.json").write_text(json.dumps(as_plain(result, part_name), indent=1))
    with Ledger(where) as ledger:
        study_id, made = lessons.record(
            ledger, part=part_name, stl_name=stl.name,
            stl_sha256=hashlib.sha256(stl.read_bytes()).hexdigest(), features=features,
            part_keys=keys, filament=filament,
            cases=[{"name": c.name, "args": list(c.args), "number": c.number,
                    "at_most": c.at_most, "at_least": c.at_least} for c in cases],
            result=result, started_at=started)
    print((out / "study.md").read_text())
    print(f"the ledger keeps it as {study_id}, with {len(made)} lessons for a reviewer "
          f"(settings_study.py lessons {study_id})")
    print(f"written in {out}: study.md, study.json, candidates/")
    return 0


def _orders(args: argparse.Namespace) -> int:
    """The studies queued by themselves: a flag, the list, or the worker (`study.orders`)."""
    if args.command == "queue":
        folder = args.run.resolve()
        design = DesignLedger(args.design_ledger or folder.parent / "ledger.sqlite")
        try:
            limits = Limits.read(args.limits) if args.limits else None
            made = study_orders.flag(folder, design, args.by, args.file, args.case, limits,
                                     args.assembly)
        except (Refused, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"queued {len(made)} settings studies of {folder.name}: "
              + ", ".join(f"order {n}" for n in made))
        return 0
    root = args.root.expanduser().resolve()
    design = DesignLedger(root / "ledger.sqlite")
    if args.command == "queued":
        print(study_orders.listing(design), end="")
        return 0
    return work(root, design, args.once, args.ledger)


def work(root: Path, design: DesignLedger, once: bool = False, ledger: Path | None = None,
         run_study: Callable[[list[str]], int] | None = None) -> int:
    """Each queued study, the oldest first, one at a time, until none is queued: one worker per
    root, held by a lock, so studies never run two at once from it. A study that fails is said
    in its order, and the next goes on; nothing here touches a printer, an approval or a print.
    `run_study` is `main`, unless given."""
    run_study = run_study or main
    env = load_env(SETTINGS_PATH)
    lock = (root / "studies.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"another worker runs {root}'s studies", file=sys.stderr)
        return 3
    try:
        while (order := study_orders.claim(design)) is not None:
            folder = root / order.run
            print(f"order {order.id}: studying {order.run} {order.part}", flush=True)
            try:
                words = study_orders.prepare(order, folder, env)
                if ledger is not None:
                    words += ["--ledger", str(ledger)]
                code = run_study(words)
                out = Path(words[words.index("--out") + 1])
                if code == 0:
                    study_orders.finish(design, order, "done",
                                        f"its table is {out / 'study.md'}", out)
                else:
                    study_orders.finish(design, order, "failed",
                                        f"the study exited {code}: see {out}", out)
            except Exception as exc:  # noqa: BLE001 - a study failing is its order's answer
                study_orders.finish(design, order, "failed", f"{type(exc).__name__}: {exc}")
            if once:
                break
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run", help="study a part's settings")
    go.add_argument("stl", type=Path, help="the part, in its own file's axes")
    go.add_argument("--assembly", type=Path,
                    help="the assembly file load reads the part's load cases on")
    go.add_argument("--part", help="the part's name in the assembly file: its file's, unless "
                                   "given")
    go.add_argument("--nozzle", type=float, help="the nozzle, in mm (0.4): else the limits'")
    go.add_argument("--filament", help="a variety, or a tuning's name: else the limits'")
    go.add_argument("--limits", type=Path,
                    help="the Sponsor's limits, binding on the study, as JSON (study.limits)")
    go.add_argument("--no-cases", action="store_true",
                    help="no load cases: a study of time, grams and finish only")
    go.add_argument("--turn", type=_turn, default=AXIS_ALIGNED[0], metavar="RX,RY,RZ",
                    help="the pose it prints in, in degrees about x, then y, then z, the "
                         "part's up axis; RX,RY,fit turns it about z by fit-bed's own turn")
    go.add_argument("--context", type=Path, help="a sidecar or design run's folder, for its fits")
    go.add_argument("--case", type=_case, action="append", metavar="NAME=ARGS",
                    help="a load case: its name and load's words after the assembly file")
    go.add_argument("--needs", type=Path, help="a design run's record/needs.json")
    go.add_argument("--need", action="append", metavar="ID", help="only these needs")
    go.add_argument("--out", type=Path, help="where the study is written")
    go.add_argument("--ledger", type=Path, help="the ledger: the Mac's unless given")
    go.add_argument("--reserve-memory", metavar="SIZE",
                    help="Docker memory kept for other work, as 4g: else BENCH_RESERVE_MEMORY "
                         "in the settings, else one bench command's 3g")
    go.add_argument("--reserve-cpus", type=int, metavar="N",
                    help="Docker CPUs kept for other work: else BENCH_RESERVE_CPUS in the "
                         "settings, else one bench command's 2")
    go.add_argument("--serial", action="store_true",
                    help="read one slice or load at a time, as before")
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
    queue = sub.add_parser("queue", help="flag an approved design run for a study")
    queue.add_argument("run", type=Path, help="the approved run's folder")
    queue.add_argument("--by", required=True, help='"Sponsor: WHO" or "operator: WHO"')
    queue.add_argument("--file", action="append", metavar="NAME",
                       help="only these approved files, by name or stem")
    queue.add_argument("--case", action="append", metavar="NAME=ARGS",
                       help="a load case, read on the assembly its load needs read")
    queue.add_argument("--limits", type=Path, help="the Sponsor's limits, as JSON")
    queue.add_argument("--assembly", type=Path,
                       help="the assembly the cases are read on, in the run's work folder: "
                            "its load needs' own, unless given")
    queue.add_argument("--design-ledger", type=Path,
                       help="the design runs' ledger: the run's root's, unless given")
    queued = sub.add_parser("queued", help="each queued study, and what came of it")
    queued.add_argument("--root", type=Path, required=True, help="the design runs' root")
    work = sub.add_parser("work", help="run the queued studies, one at a time")
    work.add_argument("--root", type=Path, required=True, help="the design runs' root")
    work.add_argument("--once", action="store_true", help="one study, then stop")
    work.add_argument("--ledger", type=Path, help="the shop's ledger: the Mac's unless given")
    args = parser.parse_args(argv)
    if args.command == "run":
        return study(args)
    if args.command in ("queue", "queued", "work"):
        return _orders(args)
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
