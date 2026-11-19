"""Compaction's line from the window, and a call refused as over it (mqucifer/print-shop#178).

Every model here is scripted, and LiteLLM's model info is a mock: nothing calls a model or LiteLLM.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from print_shop.rounds import send, transcript
from print_shop.rounds.run import Run, summarise
from print_shop.rounds.send import OverWindow, Sender
from print_shop.rounds.session import (
    COMPACT_AT,
    ESTIMATE_MARGIN,
    MAX_TOKENS,
    WINDOW,
    Limits,
    Session,
    compact_line,
)
from tests.rounds_fakes import Scripted, tool
from tests.test_rounds import Echo

# The line


def test_the_line_is_the_window_less_the_answer_less_the_margin() -> None:
    assert compact_line(262_144, 65_536, 8_000) == 188_608
    assert (WINDOW, MAX_TOKENS, ESTIMATE_MARGIN) == (262_144, 65_536, 8_000)
    assert COMPACT_AT == Limits().line() == 188_608
    assert Limits(window=200_000).line() == 200_000 - 65_536 - 8_000
    assert Limits(max_tokens=32_768, estimate_margin=4_000).line() == 262_144 - 32_768 - 4_000
    assert Limits(compact_at=500).line() == 500, "a line that's set is the line"
    assert Limits().recorded()["compact_at"] == 188_608, "the run's record says the line drawn"


#: A small window, so a short conversation reaches its line: 100,000 less 60,000 less 10,000.
SMALL = {"window": 100_000, "max_tokens": 60_000, "estimate_margin": 10_000}
LINE = 30_000


def at(tmp_path: Path, model: Scripted, prompt: int) -> Session:
    """A session whose next estimate is `prompt`: one turn with long reasoning before the newest,
    which compaction cuts, and the last prompt measured as `prompt` on what's there now."""
    s = Session(
        tools=Echo(),
        send=model,
        alias="crew-code-think",
        events=tmp_path / "events.jsonl",
        limits=Limits(**SMALL),
        sleep=lambda _: None,
    )
    s.begin({"role": "system", "content": "sys"}, {"role": "user", "content": "go"})
    for n, thought in ((1, "r" * 40_000), (2, "short")):
        s.messages.append(
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": thought,
                "tool_calls": [
                    {
                        "id": f"c{n}",
                        "type": "function",
                        "function": {"name": "x", "arguments": "{}"},
                    }
                ],
            }
        )
        s.messages.append({"role": "tool", "tool_call_id": f"c{n}", "content": "x did it"})
    s.last_chars = s._chars()
    s.last_prompt = prompt
    assert not prompt or s.estimate() == prompt
    return s


def kinds(s: Session, kind: str) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(s.events) if e["kind"] == kind]


def test_a_conversation_isnt_compacted_before_the_line(tmp_path: Path) -> None:
    assert Limits(**SMALL).line() == LINE
    s = at(tmp_path, Scripted([tool("stop")]), LINE - 1)
    assert s.go().name == "blocked"
    assert s.compactions == 0 and not kinds(s, "compacted")


def test_a_conversation_is_compacted_at_the_line_and_goes_on(tmp_path: Path) -> None:
    model = Scripted([tool("stop")])
    s = at(tmp_path, model, LINE)
    assert s.go().name == "blocked"
    compacted = kinds(s, "compacted")
    assert len(compacted) == 1 and compacted[0]["estimated"] == LINE
    assert compacted[0]["estimated_after"] < LINE
    assert "r" * 40_000 not in json.dumps(model.bodies[0]["messages"])


def test_each_call_keeps_its_estimate_its_prompt_and_its_time(tmp_path: Path) -> None:
    s = at(tmp_path, Scripted([tool("stop")], prompt_tokens=12_345), LINE - 1)
    s.go()
    (call,) = kinds(s, "call")
    assert call["estimated"] == LINE - 1
    assert call["usage"]["prompt_tokens"] == 12_345
    assert call["seconds"] >= 0 and call["sent_chars"] > 0


# A call over the window after all


def over(request: httpx.Request | None = None) -> OverWindow:
    request = request or httpx.Request("POST", "http://x")
    return OverWindow("over", request=request, response=httpx.Response(400))


