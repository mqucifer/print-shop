"""The questions call is streamed, so that it can be ended (mqucifer/print-shop#208).

On the calls of 2026-11-15 to 22, every cut-off was whitespace padding inside the JSON answer
to the token limit, and each call the client gave up on kept generating on the server. The
server here is a real one on a loopback port of its own, so that a closed connection is a
closed connection. No model is called, and every answer is made up.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from print_shop.expert.client import (
    RUNAWAY_CHARS,
    CutOff,
    LLMConfig,
    Stopped,
    TimedOut,
    chat_with_tools,
    stream_json_call,
)
from print_shop.setup import Proposer
from print_shop.setup.features import measure
from print_shop.setup.loop import LOOP_ANSWER_CHARS
from print_shop.setup.questions import ANSWER_CHARS, STOCK
from tests.fakes import STORE_PROFILES
from tests.fixtures import write_stl

KEY = "sk-made-up-key"
SCHEMA = {"name": "made_up", "schema": {"type": "object"}}
WAIT_S = 10.0

#: What the server sends: each chunk, with a pause before it.
Chunks = Callable[[], Iterator[tuple[float, dict[str, Any]]]]


def content(text: str, pause: float = 0.0) -> tuple[float, dict[str, Any]]:
    return pause, {"model": "fake", "choices": [{"delta": {"content": text}}]}


def thinking(pause: float = 0.0) -> tuple[float, dict[str, Any]]:
    return pause, {"model": "fake", "choices": [{"delta": {"reasoning_content": "hm" * 9000}}]}


def finish(reason: str = "stop") -> list[tuple[float, dict[str, Any]]]:
    return [
        (0.0, {"choices": [{"delta": {}, "finish_reason": reason}]}),
        (0.0, {"choices": [], "usage": {
            "prompt_tokens": 2995, "completion_tokens": 900,
            "completion_tokens_details": {"reasoning_tokens": 700}}}),
    ]


class Server:
    def __init__(self) -> None:
        self.chunks: Chunks = lambda: iter(())
        self.status = 200
        self.asked: list[dict[str, Any]] = []
        #: Set when the client closed the connection before the stream's end.
        self.hung_up = threading.Event()
        self.over = threading.Event()
        served = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                length = int(self.headers["Content-Length"])
                served.asked.append(json.loads(self.rfile.read(length)))
                self.send_response(served.status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                if served.status != 200:
                    self.wfile.write(b"refused")
                    return
                try:
                    for pause, chunk in served.chunks():
                        if served.over.wait(pause):
                            return
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError):
                    served.hung_up.set()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http.daemon_threads = True
        threading.Thread(target=self._http.serve_forever, args=(0.05,), daemon=True).start()

    def config(self, timeout_s: float = WAIT_S) -> LLMConfig:
        return LLMConfig(
            api_key=SecretStr(KEY), model="fake", max_tokens=16000, timeout_s=timeout_s,
            base_url=f"http://127.0.0.1:{self._http.server_address[1]}/v1",
        )

    def close(self) -> None:
        self.over.set()
        self._http.shutdown()
        self._http.server_close()


@pytest.fixture
def server() -> Iterator[Server]:
    made = Server()
    yield made
    made.close()


def call(server: Server, timeout_s: float = WAIT_S, stop: threading.Event | None = None) -> Any:
    return stream_json_call(server.config(timeout_s), "system", "user", SCHEMA,
                            answer_chars=ANSWER_CHARS, stop=stop)


def endlessly(text: str, pause: float = 0.002) -> Chunks:
    def chunks() -> Iterator[tuple[float, dict[str, Any]]]:
        while True:
            yield content(text, pause)

    return chunks


def test_an_answer_is_assembled_from_its_pieces_with_its_cost(server: Server) -> None:
    server.chunks = lambda: iter(
        [thinking(), content('{"questions"'), content(": []}"), *finish()])
    reply, info = call(server)
    assert reply == {"questions": []}
    assert (info.finish_reason, info.completion_tokens, info.reasoning_tokens) == ("stop", 900, 700)
    assert info.answer_chars == len('{"questions": []}'), "thinking isn't the answer"


def test_the_request_is_the_structured_one_streamed(server: Server) -> None:
    server.chunks = lambda: iter([content("{}"), *finish()])
    call(server)
    [sent] = server.asked
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["max_tokens"] == 16000


def test_a_whitespace_runaway_is_cut_and_the_server_is_let_go(server: Server) -> None:
    """The cut-offs' cause: after its first key the answer pads with whitespace."""

    def chunks() -> Iterator[tuple[float, dict[str, Any]]]:
        yield content('{"questions": [{"feature": "purpose",')
        yield from endlessly(" \n  \t" * 20)()

    server.chunks = chunks
    with pytest.raises(CutOff, match="ran on as whitespace") as cut:
        call(server)
    assert cut.value.info.finish_reason == "runaway"
    assert RUNAWAY_CHARS <= cut.value.info.whitespace_run < RUNAWAY_CHARS + 200
    assert server.hung_up.wait(WAIT_S), "the connection is closed, so generating can stop"


