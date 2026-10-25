# Test data

- **`recorded/cube.gcode`** is what the real OrcaSlicer wrote for `tests/fixtures.py`'s cube (STL sha256 `bd3eda612de075a1e177e7dbe8e97941c8393df936ccab0087cf24b910fa027a`), sliced once on the Mac on 2026-10-24 with the Elegoo Centauri Carbon 0.4 nozzle machine profile, `0.20mm Standard @Elegoo CC 0.4 nozzle` and `Elegoo PLA @ECC`. `tests/fakes.py`'s `RecordedSlicer` replays it through the real header parser, so CI slices without Orca. It holds no path or address (`scripts/check_addresses.py` reads it).
- **`profiles/`** carries only what the pipeline reads from a profile without Orca: the build volume (257 mm, what the real profile resolves to) and the process profile's printer list.
- **`sdcp/frames.json`** is **synthetic**: SDCP payloads built from pycentauri's models, not recorded from the printer. No test contacts the printer. The first real frames are recorded at the Sponsor's first deliberate print (mqucifer/print-shop#3) and replace these.
