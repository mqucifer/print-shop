"""A refused setting says what it takes, and an ok after it asks once (mqucifer/print-shop#279).

The Sponsor, 2026-11-24, at the gate of a test print: "Though I didn't want ironing at all. I
think it didn't take it". `ironing=0` was refused with `ironing_type` as the nearest, nothing
said what turns it off, and the `ok` typed next approved without it. Now a setting whose words
are all Orca's says them, and the one that turns it off; a word Orca doesn't take is refused
before anything slices; and the `ok` after a refused line asks once. The profiles and the slice
are `test_setting_names`'s made-up ones; nothing reaches a printer, broker or ledger.
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from print_shop.setup.bounds import SETTINGS
from print_shop.setup.names import choices, find, not_a_choice, off, refused
from print_shop.setup.review import Review, parse_answer
from tests.test_setting_names import CONFIG, FILAMENT, PROCESS, Person, _request

IRONING = ("'ironing' isn't an Orca setting. Nearest: ironing_type (Ironing, now no ironing; "
           "no ironing, top, topmost or solid); ironing_flow (Ironing flow, now 10%); "
           "ironing_speed (Ironing speed, now 20 mm/s). To turn it off: ironing_type=no ironing")
NOT_OFF = ("'off' isn't one of ironing_type's choices: no ironing, top, topmost or solid. To turn "
           "it off: ironing_type=no ironing")
ASKED = "  Your 'ironing=0' wasn't set. ok again to approve without it."
ROOT = Path(__file__).resolve().parent.parent
DEADLINE_S = 40.0


class Forgetting(Person):
    """A person whose typed-ahead lines are dropped (`forget`), counted where it's done."""

    def __init__(self, *answers: str) -> None:
        super().__init__(*answers)
        self.forgot: list[int] = []  # how much had been said each time

    def review(self, request, width: int = 100):  # type: ignore[no-untyped-def]
        from print_shop.setup.review import cli_reviewer  # noqa: PLC0415

        reviewer = cli_reviewer(read=self.read, write=self.shown.append,
                                forget=lambda: self.forgot.append(len(self.shown)),
                                asks=lambda: False, screen=lambda: None, width=lambda: width,
                                more=lambda: False)
        return reviewer(request)


# --------------------------------------------------------------------------
# Done when: ironing=0 is refused with the nearest's choices and its off
# --------------------------------------------------------------------------


def test_ironing_is_refused_with_the_nearests_choices_and_the_line_that_turns_it_off() -> None:
    assert refused("ironing", PROCESS, FILAMENT, CONFIG) == IRONING
    said = parse_answer("ironing=0 because a test print", _request())
    assert said == IRONING


def test_the_off_line_ends_with_its_value_where_a_space_is_in_it() -> None:
    person = Person("ironing=0 because a test print", "stop")
    person.review(_request(), width=60)
    flat = " ".join(person.said.split())
    assert flat.endswith("To turn it off: ironing_type=no ironing")


# --------------------------------------------------------------------------
# Done when: ironing_type=off is refused before slicing, and the ok after asks
# --------------------------------------------------------------------------


def test_a_word_orca_doesnt_take_is_refused_before_anything_slices() -> None:
    assert not_a_choice("ironing_type", "off") == NOT_OFF
    assert parse_answer("ironing_type=off because a test print", _request()) == NOT_OFF


def test_the_ok_after_a_refused_word_asks_once_and_the_next_approves_without_it() -> None:
    person = Person("ironing_type=off because a test print", "ok", "ok")
    assert person.review(_request()) == Review(approved=True), "nothing from it is set"
    assert " ".join(person.shown[1].split()) == NOT_OFF
    assert person.shown[2] == ("  Your 'ironing_type=off' wasn't set. ok again to approve "
                               "without it.")
    assert person.prompts == ["> ", "> ", "> "]


def test_a_word_in_another_case_is_read_as_orcas() -> None:
    review = parse_answer("ironing_type=No Ironing because a test print", _request())
    assert isinstance(review, Review)
    assert [(c.key, c.value) for c in review.corrections] == [("ironing_type", "no ironing")]


def test_the_word_that_turns_it_off_is_taken() -> None:
    review = parse_answer("ironing_type=no ironing because a test print", _request())
    assert isinstance(review, Review)
    assert [(c.key, c.value, c.reason) for c in review.corrections] == [
        ("ironing_type", "no ironing", "a test print")]


