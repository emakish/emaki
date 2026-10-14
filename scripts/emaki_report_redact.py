# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline redaction shared by report previews and saved diagnostics."""

import ipaddress
import os
import pwd
import re
import socket
from urllib.parse import unquote


CATEGORIES = ("user", "host", "email", "ip", "mac", "serial", "secret", "lines")
_VALUE = r'''(?:"(?:\\.|[^"\\])*(?:"|\Z)|'(?:\\.|[^'\\])*(?:'|\Z)|[^\s,;\}\]\"']+)'''
# Schemes can carry a parameter list instead of a single credential token.
_AUTH_VALUE = r"(?:[\w*-]+[ \t]*=[^\r\n]+|" + _VALUE + r")"
_HEADER = re.compile(
    r'''(?i)(?<![\w-])(?P<quote>["']?)(?:(?:Proxy-|X-)?Authorization|(?:Set-|X-)?Cookie2?)(?P<key_quote>["']?)[ \t]*:[ \t]*'''
    + r'''(?P<value>"(?:\\.|[^"\\])*(?:"|\Z)|'(?:\\.|[^'\\])*(?:'|\Z)|[^\r\n]+)'''
)
_SECRET_NAME = r"(?:[a-z0-9]+[-_])*(?:password|passwd|secret|token|key|apikey|auth|authorization|cookie|privatekey)(?:[-_][a-z0-9]+)*"
_ASSIGNMENT = re.compile(
    r'''(?ix)(?<![\w-])(?:--)?["']?''' + _SECRET_NAME
    + r'''["']?\s*(?P<operator>:=|=|:)\s*(?P<value>''' + _VALUE + r")"
)
_ARGUMENT = re.compile(
    r"(?i)(?<![\w-])--(?:password|passwd|token|secret|api-key|key)(?:[ \t]+|=)"
    + r"(?P<value>" + _VALUE + r")"
)
_SERIAL = re.compile(
    r'''(?ix)(?:\b(?:ID_(?:USB_)?SERIAL(?:_SHORT)?|serial(?:[\s_-]*(?:number|no))?|s/n)["']?\s*(?:=|:)\s*|\bserial[ \t_-]+(?:number|no)[ \t]+)(?P<value>'''
    + _VALUE + r")"
)
_PEM_KIND = r"(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?"
_PEM_END = re.compile(r"-----END " + _PEM_KIND + r"-----", re.I)
_JOURNAL_PREFIX = re.compile(
    r"^(?:\d{4}-\d\d-\d\dT\S+|[A-Z][a-z]{2}\s+\d+\s+\d\d:\d\d:\d\d) "
    r"(?P<host>\S+) (?P<source>\S+):[ \t]*"
)
_COMMON_HOSTS = {"emaki", "localhost", "archlinux", "linux"}
_ANSI_STYLE = re.compile(r'\x1b\[[0-9;]*m')
_BLOCK_COLUMNS = set("NAME KNAME PATH MAJ:MIN RM SIZE RO TYPE MOUNTPOINT MOUNTPOINTS SERIAL MODEL VENDOR TRAN ROTA".split())
_BLOCK_NAME = re.compile(
    r"[└├─│]*(?:/dev/)?(?:[shv]d[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+|"
    r"loop\d+|sr\d+|dm-\d+|md\d+|zram\d+)(?:p?\d+)?"
)



# Only complete, recognizable diagnostic phrases exempt credential words from
# the residual check; adding unrelated text cannot turn an unsafe line safe.
_SAFE_DIAGNOSTIC = re.compile(
    r"(?i)(?:(?:Un)?expected token [`'\"](?:[{}();:]|identifier)[`'\"]?(?: or [`'\"][{}();:][`'\"])?|ReferenceError: password is not defined|"
    r"\d+ incorrect password attempts|(?:token: expected|password: is not defined)|"
    r"secret: service unavailable|token: (?:none|expired|required)|secret: (?:Unavailable|NotFound)|"
    r"gkd_secret_service_get_objects: assertion (?:'[^'\r\n]*' )?failed|"
    r"password is not defined|secret_service_lookup: not found|password: authentication failure|"
    r"Authorization required|authorization failed: polkit denied|Cookie jar empty|The Authorization header is missing|"
    r"key-mgmt[=:][ \t]*wpa-psk|token: unexpected end|password: incorrect|"
    r"Password: Required|Authorization: denied|password not shown, host fine|password: is|service: loading credentials|USB Serial support registered(?: for generic)?)(?=$|[\r\n]| ; TTY=)"
)
_RISK_NAME = (r"(?:[a-z0-9]+[-_])*(?:password|passwd|secret|token|api[-_]key|apikey|"
              r"access[-_]key|private[-_]key|privatekey|auth|authorization|cookies?2?|wireguard|"
              r"passphrase|pwd|credentials|psk|presharedkey|privkey|secretkey|accesstoken|clientsecret|passwort)"
              r"(?:[-_][a-z0-9]+)*")
