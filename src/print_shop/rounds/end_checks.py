"""The checks a run gets when it says it's done: each held part's way on, and what bends on the
way (mqucifer/print-shop#150).

mqucifer/print-shop#142 gave the bench `fit-on` and `bend`, and nothing made a run use them. A
design run's lid was approved that could never be fitted, because nothing read its way on. The
Sponsor, 2026-11-15: "we can force the run of these after things are delivered, add that to the
learn ledger and send it back with the learnings. That's what I expected."

So when every need has passed and every standing check too, before the stop for approval is
asked, the harness runs these on what the clean run left. It calls the bench's commands and acts
on what they say: nothing here measures.

**The way on, for every held part.** `holds.py` names each held part. Its slide, `clearance ...
--slide <way out>`, names the file that moves, the files it's slid against and its way out: the
slide a settled need runs, or else the latest one the model ran, or else, for a bought part whose
fact gives its way in, that part against the model's own files. `fit-on` reads that file onto
those, `--from clear`.

**The bend, for every place `fit-on` reads `BLOCKED`.** What's in the way has to move. The arm is
looked for on each printed part of the two, by `bench/arm.py` in the second image, from the
place and that part's shape: its box, its root plane and the way it's pushed. `bend` then reads
it, pushed the place's depth, its layers stacked along the axis `on_bed` gives the file.

**What each place comes to:**
- `TAKES`: an arm was found, and `bend` reads `OK`. The place passes.
- `OVER`: a printed arm was found, and `bend` reads `FAIL`. It fails, whether what pushes
  past it is printed or given: a rigid bought part forced past a printed clip that's over its
  limit is the run's design to fix. A design run's fan couldn't be fitted in its slide-in
  pocket for exactly this.
- `STANDS`: between two printed parts, no arm was found, or `bend` had no answer for it. The
  place stands as `BLOCKED`, and fails, saying why no arm was read.
- `SEATED`: the parts overlap where the part sits, which no bend clears. Between two printed
  parts it fails.
- `PERSON`: one of the two is a given, bought part, and no printed arm was read there: none
  was found, or `bend` had no answer for it. Then what has to give way is the given part itself,
  as a flexible strip pushed past a catch does. A given part isn't PETG, so it isn't bent as
  PETG: the reading says how far it has to give, and a person judges it. It doesn't fail.

**A way that couldn't be read** (`fit-on` with no answer, no way out named, a file that isn't a
STEP) is "can't tell", neither a pass nor a fail, as `thin` is, and is shown at the stop.

**When any fails,** `run.py` writes the lesson, sends the run back with `note`, and starts the
conversation again from the record. A person who is asked about a failing pair of files at an
`ask`, and picks anything but `none`, has accepted it: it's shown at the stop as theirs.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from print_shop.bench import arm as arm_module
from print_shop.bench import placed as placed_module
from print_shop.rounds import holds, judge
from print_shop.rounds.box import Box
from print_shop.rounds.needs import UNSURE, Need

#: The standing checks' names, in the ledger's `standing`.
WAY_ON = "way-on"
BEND = "bend"
HEADING = "Each held part's way on, and what bends on the way, by `fit-on` and `bend`"
#: What a place comes to.
TAKES, OVER, STANDS, SEATED, PERSON = "takes", "over", "stands", "seated", "person"
FAILS = (OVER, STANDS, SEATED)
#: The places of one way that are bent, deepest first: each is a solve. The rest stand.
MOST = 12
#: A direction is along an axis within this.
ALONG = 1e-3
AXES = ("x", "y", "z")
STEP = (".step", ".stp")
WHY_CHARS = 600
#: Who the send-back and the lesson are by: never taken for the Sponsor's.
BY = "harness"


@dataclass(frozen=True)
class Way:
    """One held part's way on: the file that moves, what it goes onto, its way out, and where
    those came from."""

    part: str
    moving: str
    onto: tuple[str, ...]
    along: str
    source: str

    @property
    def command(self) -> str:
        onto = " ".join(shlex.quote(f) for f in self.onto)
        return f"fit-on {shlex.quote(self.moving)} --onto {onto} --along {self.along} --from clear"


@dataclass
class Place:
    """One place `fit-on` read, and what it came to."""

    n: int
    file: str
    box: list[list[float]]
    depth_mm: float
    across: str
    out_mm: float
    seated: bool
    status: str = STANDS
    #: The given file of the two, if one is.
    given: str = ""
    #: The file the arm is on, and the arm as `bench/arm.py` found it.
    arm_file: str = ""
    arm: dict[str, Any] = field(default_factory=dict)
    #: Why no arm was read, for each file it was looked for on.
    no_arm: list[str] = field(default_factory=list)
    command: str = ""
    verdict: str = ""
    why: str = ""
    numbers: dict[str, Any] = field(default_factory=dict)
    #: The copy of `file` it's on, as `fit-on` read it: `FILE as PART` where an assembly names
    #: the file in several parts (mqucifer/print-shop#343), else the file.
    read_as: str = ""

    @property
    def where(self) -> str:
        low, high = self.box
        return " to ".join(
            "(" + ", ".join(f"{c:.4g}" for c in corner) + ")" for corner in (low, high)
        )


@dataclass
class Checked:
    """One way, as read."""

    way: Way
    verdict: str
    why: str
    unsure: bool
    places: list[Place] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)

    def failing(self, accepted: Sequence[str] = ()) -> list[Place]:
        """The places that fail, less those a person accepted for their pair of files."""
        return [
            p for p in self.places if p.status in FAILS and key(self.way, p) not in set(accepted)
        ]

    def plain(self) -> dict[str, Any]:
        return {
            "part": self.way.part,
            "command": self.way.command,
            "source": self.way.source,
            "verdict": self.verdict,
            "why": self.why,
            "unsure": self.unsure,
            "places": [
                {k: v for k, v in place.__dict__.items() if k != "numbers"}
                | {"would_pass": place.numbers.get("would_pass")}
                for place in self.places
            ],
        }


def key(way: Way, place: Place) -> str:
    """The pair of files a failing place is between: what a person accepts at an ask."""
    return f"{way.moving} onto {place.file}"


def _step(name: str) -> bool:
    return name.lower().endswith(STEP)


def ways(
    held: list[holds.Held],
    wanted: list[Need],
    bench_calls: list[dict[str, Any]],
    present: Sequence[str],
    part: Sequence[str],
) -> tuple[list[Way], list[str]]:
    """Each held part's way on, and each held part with none read, with why. `wanted` are the
    needs as `done` ran them, `bench_calls` the ledger's rows oldest first, `present` what the
    clean run left, and `part` the files to be printed."""
    found: list[Way] = []
    unread: list[str] = []
    here = set(present)
    for one in held:
        slid: tuple[list[str], str, str] | None = None
        for need in wanted:
            if need.by != "bench" or not need.command:
                continue
            read = holds.slide_of(one, need.argv())
            if read is not None:
                slid = (*read, f"the need `{need.id}`'s slide")
                break
        if slid is None:
            for row in reversed(bench_calls):
                try:
                    argv = json.loads(row["argv"]) if isinstance(row["argv"], str) else row["argv"]
                except ValueError:
                    continue
                read = holds.slide_of(one, [str(w) for w in argv])
                if read is not None and all(f in here for f in read[0]):
                    slid = (*read, f"call {row['at_call']}'s slide")
                    break
        if slid is None and not one.printed and one.way_out:
            mine = [f for f in part if _step(f) and not f.startswith(holds.REFERENCES)]
            if mine:
                slid = ([*mine, one.part], one.way_out, "its fact's way in")
        named = f"`{one.part}`" if not one.printed else one.part
        if slid is None:
            unread.append(
                f"- {named}: its way on wasn't read. No slide of it, `clearance ... --slide "
                "<its way out>`, is a need's check or was run, and no fact gives its way in, so "
                "nothing names the file that moves or its way out"
            )
            continue
        files, along, source = slid
        missing = [f for f in files if f not in here]
        not_step = [f for f in files if not _step(f)]
        if missing or not_step or len(files) < 2 or not along:
            why = (
                f"a clean run didn't leave {', '.join(f'`{f}`' for f in missing)}"
                if missing
                else f"`fit-on` reads STEP files, and {', '.join(f'`{f}`' for f in not_step)} "
                "isn't one"
                if not_step
                else "its slide names no way out, or one file alone"
            )
            unread.append(f"- {named}: its way on wasn't read, from {source}: {why}")
            continue
        way = Way(one.part, files[-1], tuple(files[:-1]), along, source)
        if not any((w.moving, w.onto, w.along) == (way.moving, way.onto, way.along) for w in found):
            found.append(way)
    return found, unread


def _axis(vector: Any) -> str:
    """The axis a direction is along, or nothing where it's along none."""
    try:
        values = [abs(float(c)) for c in vector]
    except (TypeError, ValueError):
        return ""
    for axis, value in zip(AXES, values, strict=False):
        if abs(value - 1) < ALONG:
            return axis
    return ""


