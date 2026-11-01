---
name: ref-fasteners
description: metric fastener values: ISO 273 clearance holes, ISO 262 pitches and tap drills, ISO 4032 hex nuts, and a 4 mm wood screw's clearance, each with its source
---

# Fasteners

## Clearance holes (ISO 273), mm

A hole a screw passes through. Fine is close, medium is normal, coarse is free.

| Size | Fine (H12) | Medium (H13) | Coarse (H14) | Source |
|---|---|---|---|---|
| M2 | 2.2 | 2.4 | 2.6 | [S1] [S2] |
| M2.5 | 2.7 | 2.9 | 3.1 | [S1] [S2] |
| M3 | 3.2 | 3.4 | 3.6 | [S1] [S2] |
| M4 | 4.3 | 4.5 | 4.8 | [S1] [S2] |
| M5 | 5.3 | 5.5 | 5.8 | [S1] [S2] |
| M6 | 6.4 | 6.6 | 7.0 | [S1] [S2] |
| M8 | 8.4 | 9.0 | 10.0 | [S1] [S2] |
| M10 | 10.5 | 11.0 | 12.0 | [S1] [S2] |

[S3] gives a medium of 2.25 (M2), 2.8 (M2.5), 3.35 (M3) and 6.5 (M6) against ISO 724,
not ISO 273; the rest agree.

**A 4 mm wood screw** through a plastic part: no standard covers it (ISO 273 is for
metric bolts and screws). Practice: about 4.4 to 4.5 mm.

| Screw | Clearance hole | Source |
|---|---|---|
| 4 mm (gauge 8) wood screw | 4.5 (practice) | [S4] |
| #8 (0.164 in, 4.17 mm) wood screw shank | 11/64 in, 4.37 (practice) | [S5] |

## Coarse pitch (ISO 261/262) and tap drills, mm

A hole a screw threads into is the tap drill, not the screw's diameter.

| Size | Pitch | Tap drill | Source |
|---|---|---|---|
| M2 | 0.4 | 1.6 | [S6] [S7] [S2] |
| M2.5 | 0.45 | 2.05 ([S8]: 2.0) | [S6] [S7] [S2] [S8] |
| M3 | 0.5 | 2.5 | [S6] [S7] [S2] |
| M4 | 0.7 | 3.3 | [S6] [S7] [S2] |
| M5 | 0.8 | 4.2 | [S6] [S7] [S2] |
| M6 | 1.0 | 5.0 | [S6] [S7] [S2] |
| M8 | 1.25 | 6.8 ([S2]: 6.75) | [S6] [S7] [S2] [S8] |
| M10 | 1.5 | 8.5 | [S6] [S7] [S2] |

## Hex nuts (ISO 4032), mm

For a nut trap: the width across flats `s` and the height `m`. A trap is larger than
the nut by a printed fit's clearance (`ref-fdm`).

| Size | s (max / min) | m (max / min) | Source |
|---|---|---|---|
| M2 | 4.00 / 3.82 | 1.60 / 1.35 | [S9] [S10] |
| M2.5 | 5.00 / 4.82 | 2.00 / 1.75 | [S9] [S10] |
| M3 | 5.50 / 5.32 | 2.40 / 2.15 | [S9] [S10] |
| M4 | 7.00 / 6.78 | 3.20 / 2.90 | [S9] [S10] |
| M5 | 8.00 / 7.78 | 4.70 / 4.40 | [S9] [S10] |
| M6 | 10.00 / 9.78 | 5.20 / 4.90 ([S10]: 5.00 / 4.70) | [S9] [S10] |
| M8 | 13.00 / 12.73 | 6.80 / 6.44 | [S9] [S10] |
| M10 | 16.00 / 15.73 | 8.40 / 8.04 | [S9] [S10] |

## Sources

- [S1]: https://engineeringhardware.com/fastener/clearance-hole-sizes/ ("Reference Data (ISO 273)")
- [S2]: https://optimas.com/technical-resources/tapping-sizes/ (clearance and tapping sizes, coarse thread)
- [S3]: https://www.engineeringtoolbox.com/iso-metric-thread-drill-tap-size-d_2045.html
- [S4]: https://handycrowd.com/screws-explained-gauge-size-pilot-holes-and-wall-plugs/ (a DIY guide: practice)
- [S5]: https://www.engineeringtoolbox.com/wood-screws-holes-d_1519.html
- [S6]: https://en.wikipedia.org/wiki/ISO_metric_screw_thread ("ISO 262 selected sizes")
- [S7]: https://www.engineersedge.com/hardware/iso_metric_tap_14585.htm
- [S8]: https://www.trfastenings.com/Knowledge-Base/Engineering-Data/tapping-sizes-and-clearance-holes
- [S9]: https://www.wermac.org/bolts/dimensions_hex-nuts_across-flats-and-heights_din-iso.html
- [S10]: https://fasten.it/norms/norm/din_en_iso_4032
