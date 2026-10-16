#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Retain original licence documents and legal comments from pinned shim sources."""
import argparse
from pathlib import Path
import re


LEGAL = re.compile(r'copyright|redistribution|licen[cs]e|public domain', re.I)
DOCUMENT = re.compile(r'^(?:copying|copyright|licen[cs]e|notice)(?:[._-].*)?$', re.I)

# Cryptlib's Intel headers refer to EDK2's BSD 2-Clause licence by URL.
# Keep the source copyright notices above the referenced standard terms.
# https://github.com/tianocore/edk2/blob/edk2-stable201903/License.txt
BSD2 = '''BSD 2-Clause License (copyright notices appear in the source extracts above)

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

* Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in
  the documentation and/or other materials provided with the
  distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
'''

# https://spdx.org/licenses/BSD-2-Clause-Patent.html, word for word (the disclaimer
# names the copyright HOLDERS twice, as edk2's License.txt does).
BSD2_PATENT = '''BSD-2-Clause-Patent (copyright notices appear in the source extracts above)

Redistribution and use in source and binary forms, with or without
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
POSSIBILITY OF SUCH DAMAGE.
'''

def collect(root, paths):
    notices = {}
    for path in sorted(set(paths)):
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        if DOCUMENT.match(path.name) or path.name.startswith('README.'):
            blocks = [text] if LEGAL.search(text) else []
        elif path.suffix.lower() in {'.c', '.h', '.s'}:
            comments = re.findall(r'/\*.*?\*/|^[ \t]*//[^\n]*(?:\n[ \t]*//[^\n]*)*',
                                  text, re.S | re.M)
            blocks = [block for block in comments if LEGAL.search(block)]
        else:
            blocks = []
        for block in blocks:
            notices.setdefault(block, []).append(path.relative_to(root).as_posix())
    if not notices:
        raise ValueError('component has no original licence notices')
    return '\n\n'.join('Source: ' + ', '.join(paths) + '\n\n' + block
                       for block, paths in notices.items()) + '\n'


def generate(root, output):
    for relative in ('COPYRIGHT', 'gnu-efi/README.efilib', 'Cryptlib/OpenSSL'):
        if not (root / relative).exists():
            raise ValueError(f'missing shim notice source: {relative}')
    components = {
        'SHIM-NOTICES': [p for p in root.rglob('*')
                         if p.relative_to(root).parts[0] not in {'gnu-efi', 'Cryptlib'}],
        'GNU-EFI-NOTICES': list((root / 'gnu-efi').rglob('*')),
        'CRYPTLIB-NOTICES': [p for p in (root / 'Cryptlib').rglob('*')
                            if 'OpenSSL' not in p.relative_to(root / 'Cryptlib').parts],
        'OPENSSL-NOTICES': list((root / 'Cryptlib/OpenSSL').rglob('*')),
    }
    rendered = {name: collect(root, paths) for name, paths in components.items()}
    # Do not silently ship only short licence references if upstream layout changes.
    for name, required in {
        'SHIM-NOTICES': ('Redistribution and use',),
        'GNU-EFI-NOTICES': ('Intel Corporation', 'Redistribution and use'),
        'CRYPTLIB-NOTICES': ('Intel Corporation', 'BSD'),
        'OPENSSL-NOTICES': ('OpenSSL Project', 'Eric Young', 'Redistribution and use',
                            'THIS SOFTWARE IS PROVIDED', 'This product includes'),
    }.items():
        normalized = ' '.join(rendered[name].replace('*', ' ').split())
        for phrase in required:
            if phrase not in normalized:
                raise ValueError(f'{name}: missing original licence terms: {phrase}')
    rendered['SHIM-NOTICES'] += '\n' + BSD2_PATENT
    rendered['CRYPTLIB-NOTICES'] += '\n' + BSD2
    output.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (output / name).write_text(text, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        generate(args.source, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
