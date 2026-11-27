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

import fcntl
import json
import re
import shlex
import shutil
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import trimesh

from print_shop.bench import host
from print_shop.ingest.orientation import AXIS_ALIGNED
from print_shop.refine import approved as approved_mod
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.paths import content_hash
from print_shop.slicer.regions import Pose
from print_shop.study.limits import Limits
from print_shop.study.reading import _files

__all__ = ["Order", "checkout", "claim", "finish", "flag", "laid_for", "listing", "on_approval",
           "orders", "orphaned", "prepare", "queued", "retry", "start_worker"]

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
    -- Each study of one part of one approval, 1 on: a failed or done one queued again is the next.
    attempt INTEGER NOT NULL DEFAULT 1,
    -- The approved file, held to its hash.
    file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    -- The side it lies on the bed, as the run's bed need or `done` named it: -z to +y.
    side TEXT NOT NULL DEFAULT '',
    -- The part's material as the assembly gives it, where it does.
    material TEXT,
    -- The assembly its cases are read on, by name in `inputs`, and the run's needs file.
    assembly TEXT,
    needs TEXT,
    -- The cases the Sponsor named, each NAME=LOAD ARGS, as JSON; and the Sponsor's limits.
    cases TEXT NOT NULL DEFAULT '[]',
    limits TEXT NOT NULL DEFAULT '{}',
    -- What the study reads, copied as it was queued: the folder, and each file's hash in it.
    inputs TEXT,
    manifest TEXT NOT NULL DEFAULT '{}',
    by TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    -- queued, running, done or failed: what came of it, and where it's written.
    status TEXT NOT NULL DEFAULT 'queued',
    claim TEXT,
    started_at TEXT,
    ended_at TEXT,
    out TEXT,
    said TEXT,
    UNIQUE (run, approval, part, kind, attempt)
)
"""
#: The settings that give a queued study its nozzle and its filament where its limits don't:
#: `STUDY_NOZZLE` in mm, and `STUDY_FILAMENTS` as `petg=Elegoo PETG; pla=Elegoo PLA; *=Elegoo
#: PLA`, by the material the assembly gives the part, `*` for one it gives none; or one name
#: for every part. And the checkout the worker runs from: `STUDY_CHECKOUT`, else this one's
#: main checkout, never a worktree, which a builder may remove.
NOZZLE_KEY = "STUDY_NOZZLE"
FILAMENTS_KEY = "STUDY_FILAMENTS"
CHECKOUT_KEY = "STUDY_CHECKOUT"
#: The meshing a STEP file is given for the study, as the refinement pass gives it Orca's own
#: defaults (`bench/refine.py`), and how long it may take, in s.
MESH_ANSWER = "mesh-answer.json"
MESH_TIMEOUT = 600
#: The lock one worker per root holds while it runs that root's studies, and its log.
LOCK = "studies.lock"
LOG = "studies.log"
#: The study's own script, which runs the worker.
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "settings_study.py"
#: An order not yet ended: one of these isn't queued again. `copying` is one whose inputs are
#: being copied, which no worker may claim yet.
OPEN = ("copying", "queued", "running")
#: The orders' format, as this code writes and reads them: a worker's checkout that reads an
#: older one can't run them (`start_worker`).
ORDERS_FORMAT = 2


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
    attempt: int = 1
    material: str | None = None
    inputs: Path | None = None
    manifest: dict[str, str] | None = None

    @classmethod
    def of(cls, row: Any) -> Order:
        return cls(int(row["id"]), row["run"], int(row["approval"]), row["part"], row["kind"],
                   Path(row["file"]), row["sha256"], row["side"] or "",
                   Path(row["assembly"]) if row["assembly"] else None,
                   Path(row["needs"]) if row["needs"] else None,
                   tuple(json.loads(row["cases"] or "[]")),
                   Limits.of(json.loads(row["limits"] or "{}")), row["by"], row["status"],
                   Path(row["out"]) if row["out"] else None, row["said"] or "",
                   int(row["attempt"]), row["material"] or None,
                   Path(row["inputs"]) if row["inputs"] else None,
                   json.loads(row["manifest"] or "{}"))


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


def _named(f: approved_mod.File) -> str:
    """A file by the name the run gave it, as its assembly and needs name it: a file shared by
    several groups is renamed at review (`review.shared_names`), and `was` keeps its own."""
    return Path(f.was or f.name).name


def _sides(approved: approved_mod.Approved) -> dict[str, str]:
    """Each file's side on the bed, by its own name: its bed need's, else what `done` named."""
    sides = {_named(f): f.on_bed for g in approved.groups for f in g.files if f.on_bed}
    sides.update({Path(n.file).name: n.side for n in approved.needs
                  if n.by == "bed" and n.file and n.side})
    return sides


def _delivered(approved: approved_mod.Approved) -> list[approved_mod.File]:
    """Each file the run delivers, once: a file shared by several groups is one part."""
    seen: set[str] = set()
    found = []
    for group in approved.groups:
        for f in group.files:
            if _named(f) not in seen:
                seen.add(_named(f))
                found.append(f)
    return found


def _key(name: str) -> str:
    """A file's name as an assembly names it, relative to its folder."""
    return PurePosixPath(name.replace("\\", "/")).as_posix().removeprefix("./")


