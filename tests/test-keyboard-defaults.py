#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep approved keyboard defaults and their existing discovery routes intact."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "shortcut_defaults", ROOT / "shell/helpers/shortcuts.py"
)
SHORTCUTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHORTCUTS)

# The October 8 decision names Alt+Tab explicitly; retain existing combinations
# for screenshots and the searchable sheet. Unspecified combinations need a decision.
REQUIRED = {
    "Alt+Tab": ["focus-window-previous"],
    "Mod+Shift+S": ["screenshot"],
    "Print": ["screenshot"],
    "Mod+Slash": ["spawn", "emaki-shell", "call", "keyboard", "open", "shortcuts"],
    "Mod+Period": ["spawn", "emaki-shell", "call", "keyboard", "open", "shortcuts"],
}


def validate_defaults(text):
    bindings = {}
    for head, children in SHORTCUTS.nodes(text):
        if head[0] != "binds":
            continue
        for binding, actions in children:
            key = binding[0]
            if key in bindings:
                raise ValueError(f"Duplicate shortcut: {key}")
            bindings[key] = actions
    for key, action in REQUIRED.items():
        if bindings.get(key) != [(action, [])]:
            raise ValueError(f"Default shortcut changed: {key}")


class KeyboardDefaults(unittest.TestCase):
    def setUp(self):
        self.config = (ROOT / "niri/default.kdl").read_text()

    def test_shipped_defaults(self):
        validate_defaults(self.config)
        self.assertIn(
            {"keys": "Alt+Tab", "description": "Focus previous window"},
            SHORTCUTS.read_shortcuts(ROOT / "niri/default.kdl"),
        )

    def test_each_removed_default_is_rejected(self):
        for key in REQUIRED:
            with self.subTest(key=key):
                changed = "\n".join(
                    line for line in self.config.splitlines()
                    if not line.strip().startswith(key + " ")
                )
                with self.assertRaisesRegex(ValueError, "Default shortcut changed"):
                    validate_defaults(changed)

    def test_changed_action_is_rejected(self):
        changed = self.config.replace("focus-window-previous;", "focus-workspace-previous;")
        with self.assertRaisesRegex(ValueError, "Alt\\+Tab"):
            validate_defaults(changed)

    def test_disabled_binding_is_rejected(self):
        changed = self.config.replace("    Alt+Tab ", "    /- Alt+Tab ")
        with self.assertRaisesRegex(ValueError, "Alt\\+Tab"):
            validate_defaults(changed)

    def test_later_replacement_is_rejected(self):
        changed = self.config + "\nbinds { Alt+Tab { close-window; } }\n"
        with self.assertRaisesRegex(ValueError, "Duplicate shortcut"):
            validate_defaults(changed)


if __name__ == "__main__":
    unittest.main()
