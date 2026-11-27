"""Settings studies queued by themselves, from approved design runs (mqucifer/print-shop#359).

The Sponsor, 2026-11-27: the settings study "is what print shop should be doing, learning from
it", and of which: "Everything with a load case to start with the option to include flagged
designs." So:

- **On approval** (`scripts/rounds.py resume NAME --approve`), a run any of whose needs is a
  `load` need has a study queued for each part it delivers (`on_approval`): each approved file
  that's a part of the assembly its `load` needs read, with the run's own load cases, its folder
  for the part's fits, and the side its bed need lays the part on. Queuing it can't refuse or
  undo the approval: what goes wrong is said, and the run is approved as it was.
- **A design the Sponsor flags** (`scripts/settings_study.py queue RUN --by WHO`) is studied too:
  each approved file, or those named, with no load cases (time, grams and finish only), or with
  cases the Sponsor names (`flag`).
- **The Sponsor's limits** (`study.limits`) are kept with each order, and bind its study.
- **One at a time** (`scripts/settings_study.py work`): the oldest order is claimed (`claim`),
  its inputs made in the run's own folder (`prepare`), and the study run within the host's budget
  (mqucifer/print-shop#364), with no printer, arming or queue. Its table and records are written
  with the run's, in `RUN/studies/N-PART/`, and the order says where (`finish`).

The orders are a table of the design runs' own ledger (`study_orders`), beside the approvals they
follow. Nothing here reads or writes the shop's ledger: the study does, as it always has.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import trimesh

from print_shop.bench import host
from print_shop.ingest.orientation import AXIS_ALIGNED
from print_shop.refine import approved as approved_mod
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.paths import content_hash
from print_shop.slicer.regions import Pose
from print_shop.study.limits import Limits

__all__ = ["Order", "claim", "finish", "flag", "laid_for", "listing", "on_approval",
           "orders", "prepare"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS study_orders (
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL,
    -- The approval it follows: the approved folder its file is from.
    approval INTEGER NOT NULL,
    -- The part's name in the assembly, or its file's stem where there's none.
    part TEXT NOT NULL,
    -- 'load', queued on approval with the run's load cases; 'flagged', queued by the Sponsor.
    kind TEXT NOT NULL,
    -- The approved file, held to its hash.
    file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    -- The side it lies on the bed, as the run's bed need or `done` named it: -z to +y.
    side TEXT NOT NULL DEFAULT '',
    -- The assembly its load needs read, in the run's work folder, and its needs file.
    assembly TEXT,
    needs TEXT,
    -- The cases the Sponsor named, each NAME=LOAD ARGS, as JSON; and the Sponsor's limits.
    cases TEXT NOT NULL DEFAULT '[]',
    limits TEXT NOT NULL DEFAULT '{}',
    by TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    -- queued, running, done or failed: what came of it, and where it's written.
    status TEXT NOT NULL DEFAULT 'queued',
    claim TEXT,
    started_at TEXT,
    ended_at TEXT,
    out TEXT,
    said TEXT,
    UNIQUE (run, approval, part, kind)
)
"""
#: The settings that give a queued study its nozzle and its filament where its limits don't:
#: `STUDY_NOZZLE` in mm, and `STUDY_FILAMENTS` as `petg=Elegoo PETG; pla=Elegoo PLA`, by the
#: material the assembly gives the part, or one name for every part.
NOZZLE_KEY = "STUDY_NOZZLE"
FILAMENTS_KEY = "STUDY_FILAMENTS"
#: The meshing a STEP file is given for the study, as the refinement pass gives it Orca's own
#: defaults (`bench/refine.py`).
MESH_ANSWER = "mesh-answer.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure(ledger: DesignLedger) -> None:
    ledger.change(SCHEMA)


@dataclass(frozen=True)
class Order:
    """One queued study, as its row holds it."""

    id: int
    run: str
    approval: int
    part: str
    kind: str
    file: Path
    sha256: str
    side: str
    assembly: Path | None
    needs: Path | None
    cases: tuple[str, ...]
    limits: Limits
    by: str
    status: str
    out: Path | None = None
    said: str = ""

    @classmethod
    def of(cls, row: Any) -> Order:
        return cls(int(row["id"]), row["run"], int(row["approval"]), row["part"], row["kind"],
                   Path(row["file"]), row["sha256"], row["side"] or "",
                   Path(row["assembly"]) if row["assembly"] else None,
                   Path(row["needs"]) if row["needs"] else None,
                   tuple(json.loads(row["cases"] or "[]")),
                   Limits.of(json.loads(row["limits"] or "{}")), row["by"], row["status"],
                   Path(row["out"]) if row["out"] else None, row["said"] or "")


