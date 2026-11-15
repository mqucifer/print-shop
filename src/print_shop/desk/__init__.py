"""The print desk: a local page that reads every job, and copies it to run again.

mqucifer/print-shop#139, its first step. The Sponsor, 2026-11-15: "since im often re-running
with the same settings, to be be able to see and re-copy those, read and answer the questions
easier, the images when ready, log this where you can see etc. Hard to read via the command
line all the time. See the filaments loaded etc."

`scripts/print_desk.py` serves it on 127.0.0.1 only. It shows:
- what's on the printer: the fitted nozzle, the loaded spool, and the spools on hand;
- each job, newest first: the part, its pose and why, the questions and answers, the settings
  proposed with each one's reason and source, the corrections at the review, the slice's
  reading and flags, a colour change, the time and plastic, the outcome with its note and
  frames, and the job's log (`print_shop.joblog`);
- "run it again" (`again.py`): the command line, the correction line and the answers file, each
  to copy.

**It reads and copies; it never acts.** It opens the ledger read-only (`Ledger.read_only`), and
it starts no job, answers no question and arms nothing: the arming rule stays on
`print_job.py`'s own command line. Answering the questions and doing the review here wait for
the Sponsor's design session, which redoes the look too.
"""

from print_shop.desk.read import Desk
from print_shop.desk.server import HOST, make_server

__all__ = ["HOST", "Desk", "make_server"]
