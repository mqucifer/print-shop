# A part's settings studied

mqucifer/print-shop#339. The Sponsor, 2026-11-26: "I was hoping that the model could go through a couple of different iterations of infill or other strength settings to try and see where the trade off between strength, material use and time is ... depending on print orientation different infill will have a different impact. ... Today it's just spitting my defaults back at me."

So the print loop's model (`setup.model.setup_llm`, through LiteLLM :4000) studies a part's settings with tools, as the print loop chooses a pose (mqucifer/print-shop#129). It isn't a fixed sweep: it reads where the part is stressed and which way, chooses candidates for that stress, goes on where the trade-off is still open, and recommends one against the profile's own. **Nothing is printed, armed or sent to the queue.** The table and the recommendation are for the Sponsor at the gate.

It builds on two pieces: regions of a part's own settings sliced through Orca's own modifiers (mqucifer/print-shop#341, `slicer.regions`, `slicer.joints`) and `load` reading a part as printed (mqucifer/print-shop#342, `bench.as_printed`, `bench.infill`).

## Running one

On the Mac, with Orca installed and the bench's second image built (`scripts/bench.py build cae`):

```
uv run python scripts/settings_study.py run PART.stl --assembly A.json --part NAME \
    --nozzle 0.4 --filament "Elegoo PETG" --turn 0,0,0 \
    --case 'standing=--weight 30 --at 10,10,700' --case 'lifted=--lift top' \
    --out ~/print-shop/design-runs/RUN/studies/NAME
```

- **`PART.stl`** is the part in its own file's axes: the same solid as the STEP file the assembly names for it, which `load` reads. The regions are in those axes, so both sides read them alike.
- **`--assembly`** is what `load` reads the load cases on, and **`--part`** the part's name in it. A part's loads come from its own design run's load cases, read on the part alone with what holds and loads it, so it reads in one pass: a printed part read in two passes has no answer yet (below).
- **`--turn`** is the pose it prints in, one of the print loop's (`ingest.orientation.AXIS_ALIGNED`), and must stand the assembly's `layers` axis up. The model may read another pose where that changes what the layers carry; `load` is then told the layers' axis that pose gives.
- **Each `--case`** is a name and `load`'s words after the assembly file. Each case's name is its own, of letters, digits, `.`, `_` and `-`: the table's columns and each case's `load` replies are keyed by it, so two of one name, between `--case` and the needs' ids, are refused. Or `--needs RUN/record/needs.json` takes each `load` need of a design run as a case (only `--need ID`'s, where given), with the need's own figure and limit (`moves_mm` at most 3, say) besides the material's; the run's assembly word is replaced by `--assembly`, and its `--out` left out.
- **`--context`** is a sidecar or a design run's folder, for the part's fits (`setup.context`): a region can be put round each.
- **`--out`** is where it writes: `study.md`, the table and recommendation for the gate; `study.json`; and `candidates/NAME/` for each, with its Orca project (`regions.3mf`), its G-code and each case's `load` reply. Without it, a new folder under the system's temporary one. Keep a real part's study with its run's records, off this repository.
- **`--ledger`**: the Mac's ledger unless given. `--max-candidates` (10) and `--max-hours` (6) are the loop's limits.

A candidate is two slices and a `load` for each case, about a minute each on a small part, so a study of six candidates under two cases is about fifteen minutes, and a leg's longer. The model's calls are limited too (`study.loop.MAX_CALLS`).

## What the model can do

- **`published`**: what the published tests say of each pattern `load` reads (`bench/infill.py`, each cited there and in `docs/bench.md`): which way its lines run and whether they build walls through the layers, how stiff it is along its angle and across its layers at 20, 40 and 60%, what strain it takes, and from what density a test checks it; the walls' thickness by count, by Orca's bead spacing; and Orca 2.4.2's own words for each wall order. **Direction matters as much as density**: a pattern is strongest along its lines, walls run along the outline, and a pull across the layers is the layers' bond.
- **`read`**: a candidate (`study.candidate`): the part's `walls`, `top`, `bottom`, `infill`, `pattern` and `infill_angle` over the profile's own; its `wall_sequence`, for finish only, as `load` reads walls by their count in any order; its `turn`; and `regions`: a `box`, a `cylinder` or a band of `layers` as `bench/printed.py` takes them, or `{"joints": "all" or [names], "margin": MM, "faces": true, ...settings}` for a region round each of the part's joints: a cylinder round each bolt's hole, and round each fit and latch a box, or with `faces` a mesh of the part's own faces in it, `margin` deep (`candidate.faces_mesh`). Code bounds each: the infill 10 to 60% and the patterns `load` reads, walls 1 to 10 and shells 3 to 15. The line width and layer height are the profile's. The reply has its time and grams against the defaults, each case's worst figures against their limits, where and which way, what Orca built in each region, the walls' band as built, and the trade-off so far: the candidates no other passing one beats on time, grams and margin at once.
- **`past_studies`**: the kept lessons from studies of parts with the same keys, and what those studies recommended.
- **`recommend`**: a candidate it read, or `none` where none passes, with its reason and what it found as lessons. Code refuses one that fails a case or has no answer under one, and any before it has read two candidates of its own: it's there to study the settings, not to restate them.

The profile's own settings are read first, by code, so every candidate is compared with them. Past a limit, or with the model out of reach, the study ends with the table so far and no recommendation, saying why.

## The figures, and what they rest on

Of each `load` reply the study reads the part's own figures (`study.reading.figures`): its skin pulled across its layers and stretched any way, against the material's limits; each core's, against its own; and each joint it's in, by its nearest figure to its limit. Each is a share of its limit, and its margin is what's left. **The case's own need is judged exactly as the design run's is,** by `rounds.needs`' own `number_at` and `_judge_number`: a list's index followed (`pairs.0.gap_mm`), a figure at its limit to arithmetic's noise passing, a limit of 0 and a figure of 0 or less judged as they are, and no figure at all failing. A candidate passes where every case is `OK` and every figure is within its limit. `NO ANSWER`, `UNREAD` and `DISAGREE` fail it, saying why.

**A region Orca didn't build as asked fails its candidate, as not as sliced.** `load` reads a region as asked, so where the reader of the layers (`slicer.regions`) finds Orca built less, as 2 walls where 5 were asked in a band too narrow for them, or the part's own where the region's were asked, `load` would read the part stronger than it prints. The candidate isn't read by `load`, the table says so, and it can't be recommended. What couldn't be read from the layers at all, such as a pattern Orca was given by name, is taken as asked, and the table says that too.

The table says what each figure rests on, from the limits of `load`'s as-printed reading (mqucifer/print-shop#337):
- **The infill model.** A core's figures are published tests of PLA, ABS and PETG, as shares of their own plastic's, none yet checked against a print from this printer: that's mqucifer/print-shop#340's coupons. Every figure of a printed part rests on them, the skin's too, as what the skin carries is what the core doesn't. **Below the density any published test of a pattern reaches** (`infill.checked_from`, mqucifer/print-shop#342's `unchecked`: 20% for grid and the line patterns, 40% for gyroid and triangles), no test checks a core at all: the table marks those figures `*`, the skin's beside them too, and the recommendation's own paragraph names each cell, by its pattern and density.
- **Joints re-solved solid.** What `load` solves again round a through-bolt's hole, a dovetail's neck and walls and its latch's tab reads the plastic solid, stronger than printed: read those figures with a margin.
- **One pass only.** A printed part `load` would read in two passes is `NO ANSWER`.

**The walls as built, a second reading** (mqucifer/print-shop#341's reader, from mqucifer/print-shop#342's review): `load` takes n walls as n of Orca's bead spacings, width − layer height × (1 − π/4), 0.814 mm for 2 walls at 0.45 and 0.2. The slice's layers say how wide the walls' band lies, the part's and each region's with walls of its own (`slicer.regions.band`): on the bar of `tests/test_slice_regions.py`, 0.901 mm for 2 walls and 1.697 for 4, a bead's width past its spacing. A gap of more than half a line's width past that says the settings alone don't give the skin `load` reads there.

## The ledger

A study isn't a print, so it has no row of `prints`. It's kept in tables of its own in the same file (`study.lessons`, created if missing): `settings_studies`, with the part, its features as it prints, its keys, the cases, every candidate and its figures, the recommendation and every tool call; and `study_lessons`, what it found, each a candidate keyed by the part's features: its traits (`setup.traits`, as print lessons are, mqucifer/print-shop#106) and its joints (`joint:through`, `joint:dovetail`, `joint:latch`, `joint:fit`, ...). The model's own findings are drawn with the candidates that show them as their evidence, and code draws one from the recommendation against the defaults. **A reviewer lets each through**, as print lessons are, and only then does it reach a later study of a part with every one of its keys:

```
uv run python scripts/settings_study.py lessons [STUDY]
uv run python scripts/settings_study.py accept ID --by "reviewer: WHO"
uv run python scripts/settings_study.py edit ID --by "reviewer: WHO" [--text T] [--keys K,K] --why W
uv run python scripts/settings_study.py reject ID --by "reviewer: WHO" --why W
```

## Tests

`tests/test_settings_study.py` runs in CI: the bar of `tests/test_slice_regions.py`, a scripted model, a fake slicer and fake `load` replies, and a temporary ledger. `tests/test_settings_study_slicer.py` slices its candidates with the real Orca on the Mac (`-m slicer`). Every part in them is made up.
