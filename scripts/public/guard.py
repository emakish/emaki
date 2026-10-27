#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect public trees and history using policy from this trusted installation."""
import base64
import hashlib
import os
import re
import struct
import subprocess
import sys
import unicodedata
import zlib
from pathlib import Path
JOINERS = re.compile('[\\s\\-/#*;\\\'"`+\\\\]+')
XMP_PRIVATE = re.compile("\x47\x50\x53\x28\x3f\x3a\x4c\x61\x74\x69\x74\x75\x64\x65\x7c\x4c\x6f\x6e\x67\x69\x74\x75\x64\x65\x7c\x41\x6c\x74\x69\x74\x75\x64\x65\x7c\x44\x65\x73\x74\x4c\x61\x74\x69\x74\x75\x64\x65\x7c\x44\x65\x73\x74\x4c\x6f\x6e\x67\x69\x74\x75\x64\x65\x29\x7c\x5c\x62\x74\x69\x66\x66\x3a\x28\x3f\x3a\x4d\x61\x6b\x65\x7c\x4d\x6f\x64\x65\x6c\x29\x5c\x62\x7c\x5c\x62\x28\x3f\x3a\x65\x78\x69\x66\x45\x58\x7c\x61\x75\x78\x29\x3a\x28\x3f\x3a\x42\x6f\x64\x79\x53\x65\x72\x69\x61\x6c\x4e\x75\x6d\x62\x65\x72\x7c\x53\x65\x72\x69\x61\x6c\x4e\x75\x6d\x62\x65\x72\x7c\x4c\x65\x6e\x73\x5c\x77\x2a\x29\x5c\x62")
TO_LATIN = str.maketrans("\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u0501\u051b\u051d\u04bb\u04cf\u0261\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0423\u0425\u0406\u0408\u0405\u051a\u051c\u04ba\u04c0\u03b1\u03b2\u03b3\u03b5\u03b9\u03ba\u03bd\u03bf\u03c1\u03c4\u03c5\u03c7\u03c9\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7", 'aeopcyxijsdqwhlgABEKMHOPCTYXIJSQWHIabyeiknoptuxwABEZHIKMNOPTYX')
TO_CYRILLIC = str.maketrans('aeopcyxkAEOPCYXKMHTB', "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u043a\u0410\u0415\u041e\u0420\u0421\u0423\u0425\u041a\u041c\u041d\u0422\u0412")
FILLERS = set('ᅟᅠㅤﾠ')

def clean(text):
    text = unicodedata.normalize('NFKD', unicodedata.normalize('NFKC', text))
    text = ''.join((c for c in text if unicodedata.category(c) not in ('Cf', 'Mn', 'Me') and c not in FILLERS))
    return unicodedata.normalize('NFC', text)

def forms(line):
    if line.isascii():
        return (line,)
    text = clean(line)
    return tuple(dict.fromkeys((line, text, text.translate(TO_LATIN), text.translate(TO_CYRILLIC))))
PNG_PLAIN = {b'IHDR', b'IEND', b'cHRM', b'gAMA', b'sBIT', b'sRGB', b'cICP', b'mDCV', b'cLLI', b'pHYs', b'tIME', b'acTL', b'fcTL'}
PNG_PIXELS = {b'IDAT', b'fdAT', b'PLTE', b'tRNS', b'bKGD', b'hIST', b'sPLT'}
WEBP_PIXELS = {b'VP8 ', b'VP8L', b'ALPH', b'ANMF'}
CAMERA = {271: 'camera make', 272: "\x63\x61\x6d\x65\x72\x61\x20\x6d\x6f\x64\x65\x6c", 42033: 'camera serial number', 42035: 'lens make', 42036: "\x6c\x65\x6e\x73\x20\x6d\x6f\x64\x65\x6c", 42037: 'lens serial number'}
NARROW = re.compile('[^\x00-\x08\x0b-\x1f\x7f-\x9f�]{4,}')
WIDE = re.compile('[ -~Ѐ-ӿ]{4,}')

def strings(data):
    found = NARROW.findall(data.decode('utf-8', 'replace'))
    for start in (0, 1):
        part = data[start:len(data) - (len(data) - start) % 2]
        for encoding in ('utf-16-le', 'utf-16-be'):
            found += WIDE.findall(part.decode(encoding, 'replace'))
    return found

