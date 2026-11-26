"""A call whose words are only a compaction's note is refused, at every tool, as `write` refuses
one, and the run goes on (mqucifer/print-shop#329). On a lamp run the model sent `questions` as
`(compacted: 1659 characters)`, copied from the notes earlier calls had become, and the run stopped
for a person with an empty question. Every request, part and question here is made up for the
test, and the scripted model stands in for the served one: no model call anywhere."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds.session import (
    COMPACTED,
    EARLIER_NOTE,
    SHOWN_BEFORE,
    Ending,
    Judged,
    noted,
)
from tests.rounds_fakes import Scripted, tool
from tests.test_asking import Desk, events, session
from tests.test_intake import FREE, PLAN_ASK, SENT, begin, to_the_plan, told

NOTE = COMPACTED.format(what="1659 characters")


class Stopping(Desk):
    """As `Desk`, and each tool that stops the run for a person or ends it does."""

    def call(self, name: Any, raw: Any) -> Judged:
        if name in ("questions", "ask", "done", "set_aside", "after_action"):
            self.called.append((name, raw))
            return Judged(str(name), "ok", "", ending=Ending(str(name), {}))
        return super().call(name, raw)


# At the session, for every tool


@pytest.mark.parametrize(
    ("name", "args", "key"),
    [
        ("questions", {"questions": NOTE}, "questions"),
        ("ask", {"question": NOTE}, "question"),
        ("ask", {"question": "?", "variants": [{"name": "A", "about": NOTE}]}, "variants[0].about"),
        ("send_ask", {"says": NOTE}, "says"),
        ("done", {"script": "make.py", "part": ["a.step"], "closing": NOTE}, "closing"),
        ("done", {"script": "make.py", "part": ["a.step"], "fix_shape": NOTE}, "fix_shape"),
        ("set_aside", {"check": "thin a.step", "why": NOTE}, "why"),
        ("blocked", {"reason": NOTE}, "reason"),
        ("fact", {"name": "gap", "text": NOTE}, "text"),
        ("after_action", {"lessons": [{"text": NOTE}], "scores": []}, "lessons[0].text"),
    ],
)
def test_words_that_are_only_a_note_are_refused_and_the_run_goes_on(
    tmp_path: Path, name: str, args: dict[str, Any], key: str
) -> None:
    model = Scripted([tool(name, **args)], [tool("blocked", reason="Out of turns.")])
    desk = Stopping()
    s = session(tmp_path, model, desk)
    ended = s.go()
    assert (ended.name, ended.said) == ("blocked", {}), "the refused call stopped nothing"
    assert [n for n, _ in desk.called] == ["blocked"], "the tool never saw it"
    tried = events(s, "tool")[0]
    assert (tried["outcome"], tried["why"]) == ("failed", "compaction_note"), "as `write`'s"
    reply = model.bodies[1]["messages"][-1]["content"]
    assert f"`{key}` holds only a compaction note, `{NOTE}`, not your words." in reply
    assert f"`{name}` did nothing and nothing went to anyone" in reply


@pytest.mark.parametrize(
    "words",
    [
        EARLIER_NOTE,
        SHOWN_BEFORE,
        COMPACTED.format(what="done: exit 1, verdict: FAIL (gap 0.2 mm)"),
        f"  {NOTE}\n\n{COMPACTED.format(what='80 characters')}\n",
    ],
)
def test_each_note_the_session_writes_is_refused_as_words(tmp_path: Path, words: str) -> None:
    model = Scripted([tool("questions", questions=words)], [tool("blocked", reason="x")])
    desk = Stopping()
    s = session(tmp_path, model, desk)
    assert s.go().name == "blocked"
    assert events(s, "tool")[0]["why"] == "compaction_note"


@pytest.mark.parametrize(
    "words",
    [
        f"The bench said {NOTE} for my last try. How wide should the tab be?",
        f"How wide should the tab be?\nEarlier calls read {NOTE}, so I'm asking.",
        "Is the gap (compacted: or not) what you meant?",
    ],
)
def test_words_that_quote_a_note_among_others_go_out(tmp_path: Path, words: str) -> None:
    model = Scripted([tool("questions", questions=words)], [tool("blocked", reason="x")])
    desk = Stopping()
    s = session(tmp_path, model, desk)
    assert s.go().name == "questions"
    assert [n for n, _ in desk.called] == ["questions"]


def test_a_file_keeps_its_stricter_check_and_its_own_reply(tmp_path: Path) -> None:
    """A file's text is refused for a note on any line, words only for nothing but notes."""
    text = f"x = 1\n{NOTE}\n"
    assert noted(text, anywhere=True) == NOTE
    assert noted(text, anywhere=False) is None
    model = Scripted([tool("write", path="a.py", content=text)], [tool("blocked", reason="x")])
    desk = Desk()
    s = session(tmp_path, model, desk)
    s.go()
    reply = model.bodies[1]["messages"][-1]["content"]
    assert reply.startswith("Not written: `content` holds a compaction note")
    assert [n for n, _ in desk.called] == ["blocked"]


def test_a_picture_note_on_a_files_line_is_refused_too(tmp_path: Path) -> None:
    model = Scripted(
        [tool("write", path="a.py", content=f"x = 1\n{SHOWN_BEFORE}\n")],
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model, Desk())
    s.go()
    assert events(s, "tool")[0]["why"] == "compaction_note"


# At the run


def test_questions_that_are_only_a_note_dont_stop_the_run(tmp_path: Path) -> None:
    run = begin(tmp_path, FREE, [tool("questions", questions=NOTE)])
    assert run.drive().name == "blocked", "it went on to its next turn"
    assert told(run)[0]["why"] == "compaction_note"
    assert run.state.stop is None
    assert not (run.folder / "questions").exists(), "nothing went to a person"


def test_a_question_quoting_a_note_in_a_sentence_goes_out(tmp_path: Path) -> None:
    words = f"Earlier calls read {NOTE}: how wide should the tab be?"
    run = begin(tmp_path, FREE, [tool("questions", questions=words)])
    assert run.drive().name == "questions"
    assert (run.folder / "questions" / "1.md").read_text().startswith(words)


def test_says_that_is_only_a_note_is_refused_and_the_sheet_goes_once_said(
    tmp_path: Path,
) -> None:
    run = to_the_plan(tmp_path, [PLAN_ASK], [tool("send_ask", says=NOTE)], SENT)
    assert run.drive().name == "ask"
    sent = [t for t in told(run) if t["name"] == "send_ask"]
    assert [t["outcome"] for t in sent] == ["failed", "ok"]
    assert sent[0]["why"] == "compaction_note"
