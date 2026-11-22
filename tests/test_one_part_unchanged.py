"""A job of one part, as it was before a job could have several (mqucifer/print-shop#109).

The Sponsor prints one part at a time from main, so that flow is held byte for byte: what
`print_job.py` shows, the gate, the states and outcome card it publishes, every row it writes in
the ledger, the files in the job's folder, and the G-code it sends. Ids, times and temporary
paths are the only things replaced.

`tests/data/one_part/transcript.txt` was written by this test run on d597bd6, main without that
change. A change that means to alter one part's flow writes it again, and the diff shows
what it altered:

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
    command_topic,
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

TRANSCRIPT = DATA / "one_part" / "transcript.txt"


def _plain(text: str, tmp_path: Path) -> str:
    """`text` without what differs from one run to the next."""
    for path in (tmp_path.resolve(), tmp_path):
        text = text.replace(str(path), "<tmp>")
    text = re.sub(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<job>", text)
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


def test_one_part_is_byte_for_byte_what_it_was(
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
    bridge = MemoryBridge()
    printer = FramePrinter(after_start=["printing", "completed"])

    def build_run(**given: Any) -> SimpleNamespace:
        ledger = Ledger(tmp_path / "ledger.sqlite")
        daemon = Daemon(
            cfg=CFG, profiles=profiles, workdir=jobs, printer=printer, watch_printer=printer,
            watch_poll_s=0.05, slicer=RecordedSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
            proposer=Proposer(ask=_model, ask_questions=_questions),
            asker=given["asker"], reviewer=given["reviewer"],
        )
        return SimpleNamespace(daemon=daemon, ledger=ledger)

    typed = iter(["1", "1", "ok"])
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setattr(script, "WORK_DIR", jobs)
    monkeypatch.setattr(script, "build_run", build_run)
    monkeypatch.setattr(script, "time", SimpleNamespace(sleep=lambda _: time.sleep(0.02)))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))

    gates: list[dict[str, Any]] = []
    done = threading.Event()

    def person() -> None:
        pressed = False
        while not done.is_set():
            gate = json.loads(bridge.retained.get(gate_topic(CFG), "{}"))
            if not pressed and gate.get("pending"):
                gates.append(gate)
                time.sleep(0.3)  # the terminal has shown the gate
                bridge.deliver(command_topic(CFG), {"decision": "approve",
                                                    "job_id": gate["job_id"]})
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
        TRANSCRIPT.parent.mkdir(exist_ok=True)
        TRANSCRIPT.write_text(text)
    assert text == TRANSCRIPT.read_text()
