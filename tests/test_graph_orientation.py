"""The orientation agent wired into the graph.

Closes the loop: geometry is measured, a model chooses how the part sits, a
validator checks the choice, the slicer acts on it, and a human sees the
rationale alongside the numbers at the gate.

The tee is the case where every layer has to agree. Its correct orientation is
(180,0,0) — zero overhang against 300 mm2 as authored — and by the time the
G-code exists that choice has to be visible in the print time and the filament.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.types import Command

from print_shop.core.checkpoint import open_checkpointer
from print_shop.core.contracts import Gate, ungated_edges
from print_shop.core.state import EntryPath
from print_shop.orchestrator.graph import build_graph, graph_edges, new_job
from print_shop.slicer.orca import SlicerProfiles
from tests.fakes import installed_or_ci
from tests.fixtures import write_stl

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
    return installed_or_ci(
        SlicerProfiles(
            machine=MACHINE,
            process=PROCESS,
            filament=FILAMENT if FILAMENT.exists() else None,
            datadir=DATADIR,
        )
    )


GraphAndDb = tuple[Any, sqlite3.Connection]


@pytest.fixture
def app_and_db(profiles: SlicerProfiles, tmp_path: Path) -> Iterator[GraphAndDb]:
    saver, conn = open_checkpointer(tmp_path / "jobs.sqlite")
    app = build_graph(profiles, outdir_root=tmp_path / "jobs", checkpointer=saver)
    try:
        yield app, conn
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_orient_sits_between_ingest_and_slice(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    edges = set(graph_edges(build_graph(profiles, outdir_root=tmp_path)))
    assert ("ingest", "orient") in edges
    assert ("orient", "slice") in edges
    assert ("ingest", "slice") not in edges, "orientation must not be bypassable"


def test_a_rejected_orientation_goes_to_a_human(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """An orientation the validator cannot repair must not reach the slicer."""
    edges = set(graph_edges(build_graph(profiles, outdir_root=tmp_path)))
    assert ("orient", Gate.REVIEW_PROPOSAL.value) in edges


def test_the_gate_invariant_still_holds(profiles: SlicerProfiles, tmp_path: Path) -> None:
    """Adding a node must not open a path to the hardware."""
    app = build_graph(profiles, outdir_root=tmp_path)
    assert ungated_edges(graph_edges(app)) == []
    sources = {s for s, d in graph_edges(app) if d == "print"}
    assert sources == {Gate.BEFORE_PRINT.value}


# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.slicer
@needs_orca
def test_the_job_records_the_orientation_decision(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    app, _ = app_and_db
    job = new_job(write_stl("tee", tmp_path), entry=EntryPath.STL_UPLOAD)
    out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    decision = out["orientation"]
    assert decision.accepted
    assert decision.proposal.value.rotation_deg == (180.0, 0.0, 0.0)
    assert decision.proposal.rationale, "a human has to be able to see why"
    assert decision.proposal.source.startswith("model:")


@pytest.mark.llm
@pytest.mark.slicer
@needs_orca
def test_the_slicer_acts_on_the_chosen_orientation(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """The decision has to reach the G-code, not just the state.

    Sliced in the chosen orientation the tee needs no support. If the slicer
    ignored the choice and used the authored pose, the overhang would cost
    materially more filament.
    """
    app, _ = app_and_db
    job = new_job(write_stl("tee", tmp_path), entry=EntryPath.STL_UPLOAD)
    out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    gcode = Path(out["gcode_path"])
    assert gcode.exists()
    assert out["slice"].filament_cm3 < 2.9, (
        "looks like the authored orientation was sliced, not the chosen one"
    )


@pytest.mark.llm
@pytest.mark.slicer
@needs_orca
def test_the_gate_shows_a_human_the_orientation_and_why(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """Home Assistant renders this payload. It is the whole basis for approving."""
    app, _ = app_and_db
    job = new_job(write_stl("tee", tmp_path), entry=EntryPath.STL_UPLOAD)
    out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    payload = out["__interrupt__"][0].value
    assert payload["gate"] == Gate.BEFORE_PRINT.value
    assert payload["print_time_s"] > 0
    assert payload["filament_cm3"] > 0
    assert payload["orientation_deg"] == [180.0, 0.0, 0.0]
    assert payload["orientation_rationale"]


@pytest.mark.llm
@pytest.mark.slicer
@needs_orca
def test_the_decision_survives_the_checkpointer(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """An Outcome carries a Proposal and a verdict history; all of it persists."""
    app, _ = app_and_db
    job = new_job(write_stl("tee", tmp_path), entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)

    snap = app.get_state(cfg)
    decision = snap.values["orientation"]
    assert decision.proposal.value.rotation_deg == (180.0, 0.0, 0.0)
    assert decision.attempts >= 1


@pytest.mark.llm
@pytest.mark.slicer
@needs_orca
def test_rejecting_still_ends_without_printing(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    app, _ = app_and_db
    job = new_job(write_stl("tee", tmp_path), entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)

    out = app.invoke(Command(resume={"approved": False}), config=cfg)
    assert out["gate_decisions"][-1].approved is False


# --------------------------------------------------------------------------
# The human-approval paths — found in review, missed by the swarm's reviewer
# --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_approving_a_damaged_mesh_reaches_the_gate(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """`gate.review_proposal -> slice` reaches the slicer without running `orient`.

    A non-manifold mesh goes `ingest -> gate.review_proposal`, never through
    `orient`, so `state["orientation"]` was never written. Reading it
    unconditionally in the slice node raised `KeyError: 'orientation'` the
    moment a human approved — on the exact path a person takes when they decide
    to print their slightly broken mesh anyway.

    `nicked_cube` is missing one triangle: broken, but Orca will still slice it.
    """
    app, _ = app_and_db
    job = new_job(write_stl("nicked_cube", tmp_path), entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}

    parked = app.invoke(job, config=cfg)
    assert parked["__interrupt__"][0].value["gate"] == Gate.REVIEW_PROPOSAL.value
    assert "orientation" not in parked

    out = app.invoke(Command(resume={"approved": True}), config=cfg)
    assert Path(out["gcode_path"]).exists()


@pytest.mark.slicer
@needs_orca
def test_the_gate_says_which_orientation_was_actually_used(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """When no orientation was accepted, the human must be told that.

    Silently slicing the authored pose while the gate shows nothing about it is
    how a person approves something they did not actually see.
    """
    app, _ = app_and_db
    job = new_job(write_stl("nicked_cube", tmp_path), entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)
    out = app.invoke(Command(resume={"approved": True}), config=cfg)

    payload = out["__interrupt__"][0].value
    assert payload["gate"] == Gate.BEFORE_PRINT.value
    assert payload["orientation_deg"] == [0.0, 0.0, 0.0]
    assert payload["orientation_rationale"], "must say why it is the authored pose"


@pytest.mark.slicer
@needs_orca
def test_an_unsliceable_mesh_fails_loudly_rather_than_obscurely(
    app_and_db: GraphAndDb, tmp_path: Path
) -> None:
    """`open_box` is missing two whole faces; Orca exits 156, "No layers were
    detected". That is correct slicer behaviour on a mesh that encloses nothing.

    What matters is the shape of the failure: a RuntimeError naming the slicer,
    not a KeyError from the graph reading state that was never written.
    """
    app, _ = app_and_db
    job = new_job(write_stl("open_box", tmp_path), entry=EntryPath.STL_UPLOAD)
    cfg = {"configurable": {"thread_id": job["job_id"]}}
    app.invoke(job, config=cfg)

    with pytest.raises(RuntimeError, match="(?i)orcaslicer"):
        app.invoke(Command(resume={"approved": True}), config=cfg)


def test_the_slicer_never_uses_an_orientation_the_validator_rejected(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """A rejected Outcome in state must not be treated as a decision.

    `propose_validate` sets `accepted=False` and escalates when the model
    invents a rotation. If the slice node reads `.proposal.value` regardless,
    a hallucinated orientation reaches the plate as soon as a human clicks
    approve at the review gate.
    """
    import inspect

    from print_shop.orchestrator import graph as graph_mod

    src = inspect.getsource(graph_mod)
    assert "state[\"orientation\"].proposal.value" not in src, (
        "slice node reads the orientation without checking `accepted`"
    )
