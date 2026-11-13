# Print lessons

What a print taught, checked by a reviewer, and given to a later part with the same shape (mqucifer/print-shop#106, M19). Before this, the ledger saved every job's answers, proposal, corrections and outcome, and nothing read them back. On the first real prints, the same correction was typed at the review on every job: supports off, the plate temperature, the auxiliary and exhaust fans. The learning stayed with the person at the printer.

It's the CAD loop's learning (`docs/rounds.md`, "Lessons"), on the print side. "The learning loop is like training" (the Sponsor): candidates, a reviewer's step, giving to the next job, scoring, and promoting. The statuses, the signatures and the scores are imported from `rounds/lessons.py`, not a second vocabulary: `candidate`, then `accepted`, `edited` or `rejected`, then `given`, then `held` or `broken`, then `promoted` or `retired`.

## Keyed to the part, not the material

A lesson is keyed to the part's **traits** (`src/print_shop/setup/traits.py`). Each trait is read from the features measured from the mesh as it prints (`setup/features.py`) by a stated threshold:

| Trait | Read from | When |
|---|---|---|
| `long_and_thin` | `aspect` | its longest side on the plate is 3 or more times its shortest |
| `tall_and_narrow` | `slenderness` | more than 2.5 times as tall as its narrowest width |
| `flat_and_wide` | `slenderness` | less than a quarter as tall as its narrowest width |
| `small_bed_contact` | `bed_contact` | under 200 mm2 touches the plate |
| `large_bed_contact` | `bed_contact` | 5000 mm2 or more touches the plate |
| `overhangs` | `overhang` | faces past 45 degrees from vertical, off the plate |
| `bridges` | `bridge` | flat downward faces off the plate |
| `thin_walls` | `section` | a typical section under 2 mm |

`aspect` is a new feature, the longest side on the plate over the shortest. The adhesion group's thresholds in the skill are the same numbers. **A lesson reaches a later part that has every trait it's keyed to.** Not the material (the Sponsor, 2026-11-13): the filament is in a lesson's evidence, for the reviewer and the model to weigh.

The ledger now keeps each print's features (`prints.features`), so a print's traits can be read back. A print from before has none, and nothing is guessed: its lessons are listed as NOT KEYED until a reviewer keys them.

## Candidates, drawn by code

When the ledger records a print (`Ledger.record_start_requested`) and again when its outcome is answered (`record_answer`), `lessons.after_print` reads the row:
- **Each correction at the review is a candidate:** its setting and value, the Sponsor's reason as its text, and the part's traits as its keys. If the same key was corrected twice at one review, the one that stood is used.
- **The same correction on a later print is more evidence for the lesson it repeats,** not a second lesson. If that print's part doesn't have every trait the lesson is keyed to, the step says so, for the reviewer to widen the keys.
- **An outcome with something to say is a candidate:** a failure, or a success with a note. It sets nothing, and the model reads it.
- Each print's evidence is drawn once, however often it's read. A failure here never stops a print: it's logged, and `draw` does it again.

## The reviewer's steps

Nothing reaches a proposal until a reviewer lets it through. As with CAD lessons, the operator reviews, signing `--by "reviewer: <who>"`, and the Sponsor decides anything that's theirs (`sponsor`, or `stand-in: <who>`). Every step is a row of `print_lesson_steps`, with who and why, and an edit keeps the words it replaced.

```
uv run python scripts/print_lessons.py [JOB]                   every lesson, or what a print taught and was given
uv run python scripts/print_lessons.py draw                    candidates from every print, prints from before included
uv run python scripts/print_lessons.py traits                  the traits, and what each means
uv run python scripts/print_lessons.py accept ID --by WHO
uv run python scripts/print_lessons.py edit ID --by WHO [--text FILE] [--setting K=V|none] [--traits T,T] [--why W]
uv run python scripts/print_lessons.py reject ID --by WHO --why W
uv run python scripts/print_lessons.py retire ID --by WHO --why W
uv run python scripts/print_lessons.py promote ID --as skill --where owner/repo#n --by WHO
uv run python scripts/print_lessons.py score JOB ID --score used|not_used|in_the_way --by WHO [--why W]
uv run python scripts/print_lessons.py write JOB --by WHO --text FILE --traits T,T [--setting K=V]
```

`--ledger FILE` works on a copy. **A lesson keyed to no trait would reach every part, so it isn't accepted until it's keyed to some** (`edit --traits`). Widen the keys to what the lesson is about: a plate temperature learned on a long thin part is `long_and_thin`, not every trait that part happened to have.

## Giving it to a later part

The setup model's proposal (`setup.Proposer`) is given each kept lesson for the part's traits (`lessons.for_part`):
- **A lesson's setting is set,** as an intake answer is, and the model may not change it: a suggestion for that key is refused, saying which lesson set it. A value that's the preset's own is kept as a decision, so the guard leaves it: supports kept off by a lesson stay off.
- **The intake answer comes first,** as it's the Sponsor's for this part. Of two lessons for one setting, the one keyed to more traits sets it, then the newer.
- **The model reads every one,** with where it came from and who kept it, in its prompt.
- **The review shows which lesson set what:** the change says `[lesson N: its traits]`, and a block lists each lesson given, with whether it set its setting or why not. `print_job.py --list` has a `lesson` line for each.
- **The ledger records each one given,** a row of `job_lessons`, when the print's row is written. A job stopped at the review never prints, so it has no row.

## Scoring, promoting and retiring

- **The review scores a lesson's setting by what the Sponsor did with it:** it stood, `used`; they corrected it, `in_the_way`; their answer or a closer lesson set it, `not_used`. A lesson that sets nothing is scored by the reviewer (`score`). A lesson's status follows each print's latest score, as the CAD loop's does: `used` is `held`, `in_the_way` is `broken`.
- **Used on two prints that didn't fail, without a correction, it's listed to promote:** a line in the print-settings skill, by PR, then `promote ID --as skill --where owner/repo#n`. Once in the skill it's read there, and isn't given as a lesson.
- **One that got in the way, or was given twice and not used, is listed to retire.** Code retires nothing. A correction of a lesson's setting is also a candidate of its own.

## Tables

In the print ledger, created if missing, without moving its schema version: `print_lessons`, `print_lesson_steps`, `print_lesson_evidence`, `job_lessons` and `print_lesson_scores`, and the column `prints.features`, added if missing. Older code still runs against the file: it inserts into `prints` by column name. On a copy of the real ledger every row reads back as it was, and a second open and draw changes nothing.

**Not yet:** a print's slice readings as evidence (mqucifer/print-shop#105, once it lands), lessons entered from a file (`rounds.py lessons load`'s equivalent), and a lesson for the harness rather than the proposal.
