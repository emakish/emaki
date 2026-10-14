# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline redaction shared by report previews and saved diagnostics."""

import bisect
import ipaddress
import os
import pwd
import re
import socket
from urllib.parse import unquote


CATEGORIES = ("user", "host", "email", "ip", "mac", "serial", "secret", "lines")
# An unquoted value keeps a backslash escape (a\ b is one shell word).
_VALUE = r'''(?:"(?:\\.|[^"\\])*(?:"|\Z)|'(?:\\.|[^'\\])*(?:'|\Z)|(?:\\.?|[^\s,;\}\]\"'\\])++)'''
# Schemes can carry a parameter list instead of a single credential token.
_AUTH_VALUE = r"(?:[\w*-]+[ \t]*=[^\r\n]+|" + _VALUE + r")"
_HEADER = re.compile(
    r'''(?i)(?<![\w-])(?P<quote>["']?)(?:(?:Proxy-|X-)?Authorization|(?:Set-|X-)?Cookie2?)(?P<key_quote>["']?)[ \t]*:[ \t]*'''
    + r'''(?P<value>"(?:\\.|[^"\\])*(?:"|\Z)|'(?:\\.|[^'\\])*(?:'|\Z)|[^\r\n]*)'''
)
# A key whose name contains one of these words names a credential in every
# form (key=value, key: value, "key": "value", --key value, setenv), whatever
# the shape of the value: dbpass, userPassword, MYSQL_PWD, client_secret.
_CREDENTIAL_WORD = r"pass|pw|secret|token|credential|psk|privkey"
_CREDENTIAL_WORD_PATTERN = re.compile(_CREDENTIAL_WORD, re.I)
# Other key-like names are credentials as whole components of a name: key,
# api_key, x-auth, cookie. A key component joined to another one (api_key,
# ssh-key) names a credential in every form; alone, after a colon, such a name
# is often a label (key: Escape) and then needs a credential-shaped value.
_KEY_COMPONENT = r"key|apikey|auth|authorization|cookie|privatekey"
# The name is one possessive identifier: a lookahead finds the credential word,
# so a long identifier is never rescanned.
_SECRET_NAME = (r"(?=[a-z0-9_-]*?(?:" + _CREDENTIAL_WORD + r")|(?:[a-z0-9]+[-_])*?(?:" + _KEY_COMPONENT
                + r")(?![a-z0-9]))[a-z0-9_-]++")
_ASSIGNMENT = re.compile(
    r'''(?ix)(?<![\w-])(?:--)?["']?''' + _SECRET_NAME
    + r'''["']?\s*(?P<operator>:=|=|:)\s*(?P<value>''' + _VALUE + r")?"
)
_COLON_LABEL = re.compile(r"(?i)(?<![\w-])[\"']?(?P<name>" + _SECRET_NAME
                          + r")[\"']?[ \t]*:(?![=:])[ \t]*(?P<value>[^\s\[\"']\S*)")
# One complete shell word on one line: quoted parts with escapes and unquoted
# runs, up to the next whitespace. An unclosed quote runs to the end of the line.
_WORD_PART = r'''(?:"(?:\\.|[^"\\\n])*+\\?(?:"|(?=\n)|\Z)|'[^'\n]*+(?:'|(?=\n)|\Z)|\\.?|[^\s"'\\])'''
_SHELL_WORD = _WORD_PART + "++"
# A long option named like a credential (--password, --api_key, --private-key,
# --auth) passes a credential; its value is never exempt. The name may be quoted
# ("--key" value, '--key'=value); the value is still the whole next shell word.
# Inside one quoted word ("--key=value"tail) the value runs to the closing quote
# and on to the end of the word.
_ARGUMENT = re.compile(
    r"(?i)(?<![\w-])(?:(?P<quote>[\"'])--(?P<quoted_name>" + _SECRET_NAME + r")="
    r"(?P<quoted_value>(?:\\.|(?!(?P=quote))[^\\\n])*+)(?:(?P=quote)(?P<rest>" + _WORD_PART + r"*+))?"
    r"|[\"']?--(?P<name>" + _SECRET_NAME + r")[\"']?(?:(?:[ \t]+|=)(?P<value>" + _SHELL_WORD + r"))?)")
