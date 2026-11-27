"""A settings study: the print loop's model studies a part's settings for strength, filament and
time, and recommends one set against the profile's own (mqucifer/print-shop#339).

- `candidate`: what a candidate may ask for, bounded by code, and regions round the joints;
- `reading`: a candidate sliced through Orca and read by `load` under each load case;
- `published`: what the published tests say of each pattern, its walls and its wall order;
- `loop`: the model's loop with tools, to its recommendation;
- `table`: the table and recommendation for the Sponsor at the gate;
- `lessons`: the study and its lessons in the ledger, for a later, similar part.

`scripts/settings_study.py` runs one; `docs/settings-study.md` says how.
"""
