"""`cup-1`'s files, read back through the harness where the Mac has them (mqucifer/print-shop#26).

The second real run's records are off this public repository, in `~/print-shop/design-runs/cup-1/`,
and nothing of them is here: this test reads them as it runs, only by reading, and skips where
they're absent. A scripted fake model, with no model call, drives a new run in real containers on
the bench image, from `cup-1`'s request, its references and its scripts as each round left them:

- `needs` refuses the version of its needs that passed a check on FAIL;
- round 1's files don't reach the stop for approval, as `thin` fails two of its panes;
- round 2's files do, and the folder to print from is the one made by hand: the same groups, the
  same files by what they hold, and each pane by the name it was given there.

Every name it uses, of a group, a file, a colour or a need, is read from those records as it runs,
so nothing a run or a person wrote there is in this file. An eye need is answered by a stand-in's
pick, which says so. The new run is kept, as the figures cited for it come from it:
`~/print-shop/proofs/m18-truth/` unless `M18_PROOF` names another folder, off the repository and
outside the runs.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import paths
from print_shop.rounds.run import Run
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, tool
from tests.test_rounds_in_image import BROKEN_DOWN

pytestmark = [pytest.mark.bench, pytest.mark.bench_local]

RUN = Path("~/print-shop/design-runs/cup-1").expanduser()
PROOF = Path(os.environ.get("M18_PROOF", "~/print-shop/proofs/m18-truth")).expanduser()
#: A pane's name in the folder made by hand: its file's stem and number, its label, its colour.
PANE = re.compile(r"([a-z]+_)(\d+)-(.+)-([a-z]+)\.step")
STAND_IN = "stand-in: m18-truth, a Claude builder, reading a run back"


def need_entry(found: dict[str, Any]) -> dict[str, Any]:
    """A settled need, as the model sent it."""
    entry = {k: found[k] for k in ("id", "what", "source", "quote") if found.get(k)}
    if found["by"] == "eye":
        entry["check"] = {"by": "eye", "look_for": found["look_for"]}
        return entry
    check = {"by": "bench", "command": found["command"], "verdicts": found["verdicts"]}
    for key in ("number", "at_least", "at_most"):
        if found.get(key) not in (None, ""):
            check[key] = found[key]
    entry["check"] = check
    return entry


def needs_versions() -> list[list[dict[str, Any]]]:
    return [
        [need_entry(e) for e in json.loads(path.read_text())]
        for path in sorted((RUN / "needs").glob("*.json"), key=lambda p: int(p.stem))
    ]


def passes_on_fail(version: list[dict[str, Any]]) -> bool:
    return any("FAIL" in (n["check"].get("verdicts") or []) for n in version)


def by_hand() -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """The groups, and each pane's look, from the folder made by hand: a group is a folder, and a
    file in every folder is a pane."""
    folders = sorted(p for p in (RUN / "review").iterdir() if p.is_dir())
    names = [{f.name for f in folder.glob("*.step")} for folder in folders]
    shared = set.intersection(*names)
    groups = [
        {"name": folder.name.split("-", 1)[1], "files": sorted(own - shared)}
        for folder, own in zip(folders, names, strict=True)
    ]
    looks: dict[str, dict[str, str]] = {}
    for name in shared:
        matched = PANE.fullmatch(name)
        assert matched, name
        stem, number, label, colour = matched.groups()
        looks[f"{stem}{int(number)}.step"] = {"colour": colour, "label": label.replace("-", " ")}
    return groups, looks


def files_of(round_n: int) -> list[str]:
    return sorted(p.name for p in (RUN / "rounds" / f"{round_n:03}" / "judged").glob("*.step"))


def scripts_of(round_n: int) -> dict[str, str]:
    judged = RUN / "rounds" / f"{round_n:03}" / "judged"
    return {p.name: p.read_text() for p in judged.glob("*.py")}


def held(folder: Path) -> dict[str, dict[str, str]]:
    return {
        group.name: {
            f.name: paths.content_hash(f.name, f.read_bytes())
            for f in group.iterdir()
            if f.suffix == ".step"
        }
        for group in sorted(folder.iterdir())
        if group.is_dir()
    }


def tool_replies(run: Run, name: str) -> list[dict[str, Any]]:
    events = [json.loads(line) for line in run.session.events.read_text().splitlines()]
    return [e for e in events if e.get("kind") == "tool" and e.get("name") == name]


@pytest.mark.skipif(not (RUN / "review").is_dir(), reason="cup-1's records aren't on this machine")
def test_cup_1_read_back_refuses_its_fail_need_and_its_thin_panes_and_delivers_the_folder() -> None:
    root = PROOF / time.strftime("%Y%m%dT%H%M%S")
    groups, pane_looks = by_hand()
    framing = [f for g in groups for f in g["files"]]
    versions = needs_versions()
    failing_version = next(v for v in versions if passes_on_fail(v))
    settled = versions[-1]
    eye = next(n["id"] for n in settled if n["check"]["by"] == "eye")
    script = json.loads((RUN / "rounds" / "001" / "judgement.json").read_text())["script"]

    def looks(files: list[str]) -> dict[str, dict[str, str]]:
        frame = {f: {"colour": "grey", "label": "frame"} for f in files if f in framing}
        return {**frame, **{f: pane_looks[f] for f in files if f in pane_looks}}

    def asked(files: list[str]) -> list[list[dict[str, Any]]]:
        """Run the script, draw the sheet, and send it: what the eye need is answered from."""
        return [
            [tool("run", path=script)],
            [
                tool(
                    "ask",
                    question="Does it read as the picture?",
                    need=eye,
                    looks=looks(files),
                    cuts="z=3",
                    variants=[{"name": "A", "files": files, "about": "as the run left it"}],
                )
            ],
            [tool("send_ask", says="The panel as the run left it, in the legend's colours.")],
        ]

    def done(files: list[str]) -> list[dict[str, Any]]:
        words = {"fix_shape": "read back", "closing": "The run's files, read back."}
        return [
            tool("done", script=script, part=files, groups=groups, looks=looks(files), **words)
        ]

    def picked(run: Run) -> None:
        pick = {"pick": "A", "reason": "Read back: the look isn't judged here.", "by": STAND_IN}
        (run.folder / "asks" / str(run.state.asks) / "pick.json").write_text(json.dumps(pick))

    def load(run: Run, *turns: Any) -> Run:
        return Run.load(run.folder, Scripted(*turns), sleep=lambda _: None)

    # Intake, and round 1's script as the run had it.
    round_1, round_2 = files_of(1), files_of(2)
    model = Scripted(
        [BROKEN_DOWN, tool("needs", needs=failing_version)],
        [tool("needs", needs=settled)],
        *asked(round_1),
    )
    references = sorted((RUN / "work" / "references").iterdir())
    # It reads back what the run did, from before the plan was a stop: none is added.
    request = (RUN / "request.md").read_text() + "\n\n## Stops\n- no plan\n"
    run = Run.start(root, "read-back", request, references, model, Limits(), sleep=lambda _: None)
    for name, text in scripts_of(1).items():
        (run.work / name).write_text(text)
    assert run.drive().name == "ask"
    needs_said = tool_replies(run, "needs")
    assert needs_said[0]["outcome"] == "failed"
    assert "FAIL is the bench saying what" in needs_said[0]["reply"]
    assert needs_said[1]["outcome"] == "ok"
    picked(run)
    run = load(run, done(round_1), [tool("blocked", reason="round 1 read")])
    run.resume()
    assert run.drive().name == "blocked"
    done_1 = tool_replies(run, "done")[0]["reply"]
    assert "Every need passed, and the harness's own checks on the files to be printed" in done_1
    too_thin = sorted(re.findall(r"- (\S+), TOO THIN:", done_1))
    assert len(too_thin) == 2 and all(f in pane_looks for f in too_thin), too_thin
    assert run.state.approvals == 0, "no stop for approval"

    # Round 2's script, as the run left it.
    run = load(run, *asked(round_2))
    for name, text in scripts_of(2).items():
        (run.work / name).write_text(text)
    run.resume("Round 2's script, as the run left it.")
    assert run.drive().name == "ask"
    picked(run)
    run = load(run, done(round_2))
    run.resume()
    assert run.drive().name == "met"
    made = held(run.folder / "review")
    assert made == held(RUN / "review"), "the folder to print from is the one made by hand"
    figures = {
        "how_it_ran": "tests/test_rounds_cup_local.py: a fake model in real containers on the "
        "bench image, from the run's request, references, needs and round scripts",
        "run": str(run.folder),
        "needs_refused": needs_said[0]["reply"],
        "round_1_reply": done_1,
        "too_thin": too_thin,
        "folders": {group: sorted(files) for group, files in made.items()},
    }
    (root / "figures.json").write_text(json.dumps(figures, indent=1) + "\n")
