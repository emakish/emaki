#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The fallback uses an unchanged centre crop small enough for a 4096 texture."""

from pathlib import Path
import os
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
            self.assertEqual(fallback.size, (3840, 2160))
            self.assertEqual(fallback.mode, "RGB")
            self.assertLessEqual(max(fallback.size), 4096)

    @unittest.skipUnless(os.environ.get("EMAKI_CUBE_STRIP"), "external cube source not supplied")
    def test_shipped_pixels_are_the_exact_cube_centre(self):
        with Image.open(os.environ["EMAKI_CUBE_STRIP"]) as strip, Image.open(
            ROOT / "art/wallpaper/fallback.png"
        ) as fallback:
            self.assertEqual(strip.height, 2160)
            left = (strip.width - 3840) // 2
            expected = strip.crop((left, 0, left + 3840, 2160))
            self.assertEqual(fallback.mode, strip.mode)
            self.assertEqual(fallback.tobytes(), expected.tobytes())

    def test_rebuild_is_deterministic_and_preserves_centre_pixels(self):
        with tempfile.TemporaryDirectory(prefix="wallpaper-") as work:
            work = Path(work)
            first, second, source = work / "first.png", work / "second.png", work / "strip.png"
            # Distinct columns and rows catch an off-centre crop, resize or colour loss.
            row = Image.new("RGB", (4096, 1))
            row.putdata([(x % 256, x // 256, (x * 7) % 256) for x in range(4096)])
            strip = row.resize((4096, 2160), resample=Image.Resampling.NEAREST)
            strip.paste((29, 101, 233), (0, 999, 4096, 1001))
            strip.save(source)
            BUILD(source, first)
            BUILD(source, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with Image.open(first) as rebuilt:
                self.assertEqual(rebuilt.mode, "RGB")
                self.assertEqual(rebuilt.size, (3840, 2160))
                self.assertEqual(rebuilt.tobytes(), strip.crop((128, 0, 3968, 2160)).tobytes())

    def test_rejects_wrong_height_or_narrow_strip(self):
        with tempfile.TemporaryDirectory(prefix="wallpaper-") as work:
            source = Path(work) / "strip.png"
            for size in ((3840, 1200), (3839, 2160)):
                Image.new("RGB", size).save(source)
                with self.assertRaises(ValueError):
                    BUILD(source, Path(work) / "fallback.png")


if __name__ == "__main__":
    unittest.main()