def readable(data):
    even = data[:len(data) // 2 * 2]
    texts = (data.decode('utf-8', 'replace'), even.decode('utf-16-le', 'replace'), even.decode('utf-16-be', 'replace'))
    return '\n'.join((re.sub('[\x00-\t\x0b-\x1f�]+', ' ', text) for text in texts))

def exif(data):
    data = data[6:] if data[:6] == b'Exif\x00\x00' else data
    order = {b'II': '<', b'MM': '>'}.get(data[:2])
    if not order:
        raise ValueError('no TIFF header')
    texts, refusals, seen, todo = ([], [], set(), [(struct.unpack(order + 'I', data[4:8])[0], 'ifd')])
    while todo:
        offset, ifd = todo.pop()
        if not offset or offset in seen:
            continue
        seen.add(offset)
        count, = struct.unpack(order + 'H', data[offset:offset + 2])
        if ifd == 'gps' and count:
            refusals.append('EXIF GPS position: remove the metadata (exiftool -all= FILE)')
        for entry in range(offset + 2, offset + 2 + 12 * count, 12):
            tag, kind, n, value = struct.unpack(order + 'HHI4s', data[entry:entry + 12])
            if tag in (34665, 34853, 40965) and ifd == 'ifd':
                todo.append((struct.unpack(order + 'I', value)[0], 'gps' if tag == 34853 else 'ifd'))
            elif kind in (1, 2, 7):
                start = struct.unpack(order + 'I', value)[0]
                raw = value[:n] if n <= 4 else data[start:start + n]
                if len(raw) != n:
                    raise ValueError('EXIF value outside the block')
                texts.append(readable(raw))
                value = raw.strip(b'\x00 ').decode('latin-1')
                if ifd == 'ifd' and tag in CAMERA and value:
                    refusals.append(f'EXIF {CAMERA[tag]} {value!r}: remove the metadata')
        todo.append((struct.unpack(order + 'I', data[offset + 2 + 12 * count:offset + 6 + 12 * count])[0], ifd))
    return (texts, refusals)

def png(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG file')
    items, pixels, refusals, pos = ([], [], [], 8)
    while pos < len(data):
        length, kind = struct.unpack('>I4s', data[pos:pos + 8])
        start, body, label = (pos + 8, data[pos + 8:pos + 8 + length], kind.decode('latin-1'))
        if len(data[pos + 8:pos + 12 + length]) != length + 4:
            raise ValueError(f'truncated {label} chunk')
        pos += 12 + length
        if kind in PNG_PIXELS:
            pixels.append((start, start + length))
        elif kind == b'tEXt':
            items.append((label, body.replace(b'\x00', b': ', 1).decode('latin-1')))
        elif kind == b'zTXt':
            key, _, text = body.partition(b'\x00')
            items.append((label, f"{key.decode('latin-1')}: {inflate(text[1:]).decode('latin-1')}"))
            pixels.append((start + len(key) + 2, start + length))
        elif kind == b'iTXt':
            key, _, rest = body.partition(b'\x00')
            language, _, rest = rest[2:].partition(b'\x00')
            translated, _, text = rest.partition(b'\x00')
            if body[len(key) + 1]:
                pixels.append((start + length - len(text), start + length))
                text = inflate(text)
            items.append((label, b' '.join((key, language, translated, text)).decode('utf-8', 'replace')))
        elif kind == b'eXIf':
            texts, found = exif(body)
            items.extend(((label, text) for text in texts))
            refusals.extend(found)
        elif kind == b'iCCP':
            key, _, profile = body.partition(b'\x00')
            items.append((label, key.decode('latin-1')))
            items.extend(((label, text) for text in strings(inflate(profile[1:]))))
            pixels.append((start + len(key) + 2, start + length))
        elif kind not in PNG_PLAIN:
            items.append((label, readable(body)))
        if kind == b'IEND':
            return (items, pixels, refusals)
    raise ValueError('no IEND chunk')

def jpeg(data):
    if data[:2] != b'\xff\xd8':
        raise ValueError('not a JPEG file')
    items, pixels, refusals, pos = ([], [], [], 2)
    while True:
        if data[pos] != 255:
            raise ValueError('bad JPEG marker')
        marker = data[pos + 1]
        if marker == 217:
            break
        if marker == 255 or 208 <= marker <= 215 or marker == 1:
            pos += 1 if marker == 255 else 2
            continue
        length, = struct.unpack('>H', data[pos + 2:pos + 4])
        body = data[pos + 4:pos + 2 + length]
        if len(body) != length - 2:
            raise ValueError('truncated JPEG segment')
        pos += 2 + length
        if marker == 254:
            items.append(('COM', body.decode('utf-8', 'replace')))
        elif marker == 225 and body[:6] == b'Exif\x00\x00':
            texts, found = exif(body)
            items.extend((('APP1', text) for text in texts))
            refusals.extend(found)
        elif 224 <= marker <= 239:
            items.append((f'APP{marker - 224}', readable(body)))
        elif marker == 218:
            scan = pos
            while not (data[pos] == 255 and data[pos + 1] not in (0, *range(208, 216))):
                pos += 1
            pixels.append((scan, pos))
    return (items, pixels, refusals)

def webp(data):
    if data[:4] != b'RIFF' or data[8:12] != b'WEBP':
        raise ValueError('not a WebP file')
    items, pixels, refusals, pos = ([], [], [], 12)
    while pos < len(data):
        kind, length = struct.unpack('<4sI', data[pos:pos + 8])
        start, body, label = (pos + 8, data[pos + 8:pos + 8 + length], kind.decode('latin-1').strip())
        if len(body) != length:
            raise ValueError(f'truncated {label} chunk')
        pos += 8 + length + (length & 1)
        if kind in WEBP_PIXELS:
            pixels.append((start, start + length))
        elif kind == b'EXIF':
            texts, found = exif(body)
            items.extend(((label, text) for text in texts))
            refusals.extend(found)
        elif kind == b'ICCP':
            items.extend(((label, text) for text in strings(body)))
        elif kind not in (b'VP8X', b'ANIM'):
            items.append((label, readable(body)))
    return (items, pixels, refusals)

def gif(data):
    if data[:6] not in (b'GIF87a', b'GIF89a'):
        raise ValueError('not a GIF file')

    def blocks(pos):
        start, joined = (pos, bytearray())
        while data[pos]:
            joined += data[pos + 1:pos + 1 + data[pos]]
            pos += 1 + data[pos]
        return (bytes(joined), data[start:pos], pos + 1)
    table = 3 << (data[10] & 7) + 1 if data[10] & 128 else 0
    items, pixels, pos = ([], [(13, 13 + table)], 13 + table)
    while data[pos] != 59:
        if data[pos] == 33:
            label = {254: 'comment', 255: 'application'}.get(data[pos + 1])
            joined, raw, pos = blocks(pos + 2)
            if label:
                items.append((label, readable(joined) + '\n' + readable(raw)))
        elif data[pos] == 44:
            start = pos + 10
            pos += 10 + (3 << (data[pos + 9] & 7) + 1 if data[pos + 9] & 128 else 0)
            pos = blocks(pos + 1)[2]
            pixels.append((start, pos))
        else:
            raise ValueError('bad GIF block')
    return (items, pixels, [])

def tiff(data):
    texts, refusals = exif(data)
    return ([('TIFF', text) for text in texts], [], refusals)
LITERAL = re.compile(b'\\((?:\\\\.|[^\\\\()])*\\)', re.S)
HEX = re.compile(b'(?<!<)<([0-9A-Fa-f\\s]+)>')
ESCAPES = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\x08', b'f': b'\x0c'}

def pdf_text(raw):
    return raw[2:].decode('utf-16-be', 'replace') if raw[:2] == b'\xfe\xff' else raw.decode('latin-1')

def unescape(body):
    out, i = (bytearray(), 0)
    while i < len(body):
        if body[i] != 92:
            out.append(body[i])
            i += 1
            continue
        octal = re.match(b'[0-7]{1,3}', body[i + 1:i + 4])
        if octal:
            out.append(int(octal.group(0), 8) & 255)
            i += 1 + len(octal.group(0))
        else:
            out += ESCAPES.get(body[i + 1:i + 2], body[i + 1:i + 2])
            i += 2
    return bytes(out)

def pdf_strings(data):
    texts = [pdf_text(unescape(m.group(0)[1:-1])) for m in LITERAL.finditer(data)]
    for m in HEX.finditer(data):
        digits = re.sub(b'\\s', b'', m.group(1)).decode()
        texts.append(pdf_text(bytes.fromhex(digits + '0' * (len(digits) % 2))))
    return [text for text in texts if text.strip()]

def pdf(data):
    items, pixels = ([('string', text) for text in pdf_strings(data)], [])
    for m in re.finditer(b'stream\\r?\\n', data):
        end = data.find(b'endstream', m.end())
        if end < 0:
            break
        try:
            body = inflate(data[m.end():end])
        except zlib.error:
            continue
        pixels.append((m.end(), end))
        items.extend((('stream', text) for text in strings(body) + pdf_strings(body)))
    return (items, pixels, [])
READERS = {'.png': png, '.jpg': jpeg, '.jpeg': jpeg, '.webp': webp, '.gif': gif, '.tif': tiff, '.tiff': tiff, '.pdf': pdf}

def reader_of(name, data):
    by_name = READERS.get(os.path.splitext(name)[1].lower())
    if by_name:
        return by_name
    for magic, reader in ((b'\x89PNG\r\n\x1a\n', png), (b'\xff\xd8\xff', jpeg), (b'GIF87a', gif), (b'GIF89a', gif), (b'II*\x00', tiff), (b'MM\x00*', tiff), (b'%PDF-', pdf)):
        if data.startswith(magic):
            return reader
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return webp
    return None
DATA_URI = re.compile('data:[\\w.+/-]*(?:;[\\w.+=-]+)*;base64,([A-Za-z0-9+/=\\s]+)', re.I)

def searched(name, data, depth=0):
    reader = reader_of(name, data)
    if reader:
        items, pixels, refusals = reader(data)
        refusals += [f'{label}: {m.group(0)} in the image metadata: remove the metadata' for label, text in items for m in XMP_PRIVATE.finditer(text)]
        masked = bytearray(data)
        for start, end in pixels:
            masked[start:end] = bytes(end - start)
        lines = [(label, line) for label, text in items for line in text.split('\n')]
        return (lines + [('strings', text) for text in strings(bytes(masked))], refusals)
    if b'\x00' in data:
        return ([('strings', text) for text in strings(data)], [])
    text = data.decode('utf-8', 'replace')
    lines = [(str(number), line.removesuffix('\r')) for number, line in enumerate(text.split('\n'), 1)]
    refusals = []
    if os.path.basename(name) == '.gitattributes' and bytes.fromhex('6578706f72742d7375627374').decode() in text:
        refusals.append("unreviewed archive substitution")
    for m in DATA_URI.finditer(text) if depth < 2 else ():
        number = text.count('\n', 0, m.start()) + 1
        encoded = re.sub('\\s', '', m.group(1))
        try:
            decoded = base64.b64decode(encoded + '=' * (-len(encoded) % 4))
        except ValueError:
            refusals.append(f'{number}: a base64 data: URI that cannot be decoded')
            continue
        inner, found = searched('', decoded, depth + 1)
        lines += [(f'{number}:data:{label}', line) for label, line in inner]
        refusals += [f'{number}:data: {why}' for why in found]
    return (lines, refusals)

import argparse
import shutil
import tempfile
LOCKED = ('bad decrypt', 'maybe wrong password', 'unable to get passphrase', 'interrupted or cancelled',
          'bad password read')
BEGIN = b'-----BEGIN'


def openssl_key(data, form):
    """True when OpenSSL reads data as a private key, or as one that needs a passphrase."""
    result = subprocess.run(['openssl', 'pkey', '-inform', form, '-noout', '-passin', 'pass:'], input=data,
                            capture_output=True, timeout=60)
    if result.returncode == 0:
        return True
    errors = result.stderr.decode(errors='replace')
    return any(word in errors for word in LOCKED)


def with_file(data, check):
    with tempfile.TemporaryDirectory(prefix='emaki-key-scan-') as directory:
        path = Path(directory) / 'candidate'
        path.write_bytes(data)
        path.chmod(0o600)
        return check(Path(directory), path)


def ssh_key(data):
    """True when ssh-keygen reads a private key (a passphrase prompt counts)."""
    if not shutil.which('ssh-keygen'):
        raise OSError('required key reader unavailable')

    def check(_, path):
        result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(path)], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=60)
        return result.returncode == 0 or b'incorrect passphrase' in result.stderr
    return with_file(data, check)