_RISK_IDENTIFIER = re.compile(r"(?i)(?<![\w-])" + _RISK_NAME + r"(?![\w-])")


def residual_risk(line):
    """Fail closed when recognizable sensitive syntax survived replacement."""
    line = _SAFE_DIAGNOSTIC.sub('', line)
    # PWD is also sudo's working-directory field, not a credential there.
    line = re.sub(r"\bPWD=(?=/)", '=', line)
    if re.search(r"\bID_SERIAL=\[redacted serial\][ \t]+(?!ID_(?:USB_)?SERIAL(?:_SHORT)?=\[redacted serial\])\S", line):
        return True
    if re.search(r"[\w.+-]+@\[\[redacted ip\]\]", line):
        return True
    for address in re.finditer(r"(?:\d{1,3}\.){3}\d{1,3}", line):
        before = line[:address.start()]
        if (re.search(r"\b(?:addr|dhcp range)[ _:=]+$", before, re.I)
                or re.search(r"[a-zA-Z]\.$|ip_$", before)
                or any(len(n) > 1 and n.startswith('0') for n in address.group().split('.'))):
            return True
    if re.search(r"(?i)(?:\bpeer-|sshd@)[^\r\n]*(?:\d{1,3}\.){3}\d{1,3}|(?:\d{1,3}\.){4}\d+[ \t]+>", line):
        return True
    if re.search(r"\bhost-(?:\d{1,3}-){3}\d{1,3}\.|\b[a-z]+[0-9a-f]+:[0-9a-f]*::[0-9a-f]+", line, re.I):
        return True
    if set(line.upper().split()) <= _BLOCK_COLUMNS and "SERIAL" in line.upper().split():
        return False
    line = re.sub(r"(?i)(?:serial(?:[ \t_-]*(?:number|no))?|ID_(?:USB_)?SERIAL(?:_SHORT)?)[\"']?[ \t:=]+\[redacted serial\]", '', line)
    # A replaced value certifies only its field, never another field on the line.
    # Adjacent fragments after a closed value remain ambiguous and are refused.
    if re.search(r"[=:][ \t]*\[redacted secret\][,;}\"\']\w|[=:][ \t]*\[redacted secret\][a-z0-9]", line, re.I):
        return True
    line = re.sub(r"(?i)(?<![\w-])(?:--)?[\"']?" + _RISK_NAME
                  + r"[\"']?[ \t]*(?::=|=|:|[ \t])[ \t]*\[redacted secret\]", '', line)
    line = re.sub(r"\[redacted (?:user|host|email|ip|mac|serial|secret)\]", '', line)
    if re.search(r"(?i)\b(?:connector[ \t]+[A-Z]+-\d+:|Output[ \t]+\")[^\r\n]*[ \t](?=[A-Z0-9]*\d)[A-Z0-9]{6,}(?:\"|$)", line):
        return True
    if _RISK_IDENTIFIER.search(line):
        return True
    for blob in re.finditer(r"(?<![\w/+])[A-Za-z0-9+/]{32,}={0,2}(?![\w/+])|(?<![\w/+])[A-Za-z0-9+/]{16,}={1,2}(?![\w/+])", line):
        if '/' not in blob.group() or credential_like(blob.group()):
            return True
    return bool(re.search(
        r"(?ix)(?:"
        r"(?:[a-f0-9]{1,2}[:-]){5,}[a-f0-9]{1,2}|(?:[a-f0-9]{2}_){5}[a-f0-9]{2}|"
        r"\bmac[ \t]+[a-f0-9]{12}\b|\b(?:ghp_|glpat-|xox[baprs]-|sk-|AKIA)[a-z0-9_-]+|"
        r"\b(?:iSerial|ID_WWN|UUID|sn|serial|machine-id)\b[ \t:=]+(?!unavailable\b)[a-z0-9]|"
        r"disk[/-]by(?:-|\\x2d)id|\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b|"
        r"\b(?:jwt|Bearer=|Private-Lines|PuTTY-User-Key-File)|"
        r"\bmysql[ \t]+-p\b|\bcurl[ \t]+-u\b|"
        r"\\u0040|\b[a-z0-9]+[ \t]+at[ \t]+[a-z0-9]+[ \t]+dot[ \t]+[a-z]+|"
        r"\bLU[ \t]+WWN[ \t]+Device[ \t]+Id|PRIVATE[ \t]+KEY|(?:[a-f0-9]{2}\\:){5}[a-f0-9]{2}"
        r")", line))


