"""What the second image's commands share (mqucifer/print-shop#142): FreeCAD, loaded in order.

`fit-on` and `bend` run in `print-shop/cae`, where FreeCAD's modules are beside its libraries
(`host.CAE_ENTRY` puts them on the path). The first image has no FreeCAD, and a script there that
calls one of the two is told so, not left with an import's traceback.
"""

from __future__ import annotations

from typing import Any

from .report import NoAnswer


def freecad(command: str) -> tuple[Any, Any]:
    """FreeCAD and its Part module. FreeCAD first: Part stops the process if it's loaded before
    it."""
    try:
        import FreeCAD
        import Part
    except ImportError as exc:
        raise NoAnswer(
            f"`{command}` runs in the bench's second image, which has FreeCAD, and this isn't it "
            f"({exc.name} isn't here): ask for it as a bench command, not from a script"
        ) from exc
    return FreeCAD, Part
