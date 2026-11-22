# Runbook: the first deliberate print

**For the Sponsor, at the printer.** mqucifer/print-shop#3. The agent's own start path has never been run on purpose: the prototype's `_confirm_started` has only ever met a start nobody wanted (2026-09-16). This print proves it, and puts the ledger's first real row on record.

**Only you arm the printer, and only for this one command.** No builder arms it. No test arms it: the test suite refuses an armed connection even with the flag set. The flag goes on the command line of the one run that should print, and nowhere else: not exported in a shell, and never in `.env`.

About 35 minutes: 10 to set up, about 11 printing, and the rest watching and answering.

## Once: print-shop's own checkout

Prints run as commands on the Mac, from print-shop's own checkout, `~/Documents/git/print-shop`, on `main`. Never from a builder's folder (`.worktrees/…`), which is removed when its PR merges.

```
test -d ~/Documents/git/print-shop || git clone https://github.com/mqucifer/print-shop.git ~/Documents/git/print-shop
cd ~/Documents/git/print-shop
git switch main && git pull
uv sync
```

Before each print, `cd ~/Documents/git/print-shop && git pull && uv sync`, so it prints with the reviewed profiles on `main`. Every command below runs from there.

**The profiles are print-shop's own,** in `profiles/`, scoped by `profiles/scope.json` to the 0.4 mm machine, its 0.20 mm process, and PLA and PETG:
- `profiles/standard/` holds Orca's standard Elegoo presets, whole;
- `profiles/tuned/` holds your tunings of them, each only the settings you changed.

A print uses your tuning where there is one, and the standard where you haven't tuned. `uv run python scripts/print_job.py --filaments` lists them. Orca's own folders are read only by the import (`profiles/README.md`), never by a print. After tuning something in Orca, `uv run python scripts/import_profiles.py` puts the change on a branch for a PR.

## Before

