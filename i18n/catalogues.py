#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract, pseudo-localize and compile the desktop's standard catalogues."""
import argparse
import ast
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'i18n'
DOMAINS = ('shell', 'programs', 'installer', 'notes', 'cli')
SHELL_SCRIPTS = ('emaki-machine-settings', 'emaki-settings-about', 'emaki-settings-apps',
                 'emaki-settings-network', 'emaki-settings-power', 'emaki-settings-privacy',
                 'emaki-settings-users', 'emaki-settings-wallpaper')
WRAPPERS = {'notes': 'notes/emaki-notes', 'programs': 'update-manager/emaki-update-manager',
            'installer': 'installer/ui/emaki-install'}
HEADER = 'Copyright (C) 2026 Artur Yakymenko\nSPDX-License-Identifier: GPL-3.0-or-later'
ENGLISH_NUMERUS = {
    'This install can no longer be taken back as it was. %1 need(s) what it added.': (
        'This install can no longer be taken back as it was. %1 needs what it added.',
        'This install can no longer be taken back as it was. %1 need what it added.'),
    '%n update(s) are ready to install.': ('%n update is ready to install.', '%n updates are ready to install.'),
}
ACCENTS = str.maketrans('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ',
                       'àƀçðéƒĝĥîĵķļɱñöþǫŕšţûṽŵẋÿžÀƁÇÐÉƑĜĤÎĴĶĻṀÑÖÞǪŔŠŢÛṼŴẊŸŽ')
# Format fields and markup belong to the renderer, not the translator.
PROTECTED = re.compile(r'(%(?:L?\d+|L?n|\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]|[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z%])|\{[^{}]*\}|<[^>]+>|&(?:\w+|#\d+);)')


def pseudo(text):
    if not text:
        return text
    leading = text[:len(text) - len(text.lstrip('\n'))]
    trailing = text[len(text.rstrip('\n')):]
    text = text.strip('\n')
    if not text:
        return leading
    pieces = PROTECTED.split(text)
    changed = ''.join(part if i % 2 else part.translate(ACCENTS) for i, part in enumerate(pieces) if part)
    padding = max(0, math.ceil(len(text) * .35) - 2)
    return leading + '[' + changed + '·' * padding + ']' + trailing


def source_files(domain, suffixes):
    directories = {'shell': ['shell'], 'programs': ['update-manager'],
                   'installer': ['installer/emaki_installer', 'installer/ui'],
                   'notes': ['notes'], 'cli': ['crates/emaki-cli/src']}[domain]
    result = []
    for directory in directories:
        for path in (ROOT / directory).rglob('*'):
            if path.suffix not in suffixes or {'tests', 'assets', '__pycache__'} & set(path.parts):
                continue
            if domain == 'shell' and path.name.startswith('Notes'):
                continue
            result.append(path)
    if domain == 'notes' and '.qml' in suffixes:
        result.extend((ROOT / 'shell').glob('Notes*.qml'))
    if domain == 'shell' and '.py' in suffixes:
        result.extend(ROOT / 'scripts' / name for name in SHELL_SCRIPTS)
    return sorted(result)


def tool(name):
    found = shutil.which(name) or shutil.which(name + '-qt6')
    if found:
        return found
    # Qt's bindir is a build-time platform query, never a runtime path assumption.
    bindir = subprocess.check_output(['pkg-config', '--variable=bindir', 'Qt6Core'], text=True).strip()
    candidate = Path(bindir) / name
    if not candidate.is_file():
        raise RuntimeError('Missing catalogue tool: ' + name)
    return str(candidate)


def qt_catalogue(domain, temporary):
    files = source_files(domain, {'.qml', '.js'})
    if not files:
        return None
    path = temporary / (domain + '.ts')
    subprocess.run([tool('lupdate'), *map(str, files), '-silent', '-no-obsolete',
                    '-locations', 'none', '-source-language', 'en', '-target-language', 'en',
                    '-ts', str(path)], check=True)
    root = ET.parse(path).getroot()
    for context in root.findall('context'):
        for message in context.findall('message'):
            translation = message.find('translation')
            if translation is not None:
                message.remove(translation)
            translation = ET.SubElement(message, 'translation')
            source = message.findtext('source') or ''
            if message.get('numerus') == 'yes':
                for plural in (False, True):
                    text = (ENGLISH_NUMERUS[source][int(plural)] if source in ENGLISH_NUMERUS else
                            source.replace('(s)', 's' if plural else '').replace('(ies)', 'ies' if plural else 'y')
                            .replace('(es)', 'es' if plural else ''))
                    ET.SubElement(translation, 'numerusform').text = text
            else:
                translation.text = source
    return root


def xml_bytes(root):
    ET.indent(root, space='    ')
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n<!-- ' + HEADER + ' -->\n').encode() + ET.tostring(root, encoding='utf-8') + b'\n'


def python_messages(domain):
    if domain == 'cli':
        return rust_messages()
    messages = {}
    for path in source_files(domain, {'.py'}):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ''
            if name not in ('_', 'gettext', 'ngettext', 'N_') or not node.args:
                continue
            if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                raise ValueError(f'{path.relative_to(ROOT)}:{node.lineno}: gettext requires a literal source')
            source = node.args[0].value
            plural = None
            if name == 'ngettext':
                if len(node.args) < 3 or not isinstance(node.args[1], ast.Constant):
                    raise ValueError(f'{path}:{node.lineno}: ngettext requires literal forms')
                plural = node.args[1].value
            messages[source] = plural
    wrapper = WRAPPERS.get(domain)
    if wrapper:
        # Wrapper messages use literal shell single quotes; arguments are format
        # values, never source text. Preserve embedded newline layout.
        for match in re.finditer(r"\btranslate\s+'([^']*)'", (ROOT / wrapper).read_text()):
            messages[match[1]] = None
    return messages


