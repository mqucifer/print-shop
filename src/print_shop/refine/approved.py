"""What the pass works on: an approved run's folder to print from, read from its records.

It runs after approval only. The approval is the run's last `approved` decision, and the folder is
that stop's (`approvals/<n>/review/`), each file held to the hash the ledger recorded at the stop.
Each file's colour and label are the ones `done` gave: the stop's `review` record has them from
now on, and for a run from before, they're read from the `done` call that made the stop. So is the
side each file lies on (`on_bed`, mqucifer/print-shop#66), and the run's kind and `glass`, which
say what's printed and what's only context.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.rounds import needs as needs_mod
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.needs import Need
from print_shop.rounds.paths import content_hash
from print_shop.rounds.run import APPROVED, Refused


@dataclass
class File:
    """One file of the approved folder."""

    folder: str
    name: str
    #: Its name in the run, which `looks` and the needs use.
    was: str
    path: Path
    sha256: str
    colour: str = ""
    label: str = ""
    #: The side `done` named for the bed, `-z` to `+y`, or "" where it named none.
    on_bed: str = ""


@dataclass
class Group:
    """One folder of the delivered result: one way the part prints."""

    folder: str
    name: str
    files: list[File]
    #: What the model said of how it prints, from the README: its words, kept as its.
    about: str = ""


@dataclass
class Approved:
    run: str
    folder: Path
    approval: int
    decision: int
    round: int
    script: str
    groups: list[Group]
    needs: list[Need]
    #: The run's own `pause` calls, in order: each one's arguments, verdict and call.
    pause_calls: list[dict[str, Any]] = field(default_factory=list)
    #: The request's kind, as `start` read it, or None for a run from before kinds.
    kind: str | None = None
    #: Each frame, its panes and the frame's other parts, as the run's last `glass` declared them.
    glass: list[dict[str, Any]] = field(default_factory=list)

    @property
    def judged(self) -> Path:
        """What the approved round's clean run left, with the script that made it."""
        return self.folder / "rounds" / f"{self.round:03}" / "judged"


def _looks_from_events(folder: Path, approval: int, key: str = "looks") -> dict[str, Any]:
    """The `looks` of the `done` call that made stop `approval`, for a run from before they were
    in the stop's record, or another of its arguments by `key`."""
    looks: dict[str, Any] = {}
    events = folder / "events.jsonl"
    if not events.is_file():
        return looks
    last: dict[str, Any] = {}
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") == "tool" and event.get("name") == "done":
            try:
                args = json.loads(event.get("arguments") or "{}")
            except ValueError:
                continue
            if isinstance(args.get(key), dict):
                last = args[key]
        elif event.get("kind") == "ended" and event.get("name") == "met":
            if (event.get("said") or {}).get("approval") == approval:
                looks = last
    return looks


def _about(readme: Path, folder: str) -> str:
    """The model's words on how a group prints, as the README holds them."""
    if not readme.is_file():
        return ""
    lines = readme.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(f"## {folder}")
    except ValueError:
        return ""
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if line.endswith("(the model's words)"):
            return line[: -len("(the model's words)")].strip()
    return ""


def read(folder: Path, ledger: DesignLedger) -> Approved:
    """The run's approved folder, or `Refused` saying why there's none."""
    name = folder.name
    state_file = folder / "state.json"
    if not state_file.is_file():
        raise Refused(f"no run at {folder}")
    state = json.loads(state_file.read_text(encoding="utf-8"))
    ending = (state.get("ending") or {}).get("name")
    if ending != APPROVED:
        raise Refused(
            f"{name} isn't approved (it's {ending or state.get('stage') or 'running'}): the "
            "refinement pass runs after approval only"
        )
    decided = ledger.rows(
        "SELECT id, approval FROM decisions WHERE run=? AND decision=? ORDER BY id DESC LIMIT 1",
        name,
        APPROVED,
    )
    if not decided or decided[0]["approval"] is None:
        raise Refused(f"the ledger has no approval of {name}")
    approval = int(decided[0]["approval"])
    stop = ledger.rows("SELECT * FROM approvals WHERE run=? AND n=?", name, approval)
    if not stop or not stop[0]["review"]:
        raise Refused(f"approval {approval} of {name} has no folder to print from in the ledger")
    review = json.loads(stop[0]["review"])
    looks: dict[str, dict[str, str]] = review.get("looks") or _looks_from_events(folder, approval)
    on_bed: dict[str, str] = review.get("on_bed") or _looks_from_events(folder, approval, "on_bed")
    shared: dict[str, str] = review.get("shared") or {}
    renamed_from = {v: k for k, v in shared.items()}
    base = folder / "approvals" / str(approval) / "review"
    groups: list[Group] = []
    for group in review.get("groups") or []:
        files: list[File] = []
        for file_name, sha in (group.get("files") or {}).items():
            path = base / group["folder"] / file_name
            if not path.is_file():
                raise Refused(f"{path} is gone: the approved folder isn't as it was approved")
            held = content_hash(file_name, path.read_bytes())
            if held != sha:
                raise Refused(
                    f"{group['folder']}/{file_name} has changed since it was approved "
                    f"({held[:12]}, approved as {sha[:12]})"
                )
            was = renamed_from.get(file_name) or next(
                (k for k in looks if Path(k).name == file_name), file_name
            )
            look = looks.get(was) or {}
            files.append(
                File(
                    group["folder"],
                    file_name,
                    was,
                    path,
                    sha,
                    str(look.get("colour") or ""),
                    str(look.get("label") or ""),
                    str(on_bed.get(was) or ""),
                )
            )
        groups.append(
            Group(
                group["folder"],
                group.get("name", ""),
                files,
                _about(base / "README.md", group["folder"]),
            )
        )
    script = ""
    events = folder / "events.jsonl"
    if events.is_file():
        for line in events.read_text(encoding="utf-8").splitlines():
            if '"ended"' not in line:
                continue
            event = json.loads(line)
            said = event.get("said") or {}
            if event.get("name") == "met" and said.get("approval") == approval:
                script = str(said.get("script") or "")
    wanted = (
        needs_mod.load(json.loads((folder / "needs.json").read_text(encoding="utf-8")))
        if (folder / "needs.json").is_file()
        else []
    )
    calls = []
    for row in ledger.rows(
        "SELECT at_call, argv, verdict FROM bench_calls WHERE run=? ORDER BY id", name
    ):
        argv = json.loads(row["argv"])
        if argv[:1] == ["pause"] and len(argv) > 1 and not argv[1].startswith("-"):
            calls.append({"at_call": row["at_call"], "argv": argv, "verdict": row["verdict"]})
    return Approved(
        run=name,
        folder=folder,
        approval=approval,
        decision=int(decided[0]["id"]),
        round=int(stop[0]["round"]),
        script=script,
        groups=groups,
        needs=wanted,
        pause_calls=calls,
        kind=(state.get("form") or {}).get("kind") or state.get("kind") or None,
        glass=list(state.get("glass") or []),
    )


def pause_inputs(call: dict[str, Any]) -> dict[str, float] | None:
    """A `pause` call's pocket height and pane thickness, if it gave both."""
    argv = list(call["argv"])
    found: dict[str, float] = {}
    for flag, key in (("--pocket-z", "pocket_z"), ("--pane-thickness", "pane_thickness")):
        if flag in argv and argv.index(flag) + 1 < len(argv):
            try:
                found[key] = float(argv[argv.index(flag) + 1])
            except ValueError:
                return None
    return found if len(found) == 2 else None


def said(argv: list[str]) -> str:
    return shlex.join(argv)