# --------------------------------------------------------------------------
# Done when: find ironing shows the same choices
# --------------------------------------------------------------------------


def test_find_ironing_shows_the_same_choices_and_its_off() -> None:
    found = {f.key: f for f in find("ironing", PROCESS, FILAMENT, CONFIG)}
    assert found["ironing_type"].choices == ("no ironing", "top", "topmost", "solid")
    assert found["ironing_type"].off == "no ironing"
    assert found["ironing_flow"].choices == () and found["ironing_flow"].off == ""
    person = Person("find ironing", "stop")
    assert person.review(_request()) == Review(stop=True), "finding takes nothing"
    lines = person.said.splitlines()
    at = next(n for n, line in enumerate(lines) if line.split()[-1:] == ["ironing_type"])
    assert lines[at + 1] == ("      Takes no ironing, top, topmost or solid. To turn it off: "
                             "ironing_type=no ironing")
    assert sum("Takes" in line for line in lines) == 1, "only the setting that takes words"


# --------------------------------------------------------------------------
# Done when: a setting with choices and no off lists them and claims no off
# --------------------------------------------------------------------------


def test_a_seam_position_lists_its_choices_and_claims_no_off() -> None:
    said = refused("seam_positon", PROCESS, FILAMENT, CONFIG)
    assert said == ("'seam_positon' isn't an Orca setting. Nearest: seam_position (Seam position, "
                    "now aligned; aligned, aligned_back, back, nearest or random)")
    assert not_a_choice("seam_position", "rear") == (
        "'rear' isn't one of seam_position's choices: aligned, aligned_back, back, nearest or "
        "random")
    person = Person("find seam position", "stop")
    person.review(_request())
    assert "      Takes aligned, aligned_back, back, nearest or random." in person.said
    assert "turn it off" not in person.said


def test_only_settings_holding_all_of_orcas_words_say_them() -> None:
    # brim_type's are some of Orca's (it takes inner_only, outer_and_inner and painted too), and
    # support_type's and sparse_infill_pattern's are the shop's bound on the model's proposals.
    assert {k for k, s in SETTINGS.items() if s.orcas} == {"ironing_type", "seam_position"}
    assert {k: off(k) for k in SETTINGS if off(k)} == {"ironing_type": "no ironing"}
    for key in ("brim_type", "support_type", "sparse_infill_pattern"):
        assert choices(key) == () and not_a_choice(key, "anything") == ""
    review = parse_answer("brim_type=inner_only because a test print", _request())
    assert isinstance(review, Review), "a word Orca takes isn't refused by the new check"
    assert [(c.key, c.value) for c in review.corrections] == [("brim_type", "inner_only")]
    said = refused("brim", PROCESS, FILAMENT, CONFIG)
    assert said == "'brim' isn't an Orca setting. Nearest: brim_type (Brim, now auto_brim)"


# --------------------------------------------------------------------------
# Done when: after a refused line, ok asks once and a second ok approves
# --------------------------------------------------------------------------


def test_ok_after_a_refused_line_asks_once_and_a_second_ok_approves_without_it() -> None:
    person = Forgetting("ironing=0 because a test print", "ok", "ok")
    assert person.review(_request()) == Review(approved=True)
    assert person.shown[2] == ASKED
    assert person.forgot == [3], "what was typed ahead of the question is dropped once it's shown"
    assert person.prompts == ["> ", "> ", "> "]


def test_find_leaves_the_question_to_the_ok() -> None:
    person = Person("ironing=0 because a test print", "find ironing", "ok", "ok")
    assert person.review(_request()) == Review(approved=True)
    assert ASKED in person.shown


def test_several_refused_in_one_line_are_each_named() -> None:
    person = Person("ironing=0 because a test print; seam=back", "ok", "ok")
    assert person.review(_request()) == Review(approved=True)
    assert ("  Your 'ironing=0' and 'seam=back' weren't set. ok again to approve without "
            "them.") in person.shown


def test_a_setting_named_in_words_that_is_refused_asks_too() -> None:
    person = Person("ironing type=no ironing because a test print", "ok", "ok")
    assert person.review(_request()) == Review(approved=True)
    assert ("  Your 'ironing type=no ironing' wasn't set. ok again to approve without it."
            in person.shown)