def _load_needs(approved: approved_mod.Approved) -> list[Any]:
    return [n for n in approved.needs if n.by == "bench"
            and shlex.split(n.command or "")[:1] == ["load"]]


def _assembly_of(folder: Path, needs: list[Any]) -> Path | None:
    """The assembly file the run's `load` needs read, in its work folder: their first word after
    `load`, as each is run there."""
    for need in needs:
        words = shlex.split(need.command)
        if len(words) > 1 and not words[1].startswith("-"):
            return folder / "work" / words[1]
    return None


def _sides(approved: approved_mod.Approved) -> dict[str, str]:
    """Each file's side on the bed, by its name: its bed need's, else what `done` named."""
    sides = {f.name: f.on_bed for g in approved.groups for f in g.files if f.on_bed}
    sides.update({Path(n.file).name: n.side for n in approved.needs
                  if n.by == "bed" and n.file and n.side})
    return sides


def _insert(ledger: DesignLedger, **row: Any) -> int | None:
    names = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    ledger.change(f"INSERT OR IGNORE INTO study_orders ({names}) VALUES ({marks})",  # noqa: S608
                  *row.values())
    found = ledger.rows("SELECT id FROM study_orders WHERE run=? AND approval=? AND part=? "
                        "AND kind=?", row["run"], row["approval"], row["part"], row["kind"])
    return int(found[0]["id"]) if found else None


def on_approval(folder: Path, ledger: DesignLedger, by: str,
                limits: Limits | None = None) -> tuple[list[int], str]:
    """A study queued for each part an approved run delivers, where any of its needs is a `load`
    need: the orders' ids, and what was done in words. Nothing is queued twice for one approval.

    Raises:
        Refused: If the run isn't approved, or its approved folder isn't as approved.
        ValueError: If its load needs name no assembly the study can read.
    """
    ensure(ledger)
    approved = approved_mod.read(folder, ledger)
    needs = _load_needs(approved)
    if not needs:
        return [], (f"{approved.run} has no load need, so no settings study is queued by "
                    "itself: `scripts/settings_study.py queue` flags it for one")
    assembly = _assembly_of(folder, needs)
    if assembly is None or not assembly.is_file():
        raise ValueError(f"{approved.run}'s load needs read no assembly file there is "
                         f"({assembly or 'none named'})")
    parts = {Path(str(p.get("file", ""))).name: str(p["name"])
             for p in json.loads(assembly.read_text(encoding="utf-8")).get("parts") or ()
             if isinstance(p, dict) and p.get("name")}
    sides = _sides(approved)
    made, skipped = [], []
    # In the assembly's own order of its parts.
    order = list(parts)
    files = sorted((f for g in approved.groups for f in g.files),
                   key=lambda f: order.index(f.name) if f.name in parts else len(order))
    for f in files:
        part = parts.get(f.name)
        if part is None:
            skipped.append(f.name)
            continue
        found = _insert(ledger, run=approved.run, approval=approved.approval, part=part,
                        kind="load", file=str(f.path), sha256=f.sha256,
                        side=sides.get(f.name, ""), assembly=str(assembly),
                        needs=str(folder / "needs.json"),
                        limits=json.dumps((limits or Limits()).plain()), by=by,
                        queued_at=_now())
        if found is not None:
            made.append(found)
    said = (f"a settings study of each part queued, {len(made)}: "
            + ", ".join(f"order {n}" for n in made) if made
            else "no part of the assembly is among its approved files: no study queued")
    if skipped:
        said += f"; not a part of its assembly, so not studied: {', '.join(skipped)}"
    return made, said


