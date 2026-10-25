"""What's on hand, kept up one command line at a time.

    uv run python scripts/stock.py                                  # list what's on hand
    uv run python scripts/stock.py printer add cc1 "Elegoo Centauri Carbon"
    uv run python scripts/stock.py fit 0.4                          # fit a nozzle
    uv run python scripts/stock.py add "Elegoo PLA" white --grams 1000
    uv run python scripts/stock.py add "Elegoo Rapid PETG" black --tuning "Elegoo RAPID PETG 270…"
    uv run python scripts/stock.py load 3                           # load spool #3
    uv run python scripts/stock.py empty 3

mqucifer/print-shop#11: there's no AMS to read, so the Sponsor is the only
source, and keeping it up has to be quicker than not. A spool is named by its
variety as `scripts/print_job.py --filaments` lists them; its manufacturer and
type come from the profile. When the Sponsor has tuned that variety more than
once, the tuning is chosen here, once. A print then takes the fitted nozzle
and loaded spool as its defaults. `--printer` is needed only with more than one.

The inventory lives in the ledger's file (`PRINT_SHOP_LEDGER`, default
`~/print-shop/ledger.sqlite`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from print_shop.env import SETTINGS_PATH
from print_shop.inventory import Inventory, InventoryError
from print_shop.ledger import ledger_path
from print_shop.slicer.store import STORE_DIR, Store, StoredProfile


def _standard(store: Store, variety: str) -> StoredProfile | None:
    want = variety.casefold()
    return next(
        (s for s in store.standards["filament"]
         if want in (s.variety.casefold(), s.name.casefold())),
        None,
    )


def add(inventory: Inventory, args: argparse.Namespace) -> str:
    store = Store(STORE_DIR)
    standard = _standard(store, args.variety)
    if standard is None:
        if not (args.type and args.manufacturer):
            raise InventoryError(
                f"'{args.variety}' has no profile in profiles/ (`print_job.py --filaments` lists "
                "them). To record it anyway, give --type and --manufacturer; no print uses it "
                "until profiles/scope.json covers it."
            )
        spool = inventory.add_spool(
            manufacturer=args.manufacturer, type=args.type.upper(), variety=args.variety,
            colour=args.colour, grams_left=args.grams,
        )
        return f"{spool.line()}\n  (no profile for it: a print won't use it yet)"

    tunings = [t.name for t in store.of(standard)]
    if args.tuning is not None and args.tuning not in tunings:
        raise InventoryError(
            f"'{args.tuning}' isn't one of your tunings of {standard.variety}: "
            + (", ".join(f'"{t}"' for t in tunings) or "you haven't tuned it")
        )
    if args.tuning is None and len(tunings) > 1:
        listed = "\n".join(f'  --tuning "{t}"' for t in tunings)
        raise InventoryError(
            f"You've tuned {standard.variety} more than once; say which this spool prints "
            f"with:\n{listed}"
        )
    spool = inventory.add_spool(
        manufacturer=standard.manufacturer, type=standard.filament_type,
        variety=standard.variety, colour=args.colour, grams_left=args.grams,
        tuning=args.tuning,
    )
    return spool.line()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--settings", type=Path, default=SETTINGS_PATH,
                        help="the settings file (a path)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="what's on hand (the default)").add_argument(
        "--all", action="store_true", help="empty spools too")
    printer = sub.add_parser("printer", help="add a printer")
    printer.add_argument("action", choices=["add"])
    printer.add_argument("id", help="a short name, e.g. cc1")
    printer.add_argument("model")
    spool = sub.add_parser("add", help="add a spool")
    spool.add_argument("variety", help='e.g. "Elegoo PLA", as --filaments lists it')
    spool.add_argument("colour")
    spool.add_argument("--grams", type=float, help="filament left, if known")
    spool.add_argument("--tuning", help="which of your tunings it prints with")
    spool.add_argument("--type", help="for a filament with no profile, e.g. TPU")
    spool.add_argument("--manufacturer", help="for a filament with no profile")
    for name, what in (("load", "load a spool"), ("empty", "mark a spool empty")):
        p = sub.add_parser(name, help=what)
        p.add_argument("spool", type=int, help="the spool's number")
        if name == "load":
            p.add_argument("--printer")
    fit = sub.add_parser("fit", help="fit a nozzle")
    fit.add_argument("diameter", type=float, help="mm, e.g. 0.4")
    fit.add_argument("--printer")
    fit.add_argument("--note", default="", help='e.g. "hardened"')
    nozzle = sub.add_parser("nozzle", help="add a nozzle you own, not fitted")
    nozzle.add_argument("action", choices=["add"])
    nozzle.add_argument("diameter", type=float)
    nozzle.add_argument("--note", default="")
    args = parser.parse_args(argv)

    with Inventory(ledger_path(args.settings)) as inventory:
        try:
            if args.command in (None, "list"):
                print(inventory.listing(with_empty=getattr(args, "all", False)))
            elif args.command == "printer":
                added = inventory.add_printer(args.id, args.model)
                print(f"printer {added.id}  {added.model}")
            elif args.command == "add":
                print(add(inventory, args))
            elif args.command == "load":
                print(inventory.load(args.spool, args.printer).line())
            elif args.command == "empty":
                print(inventory.empty(args.spool).line())
            elif args.command == "fit":
                print(inventory.fit(args.diameter, args.printer, args.note).line())
            elif args.command == "nozzle":
                print(inventory.add_nozzle(args.diameter, args.note).line())
        except InventoryError as exc:
            print(exc, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
