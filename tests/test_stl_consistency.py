"""Specification: the mesh report and the candidates describe the same bytes.

`ingest_node` calls `analyze(stl_path)` and stores a `MeshReport`. `orient_node`
then calls `candidates(stl_path)`, which opens the same path again. Nothing
connects the two reads. If the file changed in between, the pipeline carries a
`MeshReport` describing one mesh and an `Orientation` chosen for another, and
every downstream number — overhang area, bed contact, the rotation a human
approves at the gate — silently describes the wrong part.

The mesh cannot simply be passed between the nodes: `PrintJobState` is
checkpointed to SQLite and a `trimesh.Trimesh` is not serialisable. So the two
reads stay, and what gets carried across the boundary is a hash of what the
first one saw.

This is deliberately *not* a performance change. The duplicated parse was
measured at 0.27 ms on the test fixtures and 75 ms on an 81,920-face mesh,
against a job that takes 60-180 s end to end — far too small to justify
restructuring graph routing next to the gates. The invariant is worth having;
the speed was not.

**A missing hash is a failure, not a skip.** A guard that quietly does nothing
when its input is absent is the shape of the `retain` bug this repo already
had: a documented protection that had never once fired.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from print_shop.ingest.analyzer import StlChangedError, file_sha256
from tests.fakes import RecordedSlicer, installed_or_ci
from tests.fixtures import write_stl

# --------------------------------------------------------------------------
# file_sha256 — pure
# --------------------------------------------------------------------------


def test_hashes_the_file_contents(tmp_path: Path) -> None:
    stl = write_stl("tee", tmp_path)
    assert file_sha256(stl) == hashlib.sha256(stl.read_bytes()).hexdigest()


def test_different_contents_hash_differently(tmp_path: Path) -> None:
    a = write_stl("tee", tmp_path)
    b = write_stl("cube", tmp_path)
    assert file_sha256(a) != file_sha256(b)


def test_same_contents_at_a_different_path_hash_the_same(tmp_path: Path) -> None:
    """The identity that matters is the bytes, not where they live."""
    a = write_stl("tee", tmp_path)
    copy = tmp_path / "moved.stl"
    copy.write_bytes(a.read_bytes())
    assert file_sha256(a) == file_sha256(copy)


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        file_sha256(tmp_path / "nope.stl")


def test_reads_without_loading_the_whole_file(tmp_path: Path) -> None:
    """Streamed in chunks — an STL can be hundreds of megabytes.

    Checked by hashing a file larger than any sane chunk buffer and comparing
    against hashlib, which would differ if the implementation truncated.
    """
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (4 * 1024 * 1024 + 12345))
    assert file_sha256(big) == hashlib.sha256(big.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# The graph honours it
# --------------------------------------------------------------------------


@pytest.fixture
def profiles() -> Any:
    from print_shop.slicer.orca import SlicerProfiles

    datadir = Path.home() / "Library" / "Application Support" / "OrcaSlicer"
    sysdir = datadir / "system" / "Elegoo"
    return installed_or_ci(
        SlicerProfiles(
            machine=sysdir / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json",
            process=sysdir / "process" / "ECC" / "0.20mm Standard @Elegoo CC 0.4 nozzle.json",
            filament=sysdir / "filament" / "ECC" / "Elegoo PLA @ECC.json",
            datadir=datadir,
        )
    )


def test_ingest_records_the_hash_in_state(profiles: Any, tmp_path: Path) -> None:
    """Without this key the check downstream has nothing to compare against.

    `PrintJobState` must declare `stl_sha256` — LangGraph silently drops keys a
    node returns that the state schema does not know about, so an omission here
    loses the value with no error anywhere.
    """
    from print_shop.core.state import EntryPath
    from print_shop.orchestrator.graph import build_graph, new_job

    # The graph runs on to the gate, so it slices: with the recorded slice of
    # the cube, so this runs where there is no Orca.
    app = build_graph(profiles, outdir_root=tmp_path / "jobs", slicer=RecordedSlicer())
    job = new_job(str(write_stl("cube", tmp_path)), entry=EntryPath.STL_UPLOAD)
    out = app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    assert out.get("stl_sha256"), "ingest did not record the STL hash"
    assert out["stl_sha256"] == file_sha256(Path(job["stl_path"]))


def test_a_swapped_stl_is_caught_before_the_slicer_runs(
    profiles: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: the race, injected on the real path.

    `analyze` is wrapped so the file is replaced with a different mesh the
    moment ingest finishes — exactly the window the hash exists to close. The
    graph must refuse rather than orient a part it never analysed.

    It must also refuse *before* slicing: Orca is the expensive step, and a
    pipeline that discovers the mismatch afterwards has burned a minute to
    produce G-code for the wrong object.
    """
    import print_shop.orchestrator.graph as graph_module
    from print_shop.core.state import EntryPath
    from print_shop.ingest.analyzer import analyze as real_analyze
    from print_shop.orchestrator.graph import build_graph, new_job

    stl = write_stl("tee", tmp_path)
    sliced: list[str] = []

    def analyze_then_swap(path: str | Path, *args: Any, **kwargs: Any) -> Any:
        report = real_analyze(path, *args, **kwargs)
        # Someone re-exports over the upload between the two reads.
        other = write_stl("cube", tmp_path / "other")
        Path(path).write_bytes(other.read_bytes())
        return report

    monkeypatch.setattr(graph_module, "analyze", analyze_then_swap)
    def slicer_must_not_run(*args: Any, **kwargs: Any) -> Any:
        sliced.append("ran")
        raise AssertionError("the slicer ran on a mesh that was never analysed")

    monkeypatch.setattr(graph_module, "slice_model", slicer_must_not_run)

    (tmp_path / "other").mkdir(exist_ok=True)
    app = build_graph(profiles, outdir_root=tmp_path / "jobs")
    job = new_job(str(stl), entry=EntryPath.STL_UPLOAD)

    with pytest.raises(StlChangedError) as exc:
        app.invoke(job, config={"configurable": {"thread_id": job["job_id"]}})

    message = str(exc.value)
    assert str(stl) in message, "the error does not say which file changed"
    assert not sliced, "the slicer ran despite the mismatch"


def test_the_error_names_both_hashes(tmp_path: Path) -> None:
    """A bare "file changed" tells an operator nothing they can act on."""
    from print_shop.ingest.analyzer import verify_unchanged

    stl = write_stl("tee", tmp_path)
    recorded = file_sha256(stl)
    stl.write_bytes(write_stl("cube", tmp_path).read_bytes())
    now = file_sha256(stl)

    with pytest.raises(StlChangedError) as exc:
        verify_unchanged(stl, recorded)

    message = str(exc.value)
    assert recorded[:16] in message and now[:16] in message


def test_an_unchanged_file_verifies_quietly(tmp_path: Path) -> None:
    from print_shop.ingest.analyzer import verify_unchanged

    stl = write_stl("tee", tmp_path)
    verify_unchanged(stl, file_sha256(stl))  # must not raise


def test_a_job_with_no_recorded_hash_is_refused(tmp_path: Path) -> None:
    """Absent is not the same as matching.

    A checkpoint written before this check existed has no hash, and resuming it
    must say so rather than skip the verification and look fine.
    """
    from print_shop.ingest.analyzer import verify_unchanged

    stl = write_stl("tee", tmp_path)
    with pytest.raises(StlChangedError):
        verify_unchanged(stl, "")
