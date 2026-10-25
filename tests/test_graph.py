"""Specification for `src/orchestrator/graph.py`.

The important test in this file is `test_no_ungated_path_to_hardware`. It runs
`ungated_edges()` against the **real compiled graph** rather than a synthetic
edge list, which is the point of having made the safety rule a graph property.

The previous implementation routed a failed print to `learn_loop` and straight
back to `slicer` — an unattended re-slice and reprint on a machine that reaches
250 C. No test could have caught that, because nothing asserted the shape of
the graph.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from print_shop.core.contracts import Gate, ungated_edges
from print_shop.core.state import EntryPath, PrintStatus
from print_shop.orchestrator.graph import build_graph, graph_edges, new_job
from print_shop.slicer.naming import job_id_from_filename
from print_shop.slicer.orca import SlicerProfiles
from tests.fixtures import write_stl
from tests.test_print_node import FakePrinter, idle

ORCA = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
DATADIR = Path(os.path.expanduser("~/Library/Application Support/OrcaSlicer"))
_SYS = DATADIR / "system" / "Elegoo"
MACHINE = _SYS / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json"
PROCESS = _SYS / "process" / "ECC" / "0.20mm Standard @Elegoo CC 0.4 nozzle.json"
FILAMENT = _SYS / "filament" / "ECC" / "Elegoo PLA @ECC.json"

needs_orca = pytest.mark.skipif(
    not (ORCA.exists() and MACHINE.exists() and PROCESS.exists()),
    reason="OrcaSlicer or the Elegoo Centauri Carbon profiles are not installed",
)


@pytest.fixture
def profiles() -> SlicerProfiles:
    return SlicerProfiles(
        machine=MACHINE,
        process=PROCESS,
        filament=FILAMENT if FILAMENT.exists() else None,
        datadir=DATADIR,
    )


GraphAndDb = tuple[Any, sqlite3.Connection]


@pytest.fixture
def app_and_db(profiles: SlicerProfiles, tmp_path: Path) -> Iterator[GraphAndDb]:
    """A graph backed by a real on-disk checkpointer.

    The connection is deliberately opened here and kept open for the lifetime
    of the test. The previous implementation did::

        with SqliteSaver.from_conn_string(...) as cp:
            app = graph.compile(checkpointer=cp)

    which closes the connection on leaving the `with` block, so the compiled
    graph held a dead handle (CODE_REVIEW.md P0-3).
    """
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db, check_same_thread=False)
    app = build_graph(profiles, outdir_root=tmp_path / "jobs", checkpointer=SqliteSaver(conn))
    try:
        yield app, conn
    finally:
        conn.close()


# --------------------------------------------------------------------------
# The safety invariant (AGENTS.md rule 5)
# --------------------------------------------------------------------------


def test_no_ungated_path_to_hardware(profiles: SlicerProfiles, tmp_path: Path) -> None:
    """Every inbound edge to a hardware node must originate at a Gate."""
    app = build_graph(profiles, outdir_root=tmp_path)
    assert ungated_edges(graph_edges(app)) == []


def test_every_conditional_edge_declares_its_targets(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """`add_conditional_edges` without a path map hides its targets.

    LangGraph can only report the edges of a conditional branch when it is
    given an explicit mapping. Omit it and `ungated_edges` goes blind — the
    invariant above would pass vacuously while an ungated path existed.
    """
    app = build_graph(profiles, outdir_root=tmp_path)
    edges = graph_edges(app)
    reachable = {src for src, _ in edges} | {dst for _, dst in edges}
    for node in app.get_graph().nodes:
        assert node in reachable, f"{node} has no declared edges"


def test_the_print_node_is_reached_only_from_its_gate(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    app = build_graph(profiles, outdir_root=tmp_path)
    sources = {src for src, dst in graph_edges(app) if dst == "print"}
    assert sources == {Gate.BEFORE_PRINT.value}


# --------------------------------------------------------------------------
# One job, end to end
# --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_runs_a_real_stl_to_gcode_and_parks_at_the_gate(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """The milestone: STL in, real G-code on disk, stopped for a human."""
    app, _ = app_and_db
    stl = write_stl("tee", tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}

    out = app.invoke(job, config=cfg)

    assert "__interrupt__" in out, "graph ran past the gate without stopping"
    payload = out["__interrupt__"][0].value
    assert payload["gate"] == Gate.BEFORE_PRINT.value

    gcode = Path(out["gcode_path"])
    assert gcode.exists() and gcode.stat().st_size > 10_000
    assert "M104" in gcode.read_text() or "M109" in gcode.read_text()

    # The mesh was measured on the way through.
    assert out["mesh"].is_manifold is True
    assert out["mesh"].overhang_area_mm2 == pytest.approx(300.0, abs=1e-3)


@pytest.mark.slicer
@needs_orca
def test_two_jobs_for_one_stl_produce_two_uploadable_files(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """The naming fitness function, on the path that actually runs.

    `tests/test_naming.py` proves the function. This proves the graph calls it:
    Orca writes `plate_1.gcode` every time, and the printer's storage is one
    flat namespace, so without the rename the second job's upload would
    overwrite the first and then print it.
    """
    app, _ = app_and_db
    stl = write_stl("tee", tmp_path)

    named: list[Path] = []
    for _ in range(2):
        job = new_job(stl, entry=EntryPath.STL_UPLOAD)
        out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})
        gcode = Path(out["gcode_path"])
        assert gcode.exists()
        assert job_id_from_filename(gcode.name) == job["job_id"]
        named.append(gcode)

    # What `start_print` is given is the bare name, so that is what must differ.
    assert named[0].name != named[1].name
    assert "plate_1" not in named[0].name

    # And the name describes the slice Orca actually made.
    assert named[0].name.startswith("ECC_0.4_tee_PLA0.2_")


@pytest.mark.slicer
@needs_orca
def test_the_gate_payload_carries_what_a_human_needs_to_decide(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """Home Assistant renders this. Time and filament are the whole decision."""
    app, _ = app_and_db
    stl = write_stl("tee", tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    payload = out["__interrupt__"][0].value
    assert payload["print_time_s"] > 0
    assert payload["filament_cm3"] > 0
    assert "gcode_path" in payload


@pytest.mark.slicer
@needs_orca
def test_rejecting_at_the_gate_ends_without_printing(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    app, _ = app_and_db
    stl = write_stl("cube", tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)

    out = app.invoke(Command(resume={"approved": False, "note": "wrong part"}), config=cfg)

    assert out.get("print_status") in (None, PrintStatus.PENDING, PrintStatus.ABANDONED)
    assert out["gate_decisions"][-1].approved is False


@pytest.mark.slicer
@needs_orca
def test_approving_at_the_gate_reaches_the_printer(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """Approval is what connects the graph to the hardware, and the only thing that does.

    The printer itself is a double here; what this asserts is the *graph*
    property — that an approval at `Gate.BEFORE_PRINT` is what makes `print`
    run. The node's own refusals, and the real client against the real machine,
    are in `tests/test_print_node.py`.
    """
    fake = FakePrinter(snapshot_value=idle())
    conn = sqlite3.connect(tmp_path / "print.sqlite", check_same_thread=False)
    app = build_graph(
        profiles,
        outdir_root=tmp_path / "jobs",
        checkpointer=SqliteSaver(conn),
        printer=fake,
    )
    stl = write_stl("cube", tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)

    out = app.invoke(Command(resume={"approved": True}), config=cfg)

    assert out["print_status"] is PrintStatus.PRINTING
    assert fake.verbs == ["snapshot", "upload", "start"]
    conn.close()


# --------------------------------------------------------------------------
# Routing and persistence
# --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_a_non_manifold_mesh_never_reaches_the_slicer(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """An open mesh has no defined volume and cannot be trusted to slice."""
    app, _ = app_and_db
    stl = write_stl("open_box", tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    assert "__interrupt__" in out
    assert out["__interrupt__"][0].value["gate"] == Gate.REVIEW_PROPOSAL.value
    assert out.get("gcode_path") is None
    assert out["mesh"].is_manifold is False
    assert out["mesh"].volume_mm3 is None


@pytest.mark.slicer
@needs_orca
def test_state_survives_in_the_checkpointer(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """Pins CODE_REVIEW.md P0-3: the checkpointer connection must stay open.

    A multi-hour print outlives the process that started it, so the parked
    state has to be on disk and readable.
    """
    app, conn = app_and_db
    stl = write_stl("cube", tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)

    rows = conn.execute("select count(*) from checkpoints").fetchone()[0]
    assert rows > 0, "nothing was checkpointed"

    snapshot = app.get_state(cfg)
    assert snapshot.values["job_id"] == job["job_id"]
    assert Path(snapshot.values["gcode_path"]).exists()


def test_new_job_has_identity(tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    a = new_job(stl, entry=EntryPath.STL_UPLOAD)
    b = new_job(stl, entry=EntryPath.STL_UPLOAD)
    assert a["job_id"] != b["job_id"]
    assert a["entry"] is EntryPath.STL_UPLOAD
    assert a["created_at"]
    assert Path(a["stl_path"]) == stl