def rust_messages():
    """The CLI uses literal tr/format_text calls and three help constants."""
    messages = {}
    literal = r'"(?:[^"\\]|\\[\s\S])*"'
    def decode(value):
        def escape(match):
            token = match[1]
            if token.startswith('x'):
                return chr(int(token[1:], 16))
            if token.startswith('u{'):
                return chr(int(token[2:-1], 16))
            return {'n': '\n', 'r': '\r', 't': '\t', '0': '\0', '"': '"', "'": "'", '\\': '\\'}[token]
        return re.sub(r'\\(x[0-9a-fA-F]{2}|u\{[0-9a-fA-F_]+\}|[nrt0"\x27\\])', escape, value[1:-1])
    for path in sorted((ROOT / 'crates').glob('*/src/**/*.rs')):
        if path.name == 'i18n.rs':
            continue
        text = path.read_text().split('#[cfg(test)]')[0]
        for match in re.finditer(r'\b(?:tr|format_text)\s*\(\s*(' + literal + ')', text):
            messages[decode(match[1])] = None
        if 'emaki-cli' in path.parts:
            for match in re.finditer(r'const (?:HELP|INTERNAL_HELP|NIRI_HELP):\s*&str\s*=\s*(' + literal + ')', text):
                messages[decode(match[1])] = None
    return messages


def po_bytes(messages, language, pseudo_locale=False, template=False):
    lines = ['# ' + line for line in HEADER.split('\n')]
    lines += ['msgid ""', 'msgstr ""']
    for field in ('Project-Id-Version: Emaki\n', 'MIME-Version: 1.0\n',
                  'Content-Type: text/plain; charset=UTF-8\n', 'Content-Transfer-Encoding: 8bit\n',
                  'Language: ' + language + '\n', 'Plural-Forms: nplurals=2; plural=(n != 1);\n'):
        lines.append(json.dumps(field, ensure_ascii=False))
    for source, plural in sorted(messages.items()):
        lines.append('')
        flags = []
        if re.search(r'%\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]', source):
            flags.append('python-format')
        if re.search(r'\{(?:[A-Za-z_]\w*|\d+)(?:![rsa])?(?::[^{}]*)?\}', source):
            flags.append('python-brace-format')
        if flags:
            lines.append('#, ' + ', '.join(flags))
        lines.append('msgid ' + json.dumps(source, ensure_ascii=False))
        if plural is not None:
            lines.append('msgid_plural ' + json.dumps(plural, ensure_ascii=False))
            for index, text in enumerate((source, plural)):
                lines.append(f'msgstr[{index}] ' + json.dumps('' if template else pseudo(text) if pseudo_locale else text, ensure_ascii=False))
        else:
            lines.append('msgstr ' + json.dumps('' if template else pseudo(source) if pseudo_locale else source, ensure_ascii=False))
    return ('\n'.join(lines) + '\n').encode()


def generate():
    outputs = {}
    with tempfile.TemporaryDirectory(prefix='emaki-catalogues-') as tmp:
        temporary = Path(tmp)
        for domain in DOMAINS:
            qt = qt_catalogue(domain, temporary)
            if qt is not None:
                outputs[BASE / domain / 'en.ts'] = xml_bytes(qt)
                # en_US supplies Qt's two plural forms; en_XA is the file/selection name.
                qt.set('language', 'en_US')
                for message in qt.iter('message'):
                    translation = message.find('translation')
                    # Accented pattern letters would print as literal text; brackets are not fields.
                    if (message.findtext('extracomment') or '').startswith('Qt date pattern'):
                        translation.text = '[' + (translation.text or '') + ']'
                    elif list(translation):
                        for form in translation:
                            form.text = pseudo(form.text or '')
                    else:
                        translation.text = pseudo(translation.text or '')
                outputs[BASE / domain / 'en_XA.ts'] = xml_bytes(qt)
            messages = python_messages(domain)
            if messages:
                outputs[BASE / domain / (domain + '.pot')] = po_bytes(messages, 'en', template=True)
                outputs[BASE / domain / 'en.po'] = po_bytes(messages, 'en')
                outputs[BASE / domain / 'en_XA.po'] = po_bytes(messages, 'en_XA', pseudo_locale=True)
    return outputs


def compile_catalogues(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for domain in DOMAINS:
        for path in sorted((BASE / domain).glob('*.ts')):
            subprocess.run([tool('lrelease'), '-silent', str(path), '-qm',
                            str(destination / (domain + '_' + path.stem + '.qm'))], check=True)
            if path.stem == 'pt_BR':
                shutil.copyfile(destination / (domain + '_pt_BR.qm'),
                                destination / (domain + '_pt.qm'))
        for path in sorted((BASE / domain).glob('*.po')):
            target = destination / path.stem / 'LC_MESSAGES' / (domain + '.mo')
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['msgfmt', '--check-format', str(path), '-o', str(target)], check=True)
            if path.stem == 'pt_BR':
                alias = destination / 'pt' / 'LC_MESSAGES' / target.name
                alias.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, alias)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('update', 'check', 'compile'))
    parser.add_argument('--output', type=Path, default=BASE / 'locale')
    args = parser.parse_args()
    if args.action != 'compile':
        stale = []
        for path, content in generate().items():
            if args.action == 'update':
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            elif not path.is_file() or path.read_bytes() != content:
                stale.append(str(path.relative_to(ROOT)))
        if stale:
            raise SystemExit('Stale catalogues; run make translations-update:\n' + '\n'.join(stale))
    compile_catalogues(args.output)
    print('PASS: catalogue ' + args.action + ' and compilation')


if __name__ == '__main__':
    main()
