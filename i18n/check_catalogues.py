#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Require complete shipped translations before a release can pass its checks."""
import ast
from collections import Counter
import gettext
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
DOMAINS = ('shell', 'programs', 'installer', 'notes', 'cli')
LANGUAGES = ('es', 'pt_BR', 'de', 'fr', 'ru')
PLURALS = {
    'es': (2, '(n != 1)'), 'pt_BR': (2, '(n > 1)'),
    'de': (2, '(n != 1)'), 'fr': (2, '(n > 1)'),
    'ru': (3, '(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2)'),
}
FIELDS = re.compile(r'%(?:L?\d+|L?n|\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]|[sdf]|%)|\{[^{}]*\}')
TAGS = re.compile(r'</?[A-Za-z][^>]*>')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def po_entries(path):
    entries, entry, field = [], {}, None
    for line in path.read_text().splitlines() + ['']:
        line = line.strip()
        if not line:
            if entry:
                entries.append(entry)
            entry, field = {}, None
        elif line.startswith('#~'):
            raise AssertionError(f'{path}: obsolete entry')
        elif line.startswith('#,'):
            entry['flags'] = entry.get('flags', '') + ',' + line[2:]
        elif line.startswith('#'):
            continue
        elif line.startswith('"'):
            require(field is not None, f'{path}: orphan continuation')
            entry[field] += ast.literal_eval(line)
        else:
            field, value = line.split(' ', 1)
            require(field not in entry, f'{path}: duplicate {field}')
            entry[field] = ast.literal_eval(value)
    return entries


def po_messages(path):
    result = {}
    for entry in po_entries(path):
        key = (entry.get('msgctxt', ''), entry.get('msgid'))
        require(key[1] is not None and key not in result, f'{path}: missing or duplicate msgid {key}')
        require('fuzzy' not in entry.get('flags', ''), f'{path}: fuzzy entry {key}')
        result[key] = entry
    return result


def qt_messages(path):
    root = ET.parse(path).getroot()
    result = {}
    for context in root.findall('context'):
        for message in context.findall('message'):
            key = (context.findtext('name'), message.findtext('source'), message.findtext('comment', ''))
            require(key not in result, f'{path}: duplicate message {key}')
            result[key] = message
    return root, result


def text_contract(source, target, where, unchanged=None):
    require(bool(target.strip()), f'{where}: empty translation')
    require(Counter(FIELDS.findall(source)) == Counter(FIELDS.findall(target)),
            f'{where}: broken placeholders: {source!r} / {target!r}')
    require(TAGS.findall(source) == TAGS.findall(target), f'{where}: changed markup')
    require(source.count('\n') == target.count('\n'), f'{where}: changed line breaks')
    for strip in (str.lstrip, str.rstrip):
        require(len(source) - len(strip(source, '\n')) == len(target) - len(strip(target, '\n')),
                f'{where}: changed boundary newlines')
    if unchanged is not None and source == target:
        require(source in unchanged, f'{where}: untranslated {source!r}')


def french_typography(target, where):
    """Keep French punctuation and grouped numbers together when labels wrap."""
    # Markup, URLs, command tokens and fixed date patterns are not prose.
    prose = TAGS.sub('', target)
    prose = re.sub(r'https?://\S+|(?<!\w):[A-Za-z][\w-]*', '', prose)
    prose = re.sub(r'format: true|\{[^{}\n]*:[^{}\n]*\}:', '', prose)
    # The emoji shortcut's semicolon is a key, not French sentence punctuation.
    prose = re.sub(r'(?<!\w)Super\+;(?=\s|$)', '', prose)
    require(not re.search(r'^[ ]?[;!?]', prose),
            f'{where}: missing French narrow non-breaking space before leading punctuation')
    require(not re.search(r'(?<=\S)[:;!?](?=\s|$)', prose),
            f'{where}: missing French non-breaking space before punctuation')
    for match in re.finditer(r'([^\S\n]+)([:;!?])', prose):
        expected = '\u00a0' if match[2] == ':' else '\u202f'
        require(match[1] == expected, f'{where}: French non-breaking space before {match[2]}')
    for match in re.finditer(r'«([^\S\n]*)|([^\S\n]*)»', prose):
        require((match[1] if match[1] is not None else match[2]) == '\u00a0',
                f'{where}: French non-breaking space inside guillemets')
    require(not re.search(r'\d[ \u00a0](?=\d{3}(?:\D|$))', prose),
            f'{where}: French narrow non-breaking thousands separator')


