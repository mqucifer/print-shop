"""A request's print side is checked by `done`'s `on_bed`, not by eye at a stop
(mqucifer/print-shop#73). No Docker: a scripted model drives each run, and a box answers each bench
command and runs nothing. The part is made up: a tray that prints on its base, open side up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import overlap as overlap_module
from print_shop.refine import approved
from print_shop.rounds import needs, request
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Run
from tests.refine_fakes import FakeOrca
from tests.refine_fixtures import make_enclosure_run
from tests.rounds_fakes import Scripted, tool
from tests.test_holding import Answers, replies
from tests.test_refine import _passing

TRAY = """\
A small tray for keys.

## Requirements
- The tray is one piece.
- It prints on its base, open side up.

## Stops
- no plan
"""
BROKEN_DOWN = tool(
    "breakdown",
    parts=[{"name": "tray", "what": "a shallow open tray, its base flat"}],
    joins=[],
    look=["a plain tray"],
    **{"from": "the request"},
)
ONE_PIECE = {
    "id": "one-piece",
    "what": "one connected solid",
    "source": "request",
    "quote": "The tray is one piece.",
    "covers": ["R1"],
    "check": {"by": "bench", "measures": "one-piece", "command": "thin tray.step --one-body"},
}
SIDE = {
    "id": "base-down",
    "what": "it prints on its base, open side up",
    "source": "request",
    "quote": "It prints on its base, open side up.",
    "covers": ["R2"],
    "check": {"by": "bed", "file": "tray.step", "side": "-z"},
}
#: The same line as an eye need, as on `eval-edges-2`: a stop only to confirm the side.
BY_EYE: dict[str, Any] = {
    **SIDE,
    "check": {
        "by": "eye",
        "look_for": "the base is the face on the bed, with the open side up",
        "no_bench": "the side it prints on is a choice, not a measurement",
    },
}
#: An eye need on how a feature holds, which stays an eye need.
HOLDS = {
    "id": "keys-stay",
    "what": "a key ring stays in the tray's notch",
    "source": "chosen",
    "check": {
        "by": "eye",
        "look_for": "the notch's hook is deep enough that a ring can't slip out",
    },
}
DONE = {"script": "make.py", "part": ["tray.step"], "fix_shape": "first", "closing": "Here."}


def _read(*entries: dict[str, Any]) -> tuple[list[needs.Need], list[str]]:
    return needs.read(list(entries), TRAY)


# A need checked by the side on the bed


def test_a_bed_need_says_which_side_of_which_file_lies_on_the_bed() -> None:
    (found,), wrong = _read({**SIDE, "check": {"by": "bed", "file": "./tray.step", "side": "Z"}})
    assert wrong == []
    assert (found.by, found.file, found.side) == ("bed", "tray.step", "+z")
    assert found.plain()["file"] == "tray.step" and found.plain()["side"] == "+z"
    assert found.limit() == "`on_bed` gives `tray.step` its +z side"
    assert needs.load(needs.dump([found])) == [found]
    assert request.held([found], request.read(TRAY)) == [
        "`R1` isn't covered: \"The tray is one piece.\". Name it in a need's `covers`"
    ], "a bed need covers its line with no `no_bench`"


@pytest.mark.parametrize(
    ("check", "said"),
    [
        ({"by": "bed", "side": "-z"}, "`file` names the one file that lies on the bed"),
        ({"by": "bed", "file": "tray.png", "side": "-z"}, "`file` names the one file"),
        ({"by": "bed", "file": "references/base.step", "side": "-z"}, "is a given reference"),
        ({"by": "bed", "file": "tray.step", "side": "down"}, "Not down"),
        ({"by": "bed", "file": "tray.step"}, "Not nothing"),
        ({"by": "hand"}, "`check.by` is bench, eye or bed"),
    ],
)
def test_a_bed_needs_file_and_side_are_held(check: dict[str, Any], said: str) -> None:
    _, wrong = _read({**SIDE, "check": check})
    assert any(said in w for w in wrong), wrong


def test_an_eye_need_that_only_names_the_print_side_is_refused_saying_which_kind_to_use() -> None:
    _, wrong = _read(BY_EYE)
    assert wrong == [
        "the need 'base-down' asks the eye which side it prints on (\"on the bed\"). That isn't "
        "an eye need: `done` checks the side against its `on_bed`, so send it as a bed need, "
        '"check": {"by": "bed", "file": "lid.step", "side": "+z"}, with the side as your script '
        "stores the file, and the same `source`, `quote` and `covers`. If this eye need judges "
        "more than the side, such as how a feature looks as printed, keep it for that, with "
        "`look_for` naming only what the eye judges, and send the bed need beside it. Whether a "
        "bought part is held is the bench's, never the eye's: `clearance <part> <bought part> "
        "--slide <its way out>`, with `HELD`"
    ]


@pytest.mark.parametrize(
    "look_for",
    [
        "it prints on its base",
        "upside down, the rim on the plate",
        "floor-down print orientation",
        "printed with its outside face down",
    ],
)
def test_the_print_side_is_found_however_its_said(look_for: str) -> None:
    _, wrong = _read({**BY_EYE, "check": {**BY_EYE["check"], "look_for": look_for}})
    assert len(wrong) == 1 and "asks the eye which side it prints on" in wrong[0]


def test_an_eye_need_on_how_a_feature_holds_is_still_taken() -> None:
    (one, two), wrong = _read(
        HOLDS,
        {
            **HOLDS,
            "id": "looks",
            "check": {
                "by": "eye",
                "look_for": "the rim's top edge as printed reads soft to the hand",
            },
        },
    )
    assert wrong == []
    assert one.by == two.by == "eye"


# At `done`, against `on_bed`


class Box(Answers):
    """Every bench command OK; the script, run clean, writes the tray."""

    ran = 0

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        self.ran += 1
        (where or self.work).joinpath("tray.step").write_text("x")
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "overlap":
            said = {"files": {name: {} for name in args}, "pairs": [], "unread": {}}
            return Ran(0, overlap_module.MARK + json.dumps(said), 0.1)
        return super().helper(module, args, timeout, where)


def _run(root: Path, *turns: Any) -> Run:
    model = Scripted(*[[t] for t in turns], [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", TRAY, [], model, sleep=lambda _: None)
    for name in ("make.py", "tray.step"):
        (run.work / name).write_text("x")
    box: Any = Box(run.work)
    run.box = box
    run.tools.box = box
    return run


def test_a_print_side_line_is_a_bed_need_and_done_holds_on_bed_to_it(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[ONE_PIECE, BY_EYE]),
        tool("needs", needs=[ONE_PIECE, SIDE]),
        tool("done", **DONE),
        tool("done", on_bed={"tray.step": "+z"}, **DONE),
        tool("done", on_bed={"tray.step": "-z"}, **DONE),
    )
    assert run.drive().name == "met"
    _, by_eye, settled, none, other, met = replies(run)[:6]
    assert by_eye["outcome"] == "failed"
    assert "asks the eye which side it prints on" in by_eye["reply"]
    assert settled["outcome"] == "ok"
    (row,) = run.ledger.rows("SELECT need, file, side FROM need_beds")
    assert tuple(row) == ("base-down", "tray.step", "-z")
    assert none["why"] == other["why"] == "bed_need_unmet"
    assert none["reply"] == (
        "Nothing was checked: a need says which side of a file lies on the bed, and `on_bed` "
        "doesn't give it:\n"
        "- `base-down`: `on_bed` names no side for `tray.step`, and the need says it prints with "
        'its -z side on the bed: give `on_bed` {"tray.step": "-z"}.\n'
        "Send `done` again with `on_bed` giving each file its side, as your script stores it."
    )
    assert "`on_bed` gives `tray.step` its +z side, and the need says -z." in other["reply"]
    assert run.box.ran == 1, "nothing ran until `on_bed` held"
    assert met["outcome"] == "ok" and met["detail"]["met"] is True
    readings = {
        r["need"]: (r["by"], r["passed"], r["why"])
        for r in run.ledger.rows("SELECT * FROM readings")
    }
    assert readings["base-down"] == (
        "bed",
        1,
        "`on_bed` gives `tray.step` its -z side, as the need says",
    )
    readme = (run.folder / "approvals" / "1" / "review" / "README.md").read_text()
    assert "it prints with its -z side on the bed, as the need `base-down` says" in readme
    assert "| `on_bed` gives `tray.step` its -z side | `done`'s `on_bed` |" in readme
    assert run._eyes_open() == [], "no stop for the side"


def test_a_bed_need_on_a_file_not_in_the_part_says_to_name_it(tmp_path: Path) -> None:
    found, _ = _read({**SIDE, "check": {"by": "bed", "file": "lid.step", "side": "+z"}})
    reading = needs.bed_reading(found[0], {"tray.step": "-z"}, ["tray.step"])
    assert not reading.passed
    assert reading.why == (
        "the need says `lid.step` prints with its +z side on the bed, and `part` doesn't name "
        '`lid.step` (`tray.step`): name it in `part`, and in `on_bed` as {"lid.step": "+z"}'
    )


# The refinement pass, on the part as it lays it


def _with_bed_need(folder: Path, need_id: str, file: str, side: str) -> None:
    """The approved run's needs, as `needs` settled them: one bed need."""
    found = needs.Need(need_id, "it prints on that side", "chosen", "bed", file=file, side=side)
    (folder / "needs.json").write_text(json.dumps(needs.dump([found])), encoding="utf-8")


