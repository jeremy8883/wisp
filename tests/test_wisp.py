"""Tests for terminal detection in wisp.py (no GNOME / dbus required)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wisp import is_terminal  # noqa: E402


class IsTerminalTests(unittest.TestCase):
    def test_known_terminals(self):
        for cls in ["ghostty", "Alacritty", "org.kde.konsole", "kitty",
                    "gnome-terminal-server", "foot", "WezTerm"]:
            self.assertTrue(is_terminal(cls), cls)

    def test_gui_apps_are_not_terminals(self):
        for cls in ["firefox", "code", "org.gnome.TextEditor", "Slack", ""]:
            self.assertFalse(is_terminal(cls), cls)

    def test_none_is_safe(self):
        self.assertFalse(is_terminal(None))


if __name__ == "__main__":
    unittest.main()