@dataclass(frozen=True)
class _Assembly:
    """An assembly as the study reads it: its file, its parts by their files' paths relative to
    it, and every file it names, each where it is in the run's work folder."""

    path: Path
    raw: dict[str, Any]
    parts: dict[str, dict[str, Any]]
    files: list[str]

    @classmethod
    def read(cls, path: Path) -> _Assembly:
        """Raises:
            ValueError: If it isn't there, or a file it names isn't beside it.
        """
        if not path.is_file():
            raise ValueError(f"the assembly {path} isn't there")
        raw = json.loads(path.read_text(encoding="utf-8"))
        parts = {_key(str(p["file"])): p for p in raw.get("parts") or ()
                 if isinstance(p, dict) and p.get("name") and p.get("file")}
        files = _files(raw)
        if missing := [n for n in files if not (path.parent / n).is_file()]:
            raise ValueError(f"{', '.join(missing)}, which {path.name} names, "
                             f"{'isn' if len(missing) == 1 else 'aren'}'t in {path.parent}: its "
                             "study would read another assembly than the one approved")
        return cls(path, raw, parts, files)

    def key_of(self, f: approved_mod.File) -> str | None:
        """The part's file as the assembly names it, for an approved file: by the path the run
        gave it, else by its name where only one part's file has that name. Two parts in
        different folders may share a file's name."""
        named = _key(f.was or f.name)
        if named in self.parts:
            return named
        same = [k for k in self.parts if PurePosixPath(k).name == PurePosixPath(named).name]
        return same[0] if len(same) == 1 else None

    def part_of(self, f: approved_mod.File) -> dict[str, Any] | None:
        key = self.key_of(f)
        return self.parts[key] if key is not None else None


def _snapshot(inputs: Path, f: approved_mod.File, assembly: _Assembly | None) -> dict[str, str]:
    """What the study reads, copied into `inputs` as it's queued, and each file's hash: the
    approved file, held to the approval's own hash, and the assembly with every file it names,
    the part's own the approved copy. Hours later the work folder may have changed; what was
    approved hasn't.

    Raises:
        ValueError: If the approved file isn't as approved.
    """
    inputs.mkdir(parents=True, exist_ok=True)
    copied: dict[str, Path] = {f.name: f.path}
    if assembly is not None:
        own = assembly.key_of(f)
        copied[assembly.path.name] = assembly.path
        for name in assembly.files:
            copied[name] = f.path if _key(name) == own else assembly.path.parent / name
    manifest = {}
    for name, source in copied.items():
        target = inputs / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        manifest[name] = content_hash(Path(name).name, target.read_bytes())
    if content_hash(f.name, (inputs / f.name).read_bytes()) != f.sha256:
        raise ValueError(f"{f.path} isn't as it was approved")
    return manifest