def _corners(box: list[list[float]]) -> str:
    return ",".join(f"{c:.6g}" for corner in box for c in corner)


def _find_arm(box: Box, file: str, place: list[list[float]], across: str, where: Path) -> Any:
    """`bench/arm.py`'s answer for a place on a file, or why it gave none."""
    ran = box.helper("arm", [file, "--place", _corners(place), "--across", across], where=where)
    for line in reversed(ran.output.splitlines()):
        if line.startswith(arm_module.MARK):
            try:
                found: dict[str, Any] = json.loads(line[len(arm_module.MARK) :])
            except ValueError:
                break
            return found
    return {"found": False, "why": f"the arm couldn't be looked for: {ran.output.strip()[-300:]}"}


def bend_command(file: str, arm: dict[str, Any], push: float, layers: str) -> str:
    """The `bend` that reads an arm as `bench/arm.py` found it, as anyone could run it."""
    low, high = arm["arm"]
    box = ",".join(f"{c:g}" for c in [*low, *high])
    axis, at = arm["root"]
    return (
        f"bend {shlex.quote(file)} --arm {box} --root {axis}={at:g} --push {push:.4g} "
        f"--toward {arm['toward']} --layers {layers}"
    )


def _read(box: Box, name: str, command: str, verdicts: list[str], where: Path, kept: Path) -> Any:
    need = Need(name, "the harness's check at the end", "derived", "bench", command=command)
    need.verdicts = verdicts
    return judge.read_need(box, need, where, kept)