def flag(folder: Path, ledger: DesignLedger, by: str, files: list[str] | None = None,
         cases: list[str] | None = None, limits: Limits | None = None,
         assembly: Path | None = None) -> list[int]:
    """A study queued for each approved file of a run the Sponsor flags, or those named: with no
    load cases, or `cases` the Sponsor names, each `NAME=LOAD ARGS` read on the assembly its
    `load` needs read, or `assembly`, one of its work folder's, where it has none.

    Raises:
        Refused: If the run isn't approved, or its approved folder isn't as approved.
        ValueError: If a file named isn't approved, or cases are named and there's no assembly.
    """
    ensure(ledger)
    approved = approved_mod.read(folder, ledger)
    every = [f for g in approved.groups for f in g.files]
    if files:
        names = {f.name for f in every} | {Path(f.name).stem for f in every}
        if missing := [n for n in files if n not in names]:
            raise ValueError(f"not among {approved.run}'s approved files: {', '.join(missing)}")
        every = [f for f in every if f.name in files or Path(f.name).stem in files]
    if assembly is not None:
        assembly = assembly if assembly.is_absolute() else folder / "work" / assembly
    else:
        assembly = _assembly_of(folder, _load_needs(approved))
    if cases and (assembly is None or not assembly.is_file()):
        raise ValueError(f"cases are read on an assembly, and {approved.run}'s load needs name "
                         "none there is")
    parts: dict[str, str] = {}
    if assembly is not None and assembly.is_file():
        parts = {Path(str(p.get("file", ""))).name: str(p["name"])
                 for p in json.loads(assembly.read_text(encoding="utf-8")).get("parts") or ()
                 if isinstance(p, dict) and p.get("name")}
    sides = _sides(approved)
    made = []
    for f in every:
        part = parts.get(f.name) or Path(f.name).stem
        found = _insert(ledger, run=approved.run, approval=approved.approval, part=part,
                        kind="flagged", file=str(f.path), sha256=f.sha256,
                        side=sides.get(f.name, ""),
                        assembly=str(assembly) if cases and assembly else None,
                        needs=None, cases=json.dumps(list(cases or [])),
                        limits=json.dumps((limits or Limits()).plain()), by=by,
                        queued_at=_now())
        if found is not None:
            made.append(found)
    return made


def orders(ledger: DesignLedger, status: str | None = None) -> list[Order]:
    ensure(ledger)
    rows = (ledger.rows("SELECT * FROM study_orders WHERE status=? ORDER BY id", status)
            if status else ledger.rows("SELECT * FROM study_orders ORDER BY id"))
    return [Order.of(r) for r in rows]


def claim(ledger: DesignLedger) -> Order | None:
    """The oldest queued order, now running, or None: one claim wins it, so two workers never
    run one study."""
    ensure(ledger)
    token = uuid.uuid4().hex
    ledger.change("UPDATE study_orders SET status='running', claim=?, started_at=? WHERE id = "
                  "(SELECT id FROM study_orders WHERE status='queued' ORDER BY id LIMIT 1)",
                  token, _now())
    found = ledger.rows("SELECT * FROM study_orders WHERE claim=?", token)
    return Order.of(found[0]) if found else None


def finish(ledger: DesignLedger, order: Order, status: str, said: str,
           out: Path | None = None) -> None:
    ledger.change("UPDATE study_orders SET status=?, said=?, out=?, ended_at=? WHERE id=?",
                  status, said[:2000], str(out) if out else None, _now(), order.id)


def listing(ledger: DesignLedger) -> str:
    found = orders(ledger)
    if not found:
        return "No settings study is queued.\n"
    lines = []
    for o in found:
        where = f", in {o.out}" if o.out else ""
        lines.append(f"order {o.id}: {o.run} {o.part} ({o.kind}), {o.status}{where}"
                     + (f": {o.said}" if o.said else ""))
    return "\n".join(lines) + "\n"


def laid_for(side: str) -> tuple[float, float]:
    """The turns about x and y that lay a part on `side`, the face of its own file's axes that
    goes down (`-z` as it's drawn, `+z` turned over): of the print loop's poses
    (`ingest.orientation.AXIS_ALIGNED`), the one that turns that face's normal straight down.

    Raises:
        ValueError: If it isn't one of the six sides.
    """
    if not side:
        return 0.0, 0.0
    axes = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
    if len(side) != 2 or side[0] not in "+-" or side[1] not in axes:
        raise ValueError(f"a side on the bed is -x to +z, not {side!r}")
    normal = [v * (1 if side[0] == "+" else -1) for v in axes[side[1]]]
    box = trimesh.creation.box((1.0, 2.0, 3.0))
    for pose in AXIS_ALIGNED:
        turned = Pose.of(box, pose).matrix[:3, :3] @ normal
        if abs(turned[2] + 1.0) < 1e-6:
            return pose[0], pose[1]
    raise ValueError(f"no pose lays the part on {side}")


