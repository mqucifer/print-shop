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
   - A variety you've tuned more than once asks which tuning the spool prints with, once, when you add it (`--tuning "…"`).
   - Prints then take the fitted nozzle and the loaded spool by default, and say so.

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

7. **A dry run, unarmed.** Run step 1 below without the flag first. It first prints what it was told is fitted and the three profiles it chose: your tuning, or the standard where you haven't tuned. It slices, the approval card appears, and pressing **No, stop** ends it. That proves the settings, the broker and the model without the printer. At the gate the program prints the orientation and why. If it says "Model endpoint was unavailable", orientation fell back to geometry: fine for this print, but tell the operator.

## The print

1. **Arm and run, in one command:**

   ```
   PRINT_AGENT_ALLOW_HARDWARE=1 uv run python scripts/print_job.py ~/print-shop/parts/cube.stl --record
   ```

   It should say `ARMED`, and that it is recording to `~/print-shop/recordings/sdcp-<time>.jsonl`. If it says `Not armed`, the flag didn't reach it: stop with Ctrl-C and type it again on the same line.

   **Then a few questions about the part,** once it's oriented. They come from its measured features, and often from what its shape can't show, such as what it's for. There are at most four, and a simple part may get none. Each option shows what it would set, its impact on strength, surface and risk, and Orca's time and filament for it ("Orca: 11 min, 3.1 g", or "no estimate"). One option is marked recommended, with why. Answer with its number, or `o` and your own words. For the test cube, take the recommended option. To replay answers, pass `--answers file.json`, e.g. `{"answers": [{"choice": 1}]}`.

   **Then the settings review, here on the command line,** before Home Assistant's gate. It shows:
   - how the model read the part;
   - each setting it changes from your presets, as `your value -> this print`, with the feature or purpose behind it and its source;
   - what it weighed and kept, any design notes, and Orca's estimate.

   It reasons with `crew-code-think` (the `PRINT_SHOP_SETUP_MODEL` setting), which usually takes two to three minutes (up to about ten at its 16,000-token limit). If its answer is cut off at that limit, the review says CUT OFF and shows only the guards' changes; that is recorded, and nothing is retried. Type `ok` to go on to the gate. To correct a setting, type it by name, e.g. `layer_height=0.2 because it's a test cube`, and it slices again and shows the review again. Or type `stop`. Your corrections go in the ledger: they are what print-shop learns from.

   **How a correction grows the skill.** The proposal reasons from the print-settings skill (`skills/print-settings/`): SKILL.md and a reference per setting group, distilled from Orca's wiki. A correction that would apply to other parts too, such as "never above 0.16 mm layers for a visible curve", becomes an edit there by PR:
   - a "Change it when" line in the group's reference (`references/layer-height.md` for that example);
   - or a line in SKILL.md's "The Sponsor's practice".

   The PR quotes the correction and its job id (`--list` shows both). A correction for this part only needs nothing more: the ledger has it. Hard limits (a bound, tree supports) are code, in `src/print_shop/setup/bounds.py`, and change by PR too.

2. **Approve at the gate.** The dashboard asks "Print this?". Check the numbers before you press:
   - Time: about 11 minutes (Orca's estimate for your presets is 10 m 54 s; the review shows the estimate with its changes);
   - Plastic: about 2.9 cm³;
   - Turned: a multiple of 90° on each axis. A cube sits flat on every face.

   Press **Yes, print it**. Anything else wrong: press **No, stop**.

3. **What to watch.** Before it uploads, the program checks that the printer is idle and has room. Then it uploads, starts, and watches the printer's own status for up to 5 minutes until the print visibly begins: preheating, then printing.
   - **It says "Started. Watching the printer read-only".** That is the proof #3 is for: the start was seen, not assumed.
   - **It says "never left a ready state within 300s".** Look at the machine. It may or may not be printing. The ledger keeps the start's error; if the printer turns out to be printing this job, the watcher marks it started within 30 minutes. Note how long the preheat actually took: that number replaces the 5-minute guess.
   - **It says the printer refused, or reports another file.** Nothing more happens. Tell the operator what it printed.

4. **To stop the print,** use Home Assistant's emergency stop. A stop is acknowledged at once but takes minutes to settle (the prototype measured it): the machine cycles through preheating and homing first. Ctrl-C only stops the program watching; it does not stop the printer, and the ledger row stays open.

## After

1. **Answer.** When the printer finishes, the dashboard asks "How did it come out?" with the part's name and what the printer says (completed, stopped, error). The printer's word is not the outcome; yours is.
   - **Result:** success or failed.
   - **What went wrong:** `none` for a success. For a failure, the closest of adhesion, warping, stringing, layer_shift, spaghetti, under_extrusion, or other. Other needs a note.
   - **Note:** anything worth knowing, such as "corner lifted at layer 3".
   - Press **Record it**. If it doesn't add up (a success with a symptom, or a failure without one), the card says why and nothing is stored. Fix it and press again.

   You can answer later. Until you do, the print stays open, and `uv run python scripts/print_job.py --serve` asks about it again.

2. **Check the row against what happened.** The program prints the row when you answer, or show it any time with `uv run python scripts/print_job.py --list`. Check:
   - [ ] the part is `cube.stl`, and the G-code name carries the job id;
   - [ ] the orientation is what the program printed at the gate, and its source is `model:crew-code` (or `geometric` if the model was unavailable);
   - [ ] the profiles are `Elegoo Centauri Carbon 0.4 nozzle - Copy`, `0.20mm Standard @Elegoo CC 0.4 nozzle - Copy` and `Elegoo PLA @ECC - Copy`, and the plate line reads `Textured PEI Plate, bed 60 C`;
   - [ ] the material line names cc1, the 0.4 mm nozzle and your spool, by number and colour;
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

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `MQTT_USER is not set` or `CREW_LLM_API_KEY is not set` | the settings file is missing a key | add it; nothing was started |
| `say what's fitted: --nozzle (mm) and --filament` | the nozzle or filament wasn't given | add both; nothing was started |
| `The inventory isn't set up` | no printer in the inventory yet | do step 2 of Before, or give `--nozzle` and `--filament` |
| `no nozzle is fitted` / `no spool is loaded` | the inventory doesn't say what's on the printer | `stock fit 0.4` / `stock load <id>` |
| `the fitted nozzle on cc1 is …` / `loaded on cc1 is …` | what you named isn't what the inventory says is fitted or loaded | change the inventory to match the printer, then run again |
| `No profile covers that nozzle` | the fitted nozzle is outside `profiles/scope.json` | fit the 0.4, or add the nozzle to the scope by PR |
| `Several PLA varieties are in the store` | the filament was given by type | name the loaded variety, from the list |
| `You've tuned '…' more than once` | that variety has several tunings | name the tuning you want, from the list |
| `can't print with … by Orca's rule` | the filament isn't marked for this machine or process | don't print; tell the operator, and fix the preset in Orca, then re-import |
| `printer preflight failed — printer.ready` | the printer is busy, paused, or in error | clear it at the machine, then run again |
| `printer preflight failed — printer.free_space` | the printer's storage is full | delete old files on the printer |
| The approval card never appears | the broker or the dashboard is wrong | check the broker settings; re-paste the dashboard |
| `without a ledger` | a bug: the program refuses to start a print it can't record | tell the operator |