def _insert(ledger: DesignLedger, run: str, approval: int, part: str, kind: str,
            again_done: bool, **row: Any) -> tuple[int | None, str, int]:
    """An order's row, not yet claimable (`copying`), and its attempt: the next of its part's,
    read and written in one transaction, so two queuings can't take one attempt. Or the order
    there is already, said: one still open isn't queued again, nor one done unless
    `again_done`."""
    with ledger.immediate() as db:
        last = db.execute("SELECT id, attempt, status FROM study_orders WHERE run=? AND "
                          "approval=? AND part=? AND kind=? ORDER BY attempt DESC LIMIT 1",
                          (run, approval, part, kind)).fetchall()
        attempt = 1
        if last:
            if last[0]["status"] in OPEN:
                return None, f"order {last[0]['id']} is {last[0]['status']} already, for {part}", 0
            if last[0]["status"] == "done" and not again_done:
                return None, f"order {last[0]['id']} studied {part} already", 0
            attempt = int(last[0]["attempt"]) + 1
        fields = {"run": run, "approval": approval, "part": part, "kind": kind,
                  "attempt": attempt, **row, "status": "copying", "queued_at": _now()}
        names = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        made = db.execute(f"INSERT INTO study_orders ({names}) VALUES ({marks})",  # noqa: S608
                          tuple(fields.values()))
        return int(made.lastrowid or 0), "", attempt


def _ready(ledger: DesignLedger, order: int, part: str, copy: Callable[[Path], dict[str, str]],
           folder: Path) -> tuple[int | None, str]:
    """An order's inputs copied, and the order queued: claimable only now."""
    inputs = folder / "studies" / f"{order}-{part}" / "inputs"
    try:
        manifest = copy(inputs)
    except (OSError, ValueError) as exc:
        ledger.change("UPDATE study_orders SET status='failed', said=?, ended_at=? WHERE id=?",
                      f"its inputs couldn't be copied: {exc}", _now(), order)
        return None, f"order {order} failed: its inputs couldn't be copied: {exc}"
    ledger.change("UPDATE study_orders SET inputs=?, manifest=?, status='queued' WHERE id=? AND "
                  "status='copying'", str(inputs), json.dumps(manifest), order)
    return order, ""


def _queue(ledger: DesignLedger, folder: Path, approved: approved_mod.Approved,
           f: approved_mod.File, part: str, kind: str, assembly: _Assembly | None,
           material: str | None, sides: dict[str, str], cases: list[str],
           limits: Limits | None, by: str, again_done: bool) -> tuple[int | None, str]:
    """One order, and what was done in words: queued, or the order it already is. One still
    open isn't queued again; one failed is, as its next attempt; one done is where `again_done`,
    as the Sponsor's own flag asks it."""
    try:
        order, said, attempt = _insert(
            ledger, approved.run, approved.approval, part, kind, again_done, file=str(f.path),
            sha256=f.sha256, side=sides.get(_named(f), ""), material=material,
            assembly=assembly.path.name if assembly else None,
            needs=str(folder / "needs.json") if kind == "load" else None,
            cases=json.dumps(list(cases)), limits=json.dumps((limits or Limits()).plain()),
            by=by)
    except sqlite3.IntegrityError:
        return None, f"{part} was queued meanwhile, by another: `queued` lists it"
    if order is None:
        return None, said
    done, failed = _ready(ledger, order, part, lambda inputs: _snapshot(inputs, f, assembly),
                          folder)
    if done is None:
        return None, failed
    again = f", its attempt {attempt}" if attempt > 1 else ""
    return order, f"order {order} queued for {part}{again}"


def retry(ledger: DesignLedger, order_id: int, by: str) -> tuple[int | None, str]:
    """An order failed or done, queued again as it was, as its next attempt: of its kind, with
    its cases, needs, limits and the inputs copied as it was first queued, held to their hashes.

    Raises:
        ValueError: If there's no such order, or its inputs aren't as they were copied.
    """
    ensure(ledger)
    rows = ledger.rows("SELECT * FROM study_orders WHERE id=?", order_id)
    if not rows:
        raise ValueError(f"no order {order_id}")
    was = Order.of(rows[0])
    if was.inputs is None or not was.manifest:
        raise ValueError(f"order {order_id} has no inputs copied to queue it from again: flag "
                         "its run again (`settings_study.py queue`)")
    for name, sha in was.manifest.items():
        path = was.inputs / name
        if not path.is_file() or content_hash(Path(name).name, path.read_bytes()) != sha:
            raise ValueError(f"{path} has changed since order {order_id} was queued")
    try:
        order, said, attempt = _insert(
            ledger, was.run, was.approval, was.part, was.kind, True, file=str(was.file),
            sha256=was.sha256, side=was.side, material=was.material,
            assembly=str(was.assembly) if was.assembly else None,
            needs=str(was.needs) if was.needs else None, cases=json.dumps(list(was.cases)),
            limits=json.dumps(was.limits.plain()), by=by)
    except sqlite3.IntegrityError:
        return None, f"{was.part} was queued meanwhile, by another: `queued` lists it"
    if order is None:
        return None, said
    inputs_from = was.inputs

    def copy(inputs: Path) -> dict[str, str]:
        shutil.copytree(inputs_from, inputs, dirs_exist_ok=True)
        return dict(was.manifest or {})

    folder = inputs_from.parent.parent.parent
    done, failed = _ready(ledger, order, was.part, copy, folder)
    if done is None:
        return None, failed
    return order, (f"order {order} queued for {was.part} as order {order_id} was, its attempt "
                   f"{attempt}")