1. **The settings file.** Create `~/.config/print-shop/.env` with mode 600 (`chmod 600 ~/.config/print-shop/.env`). It holds these keys, the same names as the prototype's `.env`. Copy the values from there; none of them goes in any repository.

   | Key | What it is |
   |---|---|
   | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD` | the broker on the Pi, where both gates live |
   | `MQTT_TOPIC_PREFIX`, `MQTT_DISCOVERY_PREFIX`, `MQTT_NODE_ID` | keep the prototype's values, so the dashboard's entities stay the same |
   | `HA_TOKEN` | Home Assistant's token. Only the live dashboard checks use it |
   | `PRINTER_HOST`, `PRINTER_MAINBOARD_ID` | the Centauri Carbon |
   | `CREW_LLM_API_KEY` | a LiteLLM key for the orientation model, issued by the operator. Not the proxy's master key |

   Optional: `PRINT_SHOP_LEDGER` (default `~/print-shop/ledger.sqlite`), `PRINT_SHOP_LLM_URL` and `PRINT_SHOP_LLM_MODEL` (default LiteLLM at `127.0.0.1:4000`, alias `crew-code`), and `ORCA_BED_TYPE` (default `Textured PEI Plate`; the other value is `Cool Plate`). The `ORCA_MACHINE`, `ORCA_PROCESS`, `ORCA_FILAMENT` and `ORCA_DATADIR` keys are gone: the profiles come from `profiles/`, by what you say is fitted.

2. **What's on hand, once** (mqucifer/print-shop#11). With no AMS to read, you tell print-shop what's fitted and loaded, one line each:

   ```
   uv run python scripts/stock.py printer add cc1 "Elegoo Centauri Carbon"
   uv run python scripts/stock.py fit 0.4
   uv run python scripts/stock.py add "Elegoo PLA" white --grams 1000
   uv run python scripts/stock.py load 1
   ```

   - `stock` alone lists what's on hand, loaded and fitted.
   - `stock empty <id>` marks a spool used up.
   - `stock edit <id> --colour grey` or `stock edit <id> --tuning "…"` corrects a spool, keeping its number and whether it's loaded (mqucifer/print-shop#220). `--tuning none` is the variety's default. Prints already made keep the spool as it was. A job still waiting when you edit records the new colour; if the tuning changed, an Approve starts nothing and says so: set it back and approve again, or Reject and run the job again.
   - A variety you've tuned more than once asks which tuning the spool prints with when you add it (`--tuning "…"`).
   - Prints then take the fitted nozzle and the loaded spool by default, and say so.
   - A print with a swap leaves its last spool loaded in the stock when it ends (mqucifer/print-shop#218), once it's seen to end. A load you record after it started stands, though it's the same spool again. To start the next on the other spool, load it and record it (`stock load <id>`).

3. **Nothing from the prototype is running.** Both use the same topics. The prototype has no daemon command of its own, so one would only be running if someone started it by hand, from `~/Documents/git/3d-print-agent`. If one is, stop it.

4. **The dashboard.** In Home Assistant, replace the 3D Printing dashboard's raw configuration with `ha-configs/lovelace_3dprint.yaml` from this repository. It adds the outcome card ("How did it come out?"), which appears only when a print is waiting for an answer.

5. **The part.** The 20 mm test cube, the same one CI replays:

   ```
   cd ~/Documents/git/print-shop
   uv run python -c "from pathlib import Path; from tests.fixtures import write_stl; d = Path('~/print-shop/parts').expanduser(); d.mkdir(parents=True, exist_ok=True); print(write_stl('cube', d))"
   ```

6. **At the printer:**
   - **The bed is clear and clean.** The agent treats a printer that just finished as ready, so it would print onto a part left on the plate (the prototype's BACKLOG). You are the check.
   - **The standard 0.4 mm nozzle is fitted,** and **Elegoo PLA is loaded.** The inventory says so (step 2), and print-shop never assumes it. The print says "From the inventory: cc1 with a 0.4 mm nozzle; spool #1, Elegoo PLA, white". If what's at the printer differs, change the inventory first (`stock fit`, `stock load`). A nozzle the profiles don't cover (0.6 or 0.8 today) is refused, naming `profiles/scope.json`. It uses your tunings: `Elegoo Centauri Carbon 0.4 nozzle - Copy`, `0.20mm Standard @Elegoo CC 0.4 nozzle - Copy` and `Elegoo PLA @ECC - Copy`, each over Elegoo's standard. The nozzle heats to 220 °C, your tuning's value. If another filament is loaded, name its variety, such as `"Elegoo PETG"`. A variety you've tuned more than once (`"Elegoo Rapid PETG"`) lists your tunings to name one, and a type (`PLA`) lists the varieties.
   - **The plate is the textured PEI plate,** textured side up. The slice is for it: Orca's `Textured PEI Plate`, and your PLA preset heats it to **60 °C** (`M140 S60` in the G-code). An earlier version of this runbook said 45 °C. That came from slicing Orca's system files without their parents, so Orca filled the bed temperature from its own defaults; Elegoo's PLA profile, and your copy of it, say 60. If a different plate is fitted, stop and tell the operator: don't print.
   - You know where Home Assistant's emergency stop is. That is how a print is stopped: not this program, and not Ctrl-C.

7. **A dry run, unarmed.** Run step 1 below without the flag first. It first prints what it was told is fitted and the three profiles it chose: your tuning, or the standard where you haven't tuned. It slices, the approval card appears, and pressing **No, stop** ends it. That proves the settings, the broker and the model without the printer. Let it end before the armed run: one runs at a time (below). At the gate the program prints the orientation and why. If it says "Model endpoint was unavailable", orientation fell back to geometry: fine for this print, but tell the operator.

## The print

**One runs at a time** (mqucifer/print-shop#207). While a job is live, from its start until its outcome is recorded or you stop it, a second `print_job.py` (a part, `--sliced`, `--again` or `--serve`, armed or not) says what's running in one sentence and exits, having published and started nothing:

```
Not started, as only one runs at a time: job 3f2a… (cube.stl) is at the gate, waiting for Approve or Reject, in process 48213.
```

Finish that job, or stop it with Ctrl-C in its terminal (which doesn't stop the printer: step 4), and start again. `--serve` is live too, so stop it before you start a job. A process that's killed, or whose terminal closes, holds nothing: there's no file to delete. `--list`, `--filaments`, the print desk and `stock.py` run beside a job.

1. **Arm and run, in one command:**

   ```
   PRINT_AGENT_ALLOW_HARDWARE=1 uv run python scripts/print_job.py ~/print-shop/parts/cube.stl --record
   ```

   It should say `ARMED`, and that it is recording to `~/print-shop/recordings/sdcp-<time>.jsonl`. If it says `Not armed`, the flag didn't reach it: stop with Ctrl-C and type it again on the same line.

   **A part already laid for printing keeps its pose with `--as-laid`** (mqucifer/print-shop#128): no orientation model is asked, it prints as the file lays it, and the review and the gate say it was kept as laid, by you. Without it, the orientation model chooses among the six square-on poses. Either way, the review names the pose the part's features were read in (its height, and its area on the plate), since the questions and the lessons are keyed to them.

   **A part's context goes with it** (mqucifer/print-shop#130): which faces fit another part and with how much play, which are seen, the side it's laid on, and how it's held. For an STL inside a design run's folder (`~/print-shop/design-runs/<run>/`), it's read from the run: its needs, its facts and its latest refinement's findings, for the part the file is. A fit's place is derived from the geometry: the need's own `--box`, else where the part's box meets its mate's. The run's side is kept, with no orientation model asked, and the print loop asks before turning it off that side. A part you made or edited by hand won't match the run's file, so it gets a **sidecar**: `PART.part.json` beside `PART.stl`, or any file named with `--context`. Everything in it is in the STL's own coordinates, as the file lies:

   ```json
   {
     "fits": [{"name": "skirt over the base", "mate": "the base box", "play": [0.05, 0.15], "z": [5, 10]}],
     "seen": ["walls", "top"],
     "down": "-z",
     "holds": [{"name": "snaps on", "way": "+z"}]
   }
   ```

   - `fits`: each fit's `play` in mm (one number, or the least and most), and where it is: `z` (from, to) for a band all round, or `box` (two corners, `[[x0, y0, z0], [x1, y1, z1]]`). With neither, the whole wall is held as the fit.
   - `seen`: any of `walls`, `top` and `bottom`. The intake's answer comes first; this stands where it wasn't given.
   - `down`: the side that lies on the plate, one of `-z` (as the file lies), `+z`, `-x`, `+x`, `-y`, `+y`.
   - `holds`: the way a hold slides on or off, one of the same six.

   Every key is optional, and a sidecar that can't be read is said at the start and the job goes on as before. A part with a fit never gets a random seam, whoever proposes it, and the review's reading says why, naming the fit. A fit at the plate gets elephant foot compensation proposed (0.1 mm, inside its bound), for you to take or not. A hold that slides on along the layers' stacking is said. The review lists what was read under "The part's context", or says there was none.

   **Then a few questions about the part,** once it's oriented. There are at most four. Two need no model and show at once (mqucifer/print-shop#208): what the part is for, and which of its faces will be seen. The model's own come from its measured features, and a simple part may get none. Each option shows what it would set, its impact on strength, surface and risk, and Orca's time and filament for it ("Orca: 11 min, 3.1 g", or "no estimate"). One option is marked recommended, with why. Answer with its number, or `o` and your own words. The second, on which faces will be seen, decides what the review says about ironing and the seam. For the test cube, take the recommended option. To replay answers, pass `--answers file.json`, each answer naming its question by its text or its feature, e.g. `{"answers": [{"question": "purpose", "choice": 1}]}`. A file that doesn't answer exactly the questions asked is refused, and the job ends listing them.

   **The model reads the part while you answer those two** (mqucifer/print-shop#208). Its call has taken 3 to 19 minutes, so you don't have to wait for it:
   - **Its questions are added when they arrive,** after the answers you're giving and numbered on from them. A list never changes while you're typing an answer to it.
   - **Its own question on what the part is for can't replace your answer,** so it's shown as closer choices beside it, each with what it sets and Orca's estimate. Enter keeps your answer. A number, or `o` and your words, replaces it, and the record keeps both.
   - **If you've answered and it's still working,** the command shows how long it has run. Type `go` and Enter to go on without it (Enter alone doesn't). Its call is closed, and nothing it would have said is used or asked later.
   - **A failed call says so when it fails:** cut off, timed out or unavailable, and after how long. If that lands while you're typing, your prompt shows again under it, and what you'd typed is kept. Its time is 800 seconds at the default 16,000-token limit. Its answer is also cut at 2,000 characters of blank space in a row, which is how every cut-off of 2026-11-15 to 22 went wrong, and at 14,000 characters, which is what four questions need.
   - **The review notes a call you went on without, or that failed,** and the ledger keeps which it was. The proposal and the guards are the same either way.
   - **With `--answers` nothing is asked early:** the file is used once the model's questions are in, or once you type `go`. Its answer on what the part is for stands over any closer choices, unless it names one of them by the question's text.

   **Then the print loop decides how it prints** (mqucifer/print-shop#129): one short conversation with the setup model (`PRINT_SHOP_SETUP_MODEL`) that chooses the pose and the settings together, after the questions, which are asked of the part as the file lays it. It can slice a pose in Orca and read it, look up what a setting does and which setting answers which symptom, and look up earlier prints of parts like this one and the lessons kept from them. **It asks you, here, only before a big change:** a pose other than the file's own, supports, a temperature more than 5 °C or a fan more than 20 points past what a kept lesson or your preset backs, or more than a quarter added to the print's time. Code decides what's big, not the model. Answer `yes`, or say why not: a no is your correction, with your reason, and a lesson candidate, as a send-back is. Everything smaller comes to the review below. Every call it makes is kept with the job. If it runs past 16 calls or 15 minutes, or the model can't be reached, the job falls back to the separate orientation and settings calls, and the review says so.

   **Then the settings review, here on the command line,** before Home Assistant's gate. It shows:
   - how the model read the part;
   - each setting it changes from your presets, as `your value -> this print`, with the feature or purpose behind it and its source;
   - what it weighed and kept, any design notes, and Orca's estimate.
   - what the slice shows, read layer by layer: each layer's time and the shortest, each feature's time and area (ironing, gap fill, walls, infill), and where the seams fall;
   - what that means for this part, each with a proposed change, its reason and the reading it rests on: layers too short for the filament to cool (slow-down on), ironing on a face you said is hidden (ironing off, or only the topmost), a seam in a line on walls you said are seen (moved to the back). Nothing from the slice is applied unless you take it.

   It reasons with `crew-code-think` (the `PRINT_SHOP_SETUP_MODEL` setting), which usually takes two to three minutes (up to about ten at its 16,000-token limit). If its answer is cut off at that limit, the review says CUT OFF and shows only the guards' changes; that is recorded, and nothing is retried. Type `ok` to go on to the gate. To take what the slice proposes, type `take`, or `take 2` for one of them; it slices again. To correct a setting, type it by name, e.g. `layer_height=0.2 because it's a test cube`, and it slices again and shows the review again. **For a pause at a height,** type `pause_at=2.6 for insert` for parts to drop in, or `colour_change=12.4 from #1 to #2 because white walls, a black top` for a swap (what you leave out is asked: "What a pause is for", below; `pause_at=none` takes it away). **A pause height is the top of the last layer printed before the pause** (mqucifer/print-shop#203, [the bench](bench.md)): the print pauses after the layer that tops out at 12.4 mm, through the printer's own pause (`M600`), so everything up to 12.4 is the first colour and everything above it the second. For parts dropped in, it's the z the bench's `pause` says. The review reads the sliced G-code and shows where the pause landed, beside the height asked for ("pause after layer 62, at z=12.4, before layer 63, at z=12.6"), with how far into the print it falls in the slice's reading. If it says `PAUSE MISMATCH`, the pause isn't where you asked: correct the height, or the layer heights, before `ok`. **Don't add a layer:** before mqucifer/print-shop#203 you had to type 2.8 to drop parts into pockets topping out at 2.6, and 2.8 now pauses once the layer over them has printed, too late. Type 2.6, and change any sidecar note that says to add one. The review notes it where the part's context has that fit's height and the slice shows the layer before the pause closing over it; elsewhere it can't tell, and says nothing; the approval gate says it, so you can be at the printer; and at the outcome, say in the note how the swap or the insert went (mqucifer/print-shop#119). Or type `stop`. **The questions and the review take as long as you take** (mqucifer/print-shop#125): nothing counts down while they wait for you.

   **What a pause is for: a swap or an insert** (mqucifer/print-shop#218). The review, the gate, the print desk and the outcome use that word. A pause from before it was said, or in a file sliced by hand, is only "a pause".
   - **`pause_at=2.6 for insert`** is parts dropped in. `pause_at=2.6` alone asks which it is, `1` an insert or `2` a swap, with this review's, or the past print's, pre-selected for Enter. `back` sets no pause.
   - **`colour_change=12.4 from #1 to #2`** is a swap, by the stock's numbers: it starts on spool #1 and you swap to #2. `colour_change=12.4 to #2` starts on the loaded one. `colour_change=12.4` asks for both, with Enter for the one shown. Enter never starts it on the spool the last swap left loaded, as that would be that print reversed: type its number if you mean it.
   - **A swap printed again never starts on another spool by itself.** Where the first it had can't start this job (it's of another filament than the one now loaded and sliced for, or it's used up), "use these" says `CAN'T USE` with why, and nothing is pre-selected: load that spool and run the job again, or type the swap.
   - **The spool it starts on is of the filament it's sliced with.** Another colour of that is fine. For another filament, load it and run the job again.
   - **The spool swapped to can be any on hand.** The slice has one filament's settings, so another variety is noted at the review: the layers after the swap print with the first's.
   - **Start checks the first is loaded.** Where the stock has another, the review and the gate say `LOAD FIRST`. At `ok` you're asked "Is spool #1 …, on the printer now?": `y` records the load (it's `stock load 1`), and anything else writes nothing. Later, load it and record it with `uv run python scripts/stock.py load 1` in another terminal. An Approve before that starts nothing: the card and the terminal say `Not started`, why and what to load, and the gate waits for a new press, within its same hour.
   - **The ledger keeps both spools, in order, each with its layer,** and the print desk and a print of it again carry them.
   - **When the print ends, the stock has the last as loaded.** One stopped at the swap's pause or under it ended with the first (paused there, the printer already says the swap's layer). Where the printer doesn't say how far it got, the last is taken: correct it at the outcome, under "Spool on at the end". It never writes over a load you've recorded since the print started, or a later print's.

   **A job starts on what it was prepared for** (mqucifer/print-shop#218), with or without a pause, hand-sliced and `--again` too. As a swap's end moves the stock by itself, the loaded spool is compared again when you approve:
   - the same spool starts, with a colour you've corrected on it since recorded;
   - another spool of the same variety, colour and tuning starts, and is the one recorded (never the spool a swap is to, as its first);
   - anything else starts nothing: `Not started`, with what to load. Press Approve again once it's loaded and recorded. A press from before is never kept, and the hour isn't extended.

   An answer at the questions is a decision, even one that keeps your preset's value. "Bridge it — no support" over a preset with supports off sets nothing, and the review says `kept enable_support at 0: you chose 'Bridge it — no support' … [your answer: overhang]`; the guard that switches tree supports on for an overhang only does so when nothing decided otherwise (mqucifer/print-shop#124). An option that says no support always carries `enable_support=0`. Your corrections go in the ledger: they are what print-shop learns from. Each one is a candidate lesson, keyed to the part's shape; once a reviewer keeps it, a later part with the same shape gets that setting by itself, and the review says which lesson set it (`docs/print-lessons.md`).

   **Use these: the review offers what decided the past prints most like this part** (mqucifer/print-shop#211). It's under USE THESE, just above the prompt, and it needs no model, so it's there with `--as-laid` too.
   - **Which print:** the one with the same bytes, else the closest by the part's measures (size, height and features as numbers), its context and where it came from. It says why that print, what differs and how it came out. `from 2` shows another of those listed, each with what it would give.
   - **What:** your corrections, the pause, the pose you sent back, what you took from the slice, your answers, and the lessons' settings that applied to that print. The print, how it came out, why it's alike and what differs (then, then now) are said once at the top. The items are grouped by whose they were, each with the reason given then, and what several share is said once.
   - **The marks:** `[x]` is selected; `[ ]` may not fit and says why (another filament for a temperature or fan, a feature that's far from that part's, a pause that isn't at the height of one of this part's flat faces, with the height to type where one is a layer away, a failed print whose symptom it bears on, a value you corrected since); What's used, what's set here already and what can't be used are said in words, with why.
   - **A pause brings what it was for:** an insert, or a swap with the spools that print used. One that doesn't say is asked when you use it, and so is a second spool that's used up. A swap whose first spool can't start this job can't be used, and says why.
   - **A past intake answer isn't selected:** you're asked again for each job. On the same file it is, where this job didn't ask.
   - **A pause from an older print keeps its layer, not its number:** a height from before mqucifer/print-shop#228 meant the first layer after the pause, so it's offered as the top of the layer before it, saying both heights. One whose G-code doesn't say where it paused can't be used: type it.
   - **To use them:** `use` takes those marked `[x]` and slices again. `use 2 4` takes those numbers, marked or not. `clear 3` clears one, and `clear` all of them. `ok` without `use` carries nothing: with some still ticked, it says so once, and `ok` again goes on without them. What you type in the same answer wins: `use; wall_loops=3 because it's decorative`.
   - **With a model,** it's also asked what the differences put in doubt. It can only unselect, with its reason, and the offer doesn't wait for it: a doubt that comes late is on the next review, and `use` only ever takes what the review in front of you marked.

   What you use is your correction, and the ledger keeps the print it came from (`--list` says `used from`). It isn't a lesson kept: those still go through the reviewer (`docs/print-lessons.md`).

   **The review shows every pose the orientation step weighed** (mqucifer/print-shop#128): what would need support, any round or chamfer rising from the plate that's left out of it (it's carried by the layers under it), the area on the plate and the height, with this print's marked. The step chooses on those, and on fit. It doesn't stand a part up for warping: a pose stood up where a lower one has no more to support and twice as much on the plate is laid back down, saying so, and a part with 5000 mm² or more on the plate gets a `warping risk` note naming the plate's temperature, a brim, the exhaust and the side fan. Elephant foot is `elefant_foot_compensation` (0.1 mm on your profile). A part too big for the plate in every pose is refused before it's sliced.

   **To send the orientation back,** type `orientation` (mqucifer/print-shop#128). It's a short conversation:
   - the step says why it turned the part, in its own words;
   - you pick the pose it should print in, from the others it weighed, each with its overhang, its area on the plate and its height;
   - it asks why the turn was wrong. Where its reason was a risk that settings answer, such as warping, it lists those settings, each with Orca's time for it: a brim, a warmer plate, the chamber's exhaust off, the side fan off. Where a kept lesson sets one of them for this part, in the pose you chose and on this filament, it offers the lesson's value instead and names the lesson, such as "the side fan at 20%, as lesson 6 keeps it". Answer in your own words, or take one by its number. A reason is required.

   The part is then turned, proposed for and sliced again, keeping your answers to the questions and any corrections you'd already made. `orientation=1 because it's laid for printing` does it in one line, and `back` returns to the review. Your reason is recorded as the correction, and drawn as a lesson like any other (`docs/print-lessons.md`). Once a reviewer keeps it, the orientation step is given it, with its poses, for a later part with the same traits, and the review lists it.

   **How a correction grows the skill.** The proposal reasons from the print-settings skill (`skills/print-settings/`): SKILL.md and a reference per setting group, distilled from Orca's wiki. A correction that would apply to other parts too, such as "never above 0.16 mm layers for a visible curve", becomes an edit there by PR:
   - a "Change it when" line in the group's reference (`references/layer-height.md` for that example);
   - or a line in SKILL.md's "The Sponsor's practice".

   The PR quotes the correction and its job id (`--list` shows both). A correction for this part only needs nothing more: the ledger has it. Hard limits (a bound, tree supports) are code, in `src/print_shop/setup/bounds.py`, and change by PR too.

   **A mesh that isn't closed stops at the mesh gate first** (mqucifer/print-shop#200), before any question or slice. The command line, and the job's log, say so:

   ```
   At the mesh gate, before any question or slice. Approve or Reject on the dashboard.
     because     Mesh is not manifold; cannot be trusted to slice.
     is_manifold False
     volume_mm3  None: a mesh that isn't closed has no volume
     bbox_mm     20 x 20 x 20
     Approve     goes on to slice a mesh this gate calls untrustworthy, as the file lays it: the questions and the review follow, then the gate before printing, where today the job fails whatever you answer. Nothing prints.
     Reject      ends the job: nothing is sliced or printed
     answer by   14:32 (60 min left); after that it ends and nothing prints
   ```

   The dashboard's card doesn't tell the two gates apart: it asks "Print this?" at the mesh gate too. Read the command line before you press. Reject and mend the file: Orca slices a damaged mesh without complaint and can get it badly wrong, and today a job approved at the mesh gate fails when the approval gate is answered, with nothing sent to the printer (`can only concatenate list`).

2. **Approve at the gate.** The dashboard asks "Print this?". Check the numbers before you press:
   - Time: about 11 minutes (Orca's estimate for your presets is 10 m 54 s; the review shows the estimate with its changes);
   - Plastic: about 2.9 cm³;
   - Turned: a multiple of 90° on each axis. A cube sits flat on every face.

   Press **Yes, print it**. Anything else wrong: press **No, stop**.

   A press names the job it's for: print-shop puts the job's id in the two buttons when its gate opens, so the dashboard needs no change. A gate ignores a press that names another job or none, and its command line says `ignored approve for …`. So an automation or script that publishes to the command topic itself has to send the `job_id` the gate topic shows.

   The command line says by when, `answer by 14:32 (60 min left)`. The gate waits an hour from when it opens; the hour before it, for the slice to reach the gate, starts when you type `ok` at the review. If either runs out, the job ends in one sentence, such as "Timed out waiting for Approve or Reject at the approval gate after 60 min, so nothing printed.", nothing goes to the printer, and the ledger keeps the job as timed out (`--list` shows it under "Ended without printing"). Submit it again to print it.

3. **What to watch.** Before it uploads, the program checks that the printer is idle and has room. Then it uploads, starts, and watches the printer's own status for up to 5 minutes until the print visibly begins: preheating, then printing.
   - **It says "Started. Watching the printer read-only".** That is the proof #3 is for: the start was seen, not assumed.
   - **It says "never left a ready state within 300s".** Look at the machine. It may or may not be printing. The ledger keeps the start's error; if the printer turns out to be printing this job, the watcher marks it started within 30 minutes. Note how long the preheat actually took: that number replaces the 5-minute guess.
   - **It says the printer refused, or reports another file.** Nothing more happens. Tell the operator what it printed.

4. **To stop the print,** use Home Assistant's emergency stop. A stop is acknowledged at once but takes minutes to settle (the prototype measured it): the machine cycles through preheating and homing first. Ctrl-C only stops the program watching; it does not stop the printer, and the ledger row stays open.

## After

1. **Answer.** When the printer finishes, the dashboard asks "How did it come out?" with the part's name and what the printer says (completed, stopped, error). The printer's word is not the outcome; yours is. Beside it is the printer's camera, read-only: the last frame, and Earlier and Later to page back through one frame for each new layer the shop saw, to find where something started (mqucifer/print-shop#110). They're kept in `~/print-shop/jobs/<job>/frames/`, and a lesson from a print with a symptom names them. A frame is evidence for your answer, never the answer.

   **For now there are no frames, and the card says why** (mqucifer/print-shop#123): "No camera frame was kept for this print: frames are off: they'll come through Home Assistant, and how is still being chosen". print-shop no longer opens the printer's own camera stream, and how frames reach it through Home Assistant is the Sponsor's choice, still open. When a grab fails, the command line has one line saying why and at which layer, and the card says the last reason.

   **The printer's camera slots.** The Centauri Carbon's camera serves only a few connections, and it holds a closed one's slot for a long time afterwards, so each viewer, and each grab that opened a stream, can leave one taken (pycentauri 0.9.1, `mjpeg_broadcast.py`). When they're used up, a viewer gets `200 OK` and then no picture: the Elegoo app, Orca's device tab, Home Assistant's camera, or print-shop's frames. Closing a viewer doesn't free its slot. Restarting the printer's camera service does, and that means power-cycling the printer, between prints, never during one. Keep the viewers on the camera to the ones you use.
   - **Result:** success if the part is usable, or failed.
   - **What went wrong:** the closest of adhesion, warping, stringing, layer_shift, spaghetti, under_extrusion, or other. On a success it's a minor flaw on a usable part, read as `success, with stringing` (mqucifer/print-shop#189), or `none` if there was no flaw. A failure always names one. Other needs a note.
   - **Note:** anything worth knowing, such as "corner lifted at layer 3".
   - **Spool on at the end,** for a print with a swap (mqucifer/print-shop#218): the card says each spool and what the stock now has loaded. If another was on at the end, pick it, and Record corrects the print and the stock (the stock only where you haven't recorded a load since). A spool that ran out (`stock empty <id>`) keeps the layers it printed, with the one that finished after it. `as recorded` leaves it as it's said.
   - Press **Record it**. If it doesn't add up (a failure with `none`, or other without a note), the card says why and nothing is stored. Fix it and press again.

   You can answer later. Until you do, the print stays open, and `uv run python scripts/print_job.py --serve` asks about it again.

2. **Check the row against what happened.** The program prints the row when you answer, or show it any time with `uv run python scripts/print_job.py --list`. Check:
   - [ ] the part is `cube.stl`, and the G-code name carries the job id;
   - [ ] the orientation is what the program printed at the gate, and its source is `model:crew-code` (or `geometric` if the model was unavailable);
   - [ ] the profiles are `Elegoo Centauri Carbon 0.4 nozzle - Copy`, `0.20mm Standard @Elegoo CC 0.4 nozzle - Copy` and `Elegoo PLA @ECC - Copy`, and the plate line reads `Textured PEI Plate, bed 60 C`;
   - [ ] the material line names cc1, the 0.4 mm nozzle and your spool, by number and colour;
   - [ ] for a print with a swap, the spools lines name each in order, with its layer;
   - [ ] the store lines name `profiles/` at the commit you pulled, with no uncommitted changes, and each tuning's file over its standard's;
   - [ ] the intake line is your answers, each changed setting is listed with who changed it, and a `corrected` line lists any setting you corrected at the review;
   - [ ] the printer is the Centauri Carbon with its firmware version;
   - [ ] asked to start, started and ended are in that order, and roughly match the clock;
   - [ ] the printer's end state is what the screen showed;
   - [ ] the outcome is your answer, word for word.

   Anything that doesn't match goes to the operator, and is written on mqucifer/print-shop#3.

3. **Send the operator** the recording's path (it stays on the Mac, and its mainboard, IP and MAC fields are already redacted), plus three facts the code has been waiting for:
   - how long the start took to show as printing;
   - whether the screen named the file while it was printing;
   - whether the printer went back to idle on its own after it completed, or stayed in completed.

   The recording replaces the synthetic frames in `tests/data/sdcp/`.

## A file you sliced by hand

For a print you set up yourself in Orca (mqucifer/print-shop#164): a test with a different setting on each object, a calibration print, or a job you know is good. print-shop sends it **as it is** and keeps it in the ledger. It asks no questions, proposes nothing, applies no lesson and slices nothing again.

1. **Export it from Orca.** Slice the plate, then either:
   - **the plate's sliced file** (`.gcode.3mf`; File, Export, "Export plate sliced file"). **Use this one when objects have their own settings:** it carries each object's overrides, and print-shop keeps them. The G-code inside it is what's sent, byte for byte;
   - **G-code** (`.gcode`). Its settings are kept, but G-code names its objects without their overrides. Add `--project light-test.3mf`, the saved Orca project it was sliced from, and they're read from there, matched to the G-code's objects by name. Without it the ledger says they weren't kept.

   The plate file's overrides were tested on the file Orca 2.4.2's command line exports. **Check the dry run's "per-object overrides" lines against what you set in Orca:** if the file you exported from Orca's window carries none, it says so, and `--project` gives them.

   A file with several sliced plates ("Export all sliced file") needs `--plate N`. An unsliced project is refused.

2. **A dry run, unarmed,** then the print:

   ```
   uv run python scripts/print_job.py --sliced ~/print-shop/parts/light-test.gcode.3mf
   PRINT_AGENT_ALLOW_HARDWARE=1 uv run python scripts/print_job.py --sliced ~/print-shop/parts/light-test.gcode.3mf
   ```

   Arming is the same as for any job: only `PRINT_AGENT_ALLOW_HARDWARE=1` on the command's own line, typed by you. The bed is clear, as before.

3. **What it shows before the gate,** which is what the ledger keeps:
   - which file it took, and the sha256 of the bytes it will send;
   - Orca's estimate: the time, the filament and the layers;
   - the three profile names Orca wrote, and **each setting that differs from your tuning** for the loaded spool, as `tuning -> file`. A slice made with exactly your tuning lists none. A setting your tuning doesn't state is listed when Orca itself says it was changed from its system preset, with `Orca's default` as the tuning's value;
   - **each object's overrides,** named as the G-code names the object;
   - the slice read layer by layer, as the review reads one: each layer's time, each feature's time and area, the seam, and **any pause written in the file** (the file doesn't say whether it's a swap or an insert). It's a report. Nothing is proposed and nothing is written back.

4. **The loaded filament and the fitted nozzle are checked against the file.** If the file is sliced for another filament type, another variety print-shop knows, or another nozzle than the inventory says is on the printer, it isn't sent, and it says what differs. Orca writes Bambu's PETG as `PET`: with a profile print-shop knows, `PET` and `PETG` are the one type, and the variety still has to match. With a profile it doesn't know they aren't, as Orca also has a true PET (`Generic PET @Elegoo Centauri`). Change the inventory if it's wrong (`stock load`, `stock fit`). To print it anyway with what's loaded, add `--loaded-ok`; the ledger keeps that you did.

5. **Then the approval gate, the watching, the frames and the outcome question,** exactly as above. The file is copied into the job's folder when the job starts, under a name carrying the job id, and that copy is what you approve: it's hashed again just before the upload, and a file that has changed isn't sent. Editing the file in Orca after that changes nothing for this job; send it again for a new one.

6. **In the ledger** (`--list`, and the print desk) the job is marked `sliced by hand`, with the settings by profile, what differed, the overrides, Orca's estimate beside the real time from its start to its end, your outcome, your note and the frames. The printer doesn't report the filament it used, so only Orca's estimate is kept for that. An outcome with a note, a flaw or a failure is a lesson candidate, keyed by the file's own settings (`docs/print-lessons.md`).

**To print one again,** by its job id (the print desk shows the line to copy):

```
PRINT_AGENT_ALLOW_HARDWARE=1 uv run python scripts/print_job.py --again 3f2a…
```

It sends the file that job printed, kept in its folder, as a new job marked as a reprint of the old one, with the old one's overrides. It's refused if the kept file has changed or is gone (send the file again with `--sliced`), and if what's loaded isn't what the file names, unless you add `--loaded-ok`. A job that print-shop sliced itself isn't printed again this way: its page on the desk gives the command for it.

## The print desk: reading a job, and running it again

A page on the Mac shows what's loaded and every job (mqucifer/print-shop#139), so a job can be read after it's scrolled past, and a past job's settings copied to print it again. From print-shop's checkout:

```
uv run python scripts/print_desk.py
```

Then open <http://127.0.0.1:8719/> in a browser on the Mac. `--port` picks another port. It listens on 127.0.0.1 only, so nothing else on the network reaches it, and Ctrl-C stops it. It can run beside a print.

- **The front page** shows the printer's fitted nozzle and loaded spool, the spools on hand, and each job, newest first: where it is (printing, waiting for your answer, its outcome, or ended without printing), its time and plastic, what it pauses for (a swap with its colours in order, an insert, or a pause that doesn't say), and its frames.
- **A job's page** shows the part, its pose and why, the questions with your answers, each setting proposed with its reason and who it's from (your answer, the model, a guard, a lesson, the slice's reading, or you), your corrections at the review, the slice's reading and what it flagged, the pause and what it's for, a swap's spools with their layers, the outcome with your note and the frames, and the job's log.
- **The job's log** is what the command showed for it, with each answer you typed, kept as `~/print-shop/jobs/<job>/job.log`. No key, token or password is written to it. Jobs from before the log was kept have none.
- **Run it again** gives three things, each with a Copy button:
  - the command line: the same part, nozzle, spool and profiles, and `--answers`. The part is found by its bytes, which the ledger keeps the hash of: at the path the job's log names, else anywhere under `~/print-shop/parts/` (or `PRINT_SHOP_PARTS` in the settings), else in the job's folder. Renamed or moved, it's still found. If no file has the same bytes, the page says it wasn't found, and the command names the part by its file name for you to give its path;
  - the correction line, to paste at the review when it asks, with what a pause was for (`pause_at=2.6 for insert`, or `colour_change=12.4 from #1 to #2` with the spools that print used; one that's used up since is left out, and asked);
  - the answers file: press **Save** and it goes to `~/Downloads`, where the command line looks for it. If the browser names it differently, change the path in the command to match.

  A job you sliced by hand has one line, `--again` and its id, and a "Sliced by hand" section with what was kept of the file.

  What the lines can't carry is said under them: a pose you sent back is sent back again at the review (`orientation`), and a change you took from the slice is taken again (`take`) if it proposes it. If the questions asked are different this time, `--answers` refuses the file and lists the new ones; answer them as they come.

**The desk reads and copies; it never acts.** It opens the ledger read-only and writes nothing: it doesn't start a job, answer a question or arm the printer. The copied command line doesn't arm it either. Arming stays as above: `PRINT_AGENT_ALLOW_HARDWARE=1` on the command's own line, typed by you.

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `Not started, as only one runs at a time` | another `print_job.py` is live, and the sentence says at what | finish it, or Ctrl-C in its terminal; nothing was started |
| `ignored approve for job …` | a press named another job, or none, so this gate didn't take it | press again on the dashboard, which names the job at its gate |
| `MQTT_USER is not set` or `CREW_LLM_API_KEY is not set` | the settings file is missing a key | add it; nothing was started |
| `say what's fitted: --nozzle (mm) and --filament` | the nozzle or filament wasn't given | add both; nothing was started |
| `The inventory isn't set up` | no printer in the inventory yet | do step 2 of Before, or give `--nozzle` and `--filament` |
| `no nozzle is fitted` / `no spool is loaded` | the inventory doesn't say what's on the printer | `stock fit 0.4` / `stock load <id>` |
| `the fitted nozzle on cc1 is …` / `loaded on cc1 is …` | what you named isn't what the inventory says is fitted or loaded | change the inventory to match the printer, then run again |
| `No profile covers that nozzle` | the fitted nozzle is outside `profiles/scope.json` | fit the 0.4, or add the nozzle to the scope by PR |
| `Several PLA varieties are in the store` | the filament was given by type | name the loaded variety, from the list |
| `You've tuned '…' more than once` | that variety has several tunings | name the tuning you want, from the list |
| `can't print with … by Orca's rule` | the filament isn't marked for this machine or process | don't print; tell the operator, and fix the preset in Orca, then re-import |
| `… holds no G-code: it's a project, not a sliced plate` | `--sliced` was given an unsliced Orca project | slice it in Orca and export the plate's sliced file, or its G-code |
| `Not sent: the file is sliced for …` | the hand-sliced file names another filament or nozzle than the inventory says is loaded | load or fit what it names, fix the inventory, or add `--loaded-ok` |
| `… has changed since it was taken … nothing was uploaded` | the job's copy of a hand-sliced file was changed after the gate opened | send the file again with `--sliced` |
| `LOAD FIRST: it starts on spool #…` | the swap starts on a spool the stock doesn't have loaded | `y` at `ok` if it's on the printer, or load it and `stock load <id>` before you approve |
| `Not started: …` | you approved, and what's loaded in the stock isn't what the job was prepared for | do what it says (`stock load <id>`), then press Approve again; nothing was sent |
| `… has a swap and hasn't been seen to end` | an earlier print's swap decides what's loaded, and its end hasn't been seen | wait for it to end, then approve again |
| `… loaded on cc1, so nothing was uploaded or printed` | the stock changed between your Approve and the start | nothing was started; `stock load <id>`, then run the job again |
| `the job is sliced for …, and spool #… is …` | a swap was to start on another filament than the slice's | load that spool and run the job again, or start on one of the sliced filament |
| `spool #…'s tuning was changed from …` | the spool's tuning was edited after the job was prepared | nothing was started; set it back and approve again, or Reject and run the job again |
| `printer preflight failed — printer.ready` | the printer is busy, paused, or in error | clear it at the machine, then run again |
| `printer preflight failed — printer.free_space` | the printer's storage is full | delete old files on the printer |
| The approval card never appears | the broker or the dashboard is wrong | check the broker settings; re-paste the dashboard |
| `without a ledger` | a bug: the program refuses to start a print it can't record | tell the operator |
