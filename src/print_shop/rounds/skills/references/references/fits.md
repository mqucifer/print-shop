---
name: ref-fits
description: ISO 286 hole-basis fits: what H7/g6, H7/h6, H7/k6 and H7/p6 are for, and their deviations from 3 to 30 mm, each with its source
---

# Fits (ISO 286), hole basis

A fit is a hole's tolerance against a shaft's. These are machined tolerances, in
micrometres: a printed part can't hold them, and its clearances are in `ref-fdm`. They
say which kind of fit a spec means.

| Fit | Kind | What it's for | Source |
|---|---|---|---|
| H7/g6 | sliding (clearance) | moves and turns freely and locates accurately, but not meant to run freely | [F1] |
| H7/h6 | locational clearance | a snug fit for locating stationary parts, assembled and taken apart freely | [F1] |
| H7/k6 | locational transition | accurate location, between clearance and interference | [F1] [F2] |
| H7/p6 | locational interference (press) | rigidity and alignment; pressed together | [F1] [F2] |

## Deviations, µm (upper / lower)

| Size, mm | H7 | g6 | h6 | k6 | p6 | Source |
|---|---|---|---|---|---|---|
| over 3 to 6 | +12 / 0 | −4 / −12 | 0 / −8 | +9 / +1 | +20 / +12 | [F3] [F2] |
| over 6 to 10 | +15 / 0 | −5 / −14 | 0 / −9 | +10 / +1 | +24 / +15 | [F3] [F2] |
| over 10 to 18 | +18 / 0 | −6 / −17 | 0 / −11 | +12 / +1 | +29 / +18 | [F3] [F2] |
| over 18 to 30 | +21 / 0 | −7 / −20 | 0 / −13 | +15 / +2 | +35 / +22 | [F3] [F2] |

## Sources

- [F1]: https://amesweb.info/fits-tolerances/preferred-tolerances-table.aspx (preferred hole-basis fits)
- [F2]: https://en.wikipedia.org/wiki/Engineering_fit
- [F3]: https://www.cadforum.cz/en/tolerance.asp (ISO 286-2 hole and shaft tolerances)