def _filament(env: dict[str, str], material: str | None) -> str | None:
    said = env.get(FILAMENTS_KEY, "").strip()
    if not said:
        return None
    if "=" not in said:
        return said
    by = {k.strip().lower(): v.strip() for k, _, v in
          (item.partition("=") for item in said.split(";")) if k.strip() and v.strip()}
    return by.get((material or "").lower())


def mesh(step: Path, work: Path) -> Path:
    """A STEP file as an STL in its own axes, by the bench's image at Orca's own STEP defaults,
    as the refinement pass meshes one (`bench/refine.py`). An STL is copied as it is.

    Raises:
        RuntimeError: If the bench's image couldn't mesh it.
    """
    work.mkdir(parents=True, exist_ok=True)
    if step.suffix.lower() == ".stl":
        target = work / step.name
        shutil.copyfile(step, target)
        return target
    shutil.copyfile(step, work / step.name)
    request = {"request": "mesh", "files": [step.name], "out": "meshed", "answer": MESH_ANSWER}
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    argv = host.container(work, host.helper("refine", [json.dumps(request)]), name,
                          image=host.helper_image("refine"))
    done = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False)
    answer_path = work / MESH_ANSWER
    if not answer_path.is_file():
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
        raise RuntimeError(f"the bench's image didn't mesh {step.name}: {' / '.join(tail)}")
    row = json.loads(answer_path.read_text(encoding="utf-8"))["files"][step.name]
    if "unread" in row:
        raise RuntimeError(f"{step.name} couldn't be meshed: {row['unread']}")
    return work / str(row["stl"])


def prepare(order: Order, folder: Path, env: dict[str, str]) -> list[str]:
    """The study's inputs, made in the run's own folder (`RUN/studies/N-PART/inputs/`), and the
    words `scripts/settings_study.py` runs it with: the approved file meshed in its own axes;
    the run's assembly with the approved file in place of the work folder's; the run's load
    needs as its cases, or those the Sponsor named, or none; the side it lies on, turned about
    its up axis by fit-bed's own turn; and the Sponsor's limits.

    Raises:
        ValueError: If the approved file isn't as approved, or no nozzle or filament is given.
        RuntimeError: If it couldn't be meshed.
    """
    out = folder / "studies" / f"{order.id}-{order.part}"
    inputs = out / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    if content_hash(order.file.name, order.file.read_bytes()) != order.sha256:
        raise ValueError(f"{order.file} has changed since it was approved")
    material: str | None = None
    words: list[str] = []
    if order.assembly is not None:
        raw = json.loads(order.assembly.read_text(encoding="utf-8"))
        for part in raw.get("parts") or ():
            if not isinstance(part, dict) or not part.get("file"):
                continue
            target = inputs / str(part["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            source = (order.file if part.get("name") == order.part
                      else order.assembly.parent / str(part["file"]))
            if source.is_file():
                shutil.copyfile(source, target)
            if part.get("name") == order.part:
                material = str(part.get("material") or "") or None
        shutil.copyfile(order.assembly, inputs / order.assembly.name)
        words += ["--assembly", str(inputs / order.assembly.name)]
    stl = mesh(order.file, inputs / "mesh")
    limits = order.limits
    nozzle = limits.nozzle or (float(env[NOZZLE_KEY]) if env.get(NOZZLE_KEY) else None)
    filament = limits.filament or _filament(env, material)
    if nozzle is None or filament is None:
        raise ValueError(f"no nozzle or filament for {order.part}: give them in its limits, or "
                         f"{NOZZLE_KEY} and {FILAMENTS_KEY} in the Mac's settings")
    (inputs / "limits.json").write_text(json.dumps(limits.plain(), indent=1))
    rx, ry = laid_for(order.side)
    words = ["run", str(stl), "--part", order.part, "--nozzle", f"{nozzle:g}",
             "--filament", filament, "--turn", f"{rx:g},{ry:g},fit", "--context", str(folder),
             "--limits", str(inputs / "limits.json"), "--out", str(out), *words]
    if order.kind == "load" and order.needs is not None:
        words += ["--needs", str(order.needs)]
    for case in order.cases:
        words += ["--case", case]
    if order.kind == "flagged" and not order.cases:
        words.append("--no-cases")
    return words
