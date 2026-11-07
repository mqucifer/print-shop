"""A score for each round: how far the part is from a scenario's right answer, and how far the
round moved it (mqucifer/print-shop#67).

mrCAD (arXiv 2504.20294) scores every round as improvement toward a right answer, and found models
made the part worse on refinement rounds. An eval here takes one to four hours, so whether a note,
a send-back or a lesson made the next round better has to be read from the record, round by round.

**The right answer is a file of ranges,** the scenario's, kept apart from its request: the request
is what the model reads, and this is what the operator scores against. `start --right FILE` keeps
it as `right.json` in the run's folder, outside `work/`, so the model never sees it. Each range
names a bench command, the files its command line must name, a number in its answer, and the
right range of that number (`read_right`). eval-plug-1's opening of 12.6-13.6 by 7.6-8.6 is 0.3 to
0.8 mm all round its plug's body, which `clearance` reads as `pairs.0.gap_mesh_mm`.

**Each range is read from the bench's answers in the ledger,** never from what the model said, and
never by running anything: at a round's end, a need's reading there (`readings`), then the
harness's own checks at `done` (`standing`), then the model's last bench call in the round
(`bench_calls`). Before it, the round before's end; and before the first round, the last bench
call before it began. Each value says where it came from. A range nothing read is `unread`, and
counts in no sum.

**Distance** is each range's distance outside it, over its `scale` (its width, unless given), so
ranges in mm and in % add up. A round's `distance` is over every range read at its end. Its
`improvement` is (before - after) / before over the ranges read at both ends, as mrCAD has it: 1
is right now, 0 no change, below 0 worse. With nothing wrong before, there's none, and `said`
says why.

**A round's kind** is `first`, `again` (the model's next try after a failed `done`, with nothing
from a person between) or `refinement` (after a person sent something: a note, a send-back, a
pick or an answer), from the phase of each round's last call (`calls.phase`).
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from print_shop.rounds.needs import number_at

#: The scenario's ranges, as a run keeps them: beside its records, outside `work/`.
RIGHT_FILE = "right.json"
FIRST, AGAIN, REFINEMENT = "first", "again", "refinement"
RANGE_KEYS = {"id", "what", "command", "names", "number", "at_least", "at_most", "scale"}
FILE_KEYS = {"scenario", "ranges", "about"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

Query = Callable[..., Sequence[Any]]


class NotRight(ValueError):
    """A right-answer file that isn't in its form, said plainly."""


@dataclass(frozen=True)
class Range:
    id: str
    what: str
    command: str
    number: str
    at_least: float | None
    at_most: float | None
    scale: float
    names: tuple[str, ...] = ()

    def distance(self, value: float) -> float:
        """How far outside the range, over its scale: 0 inside it."""
        below = self.at_least - value if self.at_least is not None else 0.0
        above = value - self.at_most if self.at_most is not None else 0.0
        return round(max(below, above, 0.0) / self.scale, 6)

    def reads(self, argv: Sequence[str]) -> bool:
        """Whether a bench command line is this range's command, naming each of its files."""
        if not argv or argv[0] != self.command:
            return False
        named = {Path(word).name for word in argv[1:]}
        return all(Path(name).name in named for name in self.names)


def _bound(entry: dict[str, Any], key: str, where: str) -> float | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise NotRight(f"{where}: `{key}` is a number, not {value!r}")
    return float(value)


