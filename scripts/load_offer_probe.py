#!/usr/bin/env python3
"""Whether a `load` that meets this host's container's memory reads on a host with more, by
counts alone (mqucifer/print-shop#380).

Run from the folder the assembly is in, as `scripts/bench.py load ...` is, with the Mac's
settings naming a remote host that states a larger memory for a load
(`BENCH_HOST_<NAME>_LOAD_MEMORY`, `bench/remote.py`):

    uv run python <print-shop>/scripts/load_offer_probe.py load ASSEMBLY.json ARGS...

It reads the `load` here first, as a study's job does, under the command's own limits
(`job.BenchJob.of`), in the second image; and where its read met the container's memory, offers
it to the hosts the settings name, as a study's pool does (`pool.Offers`): the same command over
the same files, in the host's larger memory, within its budget and over its floor. It prints only
codes, words, kinds, seconds and memory: for each read, the exit code and the verdict's word,
never its reasons; whether it read the whole, a glass band's lower bound, or nothing; how its
read met the memory, as a kind (`killed`, `the cgroup's count`, `a step's need against its
room`, each `on the way, and then stopped at its time limit` where it went on and ran out of
time), with the container's limit, the most it held, and the need and room where a check said
them, in GiB; and where the offer went, as a kind (`read there`, `read there, no more than its
first answer`, `no host states more`, `no host took it`, `the host failed it`, `stopping`), with
each failure's kind (the step, and SSH's or Docker's part, as `running it: docker's exit 125`,
or `running it: past its time limit`), the host's name and its limit, and, where it was read
there, how that read met the memory there too, if it did, as it would where its whole is killed
at the larger memory as well. No figure of a part, joint,
load or mesh, no step's words and no name from the assembly is printed, and each reply stays
where `load` saves it, here and, brought back, beside it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from print_shop.bench import host
from print_shop.bench.job import BenchJob, JobResult, Met, load_files, run_here


def _gib(count: int | str | None) -> str:
    return f"{int(str(count)) / 2**30:.2f} GiB" if str(count).isdigit() else "unknown"


def _record(folder: Path, done: JobResult) -> dict[str, Any]:
    saved = [f for f in done.saved if f.endswith(".json")]
    if not saved:
        return {}
    found: dict[str, Any] = json.loads((folder / saved[0]).read_text())
    return found


def read_as(record: dict[str, Any]) -> str:
    """How a reply read: the whole, a glass band's lower bound, or nothing."""
    numbers = record.get("numbers") or {}
    if "lower_bound" in numbers:
        return "a glass band's lower bound"
    return "the whole" if numbers.get("parts") else "nothing"


def met_said(met: Met | None) -> str:
    """How a read met its container's memory, by counts and a kind alone."""
    if met is None:
        return "met the memory: no"
    kind = ("killed" if met.killed else "a step's need against its room"
            if met.need is not None or met.room is not None else "the cgroup's count")
    return (f"met the memory: yes, by {kind}"
            + (", on the way, and then stopped at its time limit" if met.stopped else "")
            + f"; limit {_gib(met.limit)}, at most {_gib(met.peak)} held, need {_gib(met.need)}, "
            f"room {_gib(met.room)}")


def reply_said(where: str, done: JobResult, record: dict[str, Any], seconds: float) -> list[str]:
    ran = record.get("ran") or {}
    return [f"{where}: exit {done.returncode}, verdict {record.get('verdict', 'none saved')}, "
            f"read {read_as(record)}, {seconds:.0f} s, its container {_gib(ran.get('memory_max'))}"
            f", at most {_gib(ran.get('memory_peak'))} held",
            f"  {met_said(done.met)}"]


def main(argv: list[str]) -> int:
    if host.command_word(argv) != "load" or len(argv) < 2:
        print(__doc__.split("\n\n")[2])
        return 2
    from print_shop.bench.pool import Offers
    from print_shop.env import SETTINGS_PATH, load_env

    offers = Offers(load_env(SETTINGS_PATH))
    if not offers:
        print("refused: no host the settings name states a larger memory for a load "
              "(BENCH_HOST_<NAME>_LOAD_MEMORY)")
        return 2
    for stated in offers.hosts:
        print(f"offered: {stated.name}, its load memory {stated.load_mb / 1024:.1f} GiB, its jobs' "
              f"{stated.memory_mb / 1024:.1f} GiB, its floor {stated.floor_mb / 1024:.1f} GiB")
    folder = Path.cwd()
    job = BenchJob.of(argv, folder, load_files(folder, argv))
    began = time.monotonic()
    here = run_here(job)
    for line in reply_said("here", here, _record(folder, here), time.monotonic() - began):
        print(line, flush=True)
    if here.met is None:
        print("offer: none, as its read didn't meet the memory")
        return here.returncode
    began = time.monotonic()
    there, _ = offers(job, here.met, here.saved)
    seconds = time.monotonic() - began
    last = offers.pool().last_offer
    print(f"offer: {last.outcome if last else 'not offered'}")
    for kind in last.failures if last else ():
        print(f"  a host failed it: {kind}")
    if last is not None and last.host:
        # Read there, kept or not: what it read, and how its read met the memory there, if it did.
        print(f"{last.host}, in its {last.memory_mb / 1024:.1f} GiB: verdict {last.verdict}, "
              f"read {last.read}, {seconds:.0f} s")
        print(f"  there, {met_said(last.met)}")
    if there is None:
        return here.returncode
    for line in reply_said(there.host, there, _record(folder, there), seconds):
        print(line)
    return there.returncode

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
