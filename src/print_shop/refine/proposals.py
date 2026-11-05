"""A change to the part a finding needs: proposed, checked where it can be, and never made.

The Sponsor, 2026-11-05: "Part of that feedback could be part changes that need approval first."
So a proposal says the change and what it costs, as "raise the pocket's ceiling 0.1 mm to the
next layer line; the top lip goes from 1.5 to 1.4 mm thick; the pane gets 0.1 mm of room at the
pause". Where the design's script has that number at its top level, the proposal is checked:
`params --keep` re-runs the script with it in the bench's container, on a copy of what the
approved round's clean run left, then the run's own bench needs are read on what it made, then
`pause`, then Orca slices the frame again. Otherwise it's words with its measured reason.

Either way it waits for the Sponsor's approval (`decide`), recorded as a decision is, and nothing
in the approved folder changes.

The one change proposed today is the pocket's ceiling, for a pane standing above the last layer
before the pocket closes: the commonest fault on the stained-glass runs (`scores.md`, 430fc1a).
"""

from __future__ import annotations

import ast
import json
import math
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from print_shop.refine.approved import File
from print_shop.rounds.judge import clean_copy, read_need
from print_shop.rounds.ledger import now
from print_shop.rounds.paths import content_hash, copy_plain
from print_shop.rounds.run import Refused, _who_decides
from print_shop.rounds.tools import recorded

if TYPE_CHECKING:
    from print_shop.refine.refinement import Pass
    from print_shop.rounds.ledger import DesignLedger

#: How many of the script's numbers are tried for one proposal, each a `params` run of 3.
TRIED = 3
#: Two heights the same, in mm.
SAME = 1e-4


def numbers_in(source: str) -> dict[str, float]:
    """Each name assigned a number once at the script's top level, as `params` reads them."""
    found: dict[str, list[Any]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target: ast.expr = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name):
            try:
                literal = ast.literal_eval(value)
            except ValueError:
                literal = None
            found.setdefault(target.id, []).append(literal)
    return {
        name: float(values[0])
        for name, values in found.items()
        if len(values) == 1
        and isinstance(values[0], int | float)
        and not isinstance(values[0], bool)
    }


def ceiling_change(
    row: dict[str, Any], first: float, height: float, top_z: float
) -> dict[str, Any]:
    """The least rise of the pocket's ceiling onto a layer line at or over the pane's top.

    A ceiling on a layer's top closes at the layer above it, so the pause comes after that layer,
    and the pane sits under it with `room` to spare.
    """
    ceiling = float(row["pocket_ceiling_z"])
    pane_top = float(row["pane_top_z"])
    k = max(1, math.ceil(round((pane_top - first) / height, 6)) + 1)
    new = round(first + height * (k - 1), 4)
    if new < pane_top - SAME:
        new = round(new + height, 4)
    after = int(round((new - first) / height)) + 1
    return {
        "ceiling": ceiling,
        "new_ceiling": new,
        "rise": round(new - ceiling, 4),
        "lip": round(top_z - ceiling, 4),
        "new_lip": round(top_z - new, 4),
        "room": round(new - pane_top, 4),
        "after_layer": after,
        "floor": row.get("pocket_floor_z"),
    }


def _words(change: dict[str, Any], height: float) -> tuple[str, str]:
    said = (
        f"raise the pocket's ceiling {change['rise']:g} mm, from z {change['ceiling']:g} to "
        f"{change['new_ceiling']:g}, a layer line, and leave its floor (z {change['floor']}) "
        "as it is"
    )
    cost = (
        f"the top lip goes from {change['lip']:g} to {change['new_lip']:g} mm thick; the pane gets "
        f"{change['room']:g} mm of room at the pause, after layer {change['after_layer']}"
    )
    if change["new_lip"] < 2 * height - SAME:
        cost += f", and a lip under two layers ({2 * height:g} mm) is thin to print over a pane"
    return said, cost


def propose(p: Pass, ids: list[int]) -> list[dict[str, Any]]:
    """A proposal for each finding that needs the part to change and has a change here."""
    made: list[dict[str, Any]] = []
    for seq, (finding, finding_id) in enumerate(zip(p.found, ids, strict=True), 1):
        if not (finding.kind == "pause" and finding.source == "bench" and finding.needs_change):
            continue
        numbers = finding.numbers
        first, height = float(numbers["first_layer"]), float(numbers["layer"])
        frame = _frame_of(p, finding.file)
        top_z = p.meshed[p._key(frame)]["mesh_box_mm"][1][2]
        for row in numbers["openings"]:
            if (row.get("room_over_pane_mm") or 0) >= 0 or row.get("pocket_ceiling_z") is None:
                continue
            change = ceiling_change(row, first, height, top_z)
            said, cost = _words(change, height)
            folder = p.out / "proposals" / str(len(made) + 1)
            checked = check(p, frame, change, numbers, folder)
            proposal_id = p.ledger.insert(
                "refine_proposals",
                refinement=p.id,
                finding=finding_id,
                change=said,
                cost=cost,
                parameter=checked.get("parameter"),
                was=checked.get("was"),
                becomes=checked.get("becomes"),
                checked=checked["checked"],
                check_said=json.dumps(checked, sort_keys=True, default=str),
                folder=str(folder),
                at=now(),
            )
            made.append(
                {
                    "id": proposal_id,
                    "finding_seq": seq,
                    "change": said,
                    "cost": cost,
                    "checked": checked["checked"],
                    "checked_said": checked["said"],
                }
            )
            break  # one change to the ceiling answers every opening of this reading
    return made


