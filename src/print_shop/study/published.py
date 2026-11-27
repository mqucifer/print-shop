"""What the published tests say of each pattern, its walls and its wall order: the study's
`published` tool (mqucifer/print-shop#339).

The Sponsor, 2026-11-26: "there's tons of data around infill out there. Some of it's just common
sense though of knowing where the stresses are and the direction the infill strengthens." So the
main source is the published tests `load` already reads its cores by (`bench/infill.py`, each
cited there and in `docs/bench.md`), said here as the model can use them: which way each pattern
is stiff, how stiff at each density, what strain it takes, and from what density a test checks
it. The coupons (mqucifer/print-shop#340) check these later and don't hold this up. And the
walls, which run along the outline, by Orca's own bead spacing; and Orca's own words for each
wall order, which `load` doesn't read: walls are as thick laid in any order.
"""

from __future__ import annotations

from print_shop.bench import infill
from print_shop.bench.as_printed import wall_thickness
from print_shop.bench.assembly import Material
from print_shop.bench.printed import Settings
from print_shop.study.candidate import PATTERNS, WALL_SEQUENCES

__all__ = ["published"]

#: The densities each pattern is said at.
DENSITIES = (20.0, 40.0, 60.0)


def _lines(pattern: str) -> str:
    lines = infill.LINES[pattern]
    if not lines:
        return ("its wave turns with the height and isn't lines: read as bending alone, the same "
                "every way, in the layers' plane and across them")
    sets = ", ".join(f"{100 * share:.0f}% of its lines at infill_angle + {offset:g}°"
                     for offset, share, _ in lines)
    walled = all(walls for _, _, walls in lines)
    how = ("the same lines every layer, so they build walls up through the layers: stiff along "
           "each set's run and across the layers in its plane" if walled else
           "turned 90° each layer, so a line meets the next layer's only where they cross: "
           "stiff along its lines in the layers' plane, and little across the layers")
    return f"{sets}; {how}"


def published(pattern: str | None, material: Material, line_width: float,
              layer_height: float) -> str:
    """The figures for one pattern, or for each `load` reads; the walls; the wall orders."""
    chosen = [pattern] if pattern else list(PATTERNS)
    unknown = [p for p in chosen if p not in PATTERNS]
    if unknown:
        return (f"load reads no pattern {', '.join(unknown)} as printed: "
                f"{', '.join(PATTERNS)} are")
    out = [
        "Each core's figures are its lines as Orca 2.4.2 lays them (bench/infill.py), held to "
        "the least share of that any published test of the pattern reached, with its strain as "
        "a share of the solid's those tests took. They're published tests of PLA, ABS and PETG, "
        "none yet checked by a print from this printer (mqucifer/print-shop#340), so every "
        "figure that rests on them says so. A core is strongest along its lines: point them "
        "along the stretch load finds, or use walls, which run along the outline.",
        f"The skin, the walls and shells, takes {material.along_pct:g}% in the layers' plane and "
        f"{material.across_pct:g}% across them ({material.source}).",
    ]
    for name in chosen:
        assert name is not None
        start = infill.checked_from(name)
        setter = infill.set_by(infill.TESTED_AS[name])
        out.append(f"\n{name}: {_lines(name)}. Checked by a published test from {start:g}% "
                   f"(set by {setter['source']}, {setter['material'].upper()} at "
                   f"{setter['density_pct']:g}%); read from {infill.LEAST:g}% to "
                   f"{infill.MOST:g}%.")
        for density in DENSITIES:
            core = infill.core(Settings(pattern=name, infill=density, infill_angle=0.0),
                               material)
            said = core.said()
            out.append(f"- at {density:g}%: {said['modulus_along_mpa']:.0f} MPa along its "
                       f"angle, {said['modulus_across_mpa']:.0f} MPa across its layers; takes "
                       f"{core.along_pct:.2f}% stretched in its layers' plane and "
                       f"{core.across_pct:.2f}% across them"
                       + ("; unchecked, below its tests" if said["unchecked"] else ""))
    walls = ", ".join(f"{n}: {wall_thickness(n, line_width, layer_height):.2f} mm"
                      for n in range(1, 7))
    out.append(f"\nWalls run along the outline and round each hole, solid plastic, at "
               f"{line_width:g} mm lines and {layer_height:g} mm layers as thick as Orca's bead "
               f"spacing (as_printed.wall_thickness): {walls}. A pull across the layers is the "
               "layers' bond whatever the walls; walls carry what runs along the part's faces, "
               "as bending does.")
    out.append("\nThe wall order (wall_sequence) changes the finish and the overhangs, not "
               "load's reading. Orca's own words:")
    out += [f"- {key}: {words}" for key, words in WALL_SEQUENCES.items()]
    return "\n".join(out)
