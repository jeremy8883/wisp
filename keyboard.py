"""Execute abstract correction ops as real keystrokes via ydotool.

`correction.py` decides *what* to press; this module presses it.  ydotool speaks
Linux input event codes (`code:1` = press, `code:0` = release), the same scheme
the existing paste logic in wisp.py uses.

The actual subprocess call is injected (`runner`) so the translation can be unit
tested without ydotool installed.
"""

from __future__ import annotations

import subprocess

from correction import CharDelete, TypeText, WordDelete

# Linux input-event key codes (see /usr/include/linux/input-event-codes.h).
KEY_BACKSPACE = 14
KEY_LEFTCTRL = 29
KEY_LEFTALT = 56

# Word-delete chord presets.  Alt+Backspace is the default the user asked for;
# Ctrl+Backspace is the common GUI variant if a given app prefers it.
ALT_BACKSPACE = (KEY_LEFTALT, KEY_BACKSPACE)
CTRL_BACKSPACE = (KEY_LEFTCTRL, KEY_BACKSPACE)


def _default_runner(argv):
    subprocess.run(argv, check=False)


class Keyboard:
    def __init__(self, runner=_default_runner, word_delete_chord=ALT_BACKSPACE,
                 safe=False):
        self._run = runner
        self._mod, self._del = word_delete_chord
        # safe mode: never trust word-delete; expand every WordDelete into the
        # exact number of single Backspaces it represents (fully predictable).
        self._safe = safe

    def _chord(self):
        # mod down, backspace down, backspace up, mod up
        return [
            "ydotool", "key",
            f"{self._mod}:1", f"{self._del}:1",
            f"{self._del}:0", f"{self._mod}:0",
        ]

    def _backspaces(self, n):
        seq = []
        for _ in range(n):
            seq += [f"{KEY_BACKSPACE}:1", f"{KEY_BACKSPACE}:0"]
        return ["ydotool", "key", *seq]

    def apply(self, ops):
        """Press the keys for a list of correction ops, in order."""
        for op in ops:
            if isinstance(op, WordDelete):
                if self._safe:
                    self._run(self._backspaces(op.chars))
                else:
                    self._run(self._chord())
            elif isinstance(op, CharDelete):
                if op.count > 0:
                    self._run(self._backspaces(op.count))
            elif isinstance(op, TypeText):
                if op.text:
                    # `--` so transcripts beginning with '-' aren't read as flags.
                    self._run(["ydotool", "type", "--", op.text])
            else:  # pragma: no cover
                raise TypeError(f"unknown op {op!r}")
