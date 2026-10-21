#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject untranslated literals at desktop presentation boundaries."""
import ast
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalogues', ROOT / 'i18n/catalogues.py')
catalogues = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalogues)
VISIBLE = {'text', 'title', 'label', 'description', 'explanation', 'placeholderText',
           'tooltip', 'toolTip', 'message', 'error', 'detail', 'summary', 'subtitle',
           'accessibleName', 'hint', 'reason', 'warning', 'name', 'model', 'buttonText', 'help'}
TOKEN = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\]|\\.)*"|\x27(?:[^\x27\\]|\\.)*\x27|`(?:[^`\\]|\\.)*`|[\w.]+|===|!==|==|!=|=>|[^\s]')


def qml_findings(text):
    tokens = [(m.group(), m.start()) for m in TOKEN.finditer(text)
              if not m.group().startswith(('//', '/*'))]
    word_ranges = []
    for match in re.finditer(r'\bwords\s*[:=]\s*\(?\s*\{', text):
        depth = 1
        for token, offset in tokens:
            if offset < match.end():
                continue
            depth += (token == '{') - (token == '}')
            if not depth:
                word_ranges.append((match.end(), offset))
                break
    stack = []
    assignment = None
    for i, (token, offset) in enumerate(tokens):
        previous = tokens[i - 1][0] if i else ''
        if token in ('(', '[', '{'):
            stack.append((token, previous))
        elif token in (')', ']', '}'):
            if stack:
                stack.pop()
            if token == '}':
                assignment = None
        elif token in (':', '='):
            before = text[max(0, tokens[i - 1][1] - 256):tokens[i - 1][1]] if i else ''
            declaration = re.search(r'(?:^|[\n{;,])\s*(?:(?:readonly|required)\s+)?(?:property\s+\w+\s+)?$', before)
            if token == '=' or declaration:
                assignment = 'name' if previous == 'Accessible.name' else previous
        elif token == ';':
            assignment = None
        if not token.startswith(('"', "'", '`')):
            continue
        value = token[1:-1]
        if not re.search('[A-Za-z]', value) or value.startswith(('\\', '#')):
            continue
        if any(call in ('qsTr', 'qsTranslate', 'QT_TR_NOOP', 'QT_TRANSLATE_NOOP') for _, call in stack):
            continue
        # Import URIs, artwork, format patterns and protocol literals are not copy.
        if previous in ('import', '===', '!==', '==', '!=') or value.startswith(('file:', 'data:', 'image:', 'http:', 'https:')):
            continue
        if any(call in ('console.log', 'console.warn', 'console.error', 'console.info',
                        'Qt.formatDateTime', 'Qt.formatDate', 'Qt.formatTime',
                        'Qt.resolvedUrl', 'Quickshell.env', 'Qt.createQmlObject') for _, call in stack):
            continue
        # Only presentation properties are inferred. Identifiers, commands and
        # external/user supplied strings are deliberately outside this boundary.
        if any(call in ('split', 'join') or call.endswith(('.split', '.join', '.toLocaleString', '.hasOwnProperty')) for _, call in stack):
            continue
        if value.endswith('-symbolic') or value in ('f', 'g', 'e'):
            continue
        # Arguments to lookups/formatters and membership tests are structural.
        if any(bracket == '(' and re.fullmatch(r'[\w.]+', call) for bracket, call in stack):
            continue
        if any(bracket == '[' for bracket, _ in stack):
            tail = ''.join(part for part, _ in tokens[i + 1:i + 25])
            if re.search(r'\]\.includes\(', tail):
                continue
        if assignment in ('reason',) and re.fullmatch(r'[a-z][a-z0-9_.:-]*', value):
            continue
        # The clock's named copy dictionaries are presentation boundaries too.
        in_words = any(start <= offset < end for start, end in word_ranges)
        if (assignment in VISIBLE or (in_words and previous == ':')) and not re.fullmatch(r'[A-Za-z][\w.:-]*\.(?:svg|png|qml|js|desktop)', value):
            yield text.count('\n', 0, offset) + 1, value


