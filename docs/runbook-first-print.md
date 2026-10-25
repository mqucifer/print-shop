# Runbook: the first deliberate print

**For the Sponsor, at the printer.** mqucifer/print-shop#3. The agent's own start path has never been run on purpose: the prototype's `_confirm_started` has only ever met a start nobody wanted (2026-09-16). This print proves it, and puts the ledger's first real row on record.

**Only you arm the printer, and only for this one command.** No builder arms it. No test arms it: the test suite refuses an armed connection even with the flag set. The flag goes on the command line of the one run that should print, and nowhere else: not exported in a shell, and never in `.env`.

About 40 minutes: 10 to set up, about 17 printing, and the rest watching and answering.

## Before

1. **The settings file.** Create `~/.config/print-shop/.env` with mode 600 (`chmod 600 ~/.config/print-shop/.env`). It holds these keys, the same names as the prototype's `.env`. Copy the values from there; none of them goes in any repository.

   | Key | What it is |
   |---|---|
   | `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD` | the broker on the Pi, where both gates live |
   | `MQTT_TOPIC_PREFIX`, `MQTT_DISCOVERY_PREFIX`, `MQTT_NODE_ID` | keep the prototype's values, so the dashboard's entities stay the same |
   | `HA_TOKEN` | Home Assistant's token. Only the live dashboard checks use it |
   | `PRINTER_HOST`, `PRINTER_MAINBOARD_ID` | the Centauri Carbon |
   | `CREW_LLM_API_KEY` | a LiteLLM key for the orientation model, issued by the operator. Not the proxy's master key |

   Optional: `PRINT_SHOP_LEDGER` (default `~/print-shop/ledger.sqlite`), `PRINT_SHOP_LLM_URL` and `PRINT_SHOP_LLM_MODEL` (default LiteLLM at `127.0.0.1:4000`, alias `crew-code`), and `ORCA_DATADIR`, `ORCA_MACHINE`, `ORCA_PROCESS`, `ORCA_FILAMENT` (default Orca's Elegoo Centauri Carbon 0.4 nozzle, 0.20 mm Standard, Elegoo PLA).

2. **Nothing from the prototype is running.** Both use the same topics. The prototype has no daemon command of its own, so one would only be running if someone started it by hand, from `~/Documents/git/3d-print-agent`. If one is, stop it.

3. **The dashboard.** In Home Assistant, replace the 3D Printing dashboard's raw configuration with `ha-configs/lovelace_3dprint.yaml` from this repository. It adds the outcome card ("How did it come out?"), which appears only when a print is waiting for an answer.

4. **The part.** The 20 mm test cube, the same one CI replays:

   ```
   cd ~/Documents/git/print-shop-ledger-wt
   uv run python -c "from pathlib import Path; from tests.fixtures import write_stl; d = Path('~/print-shop/parts').expanduser(); d.mkdir(parents=True, exist_ok=True); print(write_stl('cube', d))"
   ```

5. **At the printer:**
   - **The bed is clear and clean.** The agent treats a printer that just finished as ready, so it would print onto a part left on the plate (the prototype's BACKLOG). You are the check.
   - PLA is loaded: the profile slices for Elegoo PLA at 210 °C on the nozzle.
   - **The plate.** The slice is for Orca's **Cool Plate**, which heats the bed to only **35 °C** (`M140 S35` in the G-code). The prototype's notes describe a textured PEI plate on this printer. If that's what is fitted, stop here and tell the operator before printing: the plate type is a slicer setting, and PLA at 35 °C on PEI may not stick.
   - You know where Home Assistant's emergency stop is. That is how a print is stopped: not this program, and not Ctrl-C.

6. **A dry run, unarmed.** Run step 1 below without the flag first. It slices, the approval card appears, and pressing **No, stop** ends it. That proves the settings, the broker and the model without the printer. At the gate the program prints the orientation and why. If it says "Model endpoint was unavailable", orientation fell back to geometry: fine for this print, but tell the operator.

## The print

1. **Arm and run, in one command:**

   ```
   PRINT_AGENT_ALLOW_HARDWARE=1 uv run python scripts/print_job.py ~/print-shop/parts/cube.stl --record
   ```

   It should say `ARMED`, and that it is recording to `~/print-shop/recordings/sdcp-<time>.jsonl`. If it says `Not armed`, the flag didn't reach it: stop with Ctrl-C and type it again on the same line.

2. **Approve at the gate.** The dashboard asks "Print this?". Check the numbers before you press:
   - Time: about 17 minutes (Orca's estimate is 1,009 s);
   - Plastic: about 3.3 cm³;
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
   - [ ] the profiles are the Centauri Carbon 0.4 nozzle, 0.20 mm Standard and Elegoo PLA, on the plate you checked;
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
| `Orca profile not found` | Orca's profiles moved | set `ORCA_*` in the settings |
| `printer preflight failed — printer.ready` | the printer is busy, paused, or in error | clear it at the machine, then run again |
| `printer preflight failed — printer.free_space` | the printer's storage is full | delete old files on the printer |
| The approval card never appears | the broker or the dashboard is wrong | check the broker settings; re-paste the dashboard |
| `without a ledger` | a bug: the program refuses to start a print it can't record | tell the operator |
