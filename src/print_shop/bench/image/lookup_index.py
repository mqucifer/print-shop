"""Build the `lookup` tool's index from the library the image installs.

mqucifer/model-performance#40, A2. v1's lookup listed a class's own `vars()`, so it
said inherited methods (`Solid.fuse`, `Solid.Volume`) didn't exist, and it listed only
9 of OCP's modules. This index is built once, at the image's build, from what's
installed:

- **CadQuery:** every public class, function and member in its modules, by the method
  resolution order, so a member is found on every class that has it. Each function is
  one entry, named by the class that defines it, with `on`, every class it's on: "`fuse`,
  defined on Shape, on Compound, Shape, Solid…". `cadquery.func`'s free functions
  are named `cq.func.<name>`.
- **OCP** (CadQuery's binding of the OCCT kernel): every public name in every module,
  with its docstring's overloads as its signature, since pybind11 gives no
  `inspect.signature`. A class's members are listed by the tool, live, when it's named.

    python lookup_index.py <out.json>       (in the image: standard library and CadQuery)
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import re
import sys
import types
from typing import Any

# CadQuery's modules, by the prefix a name is shown with: the first that exports an
# object names it, so `cq.Workplane`, not `cadquery.cq.Workplane`.
CADQUERY = (
    ("cq", "cadquery"),
    ("cq.func", "cadquery.func"),
    ("cq.occ_impl.shapes", "cadquery.occ_impl.shapes"),
    ("cq.occ_impl.geom", "cadquery.occ_impl.geom"),
    ("cq.occ_impl.exporters", "cadquery.occ_impl.exporters"),
    ("cq.occ_impl.importers", "cadquery.occ_impl.importers"),
    ("cq.selectors", "cadquery.selectors"),
    ("cq.sketch", "cadquery.sketch"),
    ("cq.assembly", "cadquery.assembly"),
)
# `cq.CQ` is `cq.Workplane`: listed once, by its own name.
ALIASES = {"CQ"}
DOC_CHARS = 400
# A default's repr can name where it sits in memory (`<... object at 0xffff...>`): never
# in the index, so two builds of it are byte for byte the same (the review of #41).
ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def stable(text: str) -> str:
    return ADDRESS.sub("", text)


def first_paragraph(doc: str | None) -> str:
    return stable(inspect.cleandoc(doc or "").split("\n\n", 1)[0].strip()[:DOC_CHARS])


def overloads(doc: str | None) -> list[str]:
    """pybind11's signatures, from the head of its docstring: one, or numbered."""
    lines = inspect.cleandoc(doc or "").splitlines()
    found = [
        stable(re.sub(r"^\d+\.\s+", "", line))
        for line in lines
        if re.match(r"^(\d+\.\s+)?\w+\(", line)
    ]
    return found


def _signature(obj: Any) -> str:
    try:
        return stable(str(inspect.signature(obj)))
    except (TypeError, ValueError):
        return ""


def _kind(obj: Any) -> str:
    if inspect.isclass(obj):
        return "class"
    if isinstance(obj, property):
        return "property"
    if isinstance(obj, classmethod):
        return "classmethod"
    if isinstance(obj, staticmethod):
        return "staticmethod"
    return "function" if callable(obj) else "value"


def library(modules: list[tuple[str, types.ModuleType]], prefix: str) -> list[dict[str, Any]]:
    """Every public name `modules` define (objects whose `__module__` starts with
    `prefix`), each class's members through its method resolution order, each
    function once, with every class it's on."""
    named: dict[int, str] = {}
    classes: list[tuple[str, type]] = []
    found: list[dict[str, Any]] = []
    for shown, module in modules:
        for name, obj in sorted(vars(module).items()):
            if name.startswith("_") or name in ALIASES or id(obj) in named:
                continue
            if not (getattr(obj, "__module__", "") or "").startswith(prefix):
                continue
            qualified = f"{shown}.{name}"
            named[id(obj)] = qualified
            found.append(
                {
                    "name": qualified,
                    "kind": _kind(obj),
                    "signature": "" if inspect.isclass(obj) else _signature(obj),
                    "doc": first_paragraph(getattr(obj, "__doc__", None)),
                }
            )
            if inspect.isclass(obj):
                classes.append((qualified, obj))
    # Each member once, under the class that defines it, with every class it's on.
    members: dict[tuple[int, str], dict[str, Any]] = {}
    for qualified, cls in classes:
        for name in dir(cls):
            if name.startswith("_"):
                continue
            owner = next((b for b in cls.__mro__ if name in vars(b)), None)
            if owner is None or id(owner) not in named:
                continue
            raw = vars(owner)[name]
            key = (id(owner), name)
            if key not in members:
                member = raw.fget if isinstance(raw, property) else raw
                member = getattr(member, "__func__", member)
                members[key] = {
                    "name": f"{named[id(owner)]}.{name}",
                    "kind": _kind(raw),
                    "signature": _signature(member) if callable(member) else "",
                    "doc": first_paragraph(getattr(member, "__doc__", None)),
                    "on": [],
                }
            members[key]["on"].append(qualified)
    for entry in members.values():
        entry["on"] = sorted(entry["on"])
        found.append(entry)
    return found


def ocp() -> list[dict[str, Any]]:
    """Every public name in every OCP module, with its overloads from the docstring."""
    import OCP  # noqa: PLC0415

    found = []
    for info in sorted(pkgutil.iter_modules(OCP.__path__), key=lambda m: m.name):
        try:
            module = importlib.import_module(f"OCP.{info.name}")
        except ImportError:
            continue
        for name in sorted(dir(module)):
            if name.startswith("_"):
                continue
            obj = getattr(module, name)
            doc = getattr(obj, "__doc__", None) if not isinstance(obj, int | float | str) else None
            init = getattr(obj, "__init__", None) if inspect.isclass(obj) else None
            found.append(
                {
                    "name": f"OCP.{info.name}.{name}",
                    "kind": _kind(obj),
                    "signature": " | ".join(overloads(getattr(init, "__doc__", None) or doc))[:400],
                    "doc": first_paragraph(doc) if inspect.isclass(obj) else "",
                }
            )
    return found


def build() -> dict[str, Any]:
    from importlib.metadata import version  # noqa: PLC0415

    modules = [(shown, importlib.import_module(name)) for shown, name in CADQUERY]
    entries = library(modules, prefix="cadquery") + ocp()
    return {
        "cadquery": version("cadquery"),
        "ocp": version("cadquery-ocp"),
        "entries": entries,
    }


def main() -> None:
    index = build()
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(index, fh)
    cq = sum(1 for e in index["entries"] if e["name"].startswith("cq."))
    print(f"lookup index: {cq} CadQuery names, {len(index['entries']) - cq} OCP names")


if __name__ == "__main__":
    main()