def test_a_refused_line_answered_by_another_asks_nothing() -> None:
    person = Person("ironing=0 because a test print",
                    "ironing_type=no ironing because a test print")
    review = person.review(_request())
    assert [(c.key, c.value) for c in review.corrections] == [("ironing_type", "no ironing")]
    person = Person("ironing=0 because a test print", "frobnicate", "ok")
    assert person.review(_request()) == Review(approved=True)
    assert not any("wasn't set" in line for line in person.shown)


def test_a_line_left_by_back_asks_nothing() -> None:
    person = Person("ironing=0 because a test print; wall_loops=3", "back", "ok")
    assert person.review(_request()) == Review(approved=True)
    assert "  Back to the review: nothing from that answer is taken." in person.shown
    assert not any("wasn't set" in line for line in person.shown)


def test_an_ok_with_nothing_refused_asks_nothing() -> None:
    person = Forgetting("ok")
    assert person.review(_request()) == Review(approved=True)
    assert person.prompts == ["> "] and person.forgot == []


# --------------------------------------------------------------------------
# Done when: a number's or on/off setting's refusal reads as it does today
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "said"), [
    ("nozzle_temp", "'nozzle_temp' isn't an Orca setting. Nearest: nozzle_temperature (Nozzle "
                    "temperature, now 210 °C)"),
    ("enable_suport", "'enable_suport' isn't an Orca setting. Nearest: enable_support (Supports, "
                      "now off)"),
])
def test_a_number_or_a_switch_reads_as_today(key: str, said: str) -> None:
    assert refused(key, PROCESS, FILAMENT, CONFIG) == said
    assert not_a_choice("enable_support", "maybe") == ""
    assert not_a_choice("wall_loops", "lots") == ""


# --------------------------------------------------------------------------
# Done when: an ok typed ahead of the question doesn't answer it (a pty)
# --------------------------------------------------------------------------

CHILD = """
import sys, time
from pathlib import Path
sys.path[:0] = [{root!r}, {src!r}]
from print_shop.setup.review import cli_reviewer
from tests.test_setting_names import _request

flag = Path({flag!r})

def write(text):
    if "wasn't set" in text:
        # Held until the person has typed ahead of it.
        print("HOLD", flush=True)
        began = time.monotonic()
        while not flag.exists() and time.monotonic() - began < 30:
            time.sleep(0.02)
    print(text, flush=True)

review = cli_reviewer(write=write, asks=lambda: False)(_request())
print("RESULT", review.approved, [(c.key, c.value) for c in review.corrections], flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a pseudo-terminal")
def test_an_ok_typed_ahead_of_the_question_never_answers_it(tmp_path: Path) -> None:
    flag = tmp_path / "typed-ahead"
    fd, side = pty.openpty()
    code = CHILD.format(root=str(ROOT), src=str(ROOT / "src"), flag=str(flag))
    child = subprocess.Popen([sys.executable, "-B", "-c", code], cwd=ROOT, stdin=side,
                             stdout=side, stderr=side, start_new_session=True)
    os.close(side)
    seen = ""
    began = time.monotonic()

    def read_until(pattern: str, after: int = 0) -> re.Match[str]:
        nonlocal seen
        while time.monotonic() - began < DEADLINE_S:
            found = re.compile(pattern).search(seen, after)
            if found:
                return found
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    seen += os.read(fd, 65536).decode(errors="replace")
                except OSError:
                    break
        raise AssertionError(f"never saw {pattern!r} in:\n{seen[-3000:]}")

    try:
        at = read_until(r"> ").end()
        os.write(fd, b"ironing=0 because a test print\n")
        at = read_until(r"To turn it off:\s+ironing_type=no\s+ironing", at).end()
        at = read_until(r"> ", at).end()
        os.write(fd, b"ok\n")
        at = read_until(r"HOLD\r?\n", at).end()
        os.write(fd, b"ok\n")  # typed ahead of the question, as it's being drawn
        flag.touch()
        at = read_until(r"Your 'ironing=0' wasn't set\. ok again to approve without it\.",
                        at).end()
        at = read_until(r"> ", at).end()
        os.write(fd, b"find ironing\n")
        found = read_until(r"Settings that fit 'ironing'", at)
        assert "RESULT" not in seen[:found.start()], "the ok typed ahead didn't approve"
        os.write(fd, b"ok\n")
        result = read_until(r"RESULT ([^\r\n]*)\r?\n", found.end())
        assert result.group(1) == "True []", "the ok after the question approves without it"
    finally:
        child.kill()
        child.wait(timeout=DEADLINE_S)
        os.close(fd)
