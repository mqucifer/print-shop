# Print lessons

What a print taught, checked by a reviewer, and given to a later part with the same shape (mqucifer/print-shop#106, M19). Before this, the ledger saved every job's answers, proposal, corrections and outcome, and nothing read them back. On the first real prints, the same correction was typed at the review on every job: supports off, the plate temperature, the auxiliary and exhaust fans. The learning stayed with the person at the printer.

It's the CAD loop's learning (`docs/rounds.md`, "Lessons"), on the print side. "The learning loop is like training" (the Sponsor): candidates, a reviewer's step, giving to the next job, scoring, and promoting. The statuses, the signatures and the scores are imported from `rounds/lessons.py`, not a second vocabulary: `candidate`, then `accepted`, `edited` or `rejected`, then `given`, then `held` or `broken`, then `promoted` or `retired`.

## Keyed to the part, not the material

A lesson is keyed to the part's **traits** (`src/print_shop/setup/traits.py`). Each trait is read from the features measured from the mesh as it prints (`setup/features.py`) by a stated threshold:

| Trait | Read from | When |
|---|---|---|
| `long_and_narrow` | `length`, `footprint_aspect` | its footprint is 100 mm or longer and 3 or more times as long as it's wide |
| `tall_and_narrow` | `slenderness` | more than 2.5 times as tall as its narrowest width |
| `flat_and_wide` | `slenderness` | less than a quarter as tall as its narrowest width |
| `small_bed_contact` | `bed_contact` | under 200 mm2 touches the plate |
| `large_bed_contact` | `bed_contact` | 5000 mm2 or more touches the plate |
| `overhangs` | `overhang` | faces past 45 degrees from vertical, off the plate |
| `bridges` | `bridge` | flat downward faces off the plate |
| `thin_walls` | `section` | a typical section under 2 mm |

`long_and_narrow` is the skill's own test for the adhesion group (`skill.LONG_MM`, `skill.NARROW_ASPECT`, from mqucifer/print-shop#107), and the other thresholds are the adhesion group's numbers, so a trait and the skill read a part alike. **A lesson reaches a later part that has every trait it's keyed to.** Shape decides where a lesson applies, not the material (the Sponsor, 2026-11-13), with one exception, below.

The ledger now keeps each print's features (`prints.features`), so a print's traits can be read back. A print from before has none, and nothing is guessed: its lessons are listed as NOT KEYED until a reviewer keys them.

### A filament's temperatures and fans also need the same filament

**A lesson that sets a temperature or a fan also holds only for the filament type of the print it was learned on** (mqucifer/print-shop#117; the Sponsor, 2026-11-14). A plate temperature learned on PETG says nothing about PLA, and the model can't change a setting a lesson set, so without this it would set PETG's plate on a PLA part of the same shape.
- **Which settings:** `bounds.MATERIAL`, beside `bounds.SETTINGS`. It holds every setting of the filament's own preset (nozzle and plate temperatures, the part-cooling, auxiliary and exhaust fans, layer cooling), and Orca's other temperatures and fans a correction may name. Brim, supports, walls, seam and the rest go by shape alone, and so does a lesson only read.
- **Its material** is the `filament_type` the print it was learned on was sliced with. The same correction on another filament is a lesson of its own.
- **At the review,** a lesson whose shape matched but whose filament didn't is listed as not given, with why ("learned on PETG; this print is PLA"). It sets nothing, the model isn't given it, and the ledger neither records it as given nor scores it.
- **For the reviewer,** the listing shows each one's material. `edit ID --material any` widens it to every filament, and `--material PLA` names one.
- **Lessons from before** take their material from their print's own record when the ledger opens, each with a step saying so. A print that doesn't say leaves the material NOT KNOWN, and the lesson isn't given until a reviewer names one. On a copy of the ledger with the first 11 lessons, the 6 temperature and fan lessons each took PETG from their prints, and a second open changed nothing.

### And they're set only on the variety they were learned on

**Of its own filament type, such a lesson is set only on the variety it was learned on** (mqucifer/print-shop#219; the Sponsor, 2026-11-21, changing "same type is enough" from 2026-11-14 as far as what's pre-selected). Two varieties of a type don't start from the same preset, so one lesson's value is a different change on each: a plate temperature learned on one PETG variety is another change on a PETG whose preset is 10 degrees warmer.
- **Its variety** is the one the inventory gave the print it was learned on (`print_material`, mqucifer/print-shop#11), read from that print each time it's looked up. This print's is its loaded spool's. Where either print doesn't say, nothing is guessed from a profile's name.
- **On the same variety,** it's set as before, and a past print's item for it is pre-selected at Use these.
- **On another variety of its type, or where either variety isn't known,** it sets nothing, the model isn't given it, and the Lessons block says why ("not given: learned on Basic PETG; this print is Fast PETG"). It's neither set nor hidden: Use these lists it under **LESSONS NOT SET FOR THIS VARIETY**, unticked, with the variety it was learned on and how many prints that didn't fail it was used on, whichever past print is picked, and one `use` takes it. A past print's own override of such a setting on another variety is unticked too, saying both varieties.
- **Taken there, it's given and scored as used:** its `job_lessons` row is applied, with the why `taken at the review, from use these`. Left unticked, it's neither given nor scored, so it's never in the way or given and not used.
- **Once taken on a variety in a print that didn't fail** (answered, and no part of its plate failed), it's ticked there from then on, saying so ("taken on Fast PETG on 23 Nov 2026, on rail.stl, which didn't fail"). It's still applied only by `use`: a lesson checked once isn't then set without being seen. Making it that variety's own is the reviewer's step.
- **A lesson widened to `any` stays widened,** and is set on every variety. Another filament type's is withheld, as above, and a shape's setting (pause, supports, walls, brim) goes by shape alone.

## Candidates, drawn by code

When the ledger records a print (`Ledger.record_start_requested`) and again when its outcome is answered (`record_answer`), `lessons.after_print` reads the row:
- **Each correction at the review is a candidate:** its setting and value, the Sponsor's reason as its text, and the part's traits as its keys. If the same key was corrected twice at one review, the one that stood is used. One that didn't reach the slice is still a candidate, as it's what the Sponsor wanted, and its evidence says it wasn't applied.
- **The same correction on a later print is more evidence for the lesson it repeats, where that lesson would reach its part** (mqucifer/print-shop#212): the part has every trait the lesson is keyed to, and a filament's setting is in the lesson's filament. It's not a second lesson then.
- **Where that lesson wouldn't reach it, it's a candidate of its own,** keyed to this part's traits, so a correction can reach the shape it was typed on. The listing links lessons that set the same to the same, for the reviewer to keep both or widen one. A part with no traits adds to the candidate that's also keyed to none, and waits with it to be keyed.
- **A correction absorbed before that rule is drawn again by `draw`, once, and listed.** Before, it was evidence for the other lesson only, whatever its shape. `draw` adds it to a lesson that reaches its part, or makes it a candidate, and its evidence says which lesson had it until now. The other lesson keeps its evidence as it was. None is accepted by code, and one rejected isn't drawn again.
- **A change the Sponsor took from the slice's reading is a candidate of its own kind** (`reading`, mqucifer/print-shop#105), not a correction: the reading's words are its evidence.
- **Every lesson's evidence carries what that print's slice flagged,** with its shortest layer, ironing time and how stacked its seams were, where the slice was read.
- **An outcome with something to say is a candidate:** a failure, a success with a minor flaw, or a success with a note. It sets nothing, and the model reads it. A flawed success's text starts with `success, with stringing` (or whichever symptom it was), then the note, so the model reads that the part was usable (mqucifer/print-shop#189). A lesson used on such a print counts as used on one that didn't fail, and the list to promote shows the flaw.
- Each print's evidence is drawn once, however often it's read. A failure here never stops a print: it's logged, and `draw` does it again.

### A hand-sliced print teaches by its outcome only

A file sliced by hand and sent with `print_job.py --sliced` (mqucifer/print-shop#164) has nothing the loop chose: no questions, no proposal, no corrections. So no lesson is given to it, none is scored on it, and no correction is drawn from it. Its outcome is a candidate like any other. Its evidence is marked `sliced: by hand` and carries the file's own settings in place of a part's shape:
- the three profile names Orca wrote;
- each setting that differs from the inventory's tuning for the loaded filament;
- each object's overrides, where the file was Orca's sliced plate file.

No part was measured, so the candidate is keyed to no trait, and it can't be accepted until a reviewer keys it (`edit --traits`).

### A plate of several parts is one print

A job of several parts (mqucifer/print-shop#109) has a ledger row for each object, and one review:
- **What the review taught is drawn once,** at the first part's row: its corrections and what was taken from the slice, keyed to the traits of the one part with the most, as lessons are looked up part by part: never wider than that part's alone, and it reaches the same plate again. Every part's traits are in its evidence, for the reviewer to key it otherwise. Typed again, it's more evidence only for a lesson that would reach that part, as for one part (mqucifer/print-shop#212), else a linked candidate. `print_sequence` is that plate's choice and is never a lesson.
- **A lesson given to the plate is given and scored once.** Toward promoting or retiring it, a plate counts as one print, and one with any failed part doesn't count toward promoting.
- **Offered later by use these, a plate is one print too** (mqucifer/print-shop#211), by its part most like the one printing, and its `print_sequence` is never offered.
- **Each part's own:** a pose sent back, keyed to that part's traits, and its outcome, with that part's shape and the plate's order in its evidence (by object or by layer).

### A pose sent back is a lesson for the orientation step

**A pose sent back at the review is a correction too** (mqucifer/print-shop#128): `orientation`, from the pose the step chose to the one the person chose, with their reason.
- **Its evidence** names the job, both poses and both readings: the overhang, the area on the plate and the height of each.
- **It's keyed to the part's traits in the file's own pose,** before any turn, because that's the pose the orientation step reads when it looks lessons up. A lid laid flat is `flat_and_wide, large_bed_contact` whichever way the step turned it.
- **Once kept, it's given to the orientation step** with its poses (`lessons.for_orienting`), never to the settings proposal: it turns nothing by itself, and the model reads it. The review lists it with the others, "given to the orientation step with its poses", and the ledger has its `job_lessons` row.
- **It's scored at the review:** `used` if the pose wasn't sent back again, and `in_the_way` if it was.

## The reviewer's steps

Nothing reaches a proposal until a reviewer lets it through. As with CAD lessons, the operator reviews, signing `--by "reviewer: <who>"`, and the Sponsor decides anything that's theirs (`sponsor`, or `stand-in: <who>`). Every step is a row of `print_lesson_steps`, with who and why, and an edit keeps the words it replaced.

```
uv run python scripts/print_lessons.py [JOB]                   every lesson, or what a print taught and was given
uv run python scripts/print_lessons.py draw                    candidates from every print, prints from before included
uv run python scripts/print_lessons.py traits                  the traits, and what each means
uv run python scripts/print_lessons.py accept ID --by WHO
uv run python scripts/print_lessons.py edit ID --by WHO [--text FILE] [--setting K=V|none] [--traits T,T] [--material TYPE|any] [--why W]
uv run python scripts/print_lessons.py reject ID --by WHO --why W
uv run python scripts/print_lessons.py retire ID --by WHO --why W
uv run python scripts/print_lessons.py promote ID --as skill --where owner/repo#n --by WHO
uv run python scripts/print_lessons.py score JOB ID --score used|not_used|in_the_way --by WHO [--why W]
uv run python scripts/print_lessons.py write JOB --by WHO --text FILE --traits T,T [--setting K=V]
```

`--ledger FILE` works on a copy. **A lesson keyed to no trait would reach every part, so it isn't accepted until it's keyed to some** (`edit --traits`). Widen the keys to what the lesson is about: a plate temperature learned on a long thin part is `long_and_narrow`, not every trait that part happened to have.

## Giving it to a later part

The setup model's proposal (`setup.Proposer`) is given each kept lesson for the part's traits (`lessons.for_part`):
- **A lesson's setting is set,** as an intake answer is, and the model may not change it: a suggestion for that key is refused, saying which lesson set it. A value that's the preset's own is kept as a decision, so the guard leaves it: supports kept off by a lesson stay off.
- **Set means sliced** (mqucifer/print-shop#206). The setting is written to the profile it's declared for (`bounds.SETTINGS`, then `bounds.MATERIAL` as the filament's), else to the profile that has the key (`setup/reach.py`), and read back from the sliced G-code's config. One that can't be placed sets nothing and says why. One the slice didn't take is shown as asked and sliced ("asked 30, sliced 80"), and isn't applied.
- **The intake answer comes first,** as it's the Sponsor's for this part. Of two lessons for one setting, the one keyed to more traits sets it, then the newer.
- **The model reads every one,** with where it came from and who kept it, in its prompt.
- **The review shows which lesson set what:** the change says `[lesson N: its traits]`, and a block lists each lesson given, with whether it set its setting or why not. `print_job.py --list` has a `lesson` line for each.
- **The ledger records each one given,** a row of `job_lessons`, when the print's row is written. A job stopped at the review never prints, so it has no row.

## Scoring, promoting and retiring

- **The review scores a lesson's setting by what the Sponsor did with it:** it stood, `used`; they corrected it, or took the slice's reading instead, `in_the_way`; their answer or a closer lesson set it, or it didn't reach the slice, `not_used`. A lesson that sets nothing is scored by the reviewer (`score`). A lesson's status follows each print's latest score, as the CAD loop's does: `used` is `held`, `in_the_way` is `broken`.
- **Used on two prints that didn't fail, without a correction, it's listed to promote:** a line in the print-settings skill, by PR, then `promote ID --as skill --where owner/repo#n`. Once in the skill it's read there, and isn't given as a lesson.
- **One that got in the way, or was given twice and not used, is listed to retire.** Code retires nothing. A correction of a lesson's setting is also a candidate of its own.

## Use these, beside lessons

A lesson is for any part with its traits, so a reviewer keeps it first. A person's own overrides on a part like the last one need no reviewer: the review offers them and the person confirms them (mqucifer/print-shop#211, `src/print_shop/setup/offer.py`, and the runbook's "Use these").

- **Closeness is measured, not keyed to traits** (`src/print_shop/setup/similar.py`). The same bytes come first. Then each measure's difference is averaged into a shape score, which counts for 80%, with the context and the source 10% each. A part just across a trait's threshold still gets the offer, with the difference shown.
- **A lesson that applied to that print and wouldn't to this one is listed,** with the trait it misses, where it would have dropped out in silence. It's pre-selected unless the part is far from that trait's measure, or the lesson has been edited or retired since.
- **Using one accepts no lesson.** The person's own override, used again, counts as one typed does: evidence for a lesson that reaches this part, marked as used, or, where none does, a candidate keyed to this part for the reviewer (mqucifer/print-shop#212). So using in place of typing still reaches the shape it was used on. One a reviewer rejected or retired for this shape isn't drawn again by using it. An answer's, a lesson's or the slice's draws nothing.
- **A lesson whose setting was used at its own value stood,** and is scored `used`. Used at another value, it was `in_the_way`.
- **A lesson of this filament type not set for this variety** is offered beside the past print's, in its own group, unticked unless it was taken on this variety in a print that didn't fail (mqucifer/print-shop#219, above). Where the picked print had it too, it's said once, there.
- **In the ledger,** a used item is a correction with `used`: the print it came from, whose it was, and the print it began on.

## Tables

In the print ledger, created if missing, without moving its schema version: `print_lessons`, `print_lesson_steps`, `print_lesson_evidence`, `job_lessons` and `print_lesson_scores`, and the columns `prints.features` and `print_lessons.material`, added if missing. Older code still runs against the file: it inserts into `prints` by column name. On a copy of the real ledger every row reads back as it was, and a second open and draw changes nothing.

**Not yet:** keying a lesson to the intake's answer on which faces are seen (`questions.SEEN`), lessons entered from a file (`rounds.py lessons load`'s equivalent), and a lesson for the harness rather than the proposal.

## Lessons from a settings study

A settings study (mqucifer/print-shop#339, `docs/settings-study.md`) prints nothing, so its lessons aren't print lessons: they're kept beside them in `study_lessons`, keyed by the part's traits as these are and by its joints, with the same statuses and the same reviewer's steps (`scripts/settings_study.py lessons`, `accept`, `edit`, `reject`). A kept one reaches a later study of a part with every one of its keys, never the print loop's proposal.
