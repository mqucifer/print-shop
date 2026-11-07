"""What the refinement pass reads a part as, by its kind (mqucifer/print-shop#66).

On `eval-lid-1` every row of the pass spoke of a frame and panes ("no pane sits in the frame, and
the run never ran `pause`") for a lid, and on `eval-fit-2` the given pane wasn't in the approved
folder, so no pause was set. So a kind says, for the pass:

- **what's printed:** the approved folder's files, each on a plate of its colour, as this printer
  has one filament loaded at a time;
- **what's context only:** for stained glass, a pane the run was given and `glass` names, read as
  it sits for the pause and never printed. It's taken from `glass` and the given files, not from
  where files sit;
- **which events apply:** stained glass pauses to set its panes in; an enclosure has none.

A run from before kinds is read as the pass always read it: the largest file a frame, and a file
inside its box above its underside a pane. This is the start of the per-kind print plan parked
with stained glass: plates, filament events and the slice loop stay to come.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    #: The kind's name, as a request's `## Kind` gives it, or "" for a run from before kinds.
    kind: str
    #: Whether the part is a frame with panes set in at a pause.
    pause: bool
    #: Whether its frame and panes are the run's `glass`, not read from where files sit.
    by_glass: bool
    #: What a file that's no frame or pane is, in the ledger's `refine_files`.
    part_role: str


STAINED_GLASS = Plan("stained-glass", pause=True, by_glass=True, part_role="file")
ENCLOSURE = Plan("enclosure", pause=False, by_glass=False, part_role="part")
#: A run from before kinds: every part was stained glass then, read from where its files sit.
BEFORE = Plan("", pause=True, by_glass=False, part_role="file")
PLANS = {p.kind: p for p in (STAINED_GLASS, ENCLOSURE)}


def plan_of(kind: str | None) -> Plan:
    """The pass's plan for a kind. A kind with none of its own prints its files as they are, by
    colour, with no pause: nothing says where a pane goes."""
    if not kind:
        return BEFORE
    return PLANS.get(kind) or Plan(kind, pause=False, by_glass=False, part_role="part")
