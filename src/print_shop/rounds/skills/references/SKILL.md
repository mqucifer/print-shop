---
name: references
description: which engineering table answers which question: holes for screws and bolts, tapped holes, nut traps, fits between parts, heat-set inserts, and what a printed part needs to fit
---

# Engineering references: which table to read

Values, not procedures. When a spec names a fastener, a fit or a printed joint, read
the table before choosing a dimension: a screw's hole is not the screw's diameter.

| The question | Read |
|---|---|
| a hole a screw or bolt passes through (M2 to M10, or a 4 mm wood screw) | `ref-fasteners`, clearance holes |
| a hole a screw threads into, or a thread's pitch | `ref-fasteners`, pitch and tap drills |
| a pocket that holds a hex nut | `ref-fasteners`, hex nuts |
| a shaft in a bore: running, sliding, located, pressed | `ref-fits` for the classes; `ref-fdm` for printed parts |
| a heat-set threaded insert | `ref-fdm`, inserts |
| a printed hole that must come out at size, or a printed wall | `ref-fdm` |

Every value in the tables names its source. ISO standards are cited by number; their
text isn't reproduced, and the values come from free reproductions that were checked
against each other. Where sources disagree, both are given. Values marked
**practice** are printers' experience, not a standard.