def own_frame(
    corners: list[list[float]], across: str, placed: dict[str, Any]
) -> tuple[list[list[float]], str]:
    """A place's box and the axis it's pushed across, from where `fit-on` placed a file back
    into its own file's frame, where `arm` and `bend` read it (mqucifer/print-shop#201): the
    box round the box's corners turned back, and the axis turned back, or nothing where that's
    along no axis of the file's."""
    one = placed_module.Placed(
        "",
        str(placed.get("part", "")),
        tuple((str(a), float(d)) for a, d in placed.get("turn") or []),
        tuple(float(c) for c in placed.get("at") or (0, 0, 0)),  # type: ignore[arg-type]
        str(placed.get("assembly", "")),
    )
    back = np.linalg.inv(one.matrix())
    low, high = np.asarray(corners[0], dtype=float), np.asarray(corners[1], dtype=float)
    every = np.array([[(low, high)[k >> i & 1][i] for i in range(3)] for k in range(8)])
    moved = every @ back[:3, :3].T + back[:3, 3]
    box = [[float(v) for v in moved.min(axis=0)], [float(v) for v in moved.max(axis=0)]]
    if across not in AXES:
        return box, ""
    unit = back[:3, :3] @ np.eye(3)[AXES.index(across)]
    return box, _axis(unit)


