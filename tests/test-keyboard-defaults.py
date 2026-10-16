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

# The October 8 decision names Alt+Tab explicitly; on October 15 it became the window
# switcher with pictures (a quick tap still goes to the previous window). Retain existing
# combinations for screenshots and the searchable sheet. Unspecified combinations need a decision.
REQUIRED = {
    "Alt+Tab": ["spawn", "emaki-shell", "call", "switcher", "next"],
    "Alt+Shift+Tab": ["spawn", "emaki-shell", "call", "switcher", "previous"],
    "Alt+grave": ["spawn", "emaki-shell", "call", "switcher", "app"],
    "Alt+Shift+grave": ["spawn", "emaki-shell", "call", "switcher", "appPrevious"],
    "Mod+Shift+S": ["screenshot"],
    "Print": ["screenshot"],
    "Mod+Slash": ["spawn", "emaki-shell", "call", "keyboard", "open", "shortcuts"],
    "Mod+Period": ["spawn", "emaki-shell", "call", "keyboard", "open", "shortcuts"],
    # The shortcuts of 2026-10-15 (the approved table, DECISIONS).
    "Mod+T": ["spawn", "emaki-terminal"],
    "Mod+Return": ["spawn", "emaki-terminal"],
    "Mod+C": ["close-window"],
    "Mod+Q": ["close-window"],
    "Mod+E": ["spawn", "dolphin"],
    "Mod+Minus": ["set-column-width", "-10%"],
    "Mod+Equal": ["set-column-width", "+10%"],
    "Mod+Shift+F": ["toggle-window-floating"],
    "Mod+BracketLeft": ["consume-or-expel-window-left"],
    "Mod+BracketRight": ["consume-or-expel-window-right"],
    "Mod+Ctrl+Shift+Left": ["move-column-to-monitor-left"],
    "Mod+Ctrl+Shift+Right": ["move-column-to-monitor-right"],
    "Mod+Ctrl+Shift+Up": ["move-column-to-monitor-up"],
    "Mod+Ctrl+Shift+Down": ["move-column-to-monitor-down"],
    "Mod+Alt+R": ["spawn", "emaki-shell", "call", "capture", "record"],
}

# Every one of them has a title for niri's hotkey overlay and the shortcut search.
TITLED = {
    "Mod+T": "Open terminal",
    "Mod+Return": "Open terminal",
    "Mod+Q": "Close window",
    "Mod+C": "Close window",
    "Mod+E": "Open files",
    "Mod+Minus": "Make column narrower",
    "Mod+Equal": "Make column wider",
    "Mod+Shift+F": "Float or tile window",
    "Mod+BracketLeft": "Move window into or out of left column",
    "Mod+BracketRight": "Move window into or out of right column",
    "Mod+Ctrl+Shift+Left": "Move column to left monitor",
    "Mod+Ctrl+Shift+Right": "Move column to right monitor",
    "Mod+Ctrl+Shift+Up": "Move column to monitor above",
    "Mod+Ctrl+Shift+Down": "Move column to monitor below",
    "Mod+Alt+R": "Record screen",
}


def chord(key, mod_key):
    """A key combination as niri compares it: modifiers in any order and case."""
    *modifiers, name = key.split("+")
    aliases = {"mod": "mod", "super": "super", "win": "super", "ctrl": "ctrl", "control": "ctrl",
               "shift": "shift", "alt": "alt", "mod5": "iso_level3_shift", "iso_level3_shift": "iso_level3_shift"}
    aliases["mod"] = aliases.get(mod_key.lower(), mod_key.lower())
    return frozenset(aliases.get(m.lower(), m.lower()) for m in modifiers), name.lower()


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


def validate_claims(texts):
    parsed = {name: list(SHORTCUTS.nodes(text)) for name, text in texts.items()}
    mod_keys = [node[1] for nodes in parsed.values() for head, children in nodes
                if head[0] == "input" for node, _ in children if node[0] == "mod-key"]
    if len(mod_keys) != 1:
        raise ValueError("Expected one explicit mod-key in the shipped configuration")
    mod_key = mod_keys[0]
    claims = {}
    for name, nodes in parsed.items():
        for head, children in nodes:
            if head[0] != "binds":
                continue
            for binding, _actions in children:
                combination = chord(binding[0], mod_key)
                if combination in claims:
                    raise ValueError(f"Duplicate shortcut: {binding[0]} in {name} and {claims[combination]}")
                claims[combination] = name
    return claims, mod_key


