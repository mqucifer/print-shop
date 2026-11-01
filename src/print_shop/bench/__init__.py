"""The small CAD bench (mqucifer/print-shop#14): commands that answer in plain text and numbers.

`docs/bench.md` says what each command returns. `host.py` runs on the machine and holds the
container's limits. Everything else runs inside the image, where CadQuery, Manifold and
Shapely are, and is imported only there.
"""