def _place(
    box: Box,
    way: Way,
    place: Place,
    on_bed: dict[str, str],
    where: Path,
    kept: Path,
    name: str,
    placed: dict[str, Any] | None = None,
    moving_as: str = "",
) -> None:
    """What one place comes to: the arm looked for on each printed file of the two, and bent.
    `placed` is each file `fit-on` read placed, as its record says: the place is turned back
    into that file's own frame, where `arm` and `bend` read the file."""
    given = [f for f in (place.file, way.moving) if f.startswith(holds.REFERENCES)]
    place.given = given[0] if given else ""
    if place.seated:
        place.status = PERSON if place.given else SEATED
        return
    fallback = PERSON if place.given else STANDS
    place.status = fallback
    if not place.across:
        place.no_arm.append(
            "its depth isn't across x, y or z, and `bend` takes a push along one of them"
        )
        return
    for k, file in enumerate((place.file, way.moving)):
        if file.startswith(holds.REFERENCES):
            continue
        corners = [list(place.box[0]), list(place.box[1])]
        if k == 1:
            # The place is where the part was `out_mm` out: in the part's own file it's back
            # along its way out by that much.
            axis = AXES.index(way.along[1])
            back = (1 if way.along[0] == "+" else -1) * place.out_mm
            for corner in corners:
                corner[axis] -= back
        across = place.across
        # Each placement is by the name it's read by (mqucifer/print-shop#343): the moving
        # part's is `fit-on`'s `moving`.
        copy = (moving_as or file) if k == 1 else (place.read_as or file)
        if copy in (placed or {}):
            corners, across = own_frame(corners, across, (placed or {})[copy])
            if not across:
                place.no_arm.append(
                    f"on `{file}`: its push isn't along an axis of the file as it's built, "
                    "and `bend` takes a push along one of them"
                )
                continue
        found = _find_arm(box, file, corners, across, where)
        if not found.get("found"):
            place.no_arm.append(f"on `{file}`: {found.get('why') or 'no arm was found'}")
            continue
        layers = (on_bed.get(file) or "-z")[1]
        place.arm_file, place.arm = file, found
        place.command = bend_command(file, found, place.depth_mm, layers)
        reading = _read(box, name, place.command, ["OK"], where, kept)
        record = reading.record or {}
        place.verdict = reading.verdict
        place.why = str(record.get("why") or reading.why)[:WHY_CHARS]
        place.numbers = dict(record.get("numbers") or {})
        if reading.passed:
            place.status = TAKES
        elif reading.verdict == "FAIL":
            # Whatever pushes past it, printed or given: the printed arm is over its limit.
            place.status = OVER
        else:
            place.status = fallback
        return


def read(
    box: Box, found: list[Way], on_bed: dict[str, str], where: Path, kept: Path
) -> list[Checked]:
    """Each way read by `fit-on`, and each place it passes by `bend`, in fresh containers over
    `where`, their answers kept under `kept`."""
    checked: list[Checked] = []
    for n, way in enumerate(found):
        reading = _read(box, f"way-on-{n}", way.command, ["CLEAR"], where, kept)
        record = reading.record or {}
        numbers = dict(record.get("numbers") or {})
        why = str(record.get("why") or reading.why)[:WHY_CHARS]
        unsure = reading.verdict not in ("CLEAR", "BLOCKED") or reading.verdict in UNSURE
        one = Checked(way, reading.verdict, why, unsure, numbers=numbers)
        checked.append(one)
        if reading.verdict != "BLOCKED":
            continue
        for k, read_place in enumerate(numbers.get("places") or []):
            place = Place(
                k,
                str(read_place["file"]),
                [[float(c) for c in corner] for corner in read_place["box"]],
                float(read_place["depth_mm"]),
                _axis(read_place.get("across")),
                float(read_place["out_mm"]),
                bool(read_place.get("seated")),
                read_as=str(read_place.get("read_as") or ""),
            )
            one.places.append(place)
            if k < MOST:
                _place(
                    box,
                    way,
                    place,
                    on_bed,
                    where,
                    kept,
                    f"bend-{n}-{k}",
                    numbers.get("placed"),
                    str(numbers.get("moving") or ""),
                )
            else:
                given = [f for f in (place.file, way.moving) if f.startswith(holds.REFERENCES)]
                place.given = given[0] if given else ""
                place.status = PERSON if place.given else STANDS
                place.no_arm.append(f"only the {MOST} deepest places of a way are bent")
    return checked


def standing(checked: list[Checked], accepted: Sequence[str] = ()) -> list[judge.Standing]:
    """Each reading as a row of the ledger's `standing`: the way on, and each arm bent."""
    rows: list[judge.Standing] = []
    for one in checked:
        failing = one.failing(accepted)
        rows.append(
            judge.Standing(
                WAY_ON,
                one.way.moving,
                one.way.command,
                not one.unsure and not failing,
                one.unsure,
                one.verdict,
                one.why,
                {k: v for k, v in one.numbers.items() if k in ("depth_mm", "from_mm", "readings")}
                | {"places": [{"status": p.status, "file": p.file} for p in one.places]},
            )
        )
        for place in one.places:
            if place.command:
                rows.append(
                    judge.Standing(
                        BEND,
                        place.arm_file,
                        place.command,
                        place.status == TAKES,
                        place.status != TAKES and place.verdict != "FAIL",
                        place.verdict,
                        place.why,
                        place.numbers,
                    )
                )
    return rows


