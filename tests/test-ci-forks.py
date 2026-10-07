#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject stock binaries, stale fork packages and skipped fork validation in CI."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("forks", ROOT / "tests/ci-check-forks.py")
forks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forks)


class ForkChecks(unittest.TestCase):
    def query(self, argv, text):
        if argv[1] == "-Qqo":
            return {v: k for k, v in forks.FORKS.items()}[argv[2]]
        recipe = (ROOT / "packaging" / argv[2] / "PKGBUILD").read_text()
        values = dict(forks.re.findall(r"^(pkgver|pkgrel)=([\w.]+)$", recipe, forks.re.M))
        return f"{argv[2]} {values['pkgver']}-{values['pkgrel']}"

    def test_expected_packages(self):
        forks.check(run=self.query)

    def test_stock_stale_or_missing_package_fails(self):
        for package, binary in forks.FORKS.items():
            for defect in ("stock", "stale", "missing"):
                with self.subTest(package=package, defect=defect):
                    def query(argv, text):
                        if argv == ["pacman", "-Qqo", binary] and defect == "stock":
                            return "stock"
                        if argv == ["pacman", "-Q", package]:
                            if defect == "stale":
                                return package + " 0.0-1"
                            if defect == "missing":
                                raise subprocess.CalledProcessError(1, argv)
                        return self.query(argv, text)
                    with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
                        forks.check(run=query)

class WorkflowChecks(unittest.TestCase):
    def test_ci_requires_forks_before_checks(self):
        workflow = (ROOT / ".github/workflows/check.yml").read_text()
        self.assertIn("pacman-key --populate emaki", workflow)
        self.assertIn("SigLevel = Required TrustedOnly", workflow)
        self.assertIn("niri-emaki quickshell-emaki", workflow)
        self.assertLess(workflow.index("python3 tests/ci-check-forks.py"), workflow.index("- name: make check"))
        self.assertNotIn('grep -v -F "Skipping fork-rules.kdl', workflow)
        self.assertIn("skip fork configs:", workflow)
        self.assertIn("python3 tests/test-ci-forks.py", workflow)


if __name__ == "__main__":
    unittest.main()
