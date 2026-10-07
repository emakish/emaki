#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression checks for laptop input and shortcut help, without changing devices."""
import ctypes
import ctypes.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = (ROOT / "niri/default.kdl").read_text()


class DesktopBindings(unittest.TestCase):
    def binding(self, key):
        match = re.search(r"^\s*" + re.escape(key) + r"\s+[^\n{]*\{([^}]+)\}", CONFIG, re.M)
        self.assertIsNotNone(match, key)
        return match.group(1)

    def test_keyboard_light_does_not_target_other_leds(self):
        for key, delta in (("XF86KbdBrightnessUp", "10%+"), ("XF86KbdBrightnessDown", "10%-")):
            action = self.binding(key)
            self.assertIn('"--class=leds"', action)
            self.assertIn('"--device=*::kbd_backlight"', action)
            self.assertIn('"set" "' + delta + '"', action)

    def test_mac_media_keys_open_overview_and_launcher(self):
        # Linux hid-apple emits KEY_SCALE=120 and KEY_DASHBOARD=204; evdev adds 8.
        lib = ctypes.CDLL(ctypes.util.find_library("xkbcommon"))
        for name, restype, argtypes in (
            ("xkb_context_new", ctypes.c_void_p, [ctypes.c_int]),
            ("xkb_keymap_new_from_names", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]),
            ("xkb_state_new", ctypes.c_void_p, [ctypes.c_void_p]),
            ("xkb_state_key_get_one_sym", ctypes.c_uint32, [ctypes.c_void_p, ctypes.c_uint32]),
            ("xkb_keysym_get_name", ctypes.c_int, [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t]),
            ("xkb_state_unref", None, [ctypes.c_void_p]),
            ("xkb_keymap_unref", None, [ctypes.c_void_p]),
            ("xkb_context_unref", None, [ctypes.c_void_p]),
        ):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = restype, argtypes
        ctx = lib.xkb_context_new(0)
        keymap = lib.xkb_keymap_new_from_names(ctx, None, 0)
        state = lib.xkb_state_new(keymap)
        try:
            for code, expected in ((128, "XF86LaunchA"), (212, "XF86LaunchB")):
                buf = ctypes.create_string_buffer(128)
                lib.xkb_keysym_get_name(lib.xkb_state_key_get_one_sym(state, code), buf, len(buf))
                self.assertEqual(buf.value.decode(), expected)
            self.assertIn("toggle-overview;", self.binding("XF86LaunchA"))
            self.assertIn('"emaki-shell" "call" "launcher" "toggle"', self.binding("XF86LaunchB"))
        finally:
            lib.xkb_state_unref(state)
            lib.xkb_keymap_unref(keymap)
            lib.xkb_context_unref(ctx)

    def test_touchpad_ignores_typing_and_trackpoint(self):
        touchpad = re.search(r"touchpad\s*\{([^}]+)\}", CONFIG).group(1)
        for option in ("dwt", "dwtp"):
            self.assertRegex(touchpad, r"(?m)^\s*" + option + r"\s*$")

    def test_overview_help_keeps_the_standard_shortcut(self):
        # niri prefers a custom title over the first binding for the same action.
        # Without a title override, Mod+O keeps its built-in "Open the Overview" title.
        overview = re.findall(r"^\s*(\S+)\s+([^\n{]*)\{\s*toggle-overview;\s*\}", CONFIG, re.M)
        self.assertTrue(overview)
        self.assertEqual(overview[0][0], "Mod+O")
        for key, options in overview:
            self.assertNotIn("hotkey-overlay-title", options, key)
        for key in ("XF86LaunchA", "XF86LaunchB"):
            options = re.search(r"^\s*" + key + r"\s+([^\n{]*)\{", CONFIG, re.M).group(1)
            self.assertNotIn("hotkey-overlay-title", options)

    def test_default_shortcut_help_explains_overrides(self):
        welcome = (ROOT / "shell/Welcome.qml").read_text()
        self.assertIn("Your own shortcuts override Emaki defaults, including new ones (niri bindings).", welcome)


if __name__ == "__main__":
    unittest.main()