def _bent(place: Place) -> str:
    """What `bend` read of a place's arm, in a sentence: its strain against the limit, the room
    behind it, and what would pass."""
    numbers = place.numbers
    said = f"`{place.command}` reads {place.verdict or 'no verdict'}"
    strain, limit = numbers.get("across_layers_pct"), numbers.get("limit_pct")
    if strain is not None and limit is not None:
        said += f": {strain:.2f}% across its layers against the {limit:g}% limit"
        room, moves = numbers.get("room_behind_mm"), numbers.get("back_moves_mm")
        if room is None:
            said += ", with nothing behind it"
        elif moves is not None:
            said += f", its back moving {moves:.3f} mm with {room:.3f} mm of room behind it"
    elif place.why:
        said += f": {place.why}"
    passing = numbers.get("would_pass")
    if passing and place.verdict == "FAIL":
        said += (
            f". What would pass: at this push, an arm at most {passing['thickness_mm']:.3f} mm "
            f"thick at its root, or at least {passing['length_mm']:.3f} mm from its root to the "
            f"pushed edge; at this arm, a push of at most {passing['push_mm']:.3f} mm"
        )
    return said


def _arm_said(place: Place, lead: bool = False) -> str:
    low, high = place.arm["arm"]
    box = " to ".join("(" + ", ".join(f"{c:g}" for c in corner) + ")" for corner in (low, high))
    axis, at = place.arm["root"]
    return (
        f"{'The' if lead else 'the'} arm is `{place.arm_file}`'s, in the box {box}, from its "
        f"root at {axis}={at:g}, pushed {place.depth_mm:.4g} mm {place.arm['toward']}"
    )


def place_said(way: Way, place: Place, accepted: dict[str, dict[str, Any]] | None = None) -> str:
    """One place, with its commands and readings, as a reply, a note and a stop say it."""
    at = (
        f"place {place.n}, at {place.where}, {place.depth_mm:.3f} mm deep"
        + (f" across {place.across}" if place.across else "")
        + (", where the part sits" if place.seated else f", with it {place.out_mm:g} mm out")
    )
    no_arm = "; ".join(place.no_arm)
    yes = (accepted or {}).get(key(way, place))
    if place.status == TAKES:
        return f"  - {at}: passes. {_arm_said(place, lead=True)}. {_bent(place)}."
    if place.status == PERSON:
        printed = (
            f"What's printed there wasn't read as an arm that bends: {_arm_said(place)}; "
            f"{_bent(place)}, which is no answer to rely on. "
            if place.command
            else f"No printed arm that could move was read there ({no_arm}). "
            if no_arm
            else ""
        )
        gives = (
            f"`{place.given}` overlaps what it sits in by {place.depth_mm:.3f} mm there"
            if place.seated
            else f"`{place.given}` has to give way by {place.depth_mm:.3f} mm to pass"
        )
        return (
            f"  - {at}: for a person to judge. {printed}`{place.given}` is a given part, not "
            f"printed PETG, so it isn't bent as PETG: {gives}, and whether it can is a person's "
            "to judge."
        )
    said = f"  - {at}: "
    if place.status == SEATED:
        said += (
            "DOESN'T PASS. The parts overlap where the part sits, which no bend clears: "
            "`clearance` reads it"
        )
    elif place.status == OVER:
        said += f"DOESN'T PASS. {_arm_said(place, lead=True)}. {_bent(place)}"
    else:
        said += "DOESN'T PASS: it stands as BLOCKED. " + (
            f"{_arm_said(place, lead=True)}, and {_bent(place)}, which is no answer to rely on"
            if place.command
            else f"No arm that could move aside was found ({no_arm})"
        )
    if yes:
        said += f". Accepted at ask {yes['ask']} by {yes['by']}: {yes['reason']}"
    return said + "."


def said(
    checked: list[Checked],
    unread: Sequence[str] = (),
    accepted: dict[str, dict[str, Any]] | None = None,
    only_failing: bool = False,
) -> list[str]:
    """Every way and place read, or with `only_failing`, those that don't pass."""
    lines: list[str] = []
    for one in checked:
        failing = one.failing(list(accepted or {}))
        if only_failing and not failing:
            continue
        way = one.way
        mark = (
            "can't tell"
            if one.unsure
            else "DOESN'T PASS"
            if failing
            else "for a person to judge"
            if any(p.status == PERSON for p in one.places)
            else "accepted by a person"
            if any(p.status in FAILS for p in one.places)
            else "ok"
        )
        named = way.part if way.part == way.moving else f"{way.part} (`{way.moving}`)"
        lines.append(
            f"- {named} onto {', '.join(f'`{f}`' for f in way.onto)}, its way out {way.along} "
            f"from {way.source}, {mark}: `{way.command}` reads {one.verdict or 'no verdict'}: "
            f"{one.why}"
        )
        shown = failing if only_failing else one.places
        lines += [place_said(way, place, accepted) for place in shown]
    if not only_failing:
        lines += list(unread)
    return lines


