"""What a pause height means, and the words for it, on the bench and the print side alike.

mqucifer/print-shop#203. **A pause height is the top of the last layer printed before the pause,**
in mm above the part's underside as it prints: the pause comes after the layer that tops out
there, and the next layer is the first to print over what was dropped in. Orca takes the other
height, the top of the first layer after the pause (`slicer.orca.PauseAt`): only
`setup.proposal.pause_z` and the refinement pass turn one into the other.

The bench runs this file in its image and the print side imports it, so it imports nothing.
"""

from __future__ import annotations

#: A layer tops out at a height within this of it, in mm: heights are said to a thousandth.
TOPS_OUT = 1e-3


def mm(z: float) -> str:
    """A height as it's said and typed: 2.6, 3.05, 3."""
    return f"{round(z, 3):g}"


def where(after_layer: int, after_z: float, before_layer: int, before_z: float) -> str:
    """Where a pause falls: the layer it comes after and the one it comes before, by their tops."""
    return (
        f"after layer {after_layer}, at z={mm(after_z)}, "
        f"before layer {before_layer}, at z={mm(before_z)}"
    )