def test_a_call_over_the_window_is_compacted_and_made_again(tmp_path: Path) -> None:
    model = Scripted([tool("stop")])
    model.failures = [over()]
    s = at(tmp_path, model, LINE - 5_000)
    assert s.go().name == "blocked", "it goes on, not call_failed"
    assert len(model.bodies) == 2
    assert "r" * 40_000 in json.dumps(model.bodies[0]["messages"]), "under the line, not compacted"
    assert "r" * 40_000 not in json.dumps(model.bodies[1]["messages"]), "compacted, then again"
    (refused,) = kinds(s, "over_window")
    assert refused["n"] == 1 and refused["again"] is True and refused["said"] == "the server"
    assert (refused["estimated"], refused["limit"], refused["window"]) == (
        LINE - 5_000,
        LINE,
        100_000,
    )
    (compacted,) = kinds(s, "compacted")
    assert compacted["over_window"] == 1 and compacted["before_call"] == 2
    for n, body in enumerate(model.bodies, 1):
        assert transcript.rebuild(s.events, n)["messages"] == body["messages"], n


def test_a_call_over_the_window_twice_is_context_full(tmp_path: Path) -> None:
    model = Scripted([tool("stop")])
    model.failures = [over(), over()]
    s = at(tmp_path, model, LINE - 5_000)
    ending = s.go()
    assert ending.name == "context_full"
    assert ending.said["over_window"] == 2 and ending.said["status"] == 400
    assert [e["again"] for e in kinds(s, "over_window")] == [True, False]


def test_a_400_estimated_over_the_line_is_compacted_and_made_again(tmp_path: Path) -> None:
    """A conversation's first call isn't compacted before it, so its estimate can be over."""
    request = httpx.Request("POST", "http://x")
    bad = httpx.HTTPStatusError("x", request=request, response=httpx.Response(400))
    model = Scripted([tool("stop")])
    model.failures = [bad]
    s = at(tmp_path, model, 0)
    s.limits = Limits(compact_at=10)
    assert s.go().name in ("blocked", "context_full")
    (refused,) = kinds(s, "over_window")
    assert refused["said"] == "the estimate"
    assert kinds(s, "compacted")


# LiteLLM: the window, and a 400 that says it's over


def info(*entries: dict[str, Any], status: int = 200) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/model/info"), "no model call"
        return httpx.Response(status, json={"data": list(entries)})

    return httpx.MockTransport(answer)


def deployed(alias: str = "crew-code-think", **model_info: Any) -> dict[str, Any]:
    return {
        "model_name": alias,
        "litellm_params": {"model": "openai/served", "api_base": "http://served.example/v1"},
        "model_info": {"id": "abc", **model_info},
    }


@pytest.mark.parametrize(
    ("entries", "status", "window", "said"),
    [
        ([deployed(max_input_tokens=262_144)], 200, 262_144, "model_info.max_input_tokens"),
        ([deployed(max_input_tokens=None, max_tokens=None)], 200, None, "doesn't give"),
        ([deployed("other", max_input_tokens=1)], 200, None, "has no `crew-code-think`"),
        ([], 403, None, "LiteLLM answered 403"),
        (
            [deployed(max_input_tokens=262_144), deployed(max_input_tokens=131_072)],
            200,
            131_072,
            "the least of its deployments' 131072, 262144",
        ),
    ],
)
def test_the_window_is_read_from_litellms_model_info(
    entries: list[dict[str, Any]], status: int, window: int | None, said: str
) -> None:
    found, where = Sender(key="k", transport=info(*entries, status=status)).window(
        "crew-code-think"
    )
    assert found == window and said in where
    assert "served.example" not in where


@pytest.mark.parametrize(
    ("body", "over_window"),
    [
        ('{"error": {"message": "litellm.ContextWindowExceededError: SECRET"}}', True),
        ("This model's maximum context length is 262144 tokens. SECRET", True),
        ('{"error": {"message": "invalid tool schema SECRET"}}', False),
    ],
)
def test_a_400_over_the_window_is_named_and_its_body_isnt_kept(
    body: str, over_window: bool
) -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=body)

    with pytest.raises(httpx.HTTPStatusError) as caught:
        Sender("k", transport=httpx.MockTransport(answer))({"messages": []})
    assert isinstance(caught.value, OverWindow) is over_window
    assert "SECRET" not in str(caught.value)


def test_a_500_saying_context_isnt_over_the_window() -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="context length")

    with pytest.raises(httpx.HTTPStatusError) as caught:
        Sender("k", transport=httpx.MockTransport(answer))({"messages": []})
    assert not isinstance(caught.value, OverWindow)