def note(checked: list[Checked], accepted: Sequence[str], lessons: Sequence[int]) -> str:
    """The harness's send-back: what didn't pass, with each command and reading, and what
    comes next."""
    yes: dict[str, dict[str, Any]] = {k: {} for k in accepted}
    lines = [
        "The harness's checks at the end sent the part back. This note is the harness's own, not "
        "the Sponsor's or anyone's standing in for them: nobody has seen the part yet.",
        "",
        "Every need passed. Then the harness read each held part's way on with `fit-on`, and "
        "each place it passes with `bend`, on the files your script leaves from a clean start. "
        "These don't pass:",
        "",
        *said(checked, accepted=yes, only_failing=True),
        "",
        "Each command above is the bench's own: run it with `bench` to read the whole answer, "
        "and again after a change. `fit-on`'s depth at a place is how far what's there has to "
        "move for the part to pass, and `bend` reads whether the arm takes that. Change the "
        "part so each place passes, as `bend`'s \"what would pass\" says for an arm over its "
        "limit, then send `done` again: the harness reads them again, and the stop for approval "
        "is asked once they pass.",
        "If a place is meant as it is, ask a person: `ask` with `\"end_checks\": true`, showing "
        "the parts, puts these readings beside your question, and a pick other than none lets "
        "that pair of files by.",
    ]
    if lessons:
        numbers = ", ".join(str(n) for n in lessons)
        lines += [
            "",
            f"It's kept as lesson {numbers} in the ledger, a candidate for a reviewer, with these "
            "readings as its evidence.",
        ]
    return "\n".join(lines)


def lesson(run: str, way: Way, place: Place) -> tuple[str, str]:
    """A failing place as a lesson's text and its fix's shape, naming the commands and
    readings a reviewer can run again."""
    what = {
        OVER: f"the arm it has to push aside takes more than its material does: {_bent(place)}",
        STANDS: "nothing there was read as an arm that could move aside: "
        + (_bent(place) if place.command else "; ".join(place.no_arm)),
        SEATED: "the two overlap where the part sits, which no bend clears",
    }[place.status]
    text = (
        f"On `{run}` every need passed, and `{way.moving}` couldn't be fitted onto "
        f"`{place.file}` as designed: `{way.command}` read BLOCKED at {place.where}, "
        f"{place.depth_mm:.3f} mm deep"
        + (f" across {place.across}" if place.across else "")
        + f", and {what}. Before `done`, read each held part's way on with `fit-on` along its "
        "way out, and each arm it passes with `bend` at `fit-on`'s depth, and size the arm by "
        "`bend`'s \"what would pass\": a hold read `HELD` once the part is seated says nothing "
        "of whether it can get there."
    )
    shape = (
        "the harness's end checks read the way on and the bend after every need passed, and "
        "sent the part back"
    )
    return text, shape


def evidence(way: Way, checked: Checked, place: Place) -> list[dict[str, str]]:
    """The commands and readings a lesson's evidence keeps, for the reviewer."""
    found = [{"command": way.command, "verdict": checked.verdict, "why": checked.why}]
    if place.command:
        found.append({"command": place.command, "verdict": place.verdict, "why": place.why})
    return found


def at_stop(
    checked: list[Checked],
    unread: Sequence[str],
    accepted: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """The readings under their heading, for a person at a stop."""
    if not checked and not unread:
        return []
    return [
        f"## {HEADING}",
        "",
        "The harness ran these itself once every need had passed, on the files a clean run "
        "left. `fit-on` reads what a held part passes through on its way on and how deep, and "
        "`bend` the strain that puts on the arm that has to move, against what printed PETG "
        "takes across its layers. Neither reads friction, how a snap feels, or how well it "
        "holds.",
        "",
        *said(checked, unread, accepted),
        "",
    ]
