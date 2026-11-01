---
name: ref-fdm
description: what a printed (FDM) part needs to fit as designed: clearances between printed parts, holes printing undersize, heat-set insert holes, and minimum walls; all practice, not a standard, each with its source
---

# Printed parts that fit (FDM)

All of this is **practice, not a standard**: printers' and print bureaus' experience,
which varies with the printer. It's only what makes a printed part fit or work as
designed. How a part is oriented or supported is a later pass, not part of the design.

## Clearances between printed parts, mm

| Fit | Clearance | Source |
|---|---|---|
| moving or connecting parts, general | 0.5 (between the parts) | [P1] [P2] [P3] |
| press | −0.05 per face (−0.10 on a diameter) | [P4] |
| light press, location | +0.05 per face | [P4] |
| running, sliding | +0.15 per face (+0.30 on a diameter) | [P4] |
| loose | +0.3 per face | [P4] |

[P4] is a print bureau's guide; the major vendors give one general figure, not a value
per fit.

## Holes print undersize

| What | Value | Source |
|---|---|---|
| a vertical hole printed with no compensation | M6 hole measured 5.9 (0.1 undersize) | [P5] |
| vertical holes in FDM | often print undersize (no figure given) | [P6] |
| slicers' X-Y hole compensation | grows each hole's diameter by twice the value | [P5] [P7] |

So a printed clearance hole is drawn at its table size (`ref-fasteners`); a hole that
must fit closely may need to be drawn a little larger.

## Heat-set threaded inserts, mm

| Size | Hole Ø | Insert length | Minimum wall round it | Source |
|---|---|---|---|---|
| M2 | 3.2 | 4.0 (Ruthex), 3.0 (CNC Kitchen) | 1.3 | [P8] [P9] |
| M2.5 | 4.0 | 4.0 | 1.6 | [P9] |
| M3 | 4.0 | 5.7 (short: 3.0) | 1.6 | [P8] [P9] |
| M4 | 5.6 | 8.1 (short: 4.0) | 2.1 | [P8] [P9] |
| M5 | 6.4 | 9.5 (short: 5.8) | 2.6 | [P8] [P9] |

A hole at least 1 mm deeper than the insert is a common rule ([P10]); the makers'
tables reached don't give a depth.

## Minimum wall, mm

| Wall | Minimum | Source |
|---|---|---|
| any printed wall | 0.8 | [P11] |
| supported and unsupported walls, FDM | 1.0 | [P1] |

## Sources

- [P1]: https://www.amf.uzh.ch/dam/jcr:fd1ee024-1a98-488b-89ec-ffd85befa786/Design-Guideline-FDM.pdf (University of Zurich FDM design guideline)
- [P2]: https://www.hubs.com/knowledge-base/how-design-snap-fit-joints-3d-printing/
- [P3]: https://formlabs.com/blog/how-to-3d-print-interlocking-joints/
- [P4]: https://www.rigidprints.co.uk/design-guide/tolerances-and-fits
- [P5]: https://wiki.bambulab.com/en/software/bambu-studio/xy-hole-contour-compensation
- [P6]: https://www.hubs.com/knowledge-base/how-design-parts-fdm-3d-printing/
- [P7]: https://www.orcaslicer.com/wiki/print_settings/quality/quality_settings_precision
- [P8]: https://www.3djake.com/ruthex/m2-m3-m4-m5-threaded-insert-assortment-box (Ruthex's table)
- [P9]: https://www.3djake.com/cnc-kitchen/threaded-inserts-m2-standard (CNC Kitchen's table)
- [P10]: https://www.digikey.com/es/maker/tutorials/2025/intro-to-freecad-part-4-how-to-use-heat-set-inserts
- [P11]: https://www.hubs.com/knowledge-base/key-design-considerations-3d-printing/
