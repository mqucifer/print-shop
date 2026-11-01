"""The local model works in rounds on the bench, and every round is recorded.

mqucifer/print-shop#17, M18 in mqucifer/project-print `ROADMAP.md`, ADR 0010 there.
`docs/rounds.md` says what a run is, and where each part came from. The session, its tools and
its skills are ported from mqucifer/model-performance's `cad_eval` at 22ea8b7, with what its
README records carried over. Nothing here imports crew or model-performance: a shared core is
extracted only at a second or third real use.

This all runs on the machine, and holds no CAD library. Model-written code runs only in the
bench's container (`box.py`).
"""