def test_the_longest_whitespace_a_finished_answer_had_is_not_cut(server: Server) -> None:
    server.chunks = lambda: iter([content('{"questions":' + " " * 1725), content("[]}"), *finish()])
    reply, info = call(server)
    assert reply == {"questions": []} and info.whitespace_run == 1725


def test_whitespace_is_counted_across_chunks(server: Server) -> None:
    half = " " * (RUNAWAY_CHARS // 2)
    server.chunks = lambda: iter([content("{"), content(half), content(half), *finish()])
    with pytest.raises(CutOff) as cut:
        call(server)
    assert cut.value.info.finish_reason == "runaway"


def test_the_answer_is_bounded_to_what_four_questions_need(server: Server) -> None:
    server.chunks = endlessly("words " * 100)
    with pytest.raises(CutOff) as cut:
        call(server)
    assert cut.value.info.finish_reason == "too long"
    assert ANSWER_CHARS < cut.value.info.answer_chars <= ANSWER_CHARS + 600
    assert server.hung_up.wait(WAIT_S)


def test_an_answer_at_the_token_limit_says_so_with_its_tokens(server: Server) -> None:
    server.chunks = lambda: iter([content('{"questions'), *finish("length")])
    with pytest.raises(CutOff, match=r"max_tokens=16000 after 900 tokens \(700 reasoning\)"):
        call(server)


def test_the_time_is_the_whole_calls_though_chunks_keep_coming(server: Server) -> None:
    server.chunks = endlessly("x", pause=0.02)
    started = time.monotonic()
    with pytest.raises(TimedOut) as late:
        call(server, timeout_s=0.4)
    assert time.monotonic() - started < 3
    assert late.value.limit_s == 0.4
    assert server.hung_up.wait(WAIT_S), "a call given up on doesn't keep its share of the host"


def test_stopping_ends_it_at_once_and_closes_the_connection(server: Server) -> None:
    server.chunks = endlessly("x", pause=0.02)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    started = time.monotonic()
    with pytest.raises(Stopped):
        call(server, stop=stop)
    assert time.monotonic() - started < 3
    assert server.hung_up.wait(WAIT_S)


def test_stopping_doesnt_wait_for_a_first_byte(server: Server) -> None:
    server.chunks = lambda: iter([content("{}", pause=WAIT_S)])
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    started = time.monotonic()
    with pytest.raises(Stopped):
        call(server, stop=stop)
    assert time.monotonic() - started < 3


def test_a_refusal_never_holds_the_key(server: Server) -> None:
    server.status = 500
    with pytest.raises(RuntimeError, match="returned 500") as refused:
        call(server)
    assert KEY not in str(refused.value)


def test_an_empty_answer_says_so(server: Server) -> None:
    server.chunks = lambda: iter([thinking(), *finish()])
    with pytest.raises(RuntimeError, match="empty answer"):
        call(server)


def test_an_error_inside_the_stream_says_so(server: Server) -> None:
    server.chunks = lambda: iter([(0.0, {"error": {"message": "made-up failure"}})])
    with pytest.raises(RuntimeError, match="made-up failure"):
        call(server)


# --------------------------------------------------------------------------
# The print loop's calls have the same cuts (mqucifer/print-shop#242)
# --------------------------------------------------------------------------


def called(arguments: str, name: str = "", id: str = "", index: int = 0,
           pause: float = 0.0) -> tuple[float, dict[str, Any]]:
    part = {"index": index, "function": {"name": name, "arguments": arguments}}
    if id:
        part["id"] = id
    return pause, {"model": "fake", "choices": [{"delta": {"tool_calls": [part]}}]}


def chat(server: Server, timeout_s: float = WAIT_S, stop: threading.Event | None = None) -> Any:
    return chat_with_tools(
        server.config(timeout_s), [{"role": "user", "content": "go"}],
        [{"type": "function", "function": {"name": "propose"}}],
        answer_chars=LOOP_ANSWER_CHARS, stop=stop)


def test_tool_calls_are_assembled_from_their_pieces(server: Server) -> None:
    server.chunks = lambda: iter([
        thinking(), content("Slicing it."),
        called("", "slice", "c1"), called('{"rotation":'), called("[0,0,0]}"),
        called("{}", "past_prints", "c2", index=1), *finish("tool_calls")])
    message, info = chat(server)
    assert message["content"] == "Slicing it."
    assert message["tool_calls"] == [
        {"id": "c1", "type": "function",
         "function": {"name": "slice", "arguments": '{"rotation":[0,0,0]}'}},
        {"id": "c2", "type": "function", "function": {"name": "past_prints", "arguments": "{}"}},
    ]
    assert info.finish_reason == "tool_calls"
    [sent] = server.asked
    assert sent["stream"] is True and sent["tool_choice"] == "auto"


def test_whitespace_running_on_inside_a_tool_calls_arguments_is_cut(server: Server) -> None:
    def chunks() -> Iterator[tuple[float, dict[str, Any]]]:
        yield called('{"settings": [', "propose", "c1")
        while True:
            yield called(" \n  " * 40, pause=0.002)

    server.chunks = chunks
    with pytest.raises(CutOff) as cut:
        chat(server)
    assert cut.value.info.finish_reason == "runaway"
    assert server.hung_up.wait(WAIT_S)


def test_a_tool_calls_arguments_are_bounded(server: Server) -> None:
    def chunks() -> Iterator[tuple[float, dict[str, Any]]]:
        while True:
            yield called("words " * 100, pause=0.002)

    server.chunks = chunks
    with pytest.raises(CutOff) as cut:
        chat(server)
    assert cut.value.info.finish_reason == "too long"


def test_a_loops_call_at_the_token_limit_is_cut_off(server: Server) -> None:
    server.chunks = lambda: iter([called('{"rot', "slice", "c1"), *finish("length")])
    with pytest.raises(CutOff) as cut:
        chat(server)
    assert cut.value.info.finish_reason == "length"


def test_a_loops_call_times_out_and_stops(server: Server) -> None:
    server.chunks = lambda: iter([called("{}", "slice", "c1", pause=WAIT_S)])
    with pytest.raises(TimedOut):
        chat(server, timeout_s=0.3)
    stop = threading.Event()
    threading.Timer(0.2, stop.set).start()
    started = time.monotonic()
    with pytest.raises(Stopped):
        chat(server, stop=stop)
    assert time.monotonic() - started < 3


def test_the_proposers_questions_use_the_bounded_stream(server: Server, tmp_path: Path) -> None:
    server.chunks = endlessly(" " * 100)
    asked = Proposer(llm=server.config()).questions(
        measure(write_stl("cube", tmp_path), 400.0), STORE_PROFILES)
    assert asked.questions == STOCK
    assert asked.call["finish_reason"] == "runaway"
    assert asked.failed == "was cut off as its answer ran on as blank space"
    assert server.asked[0]["response_format"]["json_schema"]["name"] == "intake_questions"