def check_qt(source_path, target_path, language, unchanged=None):
    _, source = qt_messages(source_path)
    root, target = qt_messages(target_path)
    require(root.get('language') == language, f'{target_path}: wrong language')
    require(source.keys() == target.keys(),
            f'{target_path}: missing {source.keys() - target.keys()}, extra {target.keys() - source.keys()}')
    for key, original in source.items():
        where = f'{target_path}: {key}'
        message = target[key]
        require(original.get('numerus') == message.get('numerus'), where + ': changed numerus')
        translation = message.find('translation')
        require(translation is not None, where + ': missing translation')
        require(translation.get('type') not in ('unfinished', 'obsolete', 'vanished'), where + ': unfinished/obsolete')
        forms = translation.findall('numerusform')
        require(len(forms) == (PLURALS[language][0] if original.get('numerus') == 'yes' else 0),
                where + ': missing or extra plural form')
        for form in forms or [translation]:
            text_contract(key[1], form.text or '', where, unchanged)
            if language == 'fr':
                french_typography(form.text or '', where)
    return len(source)


def plural_index(language, n):
    return gettext.c2py(PLURALS[language][1])(n)


def check_po(source_path, target_path, language, unchanged=None):
    source, target = po_messages(source_path), po_messages(target_path)
    require(source.keys() == target.keys(),
            f'{target_path}: missing {source.keys() - target.keys()}, extra {target.keys() - source.keys()}')
    header = target[('', '')].get('msgstr', '')
    require(f'Language: {language}\n' in header, f'{target_path}: wrong language')
    plural = re.search(r'Plural-Forms:\s*nplurals=(\d+);\s*plural=([^\n]+);', header)
    require(plural is not None and int(plural[1]) == PLURALS[language][0], f'{target_path}: wrong plural count')
    select = gettext.c2py(plural[2])
    require(all(select(n) == plural_index(language, n) for n in range(1001)), f'{target_path}: wrong plural rule')
    for key, original in source.items():
        if key == ('', ''):
            continue
        where = f'{target_path}: {key}'
        entry = target[key]
        require(original.get('msgid_plural') == entry.get('msgid_plural'), where + ': changed plural source')
        fields = {name for name in entry if name.startswith('msgstr')}
        if 'msgid_plural' in original:
            require(fields == {f'msgstr[{i}]' for i in range(PLURALS[language][0])}, where + ': missing plural form')
            for i in range(PLURALS[language][0]):
                text_contract(original['msgid'] if i == 0 else original['msgid_plural'], entry[f'msgstr[{i}]'], where, unchanged)
                # Russian's first form also serves 21, 31, ... rather than only 1.
                if language == 'ru' and i == 0:
                    text_contract(original['msgid_plural'], entry['msgstr[0]'], where, unchanged)
                if language == 'fr':
                    french_typography(entry[f'msgstr[{i}]'], where)
        else:
            require(fields == {'msgstr'}, where + ': missing translation')
            text_contract(original['msgid'], entry['msgstr'], where, unchanged)
            if language == 'fr':
                french_typography(entry['msgstr'], where)
    return len(source) - 1


def check_all(base=BASE):
    for language in LANGUAGES:
        unchanged = json.loads((base / f'{language}-unchanged.json').read_text())['messages']
        require(all(isinstance(reason, str) and reason.strip() for reason in unchanged.values()),
                f'{language}: unchanged terms require explanations')
        for domain in DOMAINS:
            directory = base / domain
            if domain != 'cli':
                count = check_qt(directory / 'en.ts', directory / f'{language}.ts', language, unchanged)
                print(f'{language}/{domain}: {count} complete Qt messages')
            count = check_po(directory / f'{domain}.pot', directory / f'{language}.po', language, unchanged)
            print(f'{language}/{domain}: {count} complete gettext messages')


if __name__ == '__main__':
    check_all()
