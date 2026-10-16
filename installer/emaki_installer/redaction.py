# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registered-secret and credential-pattern redaction for local diagnostics."""
import re


class Redactor:
    def __init__(self):
        self.secrets = set()

    def add(self, value):
        if isinstance(value, str) and value:
            self.secrets.add(value)

    def text(self, value, *, keep_newlines=False):
        value = str(value)
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        # A quoted value may contain spaces; consume its closing quote as a pair.
        value = re.sub(r'(?i)((?:password|passwd|token)["\x27]?\s*[:=]\s*)(["\x27])([^\r\n]*?)\2',
                       r'\1\2[REDACTED]\2', value)
        value = re.sub(r'(?i)(password|passwd|token)([\s"\x27:=]+)(\[REDACTED\]|[^\s,}\]]+)',
                       r'\1\2[REDACTED]', value)
        # Remove terminal control sequences before displaying logs in QML.
        value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
        return ''.join(ch for ch in value if ord(ch) >= 32 or ch == '\t'
                       or (keep_newlines and ch == '\n'))

