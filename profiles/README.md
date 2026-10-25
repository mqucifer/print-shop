# print-shop's Orca profiles

Every print slices from these files, and from nothing else (mqucifer/print-shop#6). They are in Orca's own JSON, one file per preset:

- `machine/`: the printer with a given nozzle;
- `process/`: layer height, walls, infill, speeds and the rest;
- `filament/`: a material's temperatures, cooling and retraction.

Each is **whole**: everything Orca's `inherits` chain gave it is written out, so an Orca update can't change a print silently. Each keeps `inherits` naming its system parent, because Orca's CLI checks a process against the machine's system parent (it exits 239 without it). The printer's address (`print_host`, `printhost_*`) is never copied in.

**To change one:** tune it in Orca, then from print-shop's checkout run

    uv run python scripts/import_profiles.py

It reads `~/Library/Application Support/OrcaSlicer` (never writing there), writes what changed on a new branch, and prints the commands to open the PR. The PR is where a tuning change is reviewed, with its reason. A profile here that is no longer in Orca is listed and kept: removing one is the PR's decision.
