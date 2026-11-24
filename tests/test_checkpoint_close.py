"""The daemon's checkpointer is closed safely under jobs still writing to it.

Queued jobs prepare on threads of their own (mqucifer/print-shop#210), each writing its
checkpoints through the daemon's one SqliteSaver and its one connection. The daemon closed that
connection on exit with no lock, and a thread mid-write under it crashed the process: a
segmentation fault in CI, in langgraph's `put` and `put_writes`, a test after the queue's
daemon had closed. Closed under the saver's lock, a write that comes after is refused with an
error instead. The crash is native, so the stress runs in a process of its own and the test
reads its exit.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

#: Each round: a daemon entered, six threads writing checkpoints through it as queued jobs do,
#: and the daemon closed under them. Without the lock it crashed within a few rounds, every
#: run (3 of 3 here, 2026-11-24).
STRESS = textwrap.dedent("""
    import sys, tempfile, threading, uuid
    from pathlib import Path
    from langgraph.checkpoint.base import empty_checkpoint
    from print_shop.orchestrator.daemon import Daemon
    from tests.fakes import MemoryBridge
    from tests.test_job_e2e import CFG

    for _ in range(int(sys.argv[1])):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = MemoryBridge()
            daemon = Daemon(cfg=CFG, profiles=None, workdir=Path(tmp), printer=None,
                            bridge=lambda cfg: bridge, ledger=None)
            daemon.__enter__()
            saver, stop, refused = daemon.checkpointer, threading.Event(), []

            def write(n: int) -> None:
                config = {"configurable": {"thread_id": f"job-{n}", "checkpoint_ns": ""}}
                while not stop.is_set():
                    try:
                        put = saver.put(config, empty_checkpoint(),
                                        {"source": "loop", "step": 0, "parents": {}}, {})
                        saver.put_writes(put, [("x", "y" * 20000)], str(uuid.uuid4()))
                    except Exception as exc:  # the database closed: refused, not a crash
                        refused.append(type(exc).__name__)
                        return

            threads = [threading.Thread(target=write, args=(n,), daemon=True) for n in range(6)]
            for t in threads:
                t.start()
            threading.Event().wait(0.02)
            daemon.__exit__(None, None, None)
            stop.set()
            for t in threads:
                t.join(5)
            assert set(refused) <= {"ProgrammingError"}, refused
    print("closed safely")
""")


def test_the_daemon_closes_its_checkpointer_under_jobs_still_writing(tmp_path: Path) -> None:
    script = tmp_path / "stress.py"
    script.write_text(STRESS)
    checkout = Path(__file__).resolve().parents[1]
    done = subprocess.run(
        [sys.executable, str(script), "100"], cwd=checkout,
        env={**os.environ, "PYTHONPATH": str(checkout)}, capture_output=True, text=True,
        timeout=170, check=False,
    )
    assert done.returncode == 0, (
        f"exit {done.returncode} (-11 or 139 is a segmentation fault):\n{done.stderr[-3000:]}")
    assert "closed safely" in done.stdout
