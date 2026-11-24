"""Send files to the print queue, for the person to pick up when they can (mqucifer/print-shop#226).

    uv run --project ~/Documents/git/print-shop python ~/Documents/git/print-shop/scripts/send.py \\
        --by "Claude, in the cad session" --note "the lid set: lid first" \\
        PART.stl [xN] [spool=S] [for="what it's for"] [loaded=LOAD] [seen=walls,top] \\
                 [suggest="KEY=VALUE because why"] ... \\
        SLICED.gcode [xN] [spool=S] [project=P.3mf]  SLICED.gcode.3mf ...

For a Claude session or the person, from any folder. Each file is followed by what's said of it:

- `xN`: that many prints of it (one by default);
- `spool=S`: the stock's spool it's for (the loaded one by default; it needn't be loaded);
- `for="..."` and `loaded=...`: what a part is for, in words, and how it's loaded, one of
  "decoration or display", "handled, clipped or lightly loaded", "carries a real load" and "a
  prototype or test fit" (a word of one will do: `loaded=handled`). It's read back at the
  questions as the part's notes are (mqucifer/print-shop#236), never the person's answer until
  they confirm it, and it stands in place of what a sidecar says;
- `seen=walls,top`: the faces that will be seen (walls, top, bottom, or `nothing`);
- `suggest="KEY=VALUE because why"`: a setting you suggest, as a correction is typed at the
  review. It's offered at the review as yours, unticked, and applied only if the person uses it;
- `project=P.3mf`: for G-code, the Orca project it was sliced from, for its objects' overrides.
  Without it, a project beside the G-code whose objects are the G-code's, by name, is taken.

**The queue keeps its own copy** of each file and what belongs to it: its notes file
(`PART.part.json`), its design run's context as it reads now, and the Orca project. The original
can then move or change. **It answers in words to relay:** what was queued, what came with each,
and what wasn't and why. The same bytes as a job already waiting aren't queued twice; the same as
a past print are queued, saying that print and how it came out.

**Sending is all it does.** It has no option to answer, approve, arm or start, and it ignores
`PRINT_AGENT_ALLOW_HARDWARE`: nothing here takes a printer or the daemon. The files wait in the
jobs folder until the queue is open (`print_job.py --queue`), which prepares them at once if it is
already. The questions, the review, the arming and the Start are the person's there. It runs beside
any job, and takes no lock.

Exits 0 when every file was queued, 1 when some weren't, and 2 when none was.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from print_shop.arming import ALLOW_HARDWARE_ENV
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.inventory import Material
from print_shop.ledger import ledger_path
from print_shop.orchestrator.sending import send
from print_shop.queue_terminal import parse_adding
from print_shop.runtime import WORK_DIR, bed_type, job_profiles, sliced_reader
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.store import STORE_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] if __doc__ else "",
        epilog="Each file is followed by xN, spool=S, for=, loaded=, seen=, suggest= and "
               "project=, as the module's docstring says.")
    parser.add_argument("files", nargs="+", help="the files, each followed by what's said of it")
    parser.add_argument("--by", default="",
                        help="who sends them, as the person will see it (you, by default)")
    parser.add_argument("--note", default="", help="a note for the person, beside them")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_PATH, help="the settings file (a path)")
    args = parser.parse_args(argv)

    adding = parse_adding(list(args.files))
    if isinstance(adding, str):
        print(adding, file=sys.stderr)
        return 2
    if os.environ.get(ALLOW_HARDWARE_ENV):
        print(f"{ALLOW_HARDWARE_ENV} is set, and sending ignores it: nothing is armed, "
              "approved or started by sending.")
    ledger = ledger_path(args.settings)
    bed = bed_type(load_env(args.settings))

    def profiles(material: Material | None, into: Path) -> SlicerProfiles:
        if material is None:
            raise ValueError("the stock isn't set up (`scripts/stock.py`), so the spool it's for "
                             "isn't known: set it up, then send it")
        return job_profiles(material, into, bed=bed, store_root=STORE_DIR)

    done = send(adding, by=args.by, note=args.note, workdir=WORK_DIR,
                ledger_path=ledger if ledger.is_file() else None, profiles_for=profiles,
                sliced_for=sliced_reader(args.settings, STORE_DIR))
    for line in done.lines():
        print(line)
    if not done.queued:
        return 2
    return 1 if done.refused else 0


if __name__ == "__main__":
    sys.exit(main())
