"""Checkpoint serialisation for the print pipeline.

A print runs for hours and outlives the process that started it, so parked
state has to survive on disk and come back as the same types. LangGraph's
msgpack serialiser warns on any type it has not been told about::

    Deserializing unregistered type print_shop.core.state.MeshReport from checkpoint.
    This will be blocked in a future version.

A warning today is a job that cannot be resumed after the next upgrade. Every
type that can appear in `PrintJobState` is registered here, and
`tests/test_checkpoint.py` runs the round-trip with `LANGGRAPH_STRICT_MSGPACK`
enabled so a new unregistered type fails the suite instead of printing a notice
nobody reads.

**Add a type to `PrintJobState`? Add it to `PERSISTED_TYPES`.**
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

from print_shop.core.contracts import (
    Bounds,
    EscalationReason,
    Gate,
    GateDecision,
    NoAutoFix,
    OneOf,
    Outcome,
    Proposal,
    Severity,
    Verdict,
    Violation,
)
from print_shop.core.state import (
    AppliedOrientation,
    EntryPath,
    MeshReport,
    Orientation,
    PrintStatus,
    SliceResult,
)

__all__ = ["PERSISTED_TYPES", "make_serializer", "open_checkpointer"]

#: Every type that can appear in a checkpointed `PrintJobState`.
PERSISTED_TYPES: tuple[type, ...] = (
    # state.py
    EntryPath,
    AppliedOrientation,
    PrintStatus,
    MeshReport,
    SliceResult,
    Orientation,
    # contracts.py — decisions carry their full history into the checkpoint
    Proposal,
    Outcome,
    Verdict,
    Violation,
    Severity,
    EscalationReason,
    Bounds,
    OneOf,
    NoAutoFix,
    Gate,
    GateDecision,
)


def make_serializer() -> JsonPlusSerializer:
    """A serialiser that knows every type this pipeline persists.

    Built with `allowed_msgpack_modules` passed to the constructor, not via
    `with_msgpack_allowlist()`. The default serialiser's allowlist is the
    sentinel `True` ("permit everything, warn on anything unregistered"), and
    `with_msgpack_allowlist` short-circuits on that::

        if base_allowlist is True or base_allowlist is False:
            return self

    so calling it on a default instance is a silent no-op. That is worth
    knowing: the first attempt here did exactly that, and a direct round-trip
    test passed while the real graph kept warning.
    """
    return JsonPlusSerializer(allowed_msgpack_modules=_ALLOWLIST)


_ALLOWLIST: tuple[tuple[str, str], ...] = tuple(
    (t.__module__, t.__name__) for t in PERSISTED_TYPES
)


def open_checkpointer(db_path: Path | str) -> tuple[SqliteSaver, sqlite3.Connection]:
    """Open an on-disk checkpointer, returning it with its live connection.

    The connection is returned rather than managed internally because the
    caller must keep it open for the lifetime of the compiled graph. The
    previous implementation wrote::

        with SqliteSaver.from_conn_string(...) as cp:
            app = graph.compile(checkpointer=cp)

    which closed the connection on leaving the block and left the graph
    holding a dead handle (`CODE_REVIEW.md` P0-3).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(conn, serde=make_serializer()), conn