def email_domain(domain):
    """Require a dotted mail domain, excluding systemd unit instance names."""
    return (re.search(r'\.[a-z]+$', domain, re.I)
            and not re.search(r'\.(?:service|slice|socket|timer|mount)(?:\.(?:d|wants))?$', domain, re.I)
            and not re.fullmatch(r'\d+\.(?:service|slice|socket|timer|mount)\.d', domain, re.I))


def credential_like(value):
    """Disambiguate bare colon labels from ordinary diagnostic words."""
    if len(value) < 8 or re.search(r'\s', value):
        return False
    return bool(
        (re.search(r'[a-z]', value, re.I) and re.search(r'\d', value))
        or re.fullmatch(r'[a-f0-9]+', value, re.I)
        or (re.fullmatch(r'[A-Za-z0-9+/_-]+={0,2}', value)
            and re.search(r'[a-z]', value) and re.search(r'[A-Z]', value))
    )


def partial_key_spans(text):
    """Find PEM tails, retaining records interleaved from other journal sources."""
    lines = text.splitlines(keepends=True)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    for index, line in enumerate(lines):
        end = _PEM_END.search(line)
        if end is None:
            continue
        prefix = _JOURNAL_PREFIX.match(line)
        source = prefix.groups() if prefix else None
        start, stop = index, offsets[index] + end.end()
        for previous in range(index - 1, -1, -1):
            prior_prefix = _JOURNAL_PREFIX.match(lines[previous])
            if source and prior_prefix and prior_prefix.groups() != source:
                if offsets[start] < stop:
                    yield offsets[start], stop
                stop = offsets[previous]
                start = previous
                continue
            payload = _JOURNAL_PREFIX.sub('', lines[previous]).strip()
            if not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}|=[A-Za-z0-9+/]{4}", payload):
                break
            start = previous
        if offsets[start] < stop:
            yield offsets[start], stop