def test_the_refinement_pass_confirms_a_bed_need_on_the_laid_part(tmp_path: Path) -> None:
    folder, ledger = make_enclosure_run(tmp_path, "case", {"lid.stl": "+z"})
    _with_bed_need(folder, "outside-down", "lid.stl", "+z")
    p = _passing(folder, ledger, {}, FakeOrca())
    (laid,) = [f for f in p.run().findings if f.kind == "on_bed"]
    assert laid.numbers["need"] == "outside-down" and laid.numbers["laid"] == "+z"
    assert laid.means.endswith(
        "; the need `outside-down`, its +z side on the bed, holds on the laid part"
    )
    assert not laid.needs_change


def test_a_bed_need_the_pass_cant_lay_is_a_change_needed(tmp_path: Path) -> None:
    folder, ledger = make_enclosure_run(tmp_path, "case", {"cone.stl": "+z"}, ("cone.stl",))
    _with_bed_need(folder, "tip-down", "cone.stl", "+z")
    assert approved.read(folder, ledger).needs[0].side == "+z"
    p = _passing(folder, ledger, {}, FakeOrca())
    (laid,) = [f for f in p.run().findings if f.kind == "on_bed"]
    assert laid.needs_change
    assert "the need `tip-down` says its +z side lies on the bed, and the part is laid -z" in (
        laid.means
    )
