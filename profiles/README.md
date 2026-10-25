# print-shop's Orca profiles

Every print slices from these files, and from nothing else (mqucifer/print-shop#6, #8). The Sponsor's rule: "start with the standards first".

- **`scope.json`:** what's covered. Today that's the 0.4 mm nozzle machine, its 0.20 mm process, and PLA and PETG. It also lists any of the Sponsor's tunings left out, with why. Adding a printer, nozzle or filament is a PR here.
- **`standard/<kind>/`:** Orca's own system presets for the scope, written whole (everything their `inherits` chain gives them), so an Orca update can't change a print silently.
- **`tuned/<kind>/`:** the Sponsor's tunings, each **only the settings it changes** from its standard, which it names in `inherits`. The tuning reads as the Sponsor's diff, e.g. RAPID PETG at 270 °C and fan 50.

**A print** names the fitted nozzle and the loaded filament, by variety ("Elegoo Rapid PETG") or by a tuning's name. For machine, process and filament alike, print-shop uses the Sponsor's tuning where there is one and the standard where there isn't. When there are several tunings of one, it lists them to choose from.

The profile is composed whole (standard, then tuning) for Orca, which slices with a throwaway data folder. Two facts tested on Orca 2.3 decide how a composed tuning is written:
- it keeps `inherits` naming its standard, and `"from": "User"`, or Orca exits 239;
- the printer's address (`print_host`, `printhost_*`) and Orca's version stamp are never copied in.

**To change one:** tune it in Orca, then from print-shop's checkout run

    uv run python scripts/import_profiles.py

It reads `~/Library/Application Support/OrcaSlicer` (never writing there), writes what changed on a new branch, and prints the commands to open the PR. The PR is where a tuning change is reviewed, with its reason. A profile here that is no longer in Orca is listed and kept: removing one is the PR's decision.
