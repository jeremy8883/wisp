"""Pure text-correction engine for live streaming transcription.

The streaming transcription API emits text incrementally and then *revises* it
as more of the sentence is heard.  Whatever we have already typed into the
focused window therefore has to be partially rewritten: backspace the part that
changed, then type the corrected tail.

This module contains ZERO I/O.  It turns a sequence of transcript revisions into
a list of abstract keystroke ops.  `keyboard.py` is responsible for actually
pressing keys.  Keeping it pure is what makes the "corrections never go too far"
guarantee testable.

Safety model
------------
We split everything we have produced into two regions:

    committed  -- text from segments the API has marked final.  Immutable.  We
                  NEVER emit a keystroke that could delete into this region.
    pending    -- the in-progress segment.  Only this region is ever rewritten.

Because a revision can only rewrite `pending`, and a deletion is always a strict
suffix of `pending`, the number of characters we delete is bounded by
len(pending) by construction.  `commit()` resets pending to "", so once a
segment is final it can never be reached by a later correction.

Deletion strategy (word vs char)
--------------------------------
Deleting one character per Backspace is perfectly predictable but slow.  A
word-delete (Alt+Backspace) is fast but its exact behaviour varies between
applications, *especially* around punctuation.  So we use word-delete only for
"clean" runs -- maximal runs of ASCII letters/digits -- and fall back to single
Backspaces the moment an ambiguous character (punctuation, symbol, accent, CJK,
tab, double space, ...) is involved.  As soon as the ambiguous run is cleared we
switch straight back to word-delete.

See `WORD_DELETE_CONSUMES_LEADING_SPACE` for the one app-dependent assumption we
make, and how to turn it off.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

# Characters we consider unambiguous for word-deletion.  Deliberately ASCII-only:
# accented letters, CJK, emoji, etc. all delete char-by-char.
WORD_CHARS = frozenset(string.ascii_letters + string.digits)

# Most GTK/Qt apps and shells extend a word-delete leftwards over the single
# space that precedes the word ("foo bar|" -> "foo|" in one stroke).  We model
# that, which biases rounding errors towards leaving a stray space rather than
# eating an extra character.  If your environment leaves the space behind, flip
# this to False (or just run with --safe to use char-deletes only).
WORD_DELETE_CONSUMES_LEADING_SPACE = True


# --- keystroke ops -----------------------------------------------------------

@dataclass(frozen=True)
class WordDelete:
    """One Alt+Backspace press.  Removes exactly `chars` characters (per model)."""

    chars: int


@dataclass(frozen=True)
class CharDelete:
    """`count` single Backspace presses.  Removes exactly `count` characters."""

    count: int


@dataclass(frozen=True)
class TypeText:
    """Type a literal string."""

    text: str


Op = "WordDelete | CharDelete | TypeText"


def _is_word_char(c: str) -> bool:
    return c in WORD_CHARS


def common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def plan_deletion(to_delete: str, left_neighbor: str) -> list:
    """Plan keystrokes that delete the suffix `to_delete`, cursor at its end.

    `left_neighbor` is the single character immediately to the left of
    `to_delete` in the surrounding text that we are *keeping* (the last char of
    committed+retained-prefix), or "" if `to_delete` sits at the very start of
    everything we have produced.  It lets us refuse a word-delete that would
    otherwise run past the start of `to_delete` into text we must not touch.

    Guarantees (enforced by tests):
      * the ops delete exactly len(to_delete) characters, no more, no less;
      * a WordDelete is only used for a clean run, and never crosses the left
        boundary of `to_delete` into retained text.

    Ops are returned in the order they should be pressed (right-to-left
    deletion), and consecutive char-deletes are coalesced.
    """
    ops: list = []
    i = len(to_delete)  # chars [0, i) of to_delete still need deleting

    def push_char():
        if ops and isinstance(ops[-1], CharDelete):
            ops[-1] = CharDelete(ops[-1].count + 1)
        else:
            ops.append(CharDelete(1))

    while i > 0:
        c = to_delete[i - 1]

        if not _is_word_char(c):
            # Ambiguous character (space, punctuation, symbol, non-ASCII...).
            # Delete it with a single, fully-predictable Backspace.
            push_char()
            i -= 1
            continue

        # Rightmost char is a clean word char: find the start of the run.
        j = i
        while j > 0 and _is_word_char(to_delete[j - 1]):
            j -= 1
        run_len = i - j

        # Character to the left of this run, within the text we are keeping.
        left = to_delete[j - 1] if j > 0 else left_neighbor

        if (
            WORD_DELETE_CONSUMES_LEADING_SPACE
            and j > 0
            and to_delete[j - 1] == " "
            and (j - 1 == 0 or to_delete[j - 2] != " ")
        ):
            # A single space precedes the run *inside* to_delete: one stroke
            # eats the run and that space together.
            ops.append(WordDelete(run_len + 1))
            i = j - 1
            continue

        if j == 0 and left_neighbor not in ("", " "):
            # The run abuts retained text that does not end in a space (e.g. the
            # committed segment ends mid-word, or with punctuation).  A
            # word-delete here could cross the boundary and eat retained text,
            # so fall back to char-deletes for this run.
            push_char()
            i -= 1
            continue

        # Safe to word-delete the clean run on its own.  The cursor stops at the
        # space / boundary to its left, which we then handle on the next pass.
        ops.append(WordDelete(run_len))
        i = j

    return ops


class Corrector:
    """Tracks on-screen text and turns transcript revisions into keystroke ops."""

    def __init__(self) -> None:
        self.committed = ""  # finalized text; never modified
        self.pending = ""    # current segment text on screen; correctable

    @property
    def text(self) -> str:
        """Everything we believe is currently on screen."""
        return self.committed + self.pending

    def set_pending(self, new_pending: str) -> list:
        """Revise the in-progress segment to `new_pending`; return keystroke ops.

        Only the `pending` region is ever rewritten.  Deletions are a strict
        suffix of the old pending text, so we can never delete more than
        len(pending) characters and can never reach `committed`.
        """
        old = self.pending
        prefix = common_prefix_len(old, new_pending)
        to_delete = old[prefix:]
        to_add = new_pending[prefix:]

        ops: list = []
        if to_delete:
            if prefix > 0:
                left_neighbor = old[prefix - 1]
            elif self.committed:
                left_neighbor = self.committed[-1]
            else:
                left_neighbor = ""
            ops.extend(plan_deletion(to_delete, left_neighbor))
        if to_add:
            ops.append(TypeText(to_add))

        self.pending = new_pending
        return ops

    def commit(self) -> list:
        """Mark the current segment final.  No keystrokes; just makes the
        pending text immutable so future corrections can't reach it."""
        self.committed += self.pending
        self.pending = ""
        return []

    def append(self, text: str) -> list:
        """Append literal text directly to the committed (immutable) region.

        Used for inter-segment separators.  Returns the keystrokes to type it.
        """
        self.committed += text
        return [TypeText(text)] if text else []


def deleted_char_count(ops) -> int:
    """Total characters removed by the deletion ops in `ops` (per our model)."""
    total = 0
    for op in ops:
        if isinstance(op, WordDelete):
            total += op.chars
        elif isinstance(op, CharDelete):
            total += op.count
    return total
