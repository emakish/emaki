#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic privacy fixtures; no machine logs or credentials are read."""

import pathlib
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from emaki_report_redact import CATEGORIES, Redactor


class RedactionTests(unittest.TestCase):
    def redact(self, text):
        redactor = Redactor(user="testperson", host="test-machine.example")
        return redactor, redactor.redact(text)

    def test_review_secrets(self):
        redactor, result = self.redact("Authorization: Bearer C8R_SYNTHETIC_SECRET password=C8R_SYNTHETIC_PASSWORD")
        self.assertNotIn("C8R_SYNTHETIC", result)
        self.assertEqual(redactor.removed["secret"], 1)

    def test_assignments(self):
        for source in ('password=planted', 'password="planted truncated', 'Bearer "planted"', 'passwd: planted123', 'secret = planted',
                       'token=planted', 'key=planted', '--api-key=planted',
                       '--access_token=planted', '"refresh_token": "planted with spaces"',
                       "password='planted with spaces'", 'passwd=planted',
                       '"private_key": "planted\\nwith escaped lines"'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertNotIn("planted", result)
                self.assertEqual(redactor.removed["secret"], 1)

    def test_private_keys_complete_and_truncated(self):
        for kind in ("RSA PRIVATE KEY", "OPENSSH PRIVATE KEY", "PRIVATE KEY", "ENCRYPTED PRIVATE KEY", "PGP PRIVATE KEY BLOCK"):
            for tail in (f"\n-----END {kind}-----", ""):
                source = f"before\n-----BEGIN {kind}-----\nplanted-secret\nmore-secret{tail}"
                redactor, result = self.redact(source)
                self.assertNotIn("planted", result)
                self.assertNotIn("more-secret", result)
                self.assertEqual(redactor.removed["secret"], 1)

    def test_identities_and_counts(self):
        redactor, result = self.redact("/home/testperson/file testperson@test-machine.example hostname=test-machine.example host=test-machine")
        self.assertNotIn("testperson", result)
        self.assertNotIn("test-machine", result)
        self.assertEqual(redactor.removed["user"], 1)
        self.assertEqual(redactor.removed["email"], 1)
        self.assertEqual(redactor.removed["host"], 2)
        self.assertEqual(set(redactor.removed), set(CATEGORIES))

    def test_addresses(self):
        for address in ("192.0.2.42", "2001:db8::42", "::1", "::", "fe80::1234%eth0", "::ffff:192.0.2.42", "2001:db8:0:1:2:3:4:5"):
            with self.subTest(address=address):
                redactor, result = self.redact("address=[" + address + "]")
                self.assertNotIn(address, result)
                self.assertEqual(redactor.removed["ip"], 1)
        for address in ("aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabb.ccdd.eeff"):
            redactor, result = self.redact(address)
            self.assertNotIn(address, result)
            self.assertEqual(redactor.removed["mac"], 1)

    def test_serials(self):
        for source in ('Serial Number: planted', 'serial=planted', '"serial": "planted"', 'SERIAL="planted"', 'S/N: planted', 'Serial Number planted', 'NAME SERIAL TYPE\nsda planted disk\n'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertNotIn("planted", result)
                self.assertEqual(redactor.removed["serial"], 1)

    def test_ordinary_logs_and_versions(self):
        source = "Emaki 0.8 kernel 6.18.2-arch1-1 niri 26.04\n12:34:56 transaction failed with exit 1\nGPU Radeon RAM 16 GiB"
        redactor, result = self.redact(source)
        self.assertEqual(result, source)
        self.assertFalse(any(redactor.removed.values()))

    def test_serial_table_without_type_stops_at_same_width_log(self):
        redactor, result = self.redact('NAME SERIAL\nsda planted\nsshd: Accepted\n')
        self.assertEqual(result, 'NAME SERIAL\nsda [redacted serial]\nsshd: Accepted\n')
        self.assertEqual(redactor.removed['serial'], 1)

    def test_basic_description_and_ip_range(self):
        redactor, result = self.redact('Basic network check failed\nrange 192.0.2.1-192.0.2.9\n')
        self.assertEqual(result, 'Basic network check failed\nrange [redacted ip]-[redacted ip]\n')
        self.assertEqual(redactor.removed['ip'], 2)
        self.assertEqual(redactor.removed['secret'], 0)

    def test_dense_log_and_punctuation(self):
        redactor, result = self.redact(("token=planted " * 1000 + "\n") * 20)
        self.assertNotIn("planted", result)
        self.assertEqual(redactor.removed["secret"], 20000)
        redactor, result = self.redact("address 2001:db8::1. mail person@localhost")
        self.assertNotIn("2001:db8", result)
        self.assertIn("person@localhost", result)
        self.assertEqual(redactor.removed["ip"], 1)
        self.assertEqual(redactor.removed["email"], 0)

    def test_no_network_or_writes(self):
        with mock.patch("socket.socket", side_effect=AssertionError("network")), mock.patch("builtins.open", side_effect=AssertionError("file")), mock.patch("socket.getfqdn", side_effect=AssertionError("DNS")):
            redactor = Redactor()
            self.assertNotIn("planted", redactor.redact("password=planted"))

    def test_residual_risk_drops_whole_lines_and_counts_them(self):
        source = ('render started\nDB_PASSWORD_PROD malformed planted\n'
                  'QWxwaGFCZXRhR2FtbWFEZWx0YUZpeHR1cmVLZXk=\nrender resumed\n')
        redactor, result = self.redact(source)
        self.assertEqual(result, 'render started\nrender resumed\n')
        self.assertEqual(redactor.removed['lines'], 2)
        self.assertEqual(redactor.removed['secret'], 0)
        redactor, result = self.redact('password=planted auth malformed other-value\n')
        self.assertEqual(result, '')
        self.assertEqual(redactor.removed['lines'], 1)
        self.assertEqual(redactor.removed['secret'], 1)

    def test_innocent_diagnostics_survive_residual_check(self):
        source = ('key: XF86AudioRaiseVolume\nPassword: Required\n'
                  'polkit: Authorization: denied\nconnector HDMI-1: connected\n'
                  'request https://example.com/a%20b\n')
        redactor, result = self.redact(source)
        self.assertEqual(result, source)
        self.assertFalse(any(redactor.removed.values()))


if __name__ == "__main__":
    unittest.main()