# A run draws its line from the window it reads


class Windowed(Scripted):
    def __init__(self, *turns: Any, window: int | None) -> None:
        super().__init__(*turns)
        self.read = window

    def window(self, alias: str) -> tuple[int | None, str]:
        return self.read, f"LiteLLM's /model/info for `{alias}`, model_info.max_input_tokens"


def test_a_run_draws_its_line_from_the_window_litellm_gives(tmp_path: Path) -> None:
    model = Windowed([tool("blocked", reason="x")], window=200_000)
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    assert run.session.limits.window == 200_000
    assert run.session.limits.line() == 200_000 - MAX_TOKENS - ESTIMATE_MARGIN
    run.drive()
    (said,) = [e for e in transcript.events_of(run.session.events) if e["kind"] == "window"]
    assert said["window"] == 200_000 and said["compact_at"] == 126_464
    assert "max_input_tokens" in said["said"]
    limits = json.loads(run.ledger.rows("SELECT limits FROM runs")[0]["limits"])
    assert (limits["window"], limits["compact_at"]) == (200_000, 126_464)
    assert summarise(run.folder)["compact_at"] == 126_464

    again = Run.load(run.folder, Windowed(window=262_144))
    assert again.session.limits.line() == COMPACT_AT, "each process reads it again"


@pytest.mark.parametrize("window", [None, 70_000])
def test_a_run_without_a_usable_window_keeps_the_named_one(
    tmp_path: Path, window: int | None
) -> None:
    run = Run.start(
        tmp_path, "r", "A block.", [], Windowed(window=window), sleep=lambda _: None
    )
    assert run.session.limits.line() == COMPACT_AT
    (said,) = [e for e in transcript.events_of(run.session.events) if e["kind"] == "window"]
    assert said["window"] == WINDOW and "`session.WINDOW` instead" in said["said"]


def test_a_refused_call_is_in_the_timeline_and_the_index(tmp_path: Path) -> None:
    from print_shop.rounds.after_action import index

    model = Scripted([tool("blocked", reason="x")])
    model.failures = [over()]
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    assert run.drive().name == "blocked"
    rows = run.ledger.rows("SELECT kind, at_call, said FROM timeline WHERE kind='over_window'")
    assert [(r["at_call"], json.loads(r["said"])["again"]) for r in rows] == [(1, True)]
    said = "call 1 was refused as over the model's window, compacted and made again"
    assert any(said in line for line in index(run.ledger, "r"))
    assert summarise(run.folder)["over_window"] == 1


# The report of what a longer context costs


def call_costs() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "call_costs.py"
    spec = importlib.util.spec_from_file_location("call_costs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_call_costs_gives_each_band_of_prompts_its_times(tmp_path: Path) -> None:
    def call(n: int, prompt: int, seconds: float, answer: int = 100) -> dict[str, Any]:
        return {
            "kind": "call",
            "n": n,
            "seconds": seconds,
            "usage": {"prompt_tokens": prompt, "completion_tokens": answer},
            "stream": {"first_chunk_s": seconds / 10},
        }

    events = [
        {"kind": "request", "n": 1, "from": 0},
        call(1, 10_000, 10.0),
        call(2, 20_000, 30.0),
        {"kind": "compacted", "estimated": 1},
        call(3, 30_000, 50.0, 300),
        {"kind": "over_window", "n": 4},
        {"kind": "request", "n": 5, "from": 0},
        call(5, 0, 1.0),
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    found = call_costs().costs(path)
    assert (found["calls"], found["compactions"], found["restarts"], found["over_window"]) == (
        4,
        1,
        1,
        1,
    )
    assert found["largest_prompt"] == 30_000
    first, second = found["bands"]
    assert (first["prompt_from"], first["calls"], first["median_s"]) == (0, 2, 20.0)
    assert first["p90_s"] == 28.0 and first["median_first_chunk_s"] == 2.0
    assert (second["prompt_from"], second["calls"], second["median_answer_tokens"]) == (
        25_000,
        1,
        300,
    )
    assert "25k- 50k" in call_costs().said(found)


def test_send_reads_nothing_but_the_model_info() -> None:
    """The window's read is LiteLLM's own answer: no model call, and no address kept."""
    assert send.window_in(200, b'{"data": []}', "a")[0] is None
