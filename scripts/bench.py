#!/usr/bin/env python3
"""The small CAD bench, over files in the current directory. `docs/bench.md` says what it does.

scripts/bench.py build                      the one image, if the disk has room
scripts/bench.py check                      the image and the disk against their limits
scripts/bench.py clearance a.step b.step    any command, in a container with no network
scripts/bench.py python script.py           your own script, in the same container
"""

from __future__ import annotations

import sys

from print_shop.bench.host import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