def gpg_secret(data):
    """True when gpg lists a secret key or secret subkey packet in data (armored or binary)."""
    if not shutil.which('gpg'):
        raise OSError('required key reader unavailable')

    def check(directory, path):
        home = directory / 'gnupg'
        home.mkdir(mode=0o700)
        result = subprocess.run(['gpg', '--homedir', str(home), '--batch', '--no-tty', '--no-autostart',
                                 '--pinentry-mode', 'cancel', '--list-packets', str(path)], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=120, env=dict(os.environ, LC_ALL='C'))
        return b':secret key packet:' in result.stdout or b':secret sub key packet:' in result.stdout
    return with_file(data, check)


def trimmed(data):
    """The same text after the usual accidents are undone: CR line ends (CRLF or lone CR) become LF, and
    blanks (spaces, tabs) before and after every line go. Every tool sees this form as well."""
    lines = data.replace(b'\r\n', b'\n').replace(b'\r', b'\n').split(b'\n')
    return b'\n'.join(line.strip(b' \t\f\v') for line in lines)


def keys_in(data):
    found = []
    try:
        data.decode('utf-8')
        text = True
    except UnicodeDecodeError:
        text = False
    if not text or data[:1] == b'\x30':
        if gpg_secret(data):
            found.append('OpenPGP secret key')
        if openssl_key(data, 'DER'):
            found.append('DER private key')
    start = data.find(BEGIN)
    while start != -1:
        candidate = data[start:]
        label = candidate.split(b'\n', 1)[0].strip().decode(errors='replace')
        variants = [candidate]
        if trimmed(candidate) != candidate:
            variants.append(trimmed(candidate))
        if any(openssl_key(variant, 'PEM') or ssh_key(variant) or gpg_secret(variant) for variant in variants):
            found.append(f'key in the block starting "{label}"')
        start = data.find(BEGIN, start + len(BEGIN))
    return found


