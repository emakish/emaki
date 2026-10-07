#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Require the packaged fork versions before running CI checks."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORKS = {"niri-emaki": "/usr/bin/niri-emaki", "quickshell-emaki": "/usr/bin/qs"}


def check(root=ROOT, run=subprocess.check_output):
    for package, binary in FORKS.items():
        recipe = (root / "packaging" / package / "PKGBUILD").read_text()
        values = dict(re.findall(r"^(pkgver|pkgrel)=([\w.]+)$", recipe, re.M))
        expected = f"{package} {values['pkgver']}-{values['pkgrel']}"
        installed = run(["pacman", "-Q", package], text=True).strip()
        if installed != expected:
            raise RuntimeError(f"Fork package version differs: expected {expected}; found {installed}")
        owner = run(["pacman", "-Qqo", binary], text=True).strip()
        if owner != package:
            raise RuntimeError(f"Fork binary has the wrong owner: {binary}: {owner}")
        print(f"Fork package verified: {installed} ({binary})")


if __name__ == "__main__":
    check()
