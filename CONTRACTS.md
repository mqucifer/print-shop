# Contracts

Four decisions. They exist because these are the things that are expensive to
change later — everything else can be discovered while building.

The code is the spec: [`src/print_shop/core/contracts.py`](src/print_shop/core/contracts.py) and
[`src/print_shop/core/state.py`](src/print_shop/core/state.py). This file explains *why*.

---

## 1. The pipeline is one process

**Decision:** ingest, orientation, expert, and slicing run in a single Python
process. Not four containers.

The previous design had four services communicating by passing filesystem path
strings through a shared bind mount, plus one SQLite file with four writers and
three schemas. `CODE_REVIEW.md` §P2-5 calls this correctly: all the operational
cost of services, none of the isolation. It also made three separate defects
possible — a double-`/slice` in a hardcoded URL (§P0-2), a checkpointer closed
before use (§P0-3), and cross-service schema drift (§P2-1). None of those can
occur inside one process.

The work does not want to be distributed. Mesh analysis, orientation scoring and
slicing are sequential CPU work over local files, one job at a time.

**The boundaries that are real** — because the thing is genuinely somewhere else:

| Boundary | Why it is real | Protocol |
|---|---|---|
| The model, through LiteLLM :4000 on the Mac | The GPU and the weights are on the Spark, and every call goes through the proxy | HTTP, OpenAI-compatible |
| Home Assistant / MQTT on the Pi 5 | Approval gates, notifications, the UI | MQTT |
| Camera | Physically attached to the Pi 5 | MQTT |
| Orca Slicer | A CLI binary with its own dependencies | subprocess, local files |

Orca is a **process** boundary, not a network service. Invoke it, read the
G-code off local disk.

**Revisit when:** the watcher needs to run continuously next to the camera. That
is already an MQTT boundary above — it does not reopen the service-split
question.

## 2. State is typed; decisions carry their history

**Decision:** `PrintJobState` stays a `TypedDict` (LangGraph needs it), but every
value in it is a Pydantic model or an `Outcome`. No `dict[str, Any]`.

The old schema had `mesh_stats: Optional[dict[str, Any]]`. Inside it,
`thin_features_mm` returned short *edge lengths* — not thickness — and shipped
that way because nothing named or checked the shape. `MeshReport` now names each
field and documents its units and its measurement.

The overhang fix is in that model and is worth reading: the previous code
measured the angle between each face normal and **+Z** and flagged anything past
45°, so every vertical wall counted as an overhang and every model got supports.
`MeshReport.overhang_area_mm2` measures downward-facing area instead.

**Decisions are not values.** A node never writes a config into state. It writes
an `Outcome` — the proposal, its source, the full verdict history, and whether it
was accepted. A gate can then show a human what was proposed, what was objected
to, and what changed.

## 3. A model proposes, a validator disposes

**Decision:** every model-produced value passes `propose_validate` before it is
used, and components exchange typed objects, never prose.

The old loop had a validator emit English and a corrector substring-match it:

```python
if "nozzle temp" in suggestion.lower():
    fixed_config["nozzle_temp"] = 220      # regardless of material
```

Two structured components in the same file, communicating through natural
language, with a hardcoded number as the repair. Now a validator emits
`Violation(field="nozzle_temp", suggested=215, allowed=Bounds(...))` and the
corrector assigns it. `rationale` and `detail` survive for humans and logs and
are never read to make a decision.

Three properties worth knowing:

- **The validator supplies the fix.** It has the domain knowledge — it knows the
  filament. The corrector is deliberately boring; a corrector that reasons is a
  second proposer with nothing validating it.
- **Unfixable escalates immediately.** `NoAutoFix` means retries cannot help
  (non-manifold mesh, part exceeds build volume). Burning three attempts on it
  only delays the gate.
- **Confidence is never a substitute for validation.** `min_confidence` can
  escalate a low-confidence proposal, but a high-confidence one still gets
  validated. A confident model is not a validated one.

## 4. Nothing reaches the printer without a human

**Decision:** every inbound edge to a hardware node originates at a `Gate`.

```python
HARDWARE_NODES = frozenset({"print"})

class Gate(StrEnum):
    BEFORE_PRINT    = "gate.before_print"
    AFTER_FAILURE   = "gate.after_failure"
    REVIEW_PROPOSAL = "gate.review_proposal"
```

This is a graph property, not a convention, and `ungated_edges()` checks it.
The original graph routed a failed print to `learn_loop` and then straight back
to `slicer` — an unattended re-slice and reprint after a failure, on a machine
that reaches 250 °C in the house. `tests/test_contracts.py` encodes exactly that
edge as the failing case.

When the graph exists, assert `ungated_edges(graph_edges()) == []` against the
compiled graph. Until then the helper is tested against synthetic edge sets.

---

## What these do not decide

Deliberately open, to be settled while building:

- The node set and the graph topology.
- How orientation candidates are enumerated (a fixed set of rotations, PCA, or
  something else). The *contract* is fixed — `Orientation` carries the scores —
  the search is not.
- Whether the FDM expert is prompt-based or a LoRA. `PROJECT.md` recommends
  prompt-first, since a fine-tune needs print-outcome data that does not exist
  yet. Either satisfies the `Proposer` protocol.
- Where the watcher process runs.
- Anything about observability. The last attempt wired five OpenTelemetry
  packages into modules with zero call sites, one of which instrumented
  `requests` while the code used `httpx`. Add it when there is something running
  worth observing.
