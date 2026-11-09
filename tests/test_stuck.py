"""When the model is stuck it's told plainly (mqucifer/print-shop#79): a note that reopens the needs
sets each settled need beside it, a need expecting an overlap with a given part is questioned, and
a call that went round in circles is offered a way out.

No Docker and no model: a scripted model drives each run, and a box answers each bench command
with the next verdict and runs nothing. The parts are made up for the test, a lid over a fan given
as a reference; nothing here is from a run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import picture as picture_module
from print_shop.bench import usage as usage_module
from print_shop.rounds import stuck, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, Spoken, tool
from tests.test_intake import BROKEN_DOWN

REQUEST = """\
A lid for the fan given, held on by clips of its own.

## Requirements
- The lid sits over the fan.

## Stops
- no plan
"""
FIT = "clearance references/fan.step lid.step"
CODES = {"OK": 0, "CLEAR": 0, "FAIL": 1, "INTERFERE": 1}


class Answers:
    """Each bench command answered with the next verdict, CLEAR once they run out, saved as the
    bench saves one. `render` draws a sheet, and scripts run."""

    def __init__(self, work: Path, *verdicts: str) -> None:
        self.work = work
        self.verdicts = list(verdicts)
        self.asked: list[list[str]] = []

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(args)
        n = len(self.asked)
        here = where or self.work
        record: dict[str, Any] = {}
        if "render" in args:
            out = args[args.index("--renders-out") + 1]
            (here / out).mkdir(parents=True, exist_ok=True)
            (here / out / "sheet.png").write_bytes(b"png")
            verdict, record["files"] = "OK", [f"{out}/sheet.png"]
        else:
            verdict = self.verdicts.pop(0) if self.verdicts else "CLEAR"
        why = f"{verdict.lower()} on {' '.join(args)}"
        if verdict == "INTERFERE":
            why = "overlap 2.500 mm³ between the two files"
        record.update(verdict=verdict, why=why, numbers={"volume_mesh_mm3": 10.0})
        out_dir = here / "bench-out"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{n}.json").write_text(json.dumps(record))
        said = f"verdict: {verdict}: {why}\nsaved: bench-out/{n}.txt and .json\n"
        return Ran(CODES.get(verdict, 2), said, 0.1)

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "usage":
            return Ran(0, usage_module.MARK + json.dumps(["" for _ in args]), 0.1)
        if module == "picture":
            source, target = args
            (self.work / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.work / source, self.work / target)
            said = {"size": [8, 8], "shown": [8, 8], "out": target}
            return Ran(0, picture_module.MARK + json.dumps(said), 0.1)
        raise AssertionError(module)


def _boxed(run: Run, verdicts: tuple[str, ...]) -> Run:
    box: Any = Answers(run.work, *verdicts)
    run.box = box
    run.tools.box = box
    return run


def started(root: Path, *turns: Any, verdicts: tuple[str, ...] = ()) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    fan = root / "given" / "fan.step"
    fan.parent.mkdir(parents=True, exist_ok=True)
    fan.write_text("a fan, made up")
    run = Run.start(root / "runs", "r", REQUEST, [fan], model, sleep=lambda _: None)
    (run.work / "lid.step").write_text("a lid, made up")
    (run.work / "make.py").write_text("print('made')\n")
    return _boxed(run, verdicts)


def again(run: Run, *turns: Any, verdicts: tuple[str, ...] = ()) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    return _boxed(Run.load(run.folder, model, sleep=lambda _: None), verdicts)


def told(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def need(nid: str, command: str, measures: str, **check: Any) -> dict[str, Any]:
    return {
        "id": nid,
        "what": f"what {nid} holds",
        "source": "chosen",
        "covers": ["R1"],
        "check": {"by": "bench", "measures": measures, "command": command, **check},
    }


THREE = [
    need("fits", FIT, "fit", verdicts=["CLEAR"]),
    need("size", "info lid.step", "size", number="volume_mesh_mm3", at_least=1),
    {
        "id": "look",
        "what": "the lid reads plain",
        "source": "chosen",
        "check": {"by": "eye", "look_for": "whether the lid reads plain"},
    },
]
CLIPS = need("clips", FIT, "fit", verdicts=["INTERFERE"])


def settled(tmp_path: Path) -> Run:
    run = started(tmp_path, [BROKEN_DOWN], [tool("needs", needs=THREE)])
    assert run.drive().name == "blocked"
    assert run.state.needs_version == 1
    return run


# 1. A note that reopens the needs sets every settled need beside it


def test_a_note_reopening_three_needs_lists_them_and_needs_waits_for_each_mark(
    tmp_path: Path,
) -> None:
    run = settled(tmp_path)
    two = {
        "fits": {"is": "kept", "why": "The note is about the clips, not the fit."},
        "size": "kept: the note doesn't change the lid's size.",
    }
    three = {**two, "look": {"is": "changed", "why": "The note asks for the clips to show."}}
    looks = {**THREE[2], "what": "the lid reads plain, its clips showing"}
    run = again(
        run,
        [tool("bench", command="info lid.step")],
        [tool("run", path="make.py")],
        [tool("needs", needs=THREE)],
        [tool("needs", needs=[*THREE[:2], looks], against_note=two)],
        [tool("needs", needs=[*THREE[:2], looks], against_note={**three, "size": "changed: x"})],
        [tool("needs", needs=[*THREE[:2], looks], against_note=three)],
        [tool("bench", command="info lid.step")],
    )
    said = run.resume(
        "Make the clips easy to see from the side.",
        reopen_needs=True,
        note_from="stand-in: the operator",
    )
    assert said[-1] == (
        "The model is shown its 3 settled needs beside the note, and `needs` is refused until "
        "each is marked kept or changed against it."
    )
    # The list comes after the note, so it reads against it.
    relayed = [e for e in transcript.events_of(run.session.events) if e["kind"] == "relayed"]
    assert [e["relay"] for e in relayed[-3:]] == ["reopen_needs", "note", "settled_beside"]
    listed = relayed[-1]["message"]
    assert listed.startswith("The note reopened the needs. Each settled need, beside it:")
    assert f'- `fits`: "what fits holds", checked by `{FIT}` (verdict CLEAR)' in listed
    assert '- `look`: "the lid reads plain", checked by the Sponsor\'s eye' in listed
    assert "in `against_note` mark each of these kept or changed" in listed
    # A compaction's note leads with it too.
    assert run.compaction_note().startswith(stuck.REOPENED + "\n\n")

    earlier = len(told(run))
    assert run.drive().name == "blocked"
    bench, ran, bare, short, wrong, taken, after = told(run)[earlier : earlier + 7]
    assert bench["reply"].startswith(stuck.REOPENED + "\n\nexit 0")
    assert ran["reply"].startswith(stuck.REOPENED + "\n\nexit 0")
    assert bench["detail"]["stuck"] == {"reopened": True}
    assert bare["why"] == "not_marked"
    for nid in ("fits", "size", "look"):
        assert f"- `{nid}` isn't marked: it was" in bare["reply"]
    assert short["why"] == "not_marked"
    assert "`look` isn't marked" in short["reply"] and "`fits` isn't" not in short["reply"]
    assert "`size` is marked changed, and this list sends it as it was" in wrong["reply"]
    assert taken["outcome"] == "ok" and taken["reply"].startswith("The needs are settled")
    assert run.state.reopened is None
    assert not after["reply"].startswith(stuck.REOPENED), "`needs` was taken"
    assert not run.compaction_note().startswith(stuck.REOPENED)
    refused = [r["why"] for r in run.ledger.rows("SELECT why FROM refusals ORDER BY seq")]
    assert refused.count("not_marked") == 3


def test_a_reopening_with_no_note_lists_nothing_and_holds_nothing(tmp_path: Path) -> None:
    run = settled(tmp_path)
    run = again(run, [tool("bench", command="info lid.step")])
    said = run.resume(reopen_needs=True)
    assert len(said) == 1, "nothing to set the needs beside"
    assert run.state.reopened is None
    assert run.drive().name == "blocked"
    assert not told(run)[0]["reply"].startswith(stuck.REOPENED)


def test_kept_means_sent_as_it_was_and_changed_means_it_isnt() -> None:
    old = Need("fits", "the lid fits", "chosen", "bench", command=FIT, verdicts=["CLEAR"])
    looser = Need("fits", "the lid fits", "chosen", "bench", command=f"{FIT} --min-gap 0.1")

    def same(a: Need, b: Need) -> bool:
        return (a.command, a.what) == (b.command, b.what)

    kept = {"fits": {"is": "kept", "why": "unchanged"}}
    assert stuck.unmarked(kept, [old], [old], same) == []
    assert "this list changes it" in stuck.unmarked(kept, [old], [looser], same)[0]
    assert "this list doesn't have it" in stuck.unmarked(kept, [old], [], same)[0]
    gone = {"fits": "changed - the note drops it"}
    assert stuck.unmarked(gone, [old], [], same) == []
    assert "with no reason" in stuck.unmarked({"fits": "kept"}, [old], [old], same)[0]
    assert stuck.mark_of({"is": "maybe", "why": "x"}) is None


# 2. A need that expects an overlap with a bought part asks why


@pytest.mark.parametrize("pick", ["A", "none"])
def test_a_need_expecting_an_overlap_with_a_given_part_stands_only_on_a_persons_yes(
    tmp_path: Path, pick: str
) -> None:
    asked = tool(
        "ask",
        question="The clips overlap the fan's frame on purpose. Is that right?",
        variants=[{"name": "A", "files": ["lid.step"]}],
        test_pieces=["references/fan.step"],
        overlap={"need": "clips", "command": FIT},
    )
    run = started(
        tmp_path,
        [BROKEN_DOWN],
        [tool("needs", needs=[CLIPS])],
        [
            tool(
                "ask",
                question="Is it right?",
                variants=[{"name": "A", "files": ["lid.step"]}],
                overlap={"need": "clips", "command": FIT},
            )
        ],
        [asked],
        [tool("send_ask", says="The lid in its colour, over the fan in grey.")],
        verdicts=("CLEAR", "INTERFERE"),
    )
    assert run.drive().name == "ask"
    refused, clear, drawn, sent = told(run)[1:5]
    assert refused["why"] == "overlap_with_bought"
    assert "expects INTERFERE with `references/fan.step`, a part you were given" in refused["reply"]
    assert "An overlap with a bought part can't hold it" in refused["reply"]
    assert (
        "`clearance lid.step references/fan.step --slide <its way out: one of +x -x +y -y +z "
        "-z>` finds (mqucifer/print-shop#76)"
    ) in refused["reply"]
    assert clear["why"] == "no_overlap", "there's no overlap to show"
    assert "The overlap goes beside the question: " in drawn["reply"]
    assert "overlap 2.500 mm³" in drawn["reply"]
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## An overlap with a given part, for you to accept or not" in question
    assert "overlap 2.500 mm³" in question
    assert sent["outcome"] == "ok"

    (run.folder / "asks" / "1" / "pick.json").write_text(
        json.dumps({"pick": pick, "reason": "A press fit, as I asked.", "by": "sponsor"})
    )
    run = again(run, [tool("needs", needs=[CLIPS])])
    run.resume()
    message = run.session.messages[-1]["content"]
    assert run.drive().name == "blocked"
    after = told(run)[-2]
    if pick == "A":
        assert "The Sponsor accepted the overlap `clips` expects" in message
        assert run.state.overlaps[0]["need"] == "clips"
        assert after["outcome"] == "ok", "a person said yes to it"
    else:
        assert "The Sponsor didn't accept the overlap `clips` expects" in message
        assert "--slide <its way out>" in message
        assert after["why"] == "overlap_with_bought"


def test_an_overlap_between_the_models_own_files_is_no_ones_to_accept(tmp_path: Path) -> None:
    own = need("press", "clearance lid.step clip.step", "fit", verdicts=["INTERFERE"])
    assert stuck.overlaps([Need(**_plain(own))], []) == []
    assert stuck.bought(Need(**_plain(CLIPS))) == ["references/fan.step"]


def _plain(entry: dict[str, Any]) -> dict[str, Any]:
    check = entry["check"]
    return {
        "id": entry["id"],
        "what": entry["what"],
        "source": entry["source"],
        "by": "bench",
        "command": check["command"],
        "verdicts": check.get("verdicts", []),
    }


# 3. After a call that went round in circles, the next reply offers a way out


#: Reasoning that's all sentences long enough to count, said once and then again.
GROUND = " ".join(f"The catch could sit at height number {i} of the frame." for i in range(80))


def test_a_runaway_calls_next_reply_leads_with_the_way_out_and_a_normal_calls_doesnt(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        Spoken([tool("bench", command="info lid.step")], reasoning=GROUND),
        Spoken(
            [tool("bench", command="info lid.step"), tool("read", path="make.py")],
            reasoning=GROUND,
        ),
        [tool("bench", command="info lid.step")],
    )
    assert run.drive().name == "blocked"
    first, again_, read, normal = told(run)[:4]
    assert not first["reply"].startswith(stuck.CIRCLED)
    assert again_["reply"].startswith(stuck.CIRCLED + "\n\nexit 0")
    circled = {"call": 2, "why": "runaway", "marked": "repeats"}
    assert again_["detail"]["stuck"]["circled"] == circled
    assert not read["reply"].startswith(stuck.CIRCLED), "the call's first reply only"
    assert not normal["reply"].startswith(stuck.CIRCLED)
    rows = run.ledger.rows("SELECT n, why FROM runaways")
    assert [(r["n"], r["why"]) for r in rows] == [(2, "repeats")], "kept for review, not cut"


def test_a_long_call_rerunning_a_failed_check_on_the_same_files_leads_with_the_way_out(
    tmp_path: Path,
) -> None:
    long: Any = {"completion_tokens_details": {"reasoning_tokens": stuck.CIRCLING_TOKENS}}
    short: Any = {"completion_tokens_details": {"reasoning_tokens": 3000}}
    run = started(
        tmp_path,
        [tool("bench", command=FIT)],
        Spoken([tool("bench", command=f"{FIT} --min-gap 0.2")], usage=long),
        Spoken([tool("bench", command=FIT)], usage=short),
        [tool("write", path="lid.step", content="a lid, moved")],
        Spoken([tool("bench", command=FIT)], usage=long),
        verdicts=("INTERFERE", "INTERFERE", "INTERFERE", "INTERFERE"),
    )
    assert run.drive().name == "blocked"
    first, rerun, brief, _, changed = told(run)[:5]
    assert not first["reply"].startswith(stuck.CIRCLED)
    assert rerun["reply"].startswith(stuck.CIRCLED)
    assert rerun["detail"]["stuck"]["circled"] == {
        "call": 2,
        "why": "rerun",
        "reasoning_tokens": stuck.CIRCLING_TOKENS,
        "failed_at": 1,
        "command": f"{FIT} --min-gap 0.2",
    }
    assert not brief["reply"].startswith(stuck.CIRCLED), "a short call isn't circling"
    assert "rerun" in brief["detail"]
    assert not changed["reply"].startswith(stuck.CIRCLED), "the lid changed since"
    assert "rerun" not in changed["detail"]
