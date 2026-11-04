"""`needs` refuses a `number` its command never reports, naming those it does
(mqucifer/print-shop#45, item 3). No Docker: each command's keys are read from the bench's code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import print_shop
from print_shop.rounds import needs, reports
from print_shop.rounds.needs import Need

#: Read as text: the bench's modules load CAD libraries only its image has.
BENCH = Path(print_shop.__file__).parent / "bench"


def _key(node: ast.expr) -> str:
    """A number's name as the code writes it: a part it fills in is a `*`."""
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(part.value) if isinstance(part, ast.Constant) else "*" for part in node.values
        )
    raise AssertionError(f"a number named by {ast.unparse(node)}, which this test can't read")


class Module:
    """One bench module's functions, the names it imports from the others, and each function's
    numbers and the functions it calls."""

    def __init__(self, name: str) -> None:
        tree = ast.parse((BENCH / f"{name}.py").read_text(encoding="utf-8"))
        self.functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
        self.imported: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                for alias in node.names:
                    self.imported[alias.asname or alias.name] = node.module

    def keys_and_calls(self, name: str) -> tuple[set[str], set[str]]:
        keys: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(self.functions[name]):
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "fact"
                    and node.args
                    or (
                        isinstance(func, ast.Attribute)
                        and func.attr == "setdefault"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "numbers"
                    )
                ):
                    keys.add(_key(node.args[0]))
                elif isinstance(func, ast.Name):
                    calls.add(func.id)
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "numbers"
            ):
                keys.add(_key(node.slice))
        return keys, calls


def reported(module: str, function: str) -> set[str]:
    """Every number a command's entry sets, through each function it calls, in its module or
    imported from another of the bench's."""
    modules: dict[str, Module] = {}
    seen: set[tuple[str, str]] = set()
    keys: set[str] = set()
    todo = [(module, function)]
    while todo:
        where, name = todo.pop()
        if (where, name) in seen:
            continue
        seen.add((where, name))
        mod = modules.setdefault(where, Module(where))
        if name not in mod.functions:
            continue  # a class or a constant: no number is set outside a function
        found, calls = mod.keys_and_calls(name)
        keys |= found
        for called in calls:
            if called in mod.functions:
                todo.append((where, called))
            elif called in mod.imported and (BENCH / f"{mod.imported[called]}.py").is_file():
                todo.append((mod.imported[called], called))
    return keys


def entries() -> dict[str, tuple[str, str]]:
    """Each command's entry, from `cli._dispatch`: `from .module import function`."""
    tree = ast.parse((BENCH / "cli.py").read_text(encoding="utf-8"))
    dispatch = next(
        f for f in tree.body if isinstance(f, ast.FunctionDef) and f.name == "_dispatch"
    )
    found: dict[str, tuple[str, str]] = {}
    for node in ast.walk(dispatch):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and isinstance(test.comparators[0], ast.Constant)):
            continue
        command = str(test.comparators[0].value)
        imported = next(n for n in node.body if isinstance(n, ast.ImportFrom))
        names = {a.asname or a.name: a.name for a in imported.names}
        called = next(
            n.func.id
            for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names
        )
        found[command] = (str(imported.module), names[called])
    return found


def test_each_commands_numbers_are_the_ones_its_code_reports() -> None:
    found = entries()
    assert set(found) == set(reports.REPORTS) == set(needs.COMMANDS)
    for command, (module, function) in found.items():
        assert set(reports.REPORTS[command]) == reported(module, function), command


def test_a_number_its_command_never_reports_is_refused_naming_those_it_does() -> None:
    def need(command: str, number: str) -> Need:
        return Need("hang", "x", "chosen", "bench", command=command, number=number, at_least=2)

    wrong = needs.numbers_wrong([need("wall frame.step --hole 1,2,3", "min_wall_mm")])
    assert len(wrong) == 1
    assert "the need 'hang': `wall` never reports `min_wall_mm`" in wrong[0]
    assert "`least_wall_mm`" in wrong[0]
    assert not needs.numbers_wrong([need("wall frame.step --hole 1,2,3", "least_wall_mm")])
    # A path's first part is held, and what's under it depends on the part.
    assert not needs.numbers_wrong([need("clearance a.step b.step", "pairs.0.gap_mesh_mm")])
    assert needs.numbers_wrong([need("clearance a.step b.step", "pair.0.gap_mesh_mm")])
    # A name the code fills in.
    assert not needs.numbers_wrong([need("panes frame.step", "pocket_section_mm2")])
    assert not needs.numbers_wrong([need("thin pane.step", "")])