def on_approval(folder: Path, ledger: DesignLedger, by: str,
                limits: Limits | None = None) -> tuple[list[int], str]:
    """A study queued for each part an approved run delivers, where any of its needs is a `load`
    need: the orders' ids, and what was done in words. Its assembly and every file that names
    are copied as they're queued, the part's own the approved copy. An order still queued or
    running, or done, for this approval isn't queued again; one failed is.

    Raises:
        Refused: If the run isn't approved, or its approved folder isn't as approved.
        ValueError: If its load needs name no assembly there is, or a file it names isn't there.
    """
    ensure(ledger)
    folder = folder.resolve()
    approved = approved_mod.read(folder, ledger)
    needs = _load_needs(approved)
    if not needs:
        return [], (f"{approved.run} has no load need, so no settings study is queued by "
                    "itself: `scripts/settings_study.py queue` flags it for one")
    where = _assembly_of(folder, needs)
    if where is None:
        raise ValueError(f"{approved.run}'s load needs name no assembly file")
    assembly = _Assembly.read(where)
    sides = _sides(approved)
    order = list(assembly.parts)
    files = sorted(_delivered(approved), key=lambda f: order.index(key)
                   if (key := assembly.key_of(f)) is not None else len(order))
    made, said, skipped = [], [], []
    for f in files:
        part = assembly.part_of(f)
        if part is None:
            skipped.append(_named(f))
            continue
        found, words = _queue(ledger, folder, approved, f, str(part["name"]), "load", assembly,
                              str(part.get("material") or "") or None, sides, [], limits, by,
                              again_done=False)
        said.append(words)
        if found is not None:
            made.append(found)
    text = ("; ".join(said) if said
            else "no part of the assembly is among its approved files: no study queued")
    if skipped:
        text += f"; not a part of its assembly, so not studied: {', '.join(skipped)}"
    return made, text


def flag(folder: Path, ledger: DesignLedger, by: str, files: list[str] | None = None,
         cases: list[str] | None = None, limits: Limits | None = None,
         assembly: Path | None = None) -> list[tuple[int | None, str]]:
    """A study queued for each approved file of a run the Sponsor flags, or those named: with no
    load cases, or `cases` the Sponsor names, each `NAME=LOAD ARGS` read on the assembly its
    `load` needs read, or `assembly`, one of its work folder's, where it has none. A part done or
    failed is queued again, with these cases and limits, as its next attempt: each order and
    what was done, in words.

    Raises:
        Refused: If the run isn't approved, or its approved folder isn't as approved.
        ValueError: If a file named isn't approved, or cases are named and there's no assembly,
            or a file the assembly names isn't there.
    """
    ensure(ledger)
    folder = folder.resolve()
    approved = approved_mod.read(folder, ledger)
    every = _delivered(approved)
    if files:
        names = {_named(f) for f in every} | {Path(_named(f)).stem for f in every}
        if missing := [n for n in files if n not in names]:
            raise ValueError(f"not among {approved.run}'s approved files: {', '.join(missing)}")
        every = [f for f in every if _named(f) in files or Path(_named(f)).stem in files]
    if assembly is not None:
        where: Path | None = assembly if assembly.is_absolute() else folder / "work" / assembly
    else:
        where = _assembly_of(folder, _load_needs(approved))
    if cases and (where is None or not where.is_file()):
        raise ValueError(f"cases are read on an assembly, and {approved.run}'s load needs name "
                         "none there is")
    read = _Assembly.read(where) if where is not None and where.is_file() else None
    sides = _sides(approved)
    done = []
    for f in every:
        named = read.part_of(f) if read is not None else None
        part = str(named["name"]) if named else Path(_named(f)).stem
        # Its material from the assembly, cases or none: its filament is the material's.
        material = str(named.get("material") or "") or None if named else None
        done.append(_queue(ledger, folder, approved, f, part, "flagged",
                           read if cases else None, material, sides, list(cases or []),
                           limits, by, again_done=True))
    return done


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


