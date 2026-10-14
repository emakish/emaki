#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Realistic synthetic journal lines preserve diagnostics while removing secrets."""

import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from emaki_report_redact import CATEGORIES, Redactor


class RedactionCorpus(unittest.TestCase):
    def test_pem_begin_lost_to_journal_line_limit(self):
        payload = "bW9ja3ByaXZhdGVrZXlib2R5"
        lines = ["-----BEGIN PRIVATE KEY-----", payload, payload,
                 "-----END PRIVATE KEY-----"]
        lines.extend(f"service: harmless progress {index}" for index in range(198))
        window = "\n".join(lines[-200:]) + "\n"
        self.assertNotIn("BEGIN", window)
        redactor = Redactor(user="testperson", host="test-machine.example")
        result = redactor.redact(window)
        self.assertNotIn(payload, result)
        for line in lines[4:]:
            self.assertIn(line + "\n", result)
        self.assertEqual(redactor.removed["secret"], 1)

    def test_journal_corpus(self):
        sys.path.insert(0, str(ROOT / "update-manager"))
        import programs_engine as engine
        corpus = json.loads((ROOT / "tests/fixtures/report-redaction.json").read_text())
        for case in corpus["journal_cases"]:
            with self.subTest(case=case["name"]):
                instance = engine.Engine(SimpleNamespace(), "/unused-cache")
                forwarded = Mock()
                def execute(action, key, token, emit):
                    for index, message in enumerate(case["messages"]):
                        fields = {"message": message}
                        if str(index) in case.get("stages", {}):
                            fields["stage"] = case["stages"][str(index)]
                        emit("progress", **fields)
                    return case["result"]
                with patch.object(instance, "_execute", side_effect=execute), patch.object(engine, "syslog") as log:
                    self.assertEqual(instance.execute("update", "", "", forwarded), case["result"])
                records = [call.args[1] for call in log.syslog.call_args_list]
                events = [record.splitlines()[0].split("event=", 1)[1] for record in records]
                self.assertEqual(events, case["expected_events"])
                self.assertEqual([call.kwargs["message"] for call in forwarded.call_args_list], case["messages"])
                if case["expected_cause"] is not None:
                    self.assertTrue(case["expected_cause"] in "\n".join(records), "wrong failure cause")
                for forbidden in case.get("forbidden_causes", []):
                    self.assertTrue(forbidden not in "\n".join(records), "progress used as failure cause")

    def check_corpus(self, section):
        corpus = json.loads((ROOT / "tests/fixtures/report-redaction.json").read_text())
        for case in corpus[section]:
            with self.subTest(case=case["name"]):
                redactor = Redactor(user=case.get("user", "testperson"),
                                    host=case.get("host", "test-machine.example"))
                for secret in case["secrets"]:
                    self.assertTrue(secret in case["source"], "secret absent from source")
                    self.assertTrue(secret not in case["expected"], "oracle retains a supported secret")
                result = redactor.redact(case["source"])
                for secret in case["secrets"]:
                    self.assertTrue(secret not in result, "secret remains")
                self.assertEqual(result, case["expected"], "expected output mismatch")
                for innocent in case.get("innocent", []):
                    self.assertTrue(innocent in result, "innocent diagnostic damaged")
                self.assertEqual(redactor.removed, {
                    category: case["counts"].get(category, 0) for category in CATEGORIES
                })

    def test_supported_corpus(self):
        self.check_corpus("cases")


if __name__ == "__main__":
    corpus = json.loads((ROOT / "tests/fixtures/report-redaction.json").read_text())
    print(f"Supported redaction cases: {len(corpus['cases'])}; "
          f"journal cases: {len(corpus['journal_cases'])}")
    unittest.main()
