"""What the differences put in doubt, as the model reads them: it only adds to the offer.

mqucifer/print-shop#211. The offer is made without a model (`offer.py`). Where the most similar
past print is another file, the model is asked, beside the job and never in its way, which of
what's offered the differences put in doubt. It can unselect an item, with its reason, and do
nothing else: it selects nothing, sets nothing and adds no setting.

It's a call of its own, not the proposal's: a proposal shown past overrides would propose them,
and they'd be applied before the person used them.

A review shows what had come back when it was drawn, and `use` takes what that review showed, so
a late doubt never changes a selection unseen. It's on the next review.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

from print_shop.setup.model import SHORT, Ask
from print_shop.setup.offer import Offering, Past

__all__ = ["Doubting"]

logger = logging.getLogger(__name__)

_SYSTEM = (
    "A person is about to print a part, and is offered the settings that decided a similar past "
    "print of theirs. You're given how the two parts differ and each setting with the reason "
    "they gave then. Name only the settings those differences put in doubt, each with one plain "
    "reason that cites the difference. Name none if none is. Don't propose values or settings."
)


class Doubting:
    """One reading of one past print's differences, on a thread of its own."""

    def __init__(self, past: Past, ask: Ask) -> None:
        self._past = past
        self._ask = ask
        self._found: dict[str, str] | None = None
        self._failed = ""
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read, name="offer-doubts", daemon=True)
        self._thread.start()

    @classmethod
    def start(cls, offer: Offering, ask: Ask | None) -> Doubting | None:
        """Begun for the most similar print, where there are differences to read and a model."""
        past = offer.options[0] if offer.options else None
        if ask is None or past is None or past.same_file or not past.items:
            return None
        if not past.all_differences():
            return None
        return cls(past, ask)

    def wait(self, timeout_s: float) -> bool:
        """Whether it's finished, within `timeout_s`. The review never calls it."""
        return self._done.wait(timeout_s)

    def _read(self) -> None:
        past = self._past
        keys = [item.key for item in past.items]
        shape = {
            "name": "offer_doubts",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["doubts"],
                "properties": {"doubts": {
                    "type": "array",
                    "maxItems": len(keys),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["key", "why"],
                        "properties": {"key": {"type": "string", "enum": keys}, "why": SHORT},
                    },
                }},
            },
        }
        user = "\n".join([
            f"The past print: {past.stl_name}, {past.came_out}.",
            "How this part differs from it:",
            *(f"- {d}" for d in past.all_differences()),
            "What's offered:",
            *(f"- {i.key}={i.value}: {i.reason}" for i in past.items),
        ])
        try:
            reply = self._ask(_SYSTEM, user, shape)
            found: dict[str, str] = {}
            for doubt in reply["doubts"]:
                key, why = str(doubt["key"]), " ".join(str(doubt["why"]).split())
                if key in keys and why:
                    found[key] = why
            self._found = found
        except Exception as exc:  # noqa: BLE001 - the offer stands without the model
            logger.warning("the offer's doubts couldn't be read: %s", exc)
            self._failed = type(exc).__name__
        finally:
            self._done.set()

    def added_to(self, offer: Offering) -> Offering:
        """The offer with what's come back by now. The model's doubts are only ever added."""
        if not self._done.is_set():
            return replace(offer, model="the model is still reading what differs: the offer "
                                        "doesn't wait for it")
        if self._found is None:
            return replace(offer, model="the model couldn't read what differs "
                                        f"({self._failed}): the offer stands without it")
        job = self._past.job_id
        options = []
        for past in offer.options:
            if past.job_id == job:
                items = []
                for item in past.items:
                    why = self._found.get(item.key)
                    said = f"the model doubts it: {why}"
                    if why and said not in item.doubts:
                        item = replace(item, doubts=(*item.doubts, said))
                    items.append(item)
                past = replace(past, items=tuple(items))
            options.append(past)
        return replace(offer, options=tuple(options), model="")