def read_right(data: Any) -> tuple[str, list[Range]]:
    """A scenario's right answer: `{"scenario": ..., "ranges": [...]}`, each range
    `{"id", "what", "command", "names", "number", "at_least", "at_most", "scale"}`. `names` and
    `scale` may be left out, and one of `at_least` and `at_most`. A range of no width, or with one
    end, gives its `scale`, as there's no width to measure by."""
    if not isinstance(data, dict):
        raise NotRight("a right answer is a JSON object with `scenario` and `ranges`")
    if extra := sorted(set(data) - FILE_KEYS):
        raise NotRight(f"a right answer has only {sorted(FILE_KEYS)}: not {extra}")
    scenario = data.get("scenario")
    entries = data.get("ranges")
    if not isinstance(scenario, str) or not scenario.strip():
        raise NotRight("a right answer names its `scenario`")
    if not isinstance(entries, list) or not entries:
        raise NotRight("a right answer has at least one range in `ranges`")
    found: list[Range] = []
    for i, entry in enumerate(entries, 1):
        where = f"range {i}"
        if not isinstance(entry, dict):
            raise NotRight(f"{where} is an object")
        if extra := sorted(set(entry) - RANGE_KEYS):
            raise NotRight(f"{where} has only {sorted(RANGE_KEYS)}: not {extra}")
        ident = entry.get("id")
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise NotRight(f"{where}'s `id` is letters, digits, `.`, `_` and `-`")
        where = f"range `{ident}`"
        for key in ("what", "command", "number"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise NotRight(f"{where} needs `{key}`, in words")
        if " " in entry["command"].strip():
            raise NotRight(f"{where}'s `command` is one bench command, such as `clearance`")
        names = entry.get("names", [])
        if not isinstance(names, list) or not all(isinstance(n, str) and n for n in names):
            raise NotRight(f"{where}'s `names` is a list of file names its command line names")
        low, high = _bound(entry, "at_least", where), _bound(entry, "at_most", where)
        if low is None and high is None:
            raise NotRight(f"{where} needs `at_least`, `at_most` or both")
        if low is not None and high is not None and low > high:
            raise NotRight(f"{where}'s `at_least` {low} is over its `at_most` {high}")
        scale = _bound(entry, "scale", where)
        if scale is None:
            if low is None or high is None or high == low:
                raise NotRight(
                    f"{where} has no width to measure distance by: give `scale`, in its units"
                )
            scale = high - low
        if scale <= 0:
            raise NotRight(f"{where}'s `scale` is over 0")
        found.append(
            Range(ident, entry["what"].strip(), entry["command"].strip(), entry["number"].strip(),
                  low, high, scale, tuple(names))
        )
    ids = [r.id for r in found]
    if len(set(ids)) != len(ids):
        twice = sorted({i for i in ids if ids.count(i) > 1})
        raise NotRight(f"two ranges have the same id: {twice}")
    return scenario.strip(), found


def load_right(path: Path) -> tuple[str, list[Range], str]:
    """A right-answer file, read and checked, and the hash of what it holds."""
    data = path.read_bytes()
    try:
        parsed = json.loads(data)
    except ValueError as exc:
        raise NotRight(f"{path} isn't JSON: {exc}") from exc
    scenario, ranges = read_right(parsed)
    return scenario, ranges, hashlib.sha256(data).hexdigest()


def keep_right(folder: Path, source: Path, ledger: Any) -> tuple[str, list[Range], str]:
    """A run's right answer, kept in its folder (outside `work/`, where the model works) and in
    the ledger's `right_ranges`. It's checked before anything is kept."""
    scenario, ranges, sha = load_right(source)
    (folder / RIGHT_FILE).write_bytes(source.read_bytes())
    for r in ranges:
        ledger.add(
            "right_ranges",
            run=folder.name,
            right_sha=sha,
            scenario=scenario,
            id=r.id,
            what=r.what,
            command=r.command,
            names=json.dumps(list(r.names)),
            number=r.number,
            at_least=r.at_least,
            at_most=r.at_most,
            scale=r.scale,
        )
    return scenario, ranges, sha


# Reading the ledger


def _argv(command: Any) -> list[str]:
    """A command line as the ledger keeps it: a JSON list (`bench_calls`), or text."""
    if isinstance(command, list):
        return [str(w) for w in command]
    text = str(command or "")
    if text.startswith("["):
        try:
            return [str(w) for w in json.loads(text)]
        except ValueError:
            pass
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


#: A part of a number's path: a key or an index, or a list's item picked by one of its keys, as
#: `panes[file=pane_small.step]`, since a list's order follows the command line the model wrote.
PART = re.compile(r"([^.\[\]]+)(?:\[([^=\]]+)=([^\]]*)\])?")


def number_in(numbers: Any, path: str) -> Any:
    """`pairs.0.gap_mesh_mm`, as a need reads it (`needs.number_at`), or with an item picked by
    its key, `panes[file=pane_small.step].open_pct`: a file is matched by its name, wherever it
    is. Raises LookupError where there's none."""
    if "[" not in path:
        return number_at(numbers, path)
    here = numbers
    at = 0
    while at < len(path):
        found = PART.match(path, at)
        if found is None:
            raise LookupError(path)
        key, by, wanted = found.groups()
        here = number_at(here, key)
        if by is not None:
            items = [
                item
                for item in (here if isinstance(here, list) else [])
                if isinstance(item, dict)
                and (str(item.get(by)) == wanted or Path(str(item.get(by))).name == wanted)
            ]
            if len(items) != 1:
                raise LookupError(path)
            here = items[0]
        at = found.end()
        if at < len(path):
            if path[at] != ".":
                raise LookupError(path)
            at += 1
    return here


def _value(numbers: Any, path: str) -> float | None:
    if isinstance(numbers, str):
        try:
            numbers = json.loads(numbers)
        except ValueError:
            return None
    try:
        found = number_in(numbers, path)
    except LookupError:
        return None
    if isinstance(found, bool) or not isinstance(found, int | float):
        return None
    return float(found)


@dataclass
class Read:
    """One range's value, and which answer gave it."""

    value: float
    distance: float
    came_from: str


def _worst(r: Range, rows: list[tuple[str, Any, Any]]) -> Read | None:
    """The worst of the values the rows give for a range: rows from one moment, so where two
    answers read it differently, the score doesn't take the kinder."""
    found = [
        Read(value, r.distance(value), where)
        for where, command, numbers in rows
        if r.reads(_argv(command)) and (value := _value(numbers, r.number)) is not None
    ]
    return max(found, key=lambda one: one.distance) if found else None


def _latest_bench(r: Range, rows: list[Any]) -> Read | None:
    """The model's latest bench call that read a range, from rows newest first."""
    for row in rows:
        if r.reads(_argv(row["argv"])) and (value := _value(row["numbers"], r.number)) is not None:
            where = f"the model's bench call at call {row['at_call']}"
            return Read(value, r.distance(value), where)
    return None


def _round_rows(query: Query, run: str) -> list[Any]:
    return list(
        query(
            "SELECT round, first_call, last_call FROM rounds WHERE run=? AND last_call IS NOT NULL "
            "ORDER BY round",
            run,
        )
    )


def _at_end(query: Query, run: str, rnd: Any, ranges: list[Range]) -> dict[str, Read]:
    """Each range as the bench read it at a round's end."""
    readings = [
        (
            f"need `{row['need']}`'s reading at round {rnd['round']}'s end",
            row["command"],
            row["numbers"],
        )
        for row in query(
            "SELECT rd.need, rd.numbers, (SELECT n.command FROM needs AS n WHERE n.run = rd.run "
            "AND n.need = rd.need AND n.at_call <= ? ORDER BY n.version DESC LIMIT 1) AS command "
            "FROM readings AS rd WHERE rd.run=? AND rd.round=?",
            rnd["last_call"],
            run,
            rnd["round"],
        )
    ]
    standing = [
        (f"the harness's `{row['check_name']}` at round {rnd['round']}'s `done`", row["command"],
         row["numbers"])
        for row in _maybe(
            query,
            "SELECT check_name, command, numbers FROM standing WHERE run=? AND round=? "
            "AND stop='done'",
            run,
            rnd["round"],
        )
    ]
    calls = list(
        _maybe(
            query,
            "SELECT at_call, argv, numbers FROM bench_calls WHERE run=? AND at_call>=? AND "
            "at_call<=? ORDER BY at_call DESC, id DESC",
            run,
            rnd["first_call"],
            rnd["last_call"],
        )
    )
    found: dict[str, Read] = {}
    for r in ranges:
        one = _worst(r, readings) or _worst(r, standing) or _latest_bench(r, calls)
        if one is not None:
            found[r.id] = one
    return found


def _before_first(query: Query, run: str, first_call: int, ranges: list[Range]) -> dict[str, Read]:
    """Each range as the last bench call before the first round read it: the part as it stood,
    or the given files."""
    calls = list(
        _maybe(
            query,
            "SELECT at_call, argv, numbers FROM bench_calls WHERE run=? AND at_call<? "
            "ORDER BY at_call DESC, id DESC",
            run,
            first_call,
        )
    )
    found: dict[str, Read] = {}
    for r in ranges:
        one = _latest_bench(r, calls)
        if one is not None:
            found[r.id] = one
    return found


def _maybe(query: Query, sql: str, *args: Any) -> Sequence[Any]:
    """A table an older ledger may not have is read as empty."""
    try:
        return query(sql, *args)
    except sqlite3.OperationalError:
        return []


def kinds(query: Query, run: str) -> dict[int, tuple[str | None, int | None]]:
    """Each ended round's kind and the phase of its last call."""
    found: dict[int, tuple[str | None, int | None]] = {}
    before: int | None = None
    for i, rnd in enumerate(_round_rows(query, run)):
        rows = list(query("SELECT phase FROM calls WHERE run=? AND n=?", run, rnd["last_call"]))
        phase = int(rows[0]["phase"]) if rows else None
        if i == 0:
            kind: str | None = FIRST
        elif phase is None or before is None:
            kind = None
        else:
            kind = REFINEMENT if phase > before else AGAIN
        found[int(rnd["round"])] = (kind, phase)
        before = phase
    return found


@dataclass
class RoundScore:
    run: str
    round: int
    kind: str | None
    phase: int | None
    #: Over every range read at the round's end, and how many that was of how many.
    distance: float | None
    read: int
    of: int
    #: Over the ranges read at both ends.
    compared: int
    distance_before: float | None
    distance_after: float | None
    improvement: float | None
    said: str
    ranges: list[dict[str, Any]]

    def plain(self) -> dict[str, Any]:
        return asdict(self)


def _said(before: float, after: float, compared: int) -> tuple[float | None, str]:
    if compared == 0:
        return None, "no range was read both before and after it"
    if before == 0:
        if after == 0:
            return None, "right before and after"
        return None, "right before it, and outside a range after"
    gain = round((before - after) / before, 4)
    if gain > 0:
        return gain, "nearer the right answer" if after else "right after it"
    return gain, "no nearer" if gain == 0 else "further from the right answer"


def score(query: Query, run: str, ranges: list[Range]) -> list[RoundScore]:
    """Every ended round's score against the ranges, from the ledger alone."""
    rounds = _round_rows(query, run)
    kinds_of = kinds(query, run)
    found: list[RoundScore] = []
    previous: dict[str, Read] | None = None
    for rnd in rounds:
        n = int(rnd["round"])
        after = _at_end(query, run, rnd, ranges)
        if previous is None:
            before = _before_first(query, run, int(rnd["first_call"]), ranges)
        else:
            before = previous
        both = [r.id for r in ranges if r.id in before and r.id in after]
        sum_before = round(sum(before[i].distance for i in both), 6)
        sum_after = round(sum(after[i].distance for i in both), 6)
        gain, said = _said(sum_before, sum_after, len(both))
        rows = []
        for r in ranges:
            row: dict[str, Any] = {
                "id": r.id,
                "what": r.what,
                "at_least": r.at_least,
                "at_most": r.at_most,
            }
            for side, held in (("before", before), ("after", after)):
                one = held.get(r.id)
                row[side] = (
                    {"value": one.value, "distance": one.distance, "from": one.came_from}
                    if one is not None
                    else None
                )
            rows.append(row)
        kind, phase = kinds_of.get(n, (None, None))
        found.append(
            RoundScore(
                run=run,
                round=n,
                kind=kind,
                phase=phase,
                distance=round(sum(one.distance for one in after.values()), 6) if after else None,
                read=len(after),
                of=len(ranges),
                compared=len(both),
                distance_before=sum_before if both else None,
                distance_after=sum_after if both else None,
                improvement=gain,
                said=said,
                ranges=rows,
            )
        )
        previous = after
    return found


def phases(scores: list[RoundScore]) -> list[dict[str, Any]]:
    """First rounds apart from refinement rounds: each phase a round ended in, its kind (the
    kind of its first round), and what that round did, as the phases view has it."""
    by_phase: dict[Any, list[RoundScore]] = {}
    for one in scores:
        by_phase.setdefault(one.phase, []).append(one)
    return [
        {
            "phase": phase,
            "kind": held[0].kind,
            "rounds": [one.round for one in held],
            "improvement": held[0].improvement,
            "distance_after": held[-1].distance,
        }
        for phase, held in by_phase.items()
    ]


def keep(ledger: Any, scores: list[RoundScore], right_sha: str) -> None:
    """Each round's score into `round_scores`, under the right answer's hash: a score against a
    corrected file is a row of its own, and none is changed."""
    for one in scores:
        ledger.add(
            "round_scores",
            run=one.run,
            round=one.round,
            right_sha=right_sha,
            kind=one.kind,
            phase=one.phase,
            distance=one.distance,
            ranges_read=one.read,
            ranges_of=one.of,
            compared=one.compared,
            distance_before=one.distance_before,
            distance_after=one.distance_after,
            improvement=one.improvement,
            said=one.said,
            ranges=json.dumps(one.ranges, sort_keys=True),
        )


def after_round(ledger: Any, folder: Path) -> list[RoundScore]:
    """At a round's end, the run's scores, if it was started with a right answer: none if not."""
    kept = folder / RIGHT_FILE
    if not kept.is_file():
        return []
    _, ranges, sha = load_right(kept)
    found = score(ledger.rows, folder.name, ranges)
    keep(ledger, found, sha)
    return found


def read_only(path: Path) -> Query:
    """A ledger read without a write, as a run's records kept read-only must be: no table or
    column is added, as opening it as a `DesignLedger` would."""
    if not path.is_file():
        raise FileNotFoundError(f"there's no ledger {path}")

    def query(sql: str, *args: Any) -> list[sqlite3.Row]:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            return list(db.execute(sql, args))
        finally:
            db.close()

    return query