def queued(ledger: DesignLedger) -> bool:
    """Whether any order is queued."""
    ensure(ledger)
    return bool(ledger.rows("SELECT 1 FROM study_orders WHERE status='queued' LIMIT 1"))


def orphaned(ledger: DesignLedger) -> list[int]:
    """Each order still running when a worker takes the root's lock, failed: holding the lock,
    no other worker is alive, so the one that was running it died with it unended."""
    ensure(ledger)
    found = [int(r["id"]) for r in
             ledger.rows("SELECT id FROM study_orders WHERE status='running'")]
    for order in found:
        ledger.change("UPDATE study_orders SET status='failed', said=?, ended_at=? WHERE id=?",
                      f"its worker ended before it did: `settings_study.py retry {order} --root "
                      "ROOT --by WHO` queues it again", _now(), order)
    return found


def finish(ledger: DesignLedger, order: Order, status: str, said: str,
           out: Path | None = None) -> None:
    ledger.change("UPDATE study_orders SET status=?, said=?, out=?, ended_at=? WHERE id=?",
                  status, said[:2000], str(out) if out else None, _now(), order.id)


def worker_running(root: Path) -> bool:
    """Whether a worker holds `root`'s lock now."""
    path = root / LOCK
    if not path.exists():
        return False
    with path.open("a") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(held, fcntl.LOCK_UN)
    return False


def checkout(env: dict[str, str]) -> Path:
    """The checkout the worker runs from: `STUDY_CHECKOUT`, else this checkout's main one."""
    if env.get(CHECKOUT_KEY):
        return Path(env[CHECKOUT_KEY]).expanduser().resolve()
    here = SCRIPT.parents[1]
    done = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute",
                           "--git-common-dir"], capture_output=True, text=True, check=False,
                          timeout=30)
    common = done.stdout.strip()
    return Path(common).parent if done.returncode == 0 and common else here


#: How long a worker just started is watched for an end at once, as an old checkout's is, in s.
WATCH = 2.0


def format_of(home: Path) -> int | None:
    """The orders' format a checkout's code reads (`ORDERS_FORMAT`), or None where it reads
    none: one from before these orders."""
    path = home / "src" / "print_shop" / "study" / "orders.py"
    if not path.is_file():
        return None
    found = re.search(r"^ORDERS_FORMAT = (\d+)$", path.read_text(encoding="utf-8"), re.MULTILINE)
    return int(found.group(1)) if found else None


def start_worker(root: Path, env: dict[str, str] | None = None,
                 spawn: Callable[..., Any] = subprocess.Popen, watch: float = WATCH) -> str:
    """A worker for `root`'s queued studies, started detached where none runs: so a study runs
    by itself after the approval that queued it, and the approval doesn't wait for it. It runs
    until nothing is queued, writing to `root/studies.log`, from a stated checkout (`checkout`)
    with that checkout's own Python, never a worktree, which a builder may remove mid-study, nor
    one whose code reads older orders than these (`ORDERS_FORMAT`). It's watched for `watch`
    seconds, and said to have started only if it hasn't ended in them but as a worker does.
    Two started at once leave one: the other finds the lock held and ends (`work`, exit 3)."""
    if worker_running(root):
        return f"a worker already runs {root.name}'s studies: they're queued for it"
    home = checkout(env or {})
    script = home / "scripts" / "settings_study.py"
    python = home / ".venv" / "bin" / "python"
    if (home / ".git").is_file():
        return (f"no worker started: {home} is a worktree, which may be removed: set "
                f"{CHECKOUT_KEY} to a checkout, or run `scripts/settings_study.py work --root "
                f"{root}` from one")
    if not script.is_file() or not python.is_file():
        return (f"no worker started: {home} has no {script.name} or no .venv: set "
                f"{CHECKOUT_KEY}, or run `scripts/settings_study.py work --root {root}`")
    found = format_of(home)
    if found is None or found < ORDERS_FORMAT:
        return (f"no worker started: the checkout at {home} is older than these orders (it reads "
                f"{'none' if found is None else f'format {found}'}, these are {ORDERS_FORMAT}); "
                f"pull it, or set {CHECKOUT_KEY}, then `scripts/settings_study.py work --root "
                f"{root}`")
    log = (root / LOG).open("a")
    try:
        child = spawn([str(python), str(script), "work", "--root", str(root)], stdout=log,
                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      start_new_session=True, cwd=str(home))
    finally:
        log.close()
    poll = getattr(child, "poll", None)
    ends = time.monotonic() + watch
    while callable(poll) and time.monotonic() < ends:
        code = poll()
        if code is not None:
            if code == 0:
                return f"a worker started from {home}, and ran them already; see {root / LOG}"
            if code == 3:
                return f"a worker already runs {root.name}'s studies: they're queued for it"
            return (f"no worker started: the one started from {home} ended at once, exit "
                    f"{code}: see {root / LOG}")
        time.sleep(0.1)
    return f"a worker started for them from {home}, one at a time; it writes to {root / LOG}"


