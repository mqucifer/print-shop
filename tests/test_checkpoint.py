"""Checkpointed state must survive a process restart, under strict msgpack.

LangGraph logs `Deserializing unregistered type ... This will be blocked in a
future version.` for any type it has not been told about. A print runs for
hours; a job that cannot be deserialised after an upgrade is a job lost
mid-print.

The fitness function is the **warning count from a real graph run**, measured
in a subprocess with a fresh connection so the read comes off disk. Two things
this had to learn the hard way:

- `LANGGRAPH_STRICT_MSGPACK=true` does *not* block these types, so it cannot be
  used as the assertion. Verified by mutation: removing the allowlist entirely
  still round-trips cleanly under strict mode.
- Asserting on `serde.dumps_typed`/`loads_typed` directly proves nothing. The
  first version of this test did that, passed, and missed that the allowlist
  was a silent no-op while the real graph warned on every checkpoint.

Both mistakes are the same mistake: a test that does not exercise the
production path. Mutation-checked — break `make_serializer()` and this fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from print_shop.core.checkpoint import PERSISTED_TYPES, make_serializer, open_checkpointer
from print_shop.core.contracts import Gate, GateDecision
from print_shop.core.state import EntryPath, MeshReport, PrintStatus, SliceResult

REPO = Path(__file__).resolve().parent.parent


def test_every_persisted_type_is_registered() -> None:
    """Guards against a type being added to state and forgotten here."""
    from print_shop.core import state

    exported = {
        getattr(state, name)
        for name in state.__all__
        if isinstance(getattr(state, name), type)
    }
    # PrintJobState is a TypedDict; it is the container, not a persisted value.
    exported.discard(state.PrintJobState)
    missing = exported - set(PERSISTED_TYPES)
    assert not missing, f"unregistered in PERSISTED_TYPES: {sorted(t.__name__ for t in missing)}"


def test_round_trips_the_domain_payloads() -> None:
    serde = make_serializer()
    values = [
        MeshReport(
            is_manifold=True,
            volume_mm3=5000.0,
            surface_area_mm2=2000.0,
            bbox_mm=(20.0, 20.0, 20.0),
            overhang_area_mm2=300.0,
            bed_contact_area_mm2=100.0,
        ),
        SliceResult(
            gcode_path=Path("/tmp/plate_1.gcode"),
            layer_count=100,
            max_z_mm=20.0,
            filament_mm=992.93,
            filament_cm3=2.39,
            filament_g=None,
            print_time_s=850,
        ),
        GateDecision(gate=Gate.BEFORE_PRINT, approved=False, at="2026-09-13T00:00:00Z"),
        EntryPath.STL_UPLOAD,
        PrintStatus.PENDING,
    ]
    for v in values:
        assert serde.loads_typed(serde.dumps_typed(v)) == v


def test_an_open_mesh_round_trips_with_volume_none() -> None:
    """None must survive as None, not become 0.0 on the way back."""
    serde = make_serializer()
    r = MeshReport(
        is_manifold=False,
        volume_mm3=None,
        surface_area_mm2=2000.0,
        bbox_mm=(20.0, 20.0, 20.0),
        overhang_area_mm2=0.0,
    )
    assert serde.loads_typed(serde.dumps_typed(r)).volume_mm3 is None


def test_checkpointer_keeps_its_connection_open(tmp_path: Path) -> None:
    """CODE_REVIEW.md P0-3: the graph must not hold a closed handle."""
    saver, conn = open_checkpointer(tmp_path / "db" / "jobs.sqlite")
    try:
        conn.execute("select 1")  # raises ProgrammingError if closed
        assert saver.serde is not None
    finally:
        conn.close()


@pytest.mark.slicer
def test_a_real_graph_run_registers_every_type_it_checkpoints(tmp_path: Path) -> None:
    """Zero `unregistered type` warnings from an end-to-end run.

    Mutation-checked: with the allowlist removed this reports 3.
    """
    script = f"""
import sys, os, tempfile
sys.path.insert(0, {str(REPO)!r})
from pathlib import Path
from print_shop.core.checkpoint import open_checkpointer
from print_shop.orchestrator.graph import build_graph, new_job
from print_shop.slicer.orca import SlicerProfiles
from print_shop.core.state import EntryPath
from tests.fixtures import write_stl

D = Path(os.path.expanduser("~/Library/Application Support/OrcaSlicer"))
S = D / "system" / "Elegoo"
prof = SlicerProfiles(
    machine=S / "machine/ECC/Elegoo Centauri Carbon 0.4 nozzle.json",
    process=S / "process/ECC/0.20mm Standard @Elegoo CC 0.4 nozzle.json",
    filament=S / "filament/ECC/Elegoo PLA @ECC.json",
    datadir=D,
)
work = Path({str(tmp_path)!r})
saver, conn = open_checkpointer(work / "jobs.sqlite")
app = build_graph(prof, outdir_root=work / "jobs", checkpointer=saver)
job = new_job(write_stl("tee", work), entry=EntryPath.STL_UPLOAD)
cfg = {{"configurable": {{"thread_id": job["job_id"]}}}}
app.invoke(job, config=cfg)
conn.close()

# Fresh connection: forces the read to come off disk, not an in-process cache.
saver2, conn2 = open_checkpointer(work / "jobs.sqlite")
app2 = build_graph(prof, outdir_root=work / "jobs", checkpointer=saver2)
snap = app2.get_state(cfg)
assert snap.values["mesh"].overhang_area_mm2 == 300.0
assert snap.values["slice"].print_time_s > 0
assert snap.values["entry"] is EntryPath.STL_UPLOAD
conn2.close()
print("ROUNDTRIP_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300
    )
    combined = proc.stdout + proc.stderr
    assert "ROUNDTRIP_OK" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    unregistered = combined.lower().count("unregistered type")
    assert unregistered == 0, f"{unregistered} unregistered type(s):\n{proc.stderr}"