# The C shell sets environment variables without an equals sign.
_SETENV = re.compile(r"(?i)(?<![\w-])setenv[ \t]+(?P<quote>[\"']?)(?P<name>" + _SECRET_NAME
                     + r")(?P=quote)(?:[ \t]+(?P<value>" + _SHELL_WORD + r"))?")
# export and declare -x name a variable even without a value. declare and
# typeset need an option, so prose ("we declare key bindings") stays.
_EXPORT = re.compile(r"(?i)(?<![\w-])(?:export(?:[ \t]+-[a-z]+)*|(?:declare|typeset)(?:[ \t]+-[a-z]+)+)[ \t]+[\"']?(?P<name>"
                     + _SECRET_NAME + r")")
_BEARER = re.compile(r"(?i)\bBearer[ \t]+(?P<value>" + _AUTH_VALUE + r")")
_TOKEN_PREFIX = re.compile(r"(?i)\b(?:gh[ousr]_|github_pat_)[a-z0-9_]+")
_DATABASE_CLIENT = r"(?:mysql|mysqldump|mysqladmin|mariadb(?:-dump|-admin)?)"
_CLIENT_WORD = re.compile(_DATABASE_CLIENT)
_CLIENT_LINE = re.compile(r"(?<![\w-])" + _DATABASE_CLIENT + r"(?![\w-])")
# Complete diagnostic messages in which a client name is prose, not a command.
_CLIENT_MESSAGE = re.compile(r"(?:mysql|mariadb) client started -pending")
_PLAIN_RUN = re.compile(r"[^\s;|&\\\"']+")
_DOUBLE_QUOTED_TAIL = re.compile(r'(?:\\.|[^"\\])*"')
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
# A percent escape of a printable ASCII character other than the space.
_PRINTABLE_ESCAPE = re.compile(r"%(2[1-9A-Fa-f]|[3-6][0-9A-Fa-f]|7[0-9A-Ea-e])")
_BLOCK_COLUMNS = set("NAME KNAME PATH MAJ:MIN RM SIZE RO TYPE MOUNTPOINT MOUNTPOINTS SERIAL MODEL VENDOR TRAN ROTA".split())
_BLOCK_NAME = re.compile(
    r"[└├─│]*(?:/dev/)?(?:[shv]d[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+|"
    r"loop\d+|sr\d+|dm-\d+|md\d+|zram\d+)(?:p?\d+)?"
)



# Only complete, recognizable diagnostic phrases exempt credential words from
# the residual check; adding unrelated text cannot turn an unsafe line safe.
_SAFE_DIAGNOSTIC = re.compile(
    r"(?i)(?:(?:Un)?expected token [`'\"](?:[{}();:]|identifier)[`'\"]?(?: or [`'\"][{}();:][`'\"])?|"
    # A short script identifier cannot be a base64 blob (32 characters or padding).
    r"ReferenceError: [a-z_$][\w$]{0,30} is not defined|Cannot read property '[a-z_$][\w$]{0,30}' of (?:undefined|null)|"
    r"\d+ incorrect password attempts|(?:token: expected|password: is not defined)|"
    r"secret: service unavailable|token: (?:none|expired|required)|secret: (?:Unavailable|NotFound)|"
    r"gkd_secret_service_get_objects: assertion (?:'[^'\r\n]*' )?failed|"
    r"password is not defined|secret_service_lookup: not found|password: authentication failure|"
    r"Authorization required|authorization failed: polkit denied|Cookie jar empty|The Authorization header is missing|"
    r"key-mgmt[=:][ \t]*wpa-psk|token: unexpected end|password: incorrect|"
    r"Password: Required|Authorization: denied|password not shown, host fine|password: is|service: loading credentials|USB Serial support registered(?: for generic)?|"
    # pacman names a package (libsecret, keepassxc) before a colon.
    r"warning: [a-z0-9@._+-]+: local \([\w.:+~-]+\) is newer(?: than [\w-]+ \([\w.:+~-]+\))?|"
    # PostgreSQL clients name the variable without a value.
    r"PGPASSWORD is not set|set PGPASSWORD or use \.pgpass|PGPASSWORD environment variable ignored)(?=$|[\r\n]| ; TTY=)"
)
_RISK_NAME = (r"(?:[a-z0-9]+[-_])*(?:pgpassword|password|passwd|secret|token|api[-_]key|apikey|"
              r"access[-_]key|private[-_]key|privatekey|auth|authorization|cookies?2?|wireguard|"
              r"passphrase|pwd|credentials|psk|presharedkey|privkey|secretkey|accesstoken|clientsecret|passwort)"
              r"(?:[-_][a-z0-9]+)*")
