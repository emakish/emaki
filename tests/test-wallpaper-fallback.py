#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The fallback uses an unchanged centre crop small enough for a 4096 texture."""

from pathlib import Path
import runpy
import tempfile
import tomllib
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BUILD = runpy.run_path(str(ROOT / "scripts/build-wallpaper-fallback"))["build"]


class WallpaperFallback(unittest.TestCase):
    def test_default_uses_bounded_fallback(self):
        for name, section in (("etc-skel/.config/wpaperd/config.toml", "any"),
                              ("iso/profile/airootfs/home/live/.config/wpaperd/config.toml", "default")):
            with self.subTest(config=name):
                config = tomllib.loads((ROOT / name).read_text())[section]
                self.assertEqual(config["path"], "/usr/share/emaki/wallpaper/fallback.png")
                self.assertEqual(config["mode"], "center")
        with Image.open(ROOT / "art/wallpaper/fallback.png") as fallback:
            self.assertEqual(fallback.size, (3840, 1200))
            self.assertLessEqual(max(fallback.size), 4096)

    def test_shipped_pixels_are_the_exact_ring_centre(self):
        with Image.open(ROOT / "art/wallpaper/ring.png") as ring, Image.open(
            ROOT / "art/wallpaper/fallback.png"
        ) as fallback:
            left = (ring.width - 3840) // 2
            expected = ring.crop((left, 0, left + 3840, 1200))
            self.assertEqual(fallback.mode, ring.mode)
            self.assertEqual(fallback.tobytes(), expected.tobytes())

    def test_rebuild_is_deterministic_and_matches_shipped_pixels(self):
        evidence = ROOT / ".cache/evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wallpaper-", dir=evidence) as work:
            first, second = Path(work) / "first.png", Path(work) / "second.png"
            source = ROOT / "art/wallpaper/ring.png"
            BUILD(source, first)
            BUILD(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with Image.open(first) as rebuilt, Image.open(ROOT / "art/wallpaper/fallback.png") as shipped:
                self.assertEqual(rebuilt.mode, shipped.mode)
                self.assertEqual(rebuilt.size, shipped.size)
                self.assertEqual(rebuilt.tobytes(), shipped.tobytes())


if __name__ == "__main__":
    unittest.main()
