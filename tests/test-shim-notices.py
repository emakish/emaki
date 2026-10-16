#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regression fixtures for embedded shim licence payloads."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'packaging/emaki-shim-signed'
spec = importlib.util.spec_from_file_location('shim_notices', RECIPE / 'notices.py')
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class ShimNotices(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='shim-notices-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / 'shim-16.1'
        self.output = self.base / 'notices'
        self.documents = {
            'COPYRIGHT': 'Copyright shim contributors\nRedistribution and use permitted.\n',
            'gnu-efi/README.efilib': 'Copyright Intel Corporation\nRedistribution and use permitted.\n',
            'Cryptlib/Hash/hash.c': '/* Copyright Intel Corporation\nBSD License. */\nint value;\n',
            'Cryptlib/OpenSSL/crypto/hash.c': '/* Copyright OpenSSL Project\n'
                'Redistribution and use permitted.\nTHIS SOFTWARE IS PROVIDED AS IS.\n'
                'This product includes cryptographic software written by Eric Young. */\nint hash;\n',
        }
        for name, text in self.documents.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_original_notices_and_referenced_terms_survive(self):
        notices.generate(self.source, self.output)
        for name, source in zip(('SHIM-NOTICES', 'GNU-EFI-NOTICES', 'CRYPTLIB-NOTICES', 'OPENSSL-NOTICES'),
                                self.documents):
            text = (self.output / name).read_text()
            self.assertIn('Source: ' + source, text)
            self.assertIn(self.documents[source].split('\nint ')[0].strip(), text)
            self.assertNotIn('int hash;', text)
        self.assertIn('THIS SOFTWARE IS PROVIDED', (self.output / 'CRYPTLIB-NOTICES').read_text())
        first = {p.name: p.read_bytes() for p in self.output.iterdir()}
        notices.generate(self.source, self.output)
        self.assertEqual(first, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_patent_license_terms(self):
        notices.generate(self.source, self.output)
        text = ' '.join((self.output / 'SHIM-NOTICES').read_text().split())
        for phrase in ('BSD-2-Clause-Patent',
                       'perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable',
                       'patent claims, already acquired or hereafter acquired',
                       'their Contribution(s)', 'shall not apply to any other combinations',
                       'by implication, estoppel or otherwise', 'POSSIBILITY OF SUCH DAMAGE'):
            self.assertIn(phrase, text)

    def test_patent_terms_are_the_spdx_text_word_for_word(self):
        # https://spdx.org/licenses/BSD-2-Clause-Patent.html, as shipped verbatim in
        # edk2's License.txt: the disclaimer names the copyright HOLDERS twice.
        spdx = '''Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice,
this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
this list of conditions and the following disclaimer in the documentation
and/or other materials provided with the distribution.
Subject to the terms and conditions of this license, each copyright holder
and contributor hereby grants to those receiving rights under this license
a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable
(except for failure to satisfy the conditions of this license) patent
license to make, have made, use, offer to sell, sell, import, and otherwise
transfer this software, where such license applies only to those patent
claims, already acquired or hereafter acquired, licensable by such copyright
holder or contributor that are necessarily infringed by:
(a) their Contribution(s) (the licensed copyrights of copyright holders and
non-copyrightable additions of contributors, in source or binary form)
alone; or
(b) combination of their Contribution(s) with the work of authorship to
which such Contribution(s) was added by such copyright holder or
contributor, if, at the time the Contribution is added, such addition
causes such combination to be necessarily infringed. The patent license
shall not apply to any other combinations which include the
Contribution.
Except as expressly stated above, no rights or licenses from any copyright
holder or contributor is granted under this license, whether expressly, by
implication, estoppel or otherwise.
DISCLAIMER
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDERS OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.'''
        shipped = notices.BSD2_PATENT.split('\n', 1)[1]
        self.assertEqual(' '.join(shipped.split()), ' '.join(spdx.split()))

    def test_patent_package_metadata(self):
        result = subprocess.run(['bash', '-eu', '-c',
                                 'source "$1"; printf "%s\\n" "${license[@]}"', '_',
                                 str(RECIPE / 'PKGBUILD')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BSD-2-Clause-Patent', result.stdout.splitlines())

    def test_line_spdx_and_nearby_copyright_survive(self):
        header = ('// SPDX-License-Identifier: BSD-2-Clause-Patent\n'
                  '// Copyright 2024 Example contributors\n'
                  '// All rights reserved.\n')
        for name in ('shim.c', 'include.h', 'entry.S'):
            (self.source / name).write_text(header + 'int executable_code;\n')
        notices.generate(self.source, self.output)
        text = (self.output / 'SHIM-NOTICES').read_text()
        self.assertIn(header.strip(), text)
        self.assertIn('Source: entry.S, include.h, shim.c', text)
        self.assertNotIn('int executable_code;', text)

    def test_cryptlib_uses_edk2_two_clause_terms(self):
        notices.generate(self.source, self.output)
        text = ' '.join((self.output / 'CRYPTLIB-NOTICES').read_text().split())
        self.assertIn('BSD 2-Clause', text)
        self.assertIn('Redistributions of source code must retain', text)
        self.assertIn('Redistributions in binary form must reproduce', text)
        self.assertIn('POSSIBILITY OF SUCH DAMAGE', text)
        self.assertNotIn('Neither the name', text)
        self.assertNotIn('BSD 3-Clause', text)

    def test_missing_component_and_short_references_are_rejected(self):
        (self.source / 'Cryptlib/OpenSSL/crypto/hash.c').write_text('/* OpenSSL licence. */\n')
        with self.assertRaisesRegex(ValueError, 'missing original licence terms'):
            notices.generate(self.source, self.output)
        self.assertFalse(self.output.exists())
        shutil.rmtree(self.source / 'Cryptlib/OpenSSL')
        with self.assertRaisesRegex(ValueError, 'missing shim notice source'):
            notices.generate(self.source, self.output)

    def test_package_installs_all_notices_and_matches_manifest(self):
        notices.generate(self.source, self.output)
        efi = self.base / 'rpm/usr/lib/efi/shim/16.1-7/EFI/fedora'
        efi.mkdir(parents=True)
        for name in ('shimx64.efi', 'mmx64.efi'):
            (efi / name).write_bytes(b'loader fixture')
        (self.base / 'shim-16.1-COPYRIGHT').write_text(self.documents['COPYRIGHT'])
        shutil.copyfile(RECIPE / 'expected-files.list', self.base / 'expected-files.list')
        result = subprocess.run(['bash', '-eu', '-c',
                                 'source "$1"; srcdir=$2; pkgdir=$3; package', '_',
                                 str(RECIPE / 'PKGBUILD'), str(self.base), str(self.base / 'pkg')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for path in self.output.iterdir():
            installed = self.base / 'pkg/usr/share/licenses/emaki-shim-signed' / path.name
            self.assertEqual(installed.read_bytes(), path.read_bytes())


if __name__ == '__main__':
    unittest.main()
