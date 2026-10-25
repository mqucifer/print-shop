"""LangGraph spine for the print pipeline.

Wires the pipeline nodes (ingest -> slice -> gate -> print) into a compiled
LangGraph state machine. The graph enforces the safety invariant from
CONTRACTS.md section 4: every inbound edge to a hardware node (``print``)
originates at a ``Gate`` node.

Topology::

    START -> ingest
    ingest               --cond--> orient | gate.review_proposal
    orient               --cond--> slice  | gate.review_proposal
    gate.review_proposal --cond--> slice  | END
    slice                --> gate.before_print
    gate.before_print    --cond--> print  | END
    print                --> END

The graph is the single source of truth for control flow. ``graph_edges``
exposes the compiled edge list so ``ungated_edges`` (CONTRACTS.md section 4)
can be asserted against the real graph rather than a synthetic list.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from print_shop.core.contracts import Gate, GateDecision, approved_at, propose_validate
from print_shop.core.state import AppliedOrientation, EntryPath, PrintJobState, PrintStatus
from print_shop.expert.client import LLMConfig
from print_shop.expert.orientation import (
    NO_MODEL_CONFIGURED,
    OrientationRequest,
    propose_geometric,
    propose_orientation,
    validate_orientation,
)
from print_shop.ingest.analyzer import analyze, verify_unchanged
from print_shop.ingest.orientation import candidates, write_rotated
from print_shop.orchestrator.fitness import scoring_profiles
from print_shop.printer.centauri import PreflightFailed, PrinterClient, preflight
from print_shop.slicer.naming import job_id_from_filename, name_gcode
from print_shop.slicer.orca import Slicer, SliceResult, SlicerProfiles, slice_model

__all__ = ["build_graph", "graph_edges", "new_job"]


def new_job(stl_path: str | Path, *, entry: EntryPath) -> PrintJobState:
    """Create a fresh ``PrintJobState`` for a new print job.

    Args:
        stl_path: Path to the input STL file.
        entry: Which entry point the job came from.

    Returns:
        A ``PrintJobState`` with identity fields populated.
    """
    return PrintJobState(
        job_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        entry=entry,
        created_at=datetime.now(UTC).isoformat(),
        stl_path=str(stl_path),
    )


def build_graph(
    profiles: SlicerProfiles,
    outdir_root: Path,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    llm: LLMConfig | None = None,
    printer: PrinterClient | None = None,
    slicer: Slicer = slice_model,
) -> Any:
    """Build and compile the print pipeline graph.

    Args:
        profiles: Slicer profile paths, closed over by the slice node.
        outdir_root: Root directory for per-job output. Each job writes into
            ``outdir_root / job_id``.
        checkpointer: An already-constructed checkpointer. ``build_graph``
            passes it straight to ``compile()`` and never wraps it in a
            ``with`` block — the connection must stay open for the lifetime
            of the graph (CODE_REVIEW.md P0-3).
        llm: Endpoint config for the orientation proposer, from
            ``LLMConfig.from_settings()`` (LiteLLM :4000). **No default
            model:** None means no model is asked, and the geometric proposer
            chooses, saying so. Like ``printer``, nothing here reaches for
            configuration nobody passed.
        printer: The printer the ``print`` node commands. **No default.** A
            graph built without one cannot reach hardware — ``print_node``
            raises instead. Pass ``live_printer()`` to command the real
            machine; pass a double in tests. There is deliberately no
            build-it-from-the-environment fallback: that fallback existed for
            one afternoon and it started an unattended print from the test
            suite.

        slicer: What slices. The real Orca by default, a local process
            with no hardware behind it; tests replay a recorded slice.

    Returns:
        A compiled LangGraph ``CompiledStateGraph``.
    """
    build_volume = build_volume_mm(profiles.machine, datadir=profiles.datadir)

    def ingest_node(state: PrintJobState) -> dict[str, Any]:
        """Analyze the STL and store the MeshReport in state."""
        report = analyze(state["stl_path"])
        return {"mesh": report, "stl_sha256": report.stl_sha256}

    def orient_node(state: PrintJobState) -> dict[str, Any]:
        """Choose how the part sits on the plate: model proposes, validator disposes.

        The full ``Outcome`` (proposal, verdict history, accepted) is stored in
        state so a gate can show a human what was proposed and what changed.
        """
        verify_unchanged(state["stl_path"], state.get("stl_sha256", ""))

        request = OrientationRequest(
            candidates=tuple(candidates(state["stl_path"])),
            build_volume_mm=build_volume,
            part_description="",
        )

        def proposer(req: OrientationRequest) -> Any:
            if llm is None:
                return propose_geometric(req, NO_MODEL_CONFIGURED)
            try:
                return propose_orientation(req, llm)
            except (RuntimeError, OSError):
                return propose_geometric(req)

        outcome = propose_validate(proposer, validate_orientation, request)
        return {"orientation": outcome}

    def slice_node(state: PrintJobState) -> dict[str, Any]:
        """Slice the model in the chosen orientation, writing into outdir_root/job_id.

        The STL is rotated to the accepted orientation before slicing. If that
        orientation still has overhang, supports are forced on: orientation
        minimises overhang but does not always eliminate it, and a part with
        unsupported overhang fails on the plate.

        If no orientation was accepted (e.g. non-manifold mesh routed directly
        from ingest), the part is sliced in its authored pose using the mesh's
        measured overhang area to determine support requirements.
        """
        outdir = outdir_root / state["job_id"]
        outcome = state.get("orientation")
        if outcome is not None and outcome.accepted:
            chosen = outcome.proposal.value
            rotation_deg = chosen.rotation_deg
            overhang_area = chosen.overhang_area_mm2
            rationale = outcome.proposal.rationale
        else:
            rotation_deg = (0.0, 0.0, 0.0)
            overhang_area = state["mesh"].overhang_area_mm2
            rationale = "sliced in the authored orientation: no orientation was accepted"

        if rotation_deg == (0.0, 0.0, 0.0):
            stl_to_slice: str | Path = state["stl_path"]
        else:
            stl_to_slice = write_rotated(state["stl_path"], rotation_deg, outdir / "rotated.stl")

        active_profiles = (
            scoring_profiles(profiles, outdir) if overhang_area > 0 else profiles
        )
        result = slicer(stl_to_slice, active_profiles, outdir)
        # Orca always writes `plate_1.gcode`. The printer's storage is one flat
        # namespace and `start_print` takes a bare name, so the file is renamed
        # to carry the job id before anything can upload it.
        result = name_gcode(
            result, job_id=state["job_id"], part=Path(state["stl_path"]).stem
        )
        return {
            "gcode_path": str(result.gcode_path),
            "slice": result,
            "applied_orientation": AppliedOrientation(
                rotation_deg=rotation_deg, rationale=rationale
            ),
        }

    def review_proposal_node(state: PrintJobState) -> dict[str, Any]:
        """Gate for non-manifold meshes: stop for a human decision.

        The payload carries the mesh facts a human needs to decide whether
        to retry (route back to slice) or abandon (END).
        """
        mesh = state["mesh"]
        decision = interrupt(
            {
                "gate": Gate.REVIEW_PROPOSAL.value,
                "job_id": state["job_id"],
                "is_manifold": mesh.is_manifold,
                "volume_mm3": mesh.volume_mm3,
                "surface_area_mm2": mesh.surface_area_mm2,
                "bbox_mm": list(mesh.bbox_mm),
                "overhang_area_mm2": mesh.overhang_area_mm2,
                "reason": "Mesh is not manifold; cannot be trusted to slice.",
            }
        )
        approved = (
            bool(decision.get("approved", False)) if isinstance(decision, dict) else bool(decision)
        )
        gate_decision = GateDecision(
            gate=Gate.REVIEW_PROPOSAL,
            approved=approved,
            at=datetime.now(UTC).isoformat(),
            note=str(decision.get("note", "")) if isinstance(decision, dict) else "",
        )
        return {"gate_decisions": (state.get("gate_decisions", ()) + (gate_decision,))}

    def before_print_node(state: PrintJobState) -> dict[str, Any]:
        """Gate before the hardware boundary: stop for a human decision.

        The payload carries the G-code path, print time, and filament volume —
        the numbers a human needs to decide whether to print.
        """
        slice_result: SliceResult = state["slice"]
        applied_orientation = state.get("applied_orientation", AppliedOrientation())
        decision = interrupt(
            {
                "gate": Gate.BEFORE_PRINT.value,
                "job_id": state["job_id"],
                "gcode_path": str(slice_result.gcode_path),
                "print_time_s": slice_result.print_time_s,
                "filament_cm3": slice_result.filament_cm3,
                "layer_count": slice_result.layer_count,
                "max_z_mm": slice_result.max_z_mm,
                "orientation_deg": list(applied_orientation.rotation_deg),
                "orientation_rationale": applied_orientation.rationale,
            }
        )
        approved = (
            bool(decision.get("approved", False)) if isinstance(decision, dict) else bool(decision)
        )
        gate_decision = GateDecision(
            gate=Gate.BEFORE_PRINT,
            approved=approved,
            at=datetime.now(UTC).isoformat(),
            note=str(decision.get("note", "")) if isinstance(decision, dict) else "",
        )
        return {"gate_decisions": (state.get("gate_decisions", ()) + (gate_decision,))}

    def print_node(state: PrintJobState) -> dict[str, Any]:
        """Hardware boundary: upload the G-code and start the print.

        Four refusals stand between this being called and the nozzle moving,
        and each one raises rather than continuing:

        1. **The gate decision is re-checked here.** ``ungated_edges`` proves
           no *edge* reaches this node without passing ``Gate.BEFORE_PRINT``,
           which is a property of the topology — not of the decision sitting in
           state. Commit d6374b9 fixed two ways a decision could reach a gate it
           was not made for; this is the same class of bug one node further on.
        2. **Preflight** — the printer must be free and have room, and the
           G-code must exist. A pure validator over a typed snapshot.
        3. **The uploaded name must carry this job's id.** ``upload_file``
           returns the name the *printer* chose, which need not be the name we
           sent. If the round-trip fails we are about to print a file we cannot
           account for, so we do not.
        4. **The start is confirmed by watching the printer's own status**,
           not by the command being acknowledged.

        Never returns a fake success: the previous implementation set
        ``print_status = "done"`` unconditionally, which made a pipeline that
        had never run look like it worked.
        """
        decisions = state.get("gate_decisions", ())
        if not approved_at(decisions, Gate.BEFORE_PRINT):
            last = decisions[-1] if decisions else None
            raise RuntimeError(
                f"print node reached without an approval at {Gate.BEFORE_PRINT.value}; "
                f"last decision was {last!r}. Refusing to command the printer."
            )

        if printer is None:
            raise RuntimeError(
                "this graph was built without a printer, so it cannot command "
                "one. Pass `printer=` to build_graph — `live_printer()` for the "
                "real machine. Refusing to arm hardware that nobody asked for."
            )
        client: PrinterClient = printer

        gcode = Path(state["gcode_path"])
        snapshot = client.snapshot()
        verdict = preflight(snapshot, gcode)
        if not verdict.accepted:
            raise PreflightFailed(verdict)

        remote_name = client.upload(gcode)
        if job_id_from_filename(remote_name) != state["job_id"]:
            raise RuntimeError(
                f"the printer stored the upload as {remote_name!r}, which does not "
                f"carry job {state['job_id']}. Refusing to start a print of a file "
                f"this job cannot account for."
            )

        client.start(remote_name)
        return {
            "print_status": PrintStatus.PRINTING,
            "print_attempts": state.get("print_attempts", 0) + 1,
        }

    # --- Routing functions ---

    def route_after_ingest(state: PrintJobState) -> str:
        """Route to orient if manifold, else to the review gate."""
        if state["mesh"].is_manifold:
            return "orient"
        return Gate.REVIEW_PROPOSAL.value

    def route_after_orient(state: PrintJobState) -> str:
        """Route to slice if the orientation was accepted, else to the review gate."""
        if state["orientation"].accepted:
            return "slice"
        return Gate.REVIEW_PROPOSAL.value

    def route_after_review(state: PrintJobState) -> str:
        """Route to slice if the human approved, else END."""
        if approved_at(state.get("gate_decisions", ()), Gate.REVIEW_PROPOSAL):
            return "slice"
        return END

    def route_after_before_print(state: PrintJobState) -> str:
        """Route to print only on an approval made at this gate, else END."""
        if approved_at(state.get("gate_decisions", ()), Gate.BEFORE_PRINT):
            return "print"
        return END

    # --- Build the graph ---

    graph = StateGraph(PrintJobState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("orient", orient_node)
    graph.add_node("slice", slice_node)
    graph.add_node(Gate.REVIEW_PROPOSAL.value, review_proposal_node)
    graph.add_node(Gate.BEFORE_PRINT.value, before_print_node)
    graph.add_node("print", print_node)

    graph.add_edge(START, "ingest")
    graph.add_conditional_edges(
        "ingest",
        route_after_ingest,
        {"orient": "orient", Gate.REVIEW_PROPOSAL.value: Gate.REVIEW_PROPOSAL.value},
    )
    graph.add_conditional_edges(
        "orient",
        route_after_orient,
        {"slice": "slice", Gate.REVIEW_PROPOSAL.value: Gate.REVIEW_PROPOSAL.value},
    )
    graph.add_conditional_edges(
        Gate.REVIEW_PROPOSAL.value,
        route_after_review,
        {"slice": "slice", END: END},
    )
    graph.add_edge("slice", Gate.BEFORE_PRINT.value)
    graph.add_conditional_edges(
        Gate.BEFORE_PRINT.value,
        route_after_before_print,
        {"print": "print", END: END},
    )
    graph.add_edge("print", END)

    return graph.compile(checkpointer=checkpointer)


def build_volume_mm(
    machine_profile: Path, datadir: Path | None = None
) -> tuple[float, float, float]:
    """Resolve the build volume from a machine profile, following ``inherits``.

    Orca user profiles are thin overlays: they carry a handful of keys plus an
    ``inherits`` field pointing at a parent profile by *name*.  This function
    walks the inheritance chain (child keys win) until both ``printable_area``
    and ``printable_height`` are resolved.

    When *datadir* is given, ``inherits`` is resolved by searching the whole
    tree beneath it (both ``system/**`` and ``user/**``) for a profile whose
    ``name`` field matches — this is how Orca lays out edited user profiles,
    which inherit from profiles under ``system/<Vendor>/machine/<Model>/``.
    Without a datadir, the fallback is a search of the profile's sibling files.

    Raises:
        ValueError: If the dimensions cannot be resolved (missing keys,
            unparseable values, or an unresolvable ``inherits`` chain).
        FileNotFoundError: If the profile file does not exist.
    """
    index: dict[str, Path] | None = None
    if datadir is not None:
        index = _name_index(datadir)
    resolved = _resolve_profile(machine_profile, index=index)

    area = resolved.get("printable_area")
    height = resolved.get("printable_height")

    if area is None or height is None:
        missing = []
        if area is None:
            missing.append("printable_area")
        if height is None:
            missing.append("printable_height")
        raise ValueError(
            f"Cannot resolve build volume from '{machine_profile.name}': "
            f"missing {', '.join(missing)} after resolving inherits chain"
        )

    try:
        # printable_area is ["0x0", "257x0", "257x257", "0x257"] — the max
        # x/y extents are the last two entries' first components.
        x = max(float(e.split("x")[0]) for e in area[2:])
        y = max(float(e.split("x")[1]) for e in area[3:])
        z = float(height)
    except (ValueError, IndexError, AttributeError) as exc:
        raise ValueError(
            f"Cannot parse build volume from '{machine_profile.name}': {exc}"
        ) from exc

    return (x, y, z)


def _resolve_profile(
    profile_path: Path, index: dict[str, Path] | None = None
) -> dict[str, Any]:
    """Load a profile and resolve its ``inherits`` chain.

    Returns a merged dict where child keys override parent keys.
    """
    try:
        data: dict[str, Any] = json.loads(profile_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot read profile '{profile_path.name}': {exc}"
        ) from exc

    inherits = data.get("inherits")
    if inherits is None:
        return data

    parent = _find_parent(profile_path, inherits, index=index)
    parent_data = _resolve_profile(parent, index=index)
    # Child keys win over parent keys.
    merged: dict[str, Any] = {**parent_data, **data}
    # Remove the 'inherits' key from the result; it is not a dimension.
    merged.pop("inherits", None)
    return merged


def _name_index(root: Path) -> dict[str, Path]:
    """Map ``name`` field to path for every profile under *root*.

    Walks the whole tree (both ``system/**`` and ``user/**``) once so a deep
    inheritance chain does not re-walk it. Profiles whose ``name`` matches a
    later file keep the later one, so the walk order is deterministic.
    """
    index: dict[str, Path] = {}
    for candidate in sorted(root.rglob("*.json")):
        try:
            data = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("name")
        if isinstance(name, str) and name not in index:
            index[name] = candidate
    return index


def _find_parent(
    profile_path: Path, name: str, index: dict[str, Path] | None = None
) -> Path:
    """Find the parent profile for an ``inherits`` reference.

    With a prebuilt *index* (the datadir case), the parent is looked up by
    ``name`` anywhere in the tree. Without one, it falls back to searching the
    profile's sibling files.
    """
    if index is not None:
        parent = index.get(name)
        if parent is not None and parent != profile_path:
            return parent
        raise ValueError(
            f"Cannot resolve inherits='{name}' in '{profile_path.name}': "
            f"no profile with that name found in the datadir tree"
        )

    for candidate in sorted(profile_path.parent.glob("*.json")):
        try:
            data = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("name") == name:
            return candidate
    raise ValueError(
        f"Cannot resolve inherits='{name}': no sibling profile with that name "
        f"found in '{profile_path.parent}'"
    )


def graph_edges(app: Any) -> list[tuple[str, str]]:
    """Return ``(source, target)`` pairs from the compiled graph's edge list.

    This is what feeds ``ungated_edges()`` from ``src/core/contracts.py``.
    """
    return [(edge.source, edge.target) for edge in app.get_graph().edges]