import json
import posixpath

POLICY_HOME = Path(__file__).resolve().parent
MAX_BYTES = 32 * 1024 * 1024
ATTRIBUTION_VERB = re.compile(r'\b(?:wrote|written|generated|created|reviewed)\b', re.I)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_rules(rules):
    return json.dumps(rules, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def load_rules(rules, policy):
    """Authenticate the complete external rule set before compiling any expression."""
    try:
        expected = policy.get('rules_sha256')
        if ('expressions' in policy or not isinstance(expected, str)
                or not re.fullmatch('[0-9a-f]{64}', expected)
                or digest(canonical_rules(rules)) != expected):
            raise ValueError
        required = {'expressions', 'guards', 'core'}
        if (not isinstance(rules, dict) or not required <= rules.keys()
                or rules.keys() - required - {'copyright', 'spdx'}):
            raise ValueError
        expressions = rules['expressions']
        names = {'strict', 'secret', 'ambiguous', 'internal', 'path', 'attribution'}
        if not isinstance(expressions, dict) or expressions.keys() != names:
            raise ValueError
        if any(not isinstance(value, str) or not value for value in expressions.values()):
            raise ValueError
        if not isinstance(rules['core'], str) or not rules['core']:
            raise ValueError
        if not isinstance(rules['guards'], list) or len(rules['guards']) < 3:
            raise ValueError
        guards = []
        for entry in rules['guards']:
            if not isinstance(entry, dict) or entry.keys() != {'expression', 'flags', 'skip'}:
                raise ValueError
            if not isinstance(entry['expression'], str) or not entry['expression']:
                raise ValueError
            if type(entry['flags']) is not int or entry['flags'] & ~int(re.I | re.M | re.U):
                raise ValueError
            if not isinstance(entry['skip'], list) or any(not isinstance(path, str) for path in entry['skip']):
                raise ValueError
            guards.append((re.compile(entry['expression'], entry['flags']), tuple(entry['skip'])))
        return ({key: re.compile(value, re.I | re.M) for key, value in expressions.items()},
                guards, re.compile(rules['core'], re.I))
    except (TypeError, ValueError, KeyError, re.error):
        # Parser and regex diagnostics can contain private input.
        raise ValueError('Required rule set is unavailable or invalid') from None


def git(repo, *args):
    # No checkout, filters, hooks, configuration includes or candidate programs.
    env = dict(((key, value) for key, value in os.environ.items() if not key.startswith('GIT_')), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0')
    for key in list(env):
        if key.startswith('GIT_CONFIG_') and key not in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM'):
            del env[key]
    return subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                           '-C', str(repo), *args], env=env, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout


class Guard:
    def __init__(self, origin='ours', policy=None, rules=None):
        policy_bytes = (POLICY_HOME / 'policy.json').read_bytes()
        self.policy = policy or json.loads(policy_bytes)
        self.policy_digest = digest(policy_bytes)
        self.origin = origin
        self.approved_head = None
        self.approved_infrastructure = False
        self.findings = []
        self.reviewed = set()
        self.seen = set()
        supplied = json.loads((POLICY_HOME / 'rules.local.json').read_bytes()) if rules is None else rules
        self.rules, self.guards, self.core = load_rules(supplied, self.policy)
        self.lines = {(path, value) for path, values in self.policy['lines'].items() for value in values}
        self.credentials = {tuple(value) for value in self.policy['credentials']}
        baseline_bytes = (POLICY_HOME / 'reviewed-baseline.json').read_bytes()
        baseline = json.loads(baseline_bytes)
        if baseline['baseline'] != self.policy['baseline']:
            raise ValueError('reviewed baseline does not match policy')
        self.baseline_digest = digest(baseline_bytes)
        self.baseline_lines, self.baseline_paths = set(), set()
        for group in baseline['groups']:
            if not group['reason'] or group['status'] != 'owner-decision-pending':
                raise ValueError('reviewed baseline requires a reason and decision status')
            for item in group['lines']:
                if not re.fullmatch('[0-9a-f]{64}', item['sha256']):
                    raise ValueError('reviewed baseline requires exact line fingerprints')
                entries = self.baseline_paths if item['label'] == 'path' else self.baseline_lines
                entries.add((item['path'], item['sha256']))

    def add(self, rule, path, line='', structural=False, ambiguous=False):
        level = 'BLOCK' if structural or (self.origin == 'ours' and not ambiguous) else 'FLAG'
        entry = {'level': level, 'rule': rule, 'path': path, 'line': str(line)}
        if entry not in self.findings:
            self.findings.append(entry)

    def text(self, path, label, line, exceptions=True, commit_message=False):
        exact = (path, digest(line.encode('utf-8', 'surrogateescape')))
        baseline = exceptions and exact in (self.baseline_paths if label == 'path' else self.baseline_lines)
        permitted = exceptions and (exact in self.lines or baseline)
        if commit_message and line in {'Signed-off-by: ' + identity for identity in self.policy['identities']}:
            permitted = True
        variants = forms(line)
        for value in variants:
            if self.rules['strict'].search(value):
                self.add('strict', path, label, structural=True)
            if self.rules['secret'].search(value) and exact not in self.credentials:
                self.add('secret', path, label, structural=True)
            internal = self.rules['internal'].search(value)
            if not internal and ATTRIBUTION_VERB.search(value):
                internal = self.rules['attribution'].search(value)
            if internal and not baseline:
                self.add('internal', path, label)
            if self.core.search(value) and not baseline:
                self.add('product-review', path, label, ambiguous=True)
            if self.rules['ambiguous'].search(value) and not permitted:
                self.add('ambiguous', path, label, ambiguous=True)
            for expression, skipped in self.guards:
                if path not in skipped and expression.search(value) and not permitted:
                    # A short technical term asks for review; longer contextual hits block.
                    hits = list(expression.finditer(value))
                    serious = any(not self.rules['ambiguous'].fullmatch(m.group()) for m in hits)
                    if serious:
                        product = all(any(pattern.fullmatch(m.group()) for pattern, _ in self.guards[1:3])
                                      for m in hits)
                        self.add('wording', path, label, ambiguous=product)
        if permitted:
            self.reviewed.add(exact)

    def content(self, path, data, commit_message=False):
        if len(data) > MAX_BYTES:
            self.add('size', path, structural=True)
            return
        try:
            if keys_in(data):
                self.add('private-key', path, structural=True)
        except (OSError, subprocess.TimeoutExpired):
            self.add('key-reader-failed', path, structural=True)
            return
        try:
            lines, refusals = searched(path, data)
        except (ValueError, IndexError, struct.error, zlib.error, RecursionError):
            self.add('metadata-invalid', path, structural=True)
            return
        for refusal in refusals:
            self.add('metadata-private', path, structural=True)
        for label, line in lines:
            self.text(path, label, line, commit_message=commit_message)
        if b'\0' not in data and not reader_of(path, data):
            reviewed_lines = self.lines | self.baseline_lines
            pieces = [(JOINERS.sub('', clean(line).translate(TO_LATIN)),
                       (path, digest(line.removesuffix('\r').encode())) in reviewed_lines)
                      for line in data.decode('utf-8', 'replace').split('\n')]
            # Literal product terms already requested review above; retain detection of
            # concealed terms assembled across separators and line boundaries.
            joined = ''.join(JOINERS.sub('', clean(self.core.sub('', line)).translate(TO_LATIN))
                             for line, (_, covered) in zip(data.decode('utf-8', 'replace').split('\n'), pieces)
                             if not covered)
            if self.core.search(joined):
                self.add('split', path)
            whole = ''.join(value for value, covered in pieces)
            mask = ''.join(('1' if covered else '0') * len(value) for value, covered in pieces)
            overlap = re.compile(f'(?=({self.core.pattern}))', self.core.flags)
            if any(len(set(mask[m.start(1):m.end(1)])) == 2 for m in overlap.finditer(whole)):
                self.add('split-boundary', path)
        if reader_of(path, data) or b'\0' in data:
            if self.policy['binary'].get(path) != digest(data):
                self.add('binary-review', path, structural=True)

    def path(self, name, mode):
        if name not in self.policy['paths']:
            self.add('path-review', name, structural=True)
        parts = name.split('/')
        if any(part in ('', '.', '..') for part in parts) or any(ord(c) < 32 or ord(c) == 127 for c in name):
            self.add('path-invalid', name, structural=True)
            return
        if 'rules.local.json' in parts or self.rules['path'].search(name):
            self.add('path-private', name, structural=True)
        parent = posixpath.dirname(name)
        suffix = Path(name).suffix
        if parent not in self.policy['directories']:
            self.add('directory', name, structural=True)
        if not parent and name not in self.policy['top_files']:
            self.add('top-file', name, structural=True)
        if suffix not in self.policy['kinds'].get(parent, []):
            self.add('file-kind', name, structural=True)
        if mode not in ('100644', '100755', '120000'):
            self.add('mode', name, structural=True)
        self.text(name, 'path', name)

    def blob(self, name, mode, data):
        key = (name, mode, digest(data))
        if key in self.seen:
            return
        self.seen.add(key)
        self.path(name, mode)
        if mode == '120000':
            target = data.decode('utf-8', 'replace')
            self.text(name, 'link', target)
            if self.policy['links'].get(name) != target:
                self.add('link-review', name, structural=True)
            return
        if self.protected(name):
            expected = POLICY_HOME / name.removeprefix('scripts/public/')
            trusted = (expected.is_file() and expected.read_bytes() == data) if name.startswith('scripts/public/') else self.policy.get('infrastructure', {}).get(name) == digest(data)
            if not trusted and not self.approved_infrastructure:
                self.add('infrastructure', name, structural=True)
        self.content(name, data)

    @staticmethod
    def protected(name):
        return name.startswith(('.github/', 'scripts/public/')) or name in (
            '.gitattributes', '.gitmodules', 'scripts/make-public.sh',
            'tests/test-public-guard.py', 'tests/test-public-ci.py')

    def tree(self, root):
        root = root.resolve()
        if not root.is_dir():
            raise ValueError('tree must be a directory')
        # Include ignored and untracked material; only this checkout's metadata is omitted.
        for directory, directories, files in os.walk(root, followlinks=False):
            here = Path(directory)
            if here == root:
                directories[:] = [name for name in directories if name != '.git']
                files = [name for name in files if name != '.git']
            for name in list(directories):
                if (here / name).is_symlink():
                    files.append(name)
                    directories.remove(name)
            for leaf in files:
                path = here / leaf
                name = path.relative_to(root).as_posix()
                if path.is_symlink():
                    self.blob(name, '120000', os.fsencode(os.readlink(path)))
                elif path.is_file():
                    if path.stat().st_size > MAX_BYTES:
                        self.add('size', name, structural=True)
                        continue
                    self.blob(name, '100755' if path.stat().st_mode & 0o111 else '100644', path.read_bytes())
                else:
                    self.add('file-type', name, structural=True)

    def revision(self, repo, revision):
        records = git(repo, 'ls-tree', '-r', '-z', revision).split(b'\0')
        for record in records:
            if not record:
                continue
            meta, raw = record.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            name = os.fsdecode(raw)
            if kind != 'blob':
                self.add('object-kind', name, structural=True)
                continue
            if (name, mode, oid) in self.seen:
                continue
            self.seen.add((name, mode, oid))
            size = int(git(repo, 'cat-file', '-s', oid))
            if size > MAX_BYTES:
                self.add('size', name, structural=True)
                continue
            self.blob(name, mode, git(repo, 'cat-file', 'blob', oid))

    def history(self, repo, base, head):
        if self.approved_head is not None and self.approved_head != head:
            raise ValueError('reviewed infrastructure head does not match')
        self.approved_infrastructure = self.approved_head == head
        for value in (base, head):
            if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
                raise ValueError('base and head must be full object identifiers')
        graft = Path(os.fsdecode(git(repo, 'rev-parse', '--git-path', 'info/grafts')).strip())
        if not graft.is_absolute():
            graft = repo / graft
        if (graft.exists() and graft.stat().st_size) or git(repo, 'for-each-ref', '--format=%(refname)', 'refs/replace').strip():
            raise ValueError('history overrides require removal before inspection')
        git(repo, 'merge-base', '--is-ancestor', base, head)
        commits = git(repo, 'rev-list', '--reverse', f'{base}..{head}').decode().splitlines()
        self.revision(repo, head)
        for commit in commits:
            raw = git(repo, 'cat-file', 'commit', commit)
            metadata, _, message = raw.partition(b'\n\n')
            for field in (b'author ', b'committer '):
                values = [line[len(field):].decode('utf-8', 'replace')
                          for line in metadata.splitlines() if line.startswith(field)]
                if len(values) != 1:
                    self.add('identity', commit, structural=True)
                    continue
                identity = values[0].rsplit(' ', 2)[0]
                self.text(commit, field.decode().strip() + '-date', ' '.join(values[0].rsplit(' ', 2)[1:]), exceptions=False)
                if self.origin == 'ours' and identity not in self.policy['identities']:
                    self.add('identity', commit, field.decode().strip(), structural=True)
                elif self.origin == 'outside':
                    self.text(commit, field.decode().strip(), identity, exceptions=False)
            self.content('commit:' + commit, message, commit_message=True)
            self.revision(repo, commit)
        before = set(git(repo, 'ls-tree', '-r', '--name-only', '-z', base).split(b'\0'))
        after = set(git(repo, 'ls-tree', '-r', '--name-only', '-z', head).split(b'\0'))
        for raw in before - after:
            name = os.fsdecode(raw)
            if self.protected(name):
                self.add('infrastructure-deleted', name, structural=True)

    def result(self):
        levels = {entry['level'] for entry in self.findings}
        state = 'BLOCK' if 'BLOCK' in levels else 'FLAG' if levels else 'PASS'
        return {'version': 1, 'result': state, 'origin': self.origin,
                'policy_sha256': self.policy_digest,
                'reviewed_baseline_sha256': self.baseline_digest,
                'findings': self.findings,
                'reviewed_lines': len(self.reviewed)}


def inflate(data):
    output = zlib.decompressobj().decompress(data, MAX_BYTES + 1)
    if len(output) > MAX_BYTES:
        raise ValueError('compressed metadata exceeds the limit')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--repo', type=Path)
    mode.add_argument('--tree', type=Path)
    parser.add_argument('--base')
    parser.add_argument('--head')
    parser.add_argument('--approved-infrastructure-head')
    parser.add_argument('--origin', choices=('ours', 'outside'), default='ours')
    parser.add_argument('--pr-title-file', type=Path)
    parser.add_argument('--pr-body-file', type=Path)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--rules-stdin', action='store_true')
    args = parser.parse_args()
    try:
        rules = None
        if args.rules_stdin:
            raw = sys.stdin.buffer.read(128 * 1024 + 1)
            if not raw or len(raw) > 128 * 1024:
                raise ValueError
            rules = json.loads(raw)
            if not isinstance(rules, dict):
                raise ValueError
        guard = Guard(args.origin, rules=rules)
    except (OSError, ValueError, TypeError, KeyError):
        result = {'version': 1, 'result': 'BLOCK', 'origin': args.origin,
                  'findings': [{'level': 'BLOCK', 'rule': 'rules-unavailable',
                                'path': 'input', 'line': ''}], 'reviewed_lines': 0}
        rendered = json.dumps(result, indent=2) + '\n'
        if args.json:
            args.json.write_text(rendered)
        print(rendered, end='')
        return 1
    guard.approved_head = args.approved_infrastructure_head
    try:
        if args.tree and guard.approved_head:
            raise ValueError('infrastructure approval requires an exact Git head')
        if args.repo:
            if not args.base or not args.head:
                raise ValueError('--repo requires --base and --head')
            guard.history(args.repo, args.base, args.head)
        else:
            guard.tree(args.tree)
        for label, file in (('pr-title', args.pr_title_file), ('pr-body', args.pr_body_file)):
            if file:
                guard.content(label, file.read_bytes())
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        guard.add('inspection-failed', 'input', type(error).__name__, structural=True)
    result = guard.result()
    rendered = json.dumps(result, ensure_ascii=True, indent=2) + '\n'
    if args.json:
        args.json.write_text(rendered)
    print(rendered, end='')
    return {'PASS': 0, 'BLOCK': 1, 'FLAG': 2}[result['result']]


if __name__ == '__main__':
    sys.exit(main())