def python_findings(text, internal_raises=False):
    tree = ast.parse(text)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr)):
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.JoinedStr):
            continue
        visible = isinstance(parent, ast.keyword) and parent.arg in VISIBLE
        if isinstance(parent, ast.Dict):
            visible = any(v is node and isinstance(k, ast.Constant) and k.value in VISIBLE
                          for k, v in zip(parent.keys, parent.values))
            owner = parents.get(parent)
            if isinstance(owner, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'ERRORS'
                                                     for target in owner.targets):
                visible = node in parent.values
        if isinstance(parent, ast.Call) and isinstance(parents.get(parent), ast.Raise):
            visible = not internal_raises
        if visible:
            value = node.value if isinstance(node, ast.Constant) else ast.get_source_segment(text, node)
            if re.search('[A-Za-z]', value or '') and not re.fullmatch(r'[a-z][a-z0-9_.:-]*', value):
                yield node.lineno, value


def rust_findings(text):
    literal = r'"(?:[^"\\]|\\.)*"'
    output = r'\b(?:e?print(?:ln)?|write(?:ln)?)!\s*\(\s*(?:[A-Za-z_]+,\s*)?(' + literal + ')'
    for match in re.finditer(output, text, re.S):
        value = match[1]
        copy = re.sub(r'\\x1b\[[0-9;?]*[A-Za-z]', '', value)
        copy = re.sub(r'\{[^{}]*\}|\\x[0-9a-fA-F]{2}|\\[nrt0]', '', copy)
        if re.search('[A-Za-z]', copy):
            yield text.count('\n', 0, match.start()) + 1, value


def wrapper_findings(text):
    for number, line in enumerate(text.split('\n'), 1):
        if line.lstrip().startswith('#'):
            continue
        match = re.search(r"\b(?:printf|echo)\s+(['\"])(.*?)\1", line)
        if match:
            value = re.sub(r'%[-+0-9.]*[a-zA-Z]|\\[nrt]', '', match[2])
            if re.search('[A-Za-z]', value):
                yield number, match[2]


def identifier_findings(text, language):
    """Reject translation at stable protocol, lookup and comparison boundaries."""
    if language == 'python':
        tree = ast.parse(text)
        def translated(node):
            return any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                       and child.func.id in ('_', 'gettext', 'ngettext') for child in ast.walk(node))
        for node in ast.walk(tree):
            stable = []
            if isinstance(node, ast.Subscript):
                stable = [node.slice]
            elif isinstance(node, ast.Compare):
                stable = [node.left, *node.comparators]
            elif isinstance(node, ast.Dict):
                stable = [key for key in node.keys if key is not None]
                stable += [value for key, value in zip(node.keys, node.values)
                           if isinstance(key, ast.Constant) and key.value in
                           ('app_name', 'app_id', 'desktop-entry', 'phase', 'op', 'reason_code')]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('get', 'has_section', 'remove_section'):
                stable = node.args[:1]
            if any(translated(value) for value in stable):
                yield node.lineno, 'translated identifier or comparison'
        return
    tr = r'(?:qsTr|qsTranslate)\s*\('
    rules = [
        (r'\.(?:local|offer|hold)\s*\([^,;\n]*' + tr, 'translated notification app key'),
        (tr + r'[^;\n]*["\x27]name:', 'translated notification rule key'),
        (r'(?:===|!==|==|!=)\s*' + tr + r'[^\n?]*?\)(?![^\n;]*\?)', 'comparison against translated display text'),
        (r'\b(?:id|appId|app_id|desktopEntry|phase|op|reasonCode)\s*:\s*' + tr, 'translated protocol identifier'),
        (tr + r'[^\n]*["\x27]Type (?:DELETE|ERASE)["\x27]', 'confirmation word must be a stable placeholder'),
        (r'startsWith\(\s*["\x27]Keyboard: ', 'parse summary metadata, not displayed keyboard text'),
        (r'\^Updating Flatpak applications', 'parse the stable Flatpak phase'),
        (r'(?:indexOf|includes)\(\s*["\x27]hold the power button', 'parse restart recovery metadata'),
    ]
    for pattern, message in rules:
        for match in re.finditer(pattern, text):
            yield text.count('\n', 0, match.start()) + 1, message
    for match in re.finditer(r'\bfunction status\([^)]*\)[^{]*\{([\s\S]*?)\n    \}', text):
        if re.search(tr, match[1]):
            yield text.count('\n', 0, match.start()) + 1, 'translated IPC status value'


