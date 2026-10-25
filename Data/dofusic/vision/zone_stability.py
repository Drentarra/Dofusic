"""Temporal rejection of partial OCR without freezing actual zone changes."""
from __future__ import annotations

from dofusic.text import clean_text, normalize_text


class ZoneTextContinuity:
    """Hold only suspicious readings that shorten an already confirmed label.

    This is independent of the zone catalogue and music names. A genuine
    shorter location is accepted after two matching observations, while one
    incomplete frame cannot replace the full confirmed text.
    """

    def __init__(self, confirmations: int = 2) -> None:
        self.confirmations = max(2, int(confirmations))
        self.reset()

    def reset(self) -> None:
        self.confirmed = ''
        self.pending = ''
        self.count = 0

    def hold(self, observed: str) -> bool:
        new = normalize_text(observed)
        old = normalize_text(self.confirmed)
        if not old or not new or new == old:
            self.pending, self.count = '', 0
            return False
        # A partial token/parenthesis can be confused for a new exact area.
        # A clipped HUD line can lose its *leading* characters too. Its OCR
        # result is then an interior/suffix fragment of the confirmed label.
        # Treat all contiguous shorter fragments alike, independent of map.
        shorter_fragment = len(new) < len(old) and new in old
        open_parenthesis = new.count('(') > new.count(')')
        if not shorter_fragment and not open_parenthesis:
            self.pending, self.count = '', 0
            return False
        if self.pending != new:
            self.pending, self.count = new, 1
        else:
            self.count += 1
        return self.count < self.confirmations

    def confirm(self, observed: str, *, same_place: bool = False) -> None:
        text = clean_text(observed)
        if text and not (same_place and normalize_text(self.confirmed).startswith(normalize_text(text))
                         and len(normalize_text(self.confirmed)) > len(normalize_text(text))):
            self.confirmed = text
        self.pending, self.count = '', 0
