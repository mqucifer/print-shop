"""What a longer context costs a run's calls (mqucifer/print-shop#178), from its events alone.

    uv run python scripts/call_costs.py RUN [RUN ...] [--band TOKENS] [--json]

RUN is a run's folder or its `events.jsonl`. For each it gives the calls, the compactions, the
new conversations from the record and the calls refused as over the window, then each call's time
by its measured prompt (`usage.prompt_tokens`) in bands of 25k tokens: how many calls, the median
and 90th percentile of the call's seconds, the median seconds to its first chunk (where the
server reads the prompt), and the median tokens it answered with, since a longer answer takes
longer at any prompt. It reads events only: never a run's `work/`, and it calls nothing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from print_shop.rounds.transcript import events_of

BAND = 25_000


def p90(values: list[float]) -> float:
    """The 90th percentile, inclusive, so one call is its own."""
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[-1]


def costs(events: Path, band: int = BAND) -> dict[str, Any]:
    """One run's figures: its counts, and its calls' times by prompt band."""
    calls: list[dict[str, Any]] = []
    counted = {"compactions": 0, "restarts": 0, "over_window": 0}
    for event in events_of(events):
        kind = event.get("kind")
        if kind == "call":
            calls.append(event)
        elif kind == "compacted":
            counted["compactions"] += 1
        elif kind == "request" and event.get("from") == 0 and event.get("n", 1) > 1:
            counted["restarts"] += 1
        elif kind == "over_window":
            counted["over_window"] += 1
    by_band: dict[int, list[dict[str, Any]]] = {}
    for call in calls:
        prompt = int((call.get("usage") or {}).get("prompt_tokens") or 0)
        if prompt and call.get("seconds") is not None:
            by_band.setdefault(prompt // band, []).append(call)
    bands = []
    for low in sorted(by_band):
        group = by_band[low]
        seconds = [float(c["seconds"]) for c in group]
        first = [
            float(c["stream"]["first_chunk_s"])
            for c in group
            if (c.get("stream") or {}).get("first_chunk_s") is not None
        ]
        answered = [int((c.get("usage") or {}).get("completion_tokens") or 0) for c in group]
        bands.append(
            {
                "prompt_from": low * band,
                "prompt_to": (low + 1) * band,
                "calls": len(group),
                "median_s": round(statistics.median(seconds), 1),
                "p90_s": round(p90(seconds), 1),
                "median_first_chunk_s": round(statistics.median(first), 1) if first else None,
                "median_answer_tokens": int(statistics.median(answered)),
            }
        )
    prompts = [int((c.get("usage") or {}).get("prompt_tokens") or 0) for c in calls]
    return {
        "events": str(events),
        "calls": len(calls),
        **counted,
        "largest_prompt": max(prompts, default=0),
        "band_tokens": band,
        "bands": bands,
    }


def said(found: dict[str, Any]) -> str:
    """The figures as a table to read."""
    lines = [
        f"{found['events']}",
        f"  {found['calls']} calls, {found['compactions']} compactions, {found['restarts']} new "
        f"conversations from the record, {found['over_window']} refused as over the window; "
        f"largest prompt {found['largest_prompt']:,} tokens",
        f"  {'prompt tokens':>13}  {'calls':>5}  {'median s':>8}  {'p90 s':>7}  "
        f"{'1st chunk s':>11}  {'answer tok':>10}",
    ]
    for b in found["bands"]:
        first = "" if b["median_first_chunk_s"] is None else f"{b['median_first_chunk_s']:.1f}"
        lines.append(
            f"  {b['prompt_from'] // 1000:>7}k-{b['prompt_to'] // 1000:>3}k  {b['calls']:>5}  "
            f"{b['median_s']:>8.1f}  {b['p90_s']:>7.1f}  {first:>11}  "
            f"{b['median_answer_tokens']:>10,}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--band", type=int, default=BAND)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    found = []
    for run in args.runs:
        events = run / "events.jsonl" if run.is_dir() else run
        if not events.is_file():
            print(f"refused: {events} isn't a run's events", file=sys.stderr)
            return 3
        found.append(costs(events, args.band))
    if args.json:
        print(json.dumps(found, indent=1))
    else:
        print("\n\n".join(said(f) for f in found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