def invariant_findings(relative, text):
    """Named cross-component contracts not inferable from a presentation property."""
    welcome_fallbacks = set()
    if relative == 'niri/default.kdl':
        for block in re.finditer(r'^window-rule \{\n.*?^\}', text, re.M | re.S):
            if 'match app-id=r#"^org\\.emaki\\.welcome$"#' not in block.group():
                continue
            for offset, line in enumerate(block.group().splitlines()):
                if line.strip() == 'match app-id=r#"^org\\.quickshell$"# title="^Welcome to Emaki$"':
                    welcome_fallbacks.add(text[:block.start()].count('\n') + offset + 1)
    for number, line in enumerate(text.splitlines(), 1):
        if re.search(r'\b(?:_|gettext|qsTranslate)\(', line) and any(value in line for value in
                ('Emaki, with Linux', 'VPN Connection')):
            yield number, 'translated GRUB title or NetworkManager group'
        if relative == 'niri/default.kdl' and 'match ' in line and 'title=' in line and 'Welcome to Emaki' in line and number not in welcome_fallbacks:
            yield number, 'Welcome must match a stable app id'
        if relative.startswith('tests/vm/') and re.search(r"(?:get\(['\"]title|\[['\"]title).*==.*(?:Recovery snapshot|Welcome to Emaki)", line):
            yield number, 'window automation must match a stable app id'


def main():
    allow_path = ROOT / 'i18n/allow.json'
    allow = json.loads(allow_path.read_text()) if allow_path.exists() else {}
    allowed = {(path, text) for row in allow.get('literals', []) if row.get('reason')
               for path in row['paths'] for text in row['texts']}
    internal = {path for row in allow.get('internal_raises', []) if row.get('reason') for path in row['paths']}
    used = set()
    failures = []
    files = set()
    for domain in catalogues.DOMAINS:
        files.update(catalogues.source_files(domain, {'.qml', '.js', '.py'}))
    files.update((ROOT / 'crates/emaki-cli/src').glob('*.rs'))
    files.update(ROOT / path for path in catalogues.WRAPPERS.values())
    files.add(ROOT / 'niri/default.kdl')
    files.update((ROOT / 'tests/vm').rglob('*.py'))
    for path in sorted(files):
        relative = str(path.relative_to(ROOT))
        source = path.read_text()
        extra = list(invariant_findings(relative, source))
        if relative.startswith('tests/vm/') or path.suffix == '.kdl':
            findings = []
        elif path.suffix == '.rs':
            findings = rust_findings(path.read_text())
        elif relative in catalogues.WRAPPERS.values():
            findings = wrapper_findings(path.read_text())
        elif path.suffix == '.py' or path.name in catalogues.SHELL_SCRIPTS:
            findings = python_findings(path.read_text(), relative in internal)
            extra.extend(identifier_findings(source, 'python'))
        else:
            findings = qml_findings(path.read_text())
            extra.extend(identifier_findings(source, 'qml'))
        for line, text in [*findings, *extra]:
            key = (str(path.relative_to(ROOT)), text)
            if key in allowed:
                used.add(key)
            else:
                failures.append(f'{key[0]}:{line}: {text}')
    for path, text in sorted(allowed - used):
        failures.append(f'{path}: stale i18n exception: {text}')
    if failures:
        raise SystemExit('\n'.join(failures))
    print(f'PASS: translation boundaries in {len(files)} source files; {len(used)} justified exceptions')


if __name__ == '__main__':
    main()