def listing(ledger: DesignLedger) -> str:
    found = orders(ledger)
    if not found:
        return "No settings study is queued.\n"
    lines = []
    for o in found:
        where = f", in {o.out}" if o.out else ""
        again = f", attempt {o.attempt}" if o.attempt > 1 else ""
        retry_said = (f" (`settings_study.py retry {o.id} --root ROOT --by WHO` queues it "
                      "again)" if o.status == "failed" and "retry" not in o.said else "")
        lines.append(f"order {o.id}: {o.run} {o.part} ({o.kind}{again}), {o.status}{where}"
                     + (f": {o.said}" if o.said else "") + retry_said)
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
    return by.get((material or "").lower()) or by.get("*")


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
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=MESH_TIMEOUT,
                              check=False)
    except subprocess.TimeoutExpired as exc:
        # Its container goes on without its client: stopped, by its name.
        subprocess.run(["docker", "kill", name], capture_output=True, check=False, timeout=60)
        raise RuntimeError(f"meshing {step.name} took past {MESH_TIMEOUT} s") from exc
    answer_path = work / MESH_ANSWER
    if not answer_path.is_file():
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
        raise RuntimeError(f"the bench's image didn't mesh {step.name}: {' / '.join(tail)}")
    row = json.loads(answer_path.read_text(encoding="utf-8"))["files"][step.name]
    if "unread" in row:
        raise RuntimeError(f"{step.name} couldn't be meshed: {row['unread']}")
    return work / str(row["stl"])


def prepare(order: Order, folder: Path, env: dict[str, str]) -> list[str]:
    """The words `scripts/settings_study.py` runs a queued study with, from what was copied as it
    was queued (`RUN/studies/N-PART/inputs/`), each file held to its hash: the approved file
    meshed in its own axes; the assembly as approved; the run's load needs as its cases, or those
    the Sponsor named, or none; the side it lies on, turned about its up axis by fit-bed's own
    turn; and the Sponsor's limits. The nozzle and filament are found first, so a study with
    none costs no meshing.

    Raises:
        ValueError: If no nozzle or filament is given, or what was copied has changed.
        RuntimeError: If it couldn't be meshed.
    """
    limits = order.limits
    nozzle = limits.nozzle or (float(env[NOZZLE_KEY]) if env.get(NOZZLE_KEY) else None)
    filament = limits.filament or _filament(env, order.material)
    if nozzle is None or filament is None:
        kind = f"its {order.material}" if order.material else "a part of no material"
        raise ValueError(f"no nozzle or filament for {order.part}: give them in its limits, or "
                         f"{NOZZLE_KEY} and {FILAMENTS_KEY} in the Mac's settings, for {kind} "
                         "(`*=NAME` for a part of none)")
    if order.inputs is None:
        raise ValueError(f"order {order.id} has no inputs copied as it was queued")
    inputs = order.inputs
    for name, sha in (order.manifest or {}).items():
        path = inputs / name
        if not path.is_file() or content_hash(Path(name).name, path.read_bytes()) != sha:
            raise ValueError(f"{path} has changed since it was queued")
    out = inputs.parent
    words: list[str] = []
    if order.assembly is not None:
        words += ["--assembly", str(inputs / order.assembly)]
    stl = mesh(inputs / order.file.name, inputs / "mesh")
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
