# What the model did without the skill

The baseline for `designing-stained-glass`, named from the records of the three stained-glass runs, `stainedglass-1`, `cup-1` and `suncatcher-2` (their analyses and ledger rows are on the Mac, off this repository). Each gap is said in general terms, with the lesson it became where there is one. The evaluations in `evals.json` each test one or more of these. The skill was written after this file and the evaluations, to fill these gaps and nothing else.

| Gap | What the model did | Runs | Lesson | Where it's filled |
|---|---|---|---|---|
| G1 | Read the reference for what's in it, not how it's built: each ring of cells drawn as one unbroken loop, so the frame fell apart into separate pieces | `suncatcher-2` | 14 | the skill's step 1, and the breakdown at intake (mqucifer/print-shop#36) |
| G2 | Cut holes from a solid, so panes merged and the frame had separate bodies, found only once the bench read them | `stainedglass-1` | 1 | the skill's step 2: the 2D layout, the leading checked as one piece before any CAD |
| G3 | Put a floor under panes to make the frame one piece, where light must pass through every pane | `stainedglass-1`, `suncatcher-2` | 16, 24 | step 2's bars, and the `light` check |
| G4 | Never checked light through a pane on the bench: an open share printed by its own script, reported wrongly, or nothing | all three | 5, 16 | the `light` check in step 5, with its limit from the request |
| G5 | One lip size for every cell, so a small cell lost almost all its light | `suncatcher-2` | 17 | step 2's lip per cell |
| G6 | Mixed up a lip before play with a lip after it, and read a need's figure the wrong way | `suncatcher-2` | 18 | the terms, defined once |
| G7 | Lowered `capture --min-lip` try by try until it passed, where its need said otherwise | `suncatcher-2` | 18 | the feedback loop: a limit is never changed to pass |
| G8 | Settled the hanging requirement on a stand-in, the frame's bounds by `info` or the whole part's `thin`, not the wall round the hole | all three | 9, 22 | the hanging check, by `wall` |
| G9 | Left a pane failing `thin` behind and moved on to other files | `suncatcher-2` | 21 | the loop's rule: one file at a time until it passes |
| G10 | Drew a cell that tapers to a sharp point, which no nozzle prints | `suncatcher-2` | 20 | step 2's blunt tips |
| G11 | Ran `thin` only on the frame, so panes too thin to print passed every need | `cup-1` | 12 | `thin` on every pane in step 5 |
| G12 | Read `capture`'s own pane numbers as its file names, and never cut a section where a check and the design disagreed | `cup-1` | none | the reading notes in `references/checks.md` |
| G13 | Placed parts at the wrong heights, run after run | `stainedglass-1` | 2 | step 3: heights named once |
| G14 | No limit on a pane's thickness, and nothing read it: the request named none, and the run's panes were whatever the design made them | all three | none (the Sponsor, 2026-11-03) | the `thickness` check, with its limit from the request |

Every gap but G12 has an evaluation. G12 is how a reply is read, which `checking-stained-glass` says; an evaluation of it would need a frame whose openings the bench numbers in another order than its files, and a run long enough to misread one, so it's left to the next run's analysis.

What the skill doesn't take on: the stops a request names (mqucifer/print-shop#38, whose refusals hold them), and how much light PETG lets through at a thickness (out of scope).
