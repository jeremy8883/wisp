"""Tests for the ydotool keystroke translation (no ydotool required)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from correction import CharDelete, TypeText, WordDelete  # noqa: E402
from keyboard import ALT_BACKSPACE, CTRL_BACKSPACE, Keyboard  # noqa: E402


class KeyboardTests(unittest.TestCase):
    def _capture(self, ops, **kw):
        calls = []
        Keyboard(runner=calls.append, **kw).apply(ops)
        return calls

    def test_word_delete_defaults_to_ctrl_backspace_chord(self):
        calls = self._capture([WordDelete(4)])
        self.assertEqual(calls, [["ydotool", "key", "29:1", "14:1", "14:0", "29:0"]])

    def test_word_delete_chord_is_configurable(self):
        calls = self._capture([WordDelete(4)], word_delete_chord=ALT_BACKSPACE)
        self.assertEqual(calls, [["ydotool", "key", "56:1", "14:1", "14:0", "56:0"]])

    def test_char_delete_batches_backspaces(self):
        calls = self._capture([CharDelete(3)])
        self.assertEqual(
            calls,
            [["ydotool", "key", "14:1", "14:0", "14:1", "14:0", "14:1", "14:0"]],
        )

    def test_char_delete_zero_is_noop(self):
        self.assertEqual(self._capture([CharDelete(0)]), [])

    def test_type_uses_double_dash_guard(self):
        calls = self._capture([TypeText("-rf hi")])
        self.assertEqual(calls, [["ydotool", "type", "--", "-rf hi"]])

    def test_empty_type_is_noop(self):
        self.assertEqual(self._capture([TypeText("")]), [])

    def test_safe_mode_expands_word_delete_to_backspaces(self):
        calls = self._capture([WordDelete(2)], safe=True)
        self.assertEqual(
            calls, [["ydotool", "key", "14:1", "14:0", "14:1", "14:0"]]
        )

    def test_sequence_order_preserved(self):
        calls = self._capture([CharDelete(1), WordDelete(2), TypeText("ok")])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][:2], ["ydotool", "key"])
        self.assertEqual(calls[2], ["ydotool", "type", "--", "ok"])


if __name__ == "__main__":
    unittest.main()