class KeyboardDefaults(unittest.TestCase):
    def setUp(self):
        self.config = (ROOT / "niri/default.kdl").read_text()

    def test_shipped_defaults(self):
        validate_defaults(self.config)
        self.assertIn(
            {"keys": "Alt+Tab", "description": "Switch windows"},
            SHORTCUTS.read_shortcuts(ROOT / "niri/default.kdl"),
        )

    def test_new_shortcuts_are_titled(self):
        # The shortcut search (Super+/) lists the title; niri's overlay shows it too.
        rows = SHORTCUTS.read_shortcuts(ROOT / "niri/default.kdl")
        for key, title in TITLED.items():
            with self.subTest(key=key):
                self.assertIn({"keys": key.replace("Mod+", "Super+"), "description": title}, rows)

    def test_native_switcher_owns_only_super_tab(self):
        texts = {path.name: path.read_text() for path in sorted((ROOT / "niri").glob("*.kdl"))}
        claims, mod_key = validate_claims(texts)
        self.assertEqual(mod_key, "Super")
        recent = [children for text in texts.values() for head, children in SHORTCUTS.nodes(text)
                  if head[0] == "recent-windows"]
        self.assertEqual(len(recent), 1)
        # On by default; no `on` here, so a personal `off` placed anywhere still wins.
        self.assertNotIn((["on"], []), recent[0])
        self.assertNotIn((["off"], []), recent[0])
        blocks = [children for head, children in recent[0] if head[0] == "binds"]
        # niri 26.04 clears all eight implicit Alt/Mod + Tab/grave bindings at the
        # first explicit recent-windows binds block. Keep its four Super chords, as
        # before 0.9: no native Alt chords, even while the native switcher is open.
        self.assertEqual(blocks, [[
            (["Mod+Tab"], [(["next-window"], [])]),
            (["Mod+Shift+Tab"], [(["previous-window"], [])]),
            (["Mod+grave"], [(["next-window", "filter", "=", "app-id"], [])]),
            (["Mod+Shift+grave"], [(["previous-window", "filter", "=", "app-id"], [])]),
        ]])
        for key in ("Mod+Tab", "Mod+Shift+Tab", "Mod+grave", "Mod+Shift+grave"):
            self.assertNotIn(chord(key, mod_key), claims, "A global binding shadows the native switcher")
        validate_defaults(self.config)
        for row in SHORTCUTS.read_shortcuts(ROOT / "niri/default.kdl"):
            if row["keys"] == "Super+Tab":
                self.assertEqual(row["description"], "Switch windows (niri)")

    def test_no_combination_is_claimed_twice(self):
        # Modifier order and case must not hide a second claim on the same combination,
        # in any shipped kdl file (the defaults, the fork's rules, the shell's include).
        texts = {path.name: path.read_text() for path in sorted((ROOT / "niri").glob("*.kdl"))}
        claims, mod_key = validate_claims(texts)
        for key in TITLED:
            self.assertIn(chord(key, mod_key), claims, key)

    def test_modifier_alias_collisions_are_rejected(self):
        for alias in ("Super", "Win", "sUpEr"):
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(ValueError, "Duplicate shortcut"):
                    validate_claims({"default.kdl": self.config,
                                     "extra.kdl": f"binds {{ {alias}+T {{ close-window; }} }}"})
        with self.assertRaisesRegex(ValueError, "Duplicate shortcut"):
            validate_claims({"test.kdl": 'input { mod-key "Alt"; } binds { Mod+T { close-window; } Alt+T { close-window; } }'})
        self.assertNotEqual(chord("Mod+T", "Alt"), chord("Super+T", "Alt"))

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
        changed = self.config.replace('"switcher" "next";', '"launcher" "toggle";')
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