def _frame_of(p: Pass, key: str | None) -> File:
    for group in p.approved.groups:
        for f in group.files:
            if p._key(f) == key:
                return f
    raise LookupError(f"no file {key}")


def check(
    p: Pass, frame: File, change: dict[str, Any], reading: dict[str, Any], folder: Path
) -> dict[str, Any]:
    """`params` with the change, the run's needs on what it made, `pause`, and Orca again."""
    script = p.approved.script
    judged = p.approved.judged
    if not script or not (judged / script).is_file():
        return {
            "checked": "words",
            "said": "Words only: the approved round's script isn't there to re-run.",
        }
    source = (judged / script).read_text(encoding="utf-8")
    try:
        numbers = numbers_in(source)
    except SyntaxError:
        return {"checked": "words", "said": "Words only: the script doesn't parse."}
    depth = change["ceiling"] - float(change["floor"]) if change["floor"] is not None else None
    candidates = []
    for name, value in numbers.items():
        if abs(value - change["ceiling"]) < SAME:
            candidates.append((name, value, round(value + change["rise"], 4), "the ceiling's z"))
        elif depth is not None and abs(value - depth) < SAME:
            candidates.append((name, value, round(value + change["rise"], 4), "the pocket's depth"))
    if not candidates:
        return {
            "checked": "words",
            "said": f"Words only: no number at the top of `{script}` is the ceiling's z "
            f"({change['ceiling']:g}) or the pocket's depth.",
            "numbers": numbers,
        }
    tried = []
    for name, was, becomes, what in candidates[:TRIED]:
        work = folder / f"try-{name}"
        if work.exists():
            shutil.rmtree(work)
        # A clean start, as `done`'s: what the script makes is taken away, so its run makes it.
        clean_copy(judged, work)
        ran = p.box.bench(
            ["--out", "out", "params", script, "--set", f"{name}={becomes:g}", "--keep", "kept"],
            where=work,
        )
        record = recorded(ran.output, work) or {}
        attempt: dict[str, Any] = {
            "parameter": name,
            "was": was,
            "becomes": becomes,
            "as": what,
            "params": {"verdict": record.get("verdict"), "why": record.get("why")},
        }
        tried.append(attempt)
        kept = work / "kept"
        if record.get("verdict") not in ("OK", "PARTIAL") or not (kept / frame.was).is_file():
            continue
        # What the changed run made, over the approved round's: the needs name its files.
        changed = folder / f"files-{name}"
        if changed.exists():
            shutil.rmtree(changed)
        copy_plain(judged, changed)
        for made in kept.rglob("*"):
            if made.is_file():
                target = changed / made.relative_to(kept)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(made, target)
        readings: list[dict[str, Any]] = []
        for need in p.approved.needs:
            if need.by != "bench":
                readings.append(
                    {"need": need.id, "passed": None, "why": "an eye need: not re-read"}
                )
                continue
            got = read_need(p.box, need, changed, folder / f"readings-{name}")
            readings.append({"need": need.id, "passed": got.passed, "why": got.why[:300]})
        attempt["needs"] = readings
        attempt["orca"] = p.check_frame(
            changed / frame.was, frame, folder / f"slice-{name}", reading
        )
        attempt["frame_sha256"] = content_hash(frame.was, (changed / frame.was).read_bytes())
        orca = attempt["orca"]
        needs_ok = all(r["passed"] is not False for r in readings)
        if orca.get("pause_ok") and needs_ok:
            attempt["held"] = True
            return {
                "checked": "checked",
                "parameter": name,
                "was": was,
                "becomes": becomes,
                "tried": tried,
                "said": (
                    f"Checked: `{script}` with {name} {was:g} → {becomes:g} ({what}); params "
                    f"{record.get('verdict')}; every bench need passes on what it made; `pause` "
                    f"{orca.get('verdict')}, after layer {orca.get('bench_after')}; Orca pauses "
                    f"after layer {orca.get('orca_after')}, and its first layer over the panes is "
                    f"{orca.get('room')} mm above them. The files are in `{changed.name}/`."
                ),
            }
    last = tried[-1]
    return {
        "checked": "failed",
        "tried": tried,
        "parameter": last["parameter"],
        "was": last["was"],
        "becomes": last["becomes"],
        "said": "Tried, and it didn't hold: "
        + "; ".join(
            f"{t['parameter']} → {t['becomes']:g}: params {t['params']['verdict']}"
            + (f", pause {t['orca'].get('verdict')}" if t.get("orca") else "")
            + (", a need failing" if any(r["passed"] is False for r in t.get("needs", [])) else "")
            for t in tried
        )
        + ". Its words stand, with the measured reason.",
    }


def decide(ledger: DesignLedger, proposal: int, decision: str, by: str, note: str) -> int:
    """A person's yes or no to a proposal. Nothing in the approved folder changes either way:
    an approved change goes to the design, as a new run or a send-back, by the people running it."""
    if decision not in ("approved", "rejected"):
        raise Refused(f"a proposal is approved or rejected, not {decision}")
    _who_decides(by, "--by")
    found = ledger.rows("SELECT id FROM refine_proposals WHERE id=?", proposal)
    if not found:
        raise Refused(f"no proposal {proposal}")
    earlier = ledger.rows(
        "SELECT decision, by FROM proposal_decisions WHERE proposal=? ORDER BY id DESC LIMIT 1",
        proposal,
    )
    if earlier:
        raise Refused(
            f"proposal {proposal} was already {earlier[0]['decision']} by {earlier[0]['by']}"
        )
    return ledger.insert(
        "proposal_decisions",
        proposal=proposal,
        decision=decision,
        by=by,
        note=note.strip() or None,
        at=now(),
    )
