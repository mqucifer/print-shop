"""A job of one part, as it was before a job could have several (mqucifer/print-shop#109).

The Sponsor prints one part at a time from main, so that flow is held byte for byte: what
`print_job.py` shows, the gate, the states and outcome card it publishes, every row it writes in
the ledger, the files in the job's folder, and the G-code it sends. Ids, times and temporary
paths are the only things replaced.

It runs twice: with the questions asked all together (`transcript.txt`), and as `print_job.py`
asks them, print-shop's two while the model reads (`waiting.txt`, mqucifer/print-shop#208). Both
files in `tests/data/one_part/` were written by this test run on 2662e23, main without that
change. A change that means to alter one part's flow writes them again, and the diff shows what
it altered:

    PRINT_SHOP_WRITE_TRANSCRIPT=1 uv run pytest tests/test_one_part_unchanged.py -m "not slicer"
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from print_shop.core.mqtt import (
    gate_topic,
    outcome_result_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
    state_topic,
)
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from tests.fakes import CI_PROFILES, DATA, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import _model, _questions
from tests.test_runtime import settings  # noqa: F401  (the fixture)

TRANSCRIPTS = DATA / "one_part"


def _plain(text: str, tmp_path: Path) -> str:
    """`text` without what differs from one run to the next."""
    for path in (tmp_path.resolve(), tmp_path):
        text = text.replace(str(path), "<tmp>")
    text = re.sub(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<job>", text)
    text = re.sub(r"(after |since \()\d+(\.\d+)? s", r"\1<n> s", text)
    # How long the model's call took (mqucifer/print-shop#242), as the record keeps it.
    text = re.sub(r'"seconds": ?\d+(\.\d+)?', '"seconds":<n>', text)
    text = re.sub(r"(still working on settings \()\d+ s", r"\1<n> s", text)
    text = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?", "<time>", text)
    return re.sub(r"answer by   \d\d:\d\d \(\d+ min left\)", "answer by   <time>", text)


def _short(text: str) -> str:
    """A long value as its size and hash: any change in it still shows."""
    if len(text) <= 1500:
        return text
    return f"<{len(text)} characters, sha256 {hashlib.sha256(text.encode()).hexdigest()}>"


def _ledger(path: Path, tmp_path: Path) -> list[str]:
    db = sqlite3.connect(path)
    lines = []
    for (table,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        found = db.execute(f"SELECT * FROM {table}")
        columns = [c[0] for c in found.description]
        for row in found.fetchall():
            lines.append(f"[{table}]")
            lines += [f"  {column} = {_short(_plain(str(value), tmp_path))}"
                      for column, value in zip(columns, row, strict=True)]
    db.close()
    return lines


@pytest.mark.parametrize("name", ["transcript", "waiting"])
def test_one_part_is_byte_for_byte_what_it_was(
    name: str,
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    process = tmp_path / "process.json"
    process.write_text(json.dumps({**json.loads(CI_PROFILES.process.read_text()),
                                   "wall_loops": "2"}))
    profiles = CI_PROFILES.model_copy(update={"process": process})
    asks_meanwhile = name == "waiting"
    second_shown, come = threading.Event(), threading.Event()
    # The model's settings come beside the review (mqucifer/print-shop#242): once its prompt is
    # open, every run alike, and the person waits for them and adds them with Enter, then ok.
    review_open, settled = threading.Event(), threading.Event()
    prompts: list[str] = []
    bridge = MemoryBridge()

    def questions(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if asks_meanwhile:
            # The model's land while the second answer is open, every run alike.
            second_shown.wait(20)
            come.set()
        return _questions(system, user, schema)

    def proposes(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        review_open.wait(20)
        try:
            return _model(system, user, schema)
        finally:
            settled.set()
    printer = FramePrinter(after_start=["printing", "completed"])

    def build_run(**given: Any) -> SimpleNamespace:
        ledger = Ledger(tmp_path / "ledger.sqlite")
        daemon = Daemon(
            cfg=CFG, profiles=profiles, workdir=jobs, printer=printer, watch_printer=printer,
            watch_poll_s=0.05, slicer=RecordedSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
            proposer=Proposer(ask=proposes, ask_questions=questions),
            asker=given["asker"], reviewer=given["reviewer"],
            **({"waiting": given["waiting"]} if asks_meanwhile else {}),
        )
        return SimpleNamespace(daemon=daemon, ledger=ledger)

    # Asked meanwhile, the model's closer choices on purpose come after the two, and Enter keeps.
    typed = iter(["1", "1", "", "", "ok"] if asks_meanwhile else ["1", "1", "", "ok"])

    def person_types(prompt: str = "") -> str:
        prompts.append(prompt)
        if asks_meanwhile and len(prompts) == 2:
            second_shown.set()
            come.wait(20)
            time.sleep(0.3)  # what landed is said under the open prompt
        if prompt == "> " and not review_open.is_set():
            review_open.set()
            assert settled.wait(20), "the model's settings never came"
            time.sleep(0.3)  # what came is said under the open prompt
        return next(typed)
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setattr(script, "WORK_DIR", jobs)
    monkeypatch.setattr(script, "build_run", build_run)
    monkeypatch.setattr(script, "time", SimpleNamespace(sleep=lambda _: time.sleep(0.02)))
    monkeypatch.setattr("builtins.input", person_types)

    gates: list[dict[str, Any]] = []
    done = threading.Event()

    def person() -> None:
        pressed = False
        while not done.is_set():
            gate = json.loads(bridge.retained.get(gate_topic(CFG), "{}"))
            if not pressed and gate.get("pending"):
                gates.append(gate)
                time.sleep(0.3)  # the terminal has shown the gate
                bridge.press(CFG, "approve")
                pressed = True
            if json.loads(bridge.retained.get(outcome_topic(CFG), "{}")).get("pending"):
                bridge.deliver(outcome_result_topic(CFG), "success")
                bridge.deliver(outcome_symptom_topic(CFG), "none")
                bridge.deliver(outcome_submit_topic(CFG), "")
                return
            time.sleep(0.02)

    thread = threading.Thread(target=person, daemon=True)
    thread.start()
    part = write_stl("cube", tmp_path)
    try:
        code = script.main([str(part), "--nozzle", "0.4", "--filament", "Elegoo PLA",
                            "--settings", str(settings)])
    finally:
        done.set()
        thread.join(timeout=5)
    assert code == 0

    (started,) = [name for verb, name in printer.calls if verb == "start"]
    sent = next(jobs.glob(f"*/{started}"))
    cards = [p for p in bridge.on(outcome_topic(CFG)) if p.get("pending")][:1]
    said = [
        "== print_job.py",
        _plain(capsys.readouterr().out, tmp_path).rstrip("\n"),
        "== the gate",
        *(_plain(json.dumps(g), tmp_path) for g in gates),
        "== the states",
        *(_plain(json.dumps(s), tmp_path) for s in bridge.on(state_topic(CFG))),
        "== the outcome's card",
        *(_plain(json.dumps(c), tmp_path) for c in cards),
        "== the ledger",
        *_ledger(tmp_path / "ledger.sqlite", tmp_path),
        "== the job's folder",
        *sorted(_plain(str(p.relative_to(sent.parent)), tmp_path)
                for p in sent.parent.rglob("*") if p.is_file()),
        "== the G-code sent",
        hashlib.sha256(sent.read_bytes()).hexdigest(),
    ]
    text = "\n".join(said) + "\n"
    if os.environ.get("PRINT_SHOP_WRITE_TRANSCRIPT") == "1":
        TRANSCRIPTS.mkdir(exist_ok=True)
        (TRANSCRIPTS / f"{name}.txt").write_text(text)
    assert text == (TRANSCRIPTS / f"{name}.txt").read_text()