class Redactor:
    """Replace sensitive spans and count occurrences, without reading files or DNS."""

    def __init__(self, user=None, host=None):
        self.removed = dict.fromkeys(CATEGORIES, 0)
        if user is None:
            users = {os.environ.get("USER", ""), os.environ.get("LOGNAME", "")}
            try:
                users.add(pwd.getpwuid(os.getuid()).pw_name)
            except KeyError:
                pass
        else:
            users = {user}
        hosts = {socket.gethostname()} if host is None else {host}
        hosts.update(name.split(".")[0] for name in tuple(hosts))
        self.identities = {
            category: {name for name in names if name and name.casefold() not in _COMMON_HOSTS}
            for category, names in (("user", users), ("host", hosts))
        }

    def redact(self, text):
        original = text
        # Encoded identities remain unsafe even when their plain spelling would
        # have been replaced. Decode only for detection, never for presentation.
        encoded_lines = set()
        for line in text.splitlines(keepends=True):
            decoded = unquote(line)
            if decoded != line and (residual_risk(decoded) or any(
                    re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", decoded, re.I)
                    for names in self.identities.values() for name in names)
                    or re.search(r"(?:\d{1,3}\.){3}\d{1,3}", decoded)):
                encoded_lines.add(line)
        if encoded_lines:
            self.removed["lines"] += sum(line in encoded_lines for line in text.splitlines(keepends=True))
            original = text = ''.join(line for line in text.splitlines(keepends=True) if line not in encoded_lines)
        positions = None
        if _ANSI_STYLE.search(text):
            # Styling is transparent to matching. Map only sensitive spans back
            # to the original so ordinary diagnostics keep their formatting.
            positions = []
            chunks = []
            start = 0
            for style in _ANSI_STYLE.finditer(original):
                chunks.append(original[start:style.start()])
                positions.extend(range(start, style.start()))
                start = style.end()
            chunks.append(original[start:])
            positions.extend(range(start, len(original)))
            text = ''.join(chunks)
        candidates = []

        def add(start, end, category, priority):
            if start < end:
                candidates.append((priority, start, end, category))

        def matches(pattern, category, priority, group=0):
            for match in re.finditer(pattern, text):
                add(*match.span(group), category, priority)

        # A truncated journal can contain a key without its closing delimiter.
        matches(r"(?is)-----BEGIN " + _PEM_KIND + r"-----.*?(?:-----END " + _PEM_KIND + r"-----|\Z)", "secret", 0)
        for start, end in partial_key_spans(text):
            add(start, end, "secret", 0)
        for match in _HEADER.finditer(text):
            start, end = match.span('value')
            if text[start:end].strip().casefold() in {'denied', 'required'}:
                continue
            # A shell argument quotes the entire header; a dictionary quotes its key.
            if match['quote'] and not match['key_quote']:
                closing = re.search(r'(?<!\\)' + match['quote'], text[start:end])
                if closing:
                    end = start + closing.start()
            if text[start:end].strip().casefold() in {'bearer', 'basic', 'token'}:
                continuation = re.match(r"\r?\n[^\r\n]+", text[end:])
                if continuation:
                    end += continuation.end()
            add(start, end, "secret", 1)
        matches(r"(?i)\bBearer[ \t]+(?P<value>" + _AUTH_VALUE + r")", "secret", 1, "value")
        matches(_ARGUMENT, "secret", 1, "value")
        for match in _ASSIGNMENT.finditer(text):
            value = match.group('value')
            key_name = re.split(r"[=:]", match.group(), maxsplit=1)[0].strip(" \t\"'").casefold()
            if key_name == 'key-mgmt':
                continue
            if key_name == 'key' and value.strip("\"'") in {'Escape', 'Return', 'Enter', 'Tab', 'Space'}:
                continue
            if key_name == 'key' and value.strip("\"'").startswith(('XF86', 'KEY_')):
                continue
            # Equals assignments and quoted values are explicit credentials;
            # bare colon labels must also have a credential-shaped value.
            if match['operator'] == ':' and value.casefold() in {'required', 'expired', 'unavailable', 'notfound'}:
                continue
            if match['operator'] == ':' and value[0] not in "\"'" and (not credential_like(value) or value.startswith(('XF86', 'KEY_'))):
                continue
            add(*match.span('value'), "secret", 1)
        matches(_SERIAL, "serial", 2, "value")
        # niri prints one combined make/model/serial string without field boundaries.
        matches(r'(?im)^.*\bniri(?:\b|::)[^\n]*?\b(?:new connector|connector changed):[^\n]*?\b[A-Za-z][A-Za-z0-9-]*-\d+[ \t]+(?P<value>"[^\n]*"|"[^"\n]*(?=\n|\Z))', "serial", 2, "value")
        matches(r'(?im)^.*\bniri(?:\b|::)[^\n]*?\boutput[ \t]+(?P<value>"[^"\n]*[ \t][^\n]*")(?=[ \t]*\{)', "serial", 2, "value")
        for match in re.finditer(r"(?i)(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)*", text):
            domain = match.group().rsplit('@', 1)[1]
            if email_domain(domain):
                add(*match.span(), "email", 3)
        for match in re.finditer(r"(?i)(?<![\w.%+-])(?:[\w.!#$'*+/^`{|}~-]|%(?!40)[0-9a-f]{2})+%40(?P<domain>(?:[\w-]|%[0-9a-f]{2})+(?:(?:\.|%2e)(?:[\w-]|%[0-9a-f]{2})+)*)", text):
            domain = re.sub(r'%([0-9a-f]{2})', lambda part: chr(int(part[1], 16)), match['domain'], flags=re.I)
            if email_domain(domain):
                add(*match.span(), "email", 3)
        matches(r"(?i)(?<![\w:.-])(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}(?![0-9a-f]|[:-][0-9a-f]{2})", "mac", 4)
        matches(r"(?i)(?<![\w.])(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}(?![\w.])", "mac", 4)
        matches(r"(?i)\b(?:wlx|enx)(?P<value>[0-9a-f]{12})(?![0-9a-f])", "mac", 4, "value")
        matches(r"(?i)dev_(?P<value>(?:[0-9a-f]{2}_){5}[0-9a-f]{2})(?![0-9a-f])", "mac", 4, "value")
        for match in re.finditer(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)", text):
            # Package releases and explicitly labelled versions are not addresses.
            before = text[text.rfind('\n', 0, match.start()) + 1:match.start()]
            if (re.match(r"-\d+(?:\.\d+){0,2}(?![\w.])", text[match.end():])
                    or re.search(r"[a-zA-Z][\w+.-]*-$", before)
                    or re.search(r"(?i)\bversion[ \t:=]+$", before)):
                continue
            try:
                ipaddress.IPv4Address(match.group())
            except ValueError:
                continue
            add(*match.span(), "ip", 5)
        for match in re.finditer(r"(?i)(?<![\w:])[0-9a-f:.]*:[0-9a-f:.]+(?:%[\w.-]+)?(?![\w:])", text):
            value = match.group().rstrip(".")
            if value == "::" and not (
                    text[max(0, match.start() - 1):match.start()] == '['
                    or re.search(r"(?i)\b(?:address|addr|ip|ipv6)[ \t=:]+$", text[:match.start()])):
                continue
            try:
                ipaddress.IPv6Address(value)
            except ValueError:
                continue
            add(match.start(), match.start() + len(value), "ip", 4)
        # Support plain tabular lsblk output as well as its JSON and key=value forms.
        columns = None
        offset = 0
        for line in text.splitlines(keepends=True):
            fields = list(re.finditer(r"\S+", line))
            names = [field.group().upper() for field in fields]
            if "SERIAL" in names and "NAME" in names and set(names) <= _BLOCK_COLUMNS:
                columns = names
            elif (columns is not None and columns[-1] == "SERIAL" and "MODEL" in columns
                  and len(fields) >= len(columns) and _BLOCK_NAME.fullmatch(fields[0].group())):
                field = fields[-1]
                add(offset + field.start(), offset + field.end(), "serial", 2)
            elif columns is not None and len(fields) == len(columns):
                name = fields[columns.index("NAME")].group()
                is_row = _BLOCK_NAME.fullmatch(name)
                if "TYPE" in columns:
                    kind = fields[columns.index("TYPE")].group()
                    is_row = re.fullmatch(r"[\w/└├─│-]+", name) and kind in {
                        "disk", "part", "rom", "loop", "crypt", "lvm", "raid0", "raid1",
                        "raid5", "raid6", "raid10", "mpath",
                    }
                if is_row:
                    field = fields[columns.index("SERIAL")]
                    add(offset + field.start(), offset + field.end(), "serial", 2)
                else:
                    columns = None
            else:
                columns = None
            offset += len(line)
        for category, names in self.identities.items():
            for name in names:
                boundary = r"[\w-]" if category == "host" else r"\w"
                suffix = r"(?:-\d+)?" if category == "host" else ""
                forms = {name}
                if category == "user":
                    forms.add(name.replace('-', r'\x2d'))
                domain = r"(?:\.(?!(?:mount|scope|slice|home)\b)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*"
                for form in forms:
                    matches(r"(?i)(?<!" + boundary + ")" + re.escape(form) + suffix
                            + domain + "(?!" + boundary + ")", category, 6)
        accepted = []
        occupied = bytearray(len(text))
        for _, start, end, category in sorted(candidates, key=lambda span: (span[0], -(span[2] - span[1]))):
            if not any(occupied[start:end]):
                accepted.append((start, end, category))
                occupied[start:end] = b"\1" * (end - start)
        output = []
        offset = 0
        for start, end, category in sorted(accepted):
            if positions is not None:
                start, end = positions[start], positions[end - 1] + 1
            output.extend((original[offset:start], "[redacted " + category + "]"))
            self.removed[category] += 1
            offset = end
        output.append(original[offset:])
        result = []
        for line in "".join(output).splitlines(keepends=True):
            if residual_risk(_ANSI_STYLE.sub('', line)):
                self.removed["lines"] += 1
            else:
                result.append(line)
        return "".join(result)