_RISK_IDENTIFIER = re.compile(r"(?i)(?<![\w-])" + _RISK_NAME + r"(?![\w-])")
# A QML or JS source location as Qt and Quickshell print it (file://, qrc:, @ or a
# bare absolute path). A path inside a URL never starts at such a boundary. An
# empty directory is where a redacted user name was removed.
_SOURCE_LOCATION = re.compile(
    r"(?<![\w:/.])(?:file://|qrc:|@)?/(?P<directories>(?:[-\w.]*/)*)[-\w.]+\.(?:qml|js)"
    r"(?::\d+(?::\d+)?|\[\d+:\d+\])")


def residual_risk(line):
    """Fail closed when recognizable sensitive syntax survived replacement."""
    line = _SAFE_DIAGNOSTIC.sub('', line)
    # PWD is also sudo's working-directory field, not a credential there.
    line = re.sub(r"\bPWD=(?=/)", '=', line)
    # A colon label whose name has a credential word, left with a plain value.
    for label in _COLON_LABEL.finditer(line):
        if always_secret(label['name']) and not innocent_field(label['name'], label['value'], True):
            return True
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
    if re.search(r"[=:][ \t]*\[redacted secret\][,;}\]\"\']\w|[=:][ \t]*\[redacted secret\][a-z0-9]", line, re.I):
        return True
    line = re.sub(r"(?i)(?<![\w-])(?:--)?[\"']?" + _RISK_NAME
                  + r"[\"']?[ \t]*(?::=|=|:|[ \t])[ \t]*\[redacted secret\]", '', line)
    line = re.sub(r"-p\[redacted secret\]", "", line)
    line = re.sub(r"\[redacted (?:user|host|email|ip|mac|serial|secret)\]", '', line)
    if re.search(r"(?i)\b(?:connector[ \t]+[A-Z]+-\d+:|Output[ \t]+\")[^\r\n]*[ \t](?=[A-Z0-9]*\d)[A-Z0-9]{6,}(?:\"|$)", line):
        return True
    if _RISK_IDENTIFIER.search(line):
        return True
    # Mixed-case source paths resemble base64. Exempt only the file name and
    # location of a source location; each directory is still checked alone.
    blob_line = _SOURCE_LOCATION.sub(lambda match: ' ' + match['directories'].replace('/', ' '), line)
    for blob in re.finditer(r"(?<![\w/+])[A-Za-z0-9+/]{32,}={0,2}(?![\w/+])|(?<![\w/+])[A-Za-z0-9+/]{16,}={1,2}(?![\w/+])", blob_line):
        if '/' not in blob.group() or credential_like(blob.group()):
            return True
    return bool(re.search(
        r"(?ix)(?:"
        r"(?:[a-f0-9]{1,2}[:-]){5,}[a-f0-9]{1,2}|(?:[a-f0-9]{2}_){5}[a-f0-9]{2}|"
        r"\bmac[ \t]+[a-f0-9]{12}\b|\b(?:gh[oprsu]_|github_pat_|glpat-|xox[baprs]-|sk-|AKIA)[a-z0-9_-]+|"
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


# The one list of known innocent keys that contain a credential word: a
# numbered render pass, PipeWire's pw_ prefix. Each keeps its value only while
# the value does not look like a credential. Names compare case-folded, with
# '-' read as '_'.
_INNOCENT_KEYS = frozenset({'first_pass', 'second_pass', 'render_pass', 'pass_count', 'pw_backend', 'pw_node'})
_KEY_LABELS = frozenset({'Escape', 'Return', 'Enter', 'Tab', 'Space'})


def field_name(name):
    return name.strip(" \t\"'").lstrip('-').casefold().replace('-', '_')


def innocent_field(name, value, label):
    """Whether a named field keeps its value; every exemption by name is here.

    label is true for a bare colon label with an unquoted value (key: Escape).
    Command arguments and setenv never come here: their values are always
    credentials.
    """
    name, value = field_name(name), value.strip("\"'")
    if name in _INNOCENT_KEYS:
        return not credential_like(value)
    if name == 'pwd':
        # sudo's working-directory field; MYSQL_PWD and the like stay credentials.
        return value.startswith('/')
    if name == 'key_mgmt':
        # NetworkManager's key management method (wpa-psk, sae), never the key.
        return True
    if name == 'key' and label:
        # A pressed key, as compositors and input tools print it (key: Escape).
        return value in _KEY_LABELS or value.startswith(('XF86', 'KEY_'))
    return False


def always_secret(name):
    """Whether every value of the name is a credential, also after a bare colon."""
    name = field_name(name)
    return bool(_CREDENTIAL_WORD_PATTERN.search(name)) or ('key' in name.split('_') and name != 'key')


def shell_words(line):
    """Spans of the shell words and separators of one line, in linear time.

    A quoted part keeps its separators. An unclosed quote is an ordinary
    character, so the text after it is still split into words; once a quote has
    no closing partner, no later quote of that kind on the line has one either.
    """
    words, position, length = [], 0, len(line)
    unclosed = {'"': length, "'": length}
    while position < length:
        if line[position].isspace():
            position += 1
            continue
        if line[position] in ';|&':
            words.append((position, position + 1))
            position += 1
            continue
        start = position
        while position < length:
            plain = _PLAIN_RUN.match(line, position)
            character = line[position]
            if plain:
                position = plain.end()
            elif character == '\\':
                position = min(position + 2, length)
            elif character in '"\'':
                end = -1
                if position < unclosed[character]:
                    if character == "'":
                        end = line.find("'", position + 1)
                    else:
                        tail = _DOUBLE_QUOTED_TAIL.match(line, position + 1)
                        end = tail.end() - 1 if tail else -1
                if end < 0:
                    unclosed[character] = min(unclosed[character], position)
                    position += 1
                else:
                    position = end + 1
            else:
                break
        words.append((start, position))
    return words


def client_password_spans(text):
    """Attached -p values of MySQL-family clients, up to the end of their command."""
    for line in re.finditer(r"[^\n]+", text):
        body = line.group()
        if not _CLIENT_LINE.search(body):
            continue
        client = False
        for start, end in shell_words(body):
            word = body[start:end]
            if word in (';', '|', '&', '--'):
                client = False
            elif _CLIENT_WORD.fullmatch(word.rsplit('/', 1)[-1]):
                # A recognized message must span the rest of the line; any
                # other text after the client name is read as a command.
                client = not _CLIENT_MESSAGE.fullmatch(body[start:].rstrip())
            elif client and word.startswith('-p') and len(word) > 2:
                yield line.start() + start + 2, line.start() + end


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


def value_end(text, position, whole_line=False):
    """Where a shell value that starts at position ends, across lines.

    A quote that is still open, or a backslash before the newline, carries the
    value onto the next line. An unquoted space ends a word; with whole_line
    (a header value of several words) only the end of the line does.
    """
    quote, length = None, len(text)
    while position < length:
        character = text[position]
        if quote == "'":
            if character == "'":
                quote = None
        elif quote == '"':
            if character == '\\':
                position += 1
            elif character == '"':
                quote = None
        elif character == '\\':
            # An escaped CR LF is one escaped line end.
            position += 2 if text.startswith('\r\n', position + 1) else 1
        elif character in '"\'':
            quote = character
        elif character == '\n' or (character.isspace() and not whole_line):
            return position
        position += 1
    return length


_SCHEMES = frozenset({'bearer', 'basic', 'token'})


def header_end(text, position, whole_line=True):
    """Where a header value that starts at position ends, across lines.

    The value is read like any other (value_end): an open quote or a backslash
    before the newline carries it on. A scheme alone on its line (Bearer) has
    its credential on the next line, and a line that starts with a space or a
    tab folds onto the header. Each line taken this way is read again with
    value_end, so its own open quote or escaped newline carries it further.
    whole_line is false for a header quoted as one shell word; that word is
    read first, and the folds start after its line.
    """
    end = value_end(text, position, whole_line)
    bare = whole_line and text[position:end].strip().casefold() in _SCHEMES
    newline = text.find('\n', end)
    end = len(text) if newline < 0 else newline
    while end + 1 < len(text) and (text[end + 1] in ' \t' or (bare and text[end + 1] not in '\r\n')):
        bare = False
        end = value_end(text, end + 1, True)
    return end


def reading_end(text, value):
    """Where the credential value (position, kind) ends; kind names the reading."""
    position, kind = value
    if kind == 'word':
        return value_end(text, position)
    return header_end(text, position, kind == 'header')


def credential_spans(text):
    """Spans that name a credential, each with where its value starts.

    Yields (start, end, value): start-end covers the key or argument and
    whatever value it has; value is (position, kind), and the value runs from
    position to reading_end(): a shell word, or a header with its bare
    scheme's next line and folded lines. The key alone decides: a value of any
    shape, or none, changes nothing.
    The forms: key=value, key: value, "key": "value", --key value, setenv,
    a database client's -p, token prefixes, Authorization and Cookie headers,
    Bearer. A key on the innocent list (innocent_field) and a complete known
    diagnostic phrase ("Password: Required") name no credential.
    """
    headers = []
    for match in _HEADER.finditer(text):
        start, end = match.span('value')
        if text[start:end].strip().casefold() in {'denied', 'required'}:
            continue
        headers.append((match.start(), end))
        # A shell argument quotes the entire header (its word starts at that
        # quote); a dictionary quotes its key, and the value is the rest. A
        # bare scheme's next line and folded lines are read by header_end().
        if match['quote'] and not match['key_quote']:
            yield match.start(), end, (match.start(), 'quoted header')
        else:
            yield match.start(), end, (start, 'header')
    for match in _BEARER.finditer(text):
        # Inside a header, the header's own span already reads its value.
        # Header matches are ordered and disjoint.
        index = bisect.bisect_right(headers, (match.start(), len(text))) - 1
        if index >= 0 and match.start() < headers[index][1]:
            yield match.start(), match.end(), None
        else:
            yield match.start(), match.end(), (match.start('value'), 'word')
    for match in _ARGUMENT.finditer(text):
        if match['quoted_name'] is not None:
            # "--key=value"tail is one shell word from its opening quote.
            yield match.start(), match.end(), (match.start(), 'word')
        else:
            value = match.start('value') if match['value'] is not None else match.end()
            yield match.start(), match.end(), (value, 'word')
    for match in _TOKEN_PREFIX.finditer(text):
        yield match.start(), match.end(), (match.start(), 'word')
    # The short password option takes its value without a separating space.
    # It belongs to these clients only; ordinary -p flags elsewhere stay.
    for start, end in client_password_spans(text):
        yield start - 2, end, (start, 'word')
    for match in _SETENV.finditer(text):
        value = match.start('value') if match['value'] is not None else match.end()
        yield match.start(), match.end(), (value, 'word')
    for match in _EXPORT.finditer(text):
        if not innocent_field(match['name'], re.match(r"=?(\S*)", text[match.end():])[1], False):
            yield match.start(), match.end(), (match.end(), 'word')
    # Complete known diagnostic phrases ("Password: Required") are labels.
    safe_starts, safe_ends = [], []
    for safe in _SAFE_DIAGNOSTIC.finditer(text):
        safe_starts.append(safe.start())
        safe_ends.append(safe.end())
    for match in _ASSIGNMENT.finditer(text):
        value = match.group('value')
        if not value:
            # No parsable value (key=,tail or key=]tail): the exemptions read
            # the whole run after the separator.
            value = re.match(r"\S*", text[match.end():]).group()
        key_name = re.split(r"[=:]", match.group(), maxsplit=1)[0]
        label = match['operator'] == ':' and value[:1] not in ('"', "'")
        # --key=value is a command argument: no exemption keeps its value.
        if not key_name.startswith('--') and innocent_field(key_name, value, label):
            continue
        # Every other key names a credential, whatever its value; only a bare
        # colon label inside a complete known diagnostic phrase is kept. An
        # environment variable's name is never a diagnostic label.
        if label and field_name(key_name) != 'pgpassword':
            index = bisect.bisect_right(safe_starts, match.start()) - 1
            if index >= 0 and match.start() < safe_ends[index]:
                continue
        start = match.start('value') if match['value'] else match.end()
        yield match.start(), match.end(), (start, 'word')


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

    @staticmethod
    def _credential_lines(reading):
        """Indexes of the lines that name a credential or carry its value on."""
        starts = []
        offset = 0
        for line in reading:
            starts.append(offset)
            offset += len(line)
        joined = ''.join(reading)
        dropped = set()
        # A value that starts inside the value read before it belongs to that
        # word, and reading it again would take quadratic time.
        reach = -1
        spans = sorted(credential_spans(joined), key=lambda span: span[2][0] if span[2] else span[0])
        for start, end, value in spans:
            if value is not None and value[0] >= reach:
                reach = reading_end(joined, value) + 1
                end = max(end, reach)
            elif value is not None:
                end = max(end, reach)
            first = bisect.bisect_right(starts, start) - 1
            last = bisect.bisect_right(starts, min(max(start, end - 1), len(joined) - 1)) - 1
            dropped.update(range(first, last + 1))
        return dropped

    def redact(self, text):
        original = text
        # A line that names a credential is dropped whole, never redacted piece
        # by piece: no character of its value can survive beside a replacement.
        # The lines its value continues on (open quote, escaped newline, folded
        # header) go with it. Encoded lines are refused in the same pass, so a
        # refused line never leaves its continuation behind. Dropping lines can
        # join others into a new match, so look again. Lines end only at a
        # newline here: a vertical tab, form feed or lone carriage return
        # inside a credential line never splits its value off.
        while True:
            lines = re.findall(r"[^\n]*\n|[^\n]+", original)
            plain = [_ANSI_STYLE.sub('', line) for line in lines]
            dropped = set()
            # Each encoding the detection decodes is a reading of its own: a key,
            # quote or backslash written as %XX names a credential or carries a
            # value on just like the plain one. A decoded space would end a word
            # that the plain text carries on (HEAD%20\ before a newline), so one
            # more reading decodes only the printable characters. Decode only
            # for detection, never for presentation.
            readings = [plain]
            if any('%' in line for line in plain):
                readings.append([unquote(line) for line in plain])
                readings.append([_PRINTABLE_ESCAPE.sub(lambda escape: chr(int(escape[1], 16)), line)
                                 for line in plain])
            for reading in readings:
                dropped.update(self._credential_lines(reading))
            # Encoded identities remain unsafe even when their plain spelling
            # would have been replaced.
            for index, line in enumerate(lines):
                decoded_line = unquote(line)
                if decoded_line != line and (residual_risk(decoded_line) or any(
                        re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", decoded_line, re.I)
                        for names in self.identities.values() for name in names)
                        or re.search(r"(?:\d{1,3}\.){3}\d{1,3}", decoded_line)):
                    dropped.add(index)
            if not dropped:
                break
            self.removed["lines"] += len(dropped)
            original = ''.join(line for index, line in enumerate(lines) if index not in dropped)
        text = original
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
