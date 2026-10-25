"""Import the Sponsor's tuned Orca presets into print-shop's `profiles/`, on a branch for a PR.

    uv run python scripts/import_profiles.py            # a branch and a commit
    uv run python scripts/import_profiles.py --dry-run  # only say what would change

Run it after tuning something in Orca, from print-shop's own checkout with a
clean working tree. It reads `~/Library/Application Support/OrcaSlicer`, and
never writes there. `profiles/scope.json` says what it covers: Orca's standard
presets in scope are written whole to `profiles/standard/`, and each of the
Sponsor's tunings of one to `profiles/tuned/`, as only the keys it changes.
The printer's address is never copied.

Changed files go on a new branch, `profiles/import-<time>`, as one commit. It
doesn't push: the next commands are printed, and the PR is where a tuning
change is reviewed (the Sponsor, 2026-10-25, on mqucifer/print-shop#6). A
profile that is in the store but no longer in Orca is listed, not removed.
Prints never run this; they slice from the files already committed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from print_shop.slicer.catalog import (
    DEFAULT_ORCA_DIR,
    Catalog,
    CatalogError,
    Scope,
    import_presets,
)
from print_shop.slicer.store import STORE_DIR, Store


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--orca-dir", type=Path, default=DEFAULT_ORCA_DIR, help="Orca's data")
    parser.add_argument("--store", type=Path, default=STORE_DIR, help="print-shop's profiles/")
    parser.add_argument("--dry-run", action="store_true", help="say what would change, only")
    args = parser.parse_args(argv)

    repo = args.store.parent
    if not args.dry_run and _git("status", "--porcelain", cwd=repo):
        print("The working tree has changes. Commit or set them aside first.", file=sys.stderr)
        return 2

    try:
        catalog = Catalog(args.orca_dir)
        scope = Scope.read(args.store / "scope.json")
        planned = import_presets(catalog, args.store, scope, write=False)
    except CatalogError as exc:
        print(f"Nothing imported: {exc}", file=sys.stderr)
        return 1

    for warning in planned.warnings:
        print(f"warning: {warning}")
    for line in planned.not_imported:
        print(f"not imported: {line}")
    for rel in planned.only_in_store:
        print(f"in the store, not in Orca (kept): {rel}")
    if not planned.written:
        print(f"Nothing changed: {len(planned.unchanged)} profiles match Orca.")
        return 0
    for rel in planned.written:
        print(f"changed: {rel}")
    if args.dry_run:
        return 0

    branch = f"profiles/import-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    _git("switch", "-c", branch, cwd=repo)
    import_presets(catalog, args.store, scope)
    Store(args.store)  # every tuning names a standard in the store
    _git("add", "--", str(args.store), cwd=repo)
    message = "\n".join(
        [
            f"profiles: import {len(planned.written)} from Orca",
            "",
            *(f"- {rel}" for rel in planned.written),
            *([""] + [f"warning: {w}" for w in planned.warnings] if planned.warnings else []),
        ]
    )
    _git("commit", "-m", message, cwd=repo)
    print(f"\nCommitted on {branch}. To review it as a PR:")
    print(f"  git push -u origin {branch}")
    print("  gh pr create --fill --assignee mquarters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
