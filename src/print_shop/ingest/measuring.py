"""The poses a part won't print in, measured beside its job (mqucifer/print-shop#235).

A part kept as laid, or on the side its context lays it on, needs its own pose measured to go
on, and the questions don't wait for the other five. Those five are still wanted: the review
shows all six, the pose can be sent back to one, the print loop chooses among them and the past
prints' offer reads the one a print was in. Each of those waits for what it needs and no more.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from pathlib import Path

import trimesh

from print_shop.core.state import Orientation
from print_shop.ingest.orientation import AXIS_ALIGNED, read_part, scored
from print_shop.setup.proposal import pose_value

__all__ = ["Measuring"]

logger = logging.getLogger(__name__)

Rotation = tuple[float, float, float]


class Measuring:
    """A part's six poses: those known, and the rest as a thread of its own measures them."""

    def __init__(self, stl: Path | str, known: Iterable[Orientation]) -> None:
        self._stl = stl
        self._found = {pose.rotation_deg: pose for pose in known}
        self._failed: dict[Rotation, str] = {}
        self._changed = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopped = False

    def start(self) -> None:
        """Begin on those not known. The file is read here, before the job goes on, so every
        pose is of the same bytes: the caller has checked they're the ones analysed."""
        if self._thread is not None or not self.pending():
            return
        mesh = read_part(self._stl)
        self._thread = threading.Thread(
            target=self._measure, args=(mesh,), daemon=True, name="other-poses"
        )
        self._thread.start()

    def stop(self) -> None:
        """The job has no more use for them: no other pose is begun, and nobody's kept waiting."""
        with self._changed:
            self._stopped = True
            self._changed.notify_all()

    def ready(self) -> tuple[Orientation, ...]:
        """Those measured by now, in the candidates' order."""
        with self._changed:
            return tuple(self._found[r] for r in AXIS_ALIGNED if r in self._found)

    def pending(self) -> tuple[Rotation, ...]:
        """Those still being measured."""
        with self._changed:
            return self._left()

    def failed(self) -> dict[Rotation, str]:
        """Those that couldn't be measured, each with why."""
        with self._changed:
            return dict(self._failed)

    def settled(self) -> tuple[Orientation, ...]:
        """Wait until none is pending: those that could be measured."""
        self.start()
        with self._changed:
            self._changed.wait_for(lambda: self._stopped or not self._left())
        return self.ready()

    def all_six(self) -> tuple[Orientation, ...]:
        """Wait for all six, for what weighs each against the others.

        Raises:
            RuntimeError: If one couldn't be measured: a choice isn't checked against fewer.
        """
        found = self.settled()
        if len(found) < len(AXIS_ALIGNED):
            why = "; ".join(f"{pose_value(r)}: {w}" for r, w in self.failed().items())
            raise RuntimeError(f"the poses couldn't all be measured, so none is chosen: {why}")
        return found

    def lying(self, value: str) -> Orientation | None:
        """Wait for the pose named as the review names it, or None where there's none so
        named, or it couldn't be measured."""
        rotation = next((r for r in AXIS_ALIGNED if pose_value(r) == value), None)
        if rotation is None:
            return None
        self.start()
        with self._changed:
            self._changed.wait_for(lambda: self._stopped or rotation not in self._left())
            return self._found.get(rotation)

    def _left(self) -> tuple[Rotation, ...]:
        return tuple(r for r in AXIS_ALIGNED if r not in self._found and r not in self._failed)

    def _measure(self, mesh: trimesh.Trimesh) -> None:
        for rotation in self.pending():
            if self._stopped:
                return
            try:
                pose = scored(mesh, rotation)
            except Exception as exc:  # the thread's: the job says which pose, and why
                logger.exception("the pose %s couldn't be measured", pose_value(rotation))
                why = f"{type(exc).__name__}: {exc}"[:200]
                with self._changed:
                    self._failed[rotation] = why
                    self._changed.notify_all()
            else:
                with self._changed:
                    self._found[rotation] = pose
                    self._changed.notify_all()
