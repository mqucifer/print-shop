"""Memory within a run (mqucifer/print-shop#39), with no Docker: a shrunk reply keeps what failed
and why, each check's tries are kept for a summary and a new conversation, and an `edit` whose
lines aren't there says how they differ, and whether the file changed since the model had it.

Every reply and file here is made up in the test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import needs, nudge, tools, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from print_shop.rounds.session import COMPACTED, Ending, Judged, Limits, Session, gist
from tests.rounds_fakes import Scripted, tool
from tests.test_close import Answers

BENCH_FAIL = (
    "exit 1\n"
    "verdict: FAIL: pane 2's lip is under 1.2 mm for 30 mm of its edge\n"
    "least_lip_mm: 0.8\n"
    "saved: bench-out/capture-1.txt and .json\n"
)
RUN_FAIL = (
    "exit 1 after 1.8s\n"
    "Traceback (most recent call last):\n"
    '  File "/work/a.py", line 3, in <module>\n'
    "    box = cq.Workplane().box(1, 1)\n"
    "TypeError: box() missing 1 required positional argument: 'height'\n"
)


# What a shrunk reply keeps


def test_a_shrunk_reply_keeps_what_failed_and_why_never_only_an_exit_code() -> None:
    assert gist(BENCH_FAIL) == (
        "exit 1, verdict: FAIL: pane 2's lip is under 1.2 mm for 30 mm of its edge"
    )
    assert gist(RUN_FAIL) == (
        "exit 1 after 1.8s; TypeError: box() missing 1 required positional argument: 'height'"
    )
    assert gist("exit 0 after 0.9s\nmaking\nsaved frame.step\n") == (
        "exit 0 after 0.9s; saved frame.step"
    )
    assert gist("exit 0 after 0.2s\n") == "exit 0 after 0.2s; it printed nothing"
    assert gist("exit 3\nusage error: the following arguments are required: file\n") == (
        "exit 3, usage error: the following arguments are required: file"
    )
    assert gist("exit 137\n\nthe container was stopped\n") == (
        "exit 137; the container was stopped"
    )
    assert gist(f"Wrote a.py (40 characters).\n\nRan a.py: {RUN_FAIL}") == (
        "Wrote a.py (40 characters). Ran a.py: exit 1 after 1.8s; TypeError: box() missing 1 "
        "required positional argument: 'height'"
    )
    assert gist("Replaced lines in `a.py`.\nIt now reads:\n  1| x = 1") == (
        "Replaced lines in `a.py`."
    )
    long = gist("exit 1\nverdict: FAIL: " + "x" * 1000)
    assert len(long) < 320 and long.endswith("…")


class Replies:
    """Tools that answer with the text a test gives each call, by name."""

    specs = [tools.specs()[0]]

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies

    def call(self, name: Any, raw_args: Any) -> Judged:
        if name == "stop":
            return Judged("stop", "ok", "stopped", ending=Ending("blocked", {"reason": "x"}))
        return Judged(str(name), "ok", self.replies.get(str(name), f"{name} did it"))


def test_compaction_shrinks_an_older_failure_to_its_verdict_and_keeps_the_latest_whole(
    tmp_path: Path,
) -> None:
    model = Scripted(
        [tool("bench", command="capture frame.step")],
        [tool("run", path="a.py")],
        [tool("bench", command="capture frame.step")],
        [tool("run", path="a.py")],
        [tool("x")],
        [tool("stop")],
    )
    s = Session(
        tools=Replies({"bench": BENCH_FAIL, "run": RUN_FAIL}),
        send=model,
        alias="crew-code-think",
        events=tmp_path / "events.jsonl",
        sleep=lambda _: None,
    )
    s.begin({"role": "system", "content": "sys"}, {"role": "user", "content": "go"})
    s.go()
    s.compact()
    replies = [m["content"] for m in s.messages if m["role"] == "tool"]
    assert replies[0] == COMPACTED.format(what=f"bench: {gist(BENCH_FAIL)}")
    assert replies[1] == COMPACTED.format(what=f"run: {gist(RUN_FAIL)}")
    assert replies[2] == BENCH_FAIL, "the latest bench answer stays whole"
    assert replies[3] == RUN_FAIL, "the latest run's reply stays whole"


# Each check's tries, in the record


NEED = "capture frame.step --min-lip 1.2"


def test_the_record_keeps_each_checks_tries_against_the_needs_command(tmp_path: Path) -> None:
    model = Scripted(
        [tool("bench", command=f"{NEED} --layer 0.3")],
        [tool("bench", command="capture frame.step --min-lip 0.35")],
        [tool("bench", command="thin frame.step")],
        [tool("bench", command="thin frame.step --nozzle 0.6")],
        [tool("bench", command="capture frame.step --pocket-z")],  # a wrong command
        [tool("bench", command=NEED)],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    run.state.needs = needs.dump(
        [Need("held", "held", "request", "bench", command=NEED, verdicts=["OK"])]
    )
    run.state.needs_open = False
    box: Any = Answers(run.work, 1, 1, 0, 1, 3, 0)
    run.tools.box = box
    assert run.drive().name == "blocked"
    kept = (run.work / "record" / "CHECKS.md").read_text()
    assert kept == (
        f"{nudge.CHECKS_HEADING}\n\n{nudge.CHECKS_ABOUT}\n\n"
        "## `capture` on `frame.step`\n"
        f"The needs: `held` runs it as `{NEED}`.\n"
        "- call 1, `--layer 0.3` (the need: none): FAIL: try 1, frame.step --min-lip 1.2 "
        "--layer 0.3\n"
        "- call 2, `--min-lip 0.35` (the need: `--min-lip 1.2`): FAIL: try 2, frame.step "
        "--min-lip 0.35\n"
        "- call 6, as the need runs it: OK: try 6, frame.step --min-lip 1.2\n"
        "\n"
        "## `thin` on `frame.step`\n"
        "No need runs it: each try is against its first.\n"
        "- call 3, the first try, no options: OK: try 3, frame.step\n"
        "- call 4, `--nozzle 0.6` (first try: none): FAIL: try 4, frame.step --nozzle 0.6\n"
    )
    shorter = nudge.record(run._bench_tries(), run._wanted(), last=1)
    assert "- (2 earlier tries: `record/CHECKS.md`)" in shorter
    assert "call 1," not in shorter and "call 6," in shorter


def test_a_new_conversation_after_context_full_is_given_each_checks_tries(
    tmp_path: Path,
) -> None:
    model = Scripted(
        [tool("bench", command="capture frame.step --min-lip 1.2")],
        [tool("bench", command="capture frame.step --min-lip 0.35")],
        [tool("blocked", reason="after the new conversation")],
        prompt_tokens=10_000,
    )
    run = Run.start(
        tmp_path, "r", "A block.", [], model, Limits(compact_at=500), sleep=lambda _: None
    )
    box: Any = Answers(run.work, 1, 1)
    run.tools.box = box
    assert run.drive().name == "blocked"
    assert run.state.restarts == 2
    first = model.bodies[-1]["messages"][1]["content"]
    assert "An earlier conversation about this ran out of room" in first
    assert nudge.CHECKS_HEADING in first
    assert "- call 2, `--min-lip 0.35` (first try: `--min-lip 1.2`): FAIL" in first
    assert first.index(nudge.CHECKS_HEADING) < first.index("An earlier conversation")


# An edit whose lines aren't there


def test_an_edit_says_how_its_lines_differ_from_the_files() -> None:
    text = "def f():\n    x = 1\n    y = 2\n    z = 3\n    return x\n"
    indent = tools.refusal("a.py", text, "     x = 1\n     y = 2", "x = 9")
    assert indent is not None and indent[0] == "edit.closest.indent"
    assert (
        "Those lines are in `a.py` at line 2, indented otherwise: line 2 starts with 4 spaces, "
        "and yours with 5 spaces; line 3 starts with 4 spaces, and yours with 5 spaces."
    ) in indent[1]
    apart = tools.refusal("a.py", text, "    x = 1\n    return x", "x = 9")
    assert apart is not None and apart[0] == "edit.closest.apart"
    assert "lines 3-4 come between them, and you left them out" in apart[1]
    words = tools.refusal("a.py", text, "    x = 1\n    y = 7\n    z = 3", "x = 9")
    assert words is not None and words[0] == "edit.closest"
    assert "Of yours, `y = 7` is line 3's `y = 2`." in words[1]
    assert "The closest block starts at line 2" in words[1]


def test_an_edit_says_when_its_file_changed_since_the_model_had_it_and_what_changed_it(
    tmp_path: Path,
) -> None:
    script = "x = 1\ny = 2\n"
    model = Scripted(
        [tool("write", path="a.py", content=script)],
        [tool("run", path="make.py")],
        [tool("edit", path="a.py", old="y = 2", new="y = 3")],
        [tool("read", path="a.py")],
        [tool("edit", path="a.py", old="y = 2", new="y = 3")],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    (run.work / "make.py").write_text("# writes a.py\n")

    class Rewrites:
        """A box whose `run` rewrites `a.py`, as a script that writes another might."""

        def python(self, path: str, *args: Any, **kwargs: Any) -> Ran:
            (run.work / "a.py").write_text("x = 1\ny = 5\n")
            return Ran(0, "", 0.1)

    box: Any = Rewrites()
    run.tools.box = box
    assert run.drive().name == "blocked"
    told = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]
    refused = told[2]
    assert refused["outcome"] == "failed" and refused["why"] == "edit.closest"
    assert refused["reply"].startswith(
        "`a.py` has changed since you last had it whole, at call 1: call 2's `run` of `make.py` "
        "changed it. `read` it before you edit it again.\n\nThose lines aren't in `a.py`."
    )
    assert refused["detail"] == {"changed_since": [2, "`run` of `make.py`"]}
    again = told[4]
    assert again["why"] == "edit.closest"
    assert "has changed since" not in again["reply"], "a whole read is having it again"
    assert json.loads(json.dumps(run.tools.known))["a.py"][1] == 4
