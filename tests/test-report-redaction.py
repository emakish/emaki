#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic privacy fixtures; no machine logs or credentials are read."""

import pathlib
import shlex
import string
import sys
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from emaki_report_redact import CATEGORIES, Redactor


# A grid of credential keys x separators x quoting x glued suffixes (the RF6 and
# round 6 probes). Every value starts with HEAD and ends with TAIL, so a
# surviving character of either end is visible.
GRID_NAMES = ('api_key', 'PASSWORD', 'db_pass', 'key', 'auth', 'user_pw', 'key_id', 'token', 'private-key',
              'secret', 'PGPASSWORD', 'client-secret', 'cookie')
GRID_FORMS = tuple(quoted + separator + '{v}' for quoted in ('{n}', '"{n}"', "'{n}'", 'export {n}')
                   for separator in ('=', ':', ': ', ':=', ' = ')) + (
    'tool --{n} {v}', 'tool --{n}={v}', "tool '--{n}'={v}", 'tool "--{n}" {v}', 'setenv {n} {v}',
    'setenv "{n}" {v}', '{{"{n}": {v}}}')
GRID_GLUES = (']', ',', '}', ';', ']]', ',,', '}}', '""', "''", ']-', ',-', '}-', ']/', ',/', '}/', '\\ ',
              ']\\ ', ',\\ ', '}\\ ', '\\"', '\\]', '\\,', ',]', '"\'')
GRID_VALUES = tuple('HEADq7' + glue + 'TAILz9' for glue in GRID_GLUES) + (
    '"HEADq7 b"TAILz9', "'HEADq7]b'TAILz9", '"HEADq7"\'b\'TAILz9', 'HEADq7')
# Lines whose names are on the innocent list, or pressed-key labels, keep their values.
GRID_INNOCENT = ('first_pass=done', 'render_pass: shadow', 'pw_backend=pipewire', '"pw_node": "alsa_output"',
                 'key: Escape', 'key: XF86AudioRaiseVolume', 'key-mgmt=wpa-psk', 'pass_count=3',
                 'sudo: x : TTY=pts/0 ; PWD=/srv ; USER=root ; COMMAND=/bin/true')


# RF7: values that start with every ASCII punctuation character, alone and after
# each kind of whitespace. The key alone decides; the value need not parse.
GRID_SPACES = (' ', '\t', '\x0b', '\x0c', '\r', ' \t ')
GRID_LEADING = (tuple(mark + 'TAILz9' for mark in string.punctuation)
                + tuple(space + mark + 'TAILz9' for space in GRID_SPACES for mark in string.punctuation))
# RF7: values that go on to the next line through an open quote or an escaped newline.
GRID_MULTILINE = ('"HEADq7\nTAILz9"', "'HEADq7\nTAILz9'", 'HEADq7\\\nTAILz9', '"HEADq7"\'\nTAILz9\'',
                  '\'HEADq7\'"\nTAILz9"', 'HEADq7"\nTAILz9"', '"HEADq7"\\\nTAILz9', '"HEADq7\nmid"\'\nTAILz9\'',
                  'HEADq7\\\r\nTAILz9', '"HEADq7\\\nTAILz9"')
GRID_CLIENTS = ('mysql', 'mysqldump', 'mysqladmin', 'mariadb', 'mariadb-dump', 'mariadb-admin')
GRID_HEADERS = ('Authorization', 'Proxy-Authorization', 'X-Authorization', 'Cookie', 'Set-Cookie', 'Cookie2')
GRID_FOLDED = ('Bearer HEADq7\n TAILz9', 'Basic HEADq7\n\tTAILz9', 'Bearer\n HEADq7\n TAILz9',
               'a=HEADq7;\n b=TAILz9', 'Bearer\nTAILz9', '\n a=HEADq7\n\tb=TAILz9')


# RF8: multiline values written with percent escapes (a space, a slash, a quote,
# a backslash), base64-looking runs and backslash escapes. The lines they go on
# to must go with them however the opening line is refused.
GRID_ENCODED = ('"HEADq7%20\nTAILz9"', "'HEADq7%2F\nTAILz9'", 'HEADq7%20\\\nTAILz9', 'HEADq7%22\nTAILz9%22',
                '%22HEADq7\nTAILz9%22', 'HEADq7%5C\nTAILz9', '%27HEADq7%20\nTAILz9%27',
                '"HEADq7QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIz\nTAILz9"',
                'HEADq7QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIz+/==\\\nTAILz9',
                '"HEADq7\\x2d\\u0040\nTAILz9"', '"HEADq7\\"\nTAILz9"', 'HEADq7\\x2d\\\nTAILz9',
                'HEADq7%20"\nTAILz9"')
# RF8: a header value whose next line (after a bare scheme) or folded line opens
# a quote, ends with a backslash or is percent-encoded.
GRID_HEADER_CONTINUED = ('Bearer\n"HEADq7\nTAILz9\n"', 'Bearer\nHEADq7\\\nTAILz9', 'a=HEADq7\n "b\nTAILz9"',
                         'a=HEADq7\n b\\\nTAILz9', 'Bearer HEADq7%20\n TAILz9', 'Basic\n%22HEADq7\nTAILz9%22',
                         'Bearer HEADq7\n "b\nmid\nTAILz9"', 'a=HEADq7%20\\\nTAILz9')


def encoded_name(name):
    """The name with its first character written as a percent escape."""
    return '%{:02X}'.format(ord(name[0])) + name[1:]


def grid_lines(values=GRID_VALUES):
    for name in GRID_NAMES:
        for form in GRID_FORMS:
            for value in values:
                yield 'x ' + form.format(n=name, v=value) + ' next'


def multiline_lines():
    yield from grid_lines(GRID_MULTILINE)
    for client in GRID_CLIENTS:
        for value in GRID_MULTILINE:
            yield 'x ' + client + ' -p' + value + ' next'
    for header in GRID_HEADERS:
        for value in GRID_FOLDED:
            yield header + ': ' + value


def encoded_lines():
    yield from grid_lines(GRID_ENCODED)
    for client in GRID_CLIENTS:
        for value in GRID_ENCODED:
            yield 'x ' + client + ' -p' + value + ' next'
    for name in GRID_NAMES:
        for form in GRID_FORMS:
            for value in GRID_MULTILINE + GRID_ENCODED:
                yield 'x ' + form.format(n=encoded_name(name), v=value) + ' next'
    for header in GRID_HEADERS:
        for value in GRID_HEADER_CONTINUED:
            yield header + ': ' + value
            yield encoded_name(header) + ': ' + value


def all_grids():
    """Every grid, by name: (name, lines)."""
    return (('glued', list(grid_lines())), ('leading', list(grid_lines(GRID_LEADING))),
            ('multiline', list(multiline_lines())), ('encoded', list(encoded_lines())))


def closed(line):
    """Whether a shell closes every quote of the line (an open one runs on)."""
    try:
        shlex.split(line)
    except ValueError:
        return False
    return True


class RedactionTests(unittest.TestCase):
    def redact(self, text):
        redactor = Redactor(user="testperson", host="test-machine.example")
        return redactor, redactor.redact(text)

    def assertDropped(self, source, lines=1):
        redactor, result = self.redact(source)
        self.assertEqual(result, '')
        self.assertEqual(redactor.removed['lines'], lines)
        self.assertEqual(redactor.removed['secret'], 0)

    def test_review_secrets(self):
        self.assertDropped("Authorization: Bearer C8R_SYNTHETIC_SECRET password=C8R_SYNTHETIC_PASSWORD")

    def test_assignments(self):
        # A line that names a credential is dropped whole, never redacted piece by piece.
        for source in ('password=planted', 'password="planted truncated', 'Bearer "planted"', 'passwd: planted123', 'secret = planted',
                       'token=planted', 'key=planted', '--api-key=planted',
                       '--access_token=planted', '"refresh_token": "planted with spaces"',
                       "password='planted with spaces'", 'passwd=planted',
                       '"private_key": "planted\\nwith escaped lines"'):
            with self.subTest(source=source):
                self.assertDropped(source)

    def test_short_password_names_and_github_tokens(self):
        for source in ('PGPASSWORD=planted', 'pass=planted', 'pw="planted with spaces"', 'mysql -pplanted',
                       'mysql --host=localhost -p"planted with spaces" db', 'received gho_planted123',
                       'received github_pat_planted123_456'):
            with self.subTest(source=source):
                self.assertDropped(source)
        # Only the credential line goes; its neighbours stay.
        redactor, result = self.redact('before\nmysql -pplanted\nafter\n')
        self.assertEqual(result, 'before\nafter\n')
        self.assertEqual(redactor.removed['lines'], 1)

    def test_qml_source_locations_are_not_credential_blobs(self):
        shell = pathlib.Path(__file__).resolve().parents[1] / 'shell'
        sources = ['ClockPanel.qml', 'Bar.qml', 'modules/NotificationPopup.qml']
        sources.extend(str(path.relative_to(shell)) for path in shell.rglob('*')
                       if path.suffix in ('.qml', '.js'))
        for name in sources:
            for location in ('file:///opt/emaki/shell/' + name + ':42:5',
                             '@/opt/emaki/shell/' + name + '[42:5]',
                             'file:///opt/emaki/shell/' + name + ':10'):
                source = 'WARN scene: ' + location + ': TypeError: Cannot read property of null'
                with self.subTest(location=location):
                    redactor, result = self.redact(source)
                    self.assertEqual(result, source)
                    self.assertFalse(any(redactor.removed.values()))
        for secret in ('PGPASSWORD=planted', 'pass=planted', 'pw=planted',
                       'mysql -pplanted', 'gho_planted123', 'github_pat_planted123_456'):
            redactor, result = self.redact(source + ' ' + secret)
            self.assertNotIn('planted', result)
        redactor, result = self.redact(source + ' credentials unlabelled-value')
        self.assertEqual(result, '')
        self.assertEqual(redactor.removed['lines'], 1)

    def test_related_secret_arguments_clients_and_token_prefixes(self):
        sources = ['--pass planted', '--pw planted', '--pgpassword planted', 'mysql -e "select 1;" -pplanted',
                   "mysql -e 'select 1;' -pplanted db", 'mysql -e "say \\"a; b\\"" -pplanted',
                   'sudo /usr/bin/mysqldump --single-transaction -p"planted x" db | gzip', 'received ghp_planted123']
        sources += [client + ' -u root -pplanted' for client in
                    ('mysqldump', 'mysqladmin', 'mariadb', 'mariadb-dump', 'mariadb-admin')]
        sources += ['received ' + prefix + 'planted123' for prefix in ('gho_', 'ghs_', 'ghu_', 'ghr_', 'github_pat_')]
        for source in sources:
            with self.subTest(source=source):
                self.assertDropped(source)
        # The -p of another command stays.
        for source in ('mysql -e status; ssh -p2222 host', 'mysql -P3306 -h db -e status',
                       'mysql -e "select 1" -- -pnot-an-option'):
            with self.subTest(source=source):
                self.assertEqual(self.redact(source)[1], source)

    def test_short_names_need_credential_values_only_inside_longer_names(self):
        for source in ('first_pass=done', 'second_pass=skipped', 'pw_backend=pipewire',
                       'pass_count=3', '"pw_node": "alsa_output"', 'render_pass: shadow'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, source)
                self.assertFalse(any(redactor.removed.values()))
        for source in ('pass=12', 'PASS=weak', 'pw=x', '"pass": "weak"', '--pass=weak', 'first_pass=planted123',
                       'pw_backend=planted123', 'db_pass=Planted123', 'user_pw: hunter2xyz', 'pass_token=weak',
                       'PGPASSWORD: weak', 'setenv PGPASSWORD planted', 'setenv DB_PASS planted123',
                       # setenv passes a value to the environment: no exemption by name.
                       'setenv PASS_COUNT 3'):
            with self.subTest(source=source):
                self.assertDropped(source)

    def test_every_value_of_a_credential_named_key_is_replaced(self):
        # Password strength never decides: plain words, numbers, quoted
        # phrases, JSON, setenv, exports, long options and bare colon labels
        # all drop their line.
        for source in ('db_pass=weak', 'user_pw=monkey', 'db_pass=12345', 'db_pass="correct horse battery"',
                       "user_pw='correct horse'", '{"db_pass":"weak"}', '{"user_pw": "monkey", "n": 1}',
                       'setenv DB_PASS weak', 'setenv user_pw 1234', 'export DB_PASS=weak', 'dbpass=weak',
                       'userPassword=weak', 'MYSQL_PWD=weak', 'APP_SECRET=weak', 'authtoken=weak', '--db-pass weak',
                       '--user-pw=monkey', '--client-secret weak', 'first_pass=done db_pass=weak',
                       'user_pw: monkey', 'db_pass: weak', 'DB_PASS: correct horse battery', 'ssh_key: weak',
                       '"db_pass": weak', 'first_pass=done user_pw: monkey', 'key: hunter2', 'cookie: weak'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, '')
                self.assertEqual(redactor.removed['lines'], 1)

    def test_credential_arguments_and_setenv_are_never_exempt(self):
        # Pressed-key labels, innocent names and path values exempt diagnostic
        # fields only; a credential argument, setenv, or a quoted or equals key
        # field always drops its line.
        for source in ('--key KEY_planted123', '--key=KEY_planted123', '--key XF86planted123', '--key Escape',
                       'setenv key KEY_planted123', 'setenv KEY XF86planted123', 'setenv key Escape',
                       'key=KEY_planted123', 'export KEY=XF86planted123', '{"key": "KEY_planted123"}',
                       '--pwd /planted', '--pwd=/planted', 'setenv PWD /planted', 'setenv KEY_MGMT planted',
                       '--first-pass planted', '--pw-node planted', '--ssh-key planted', '--private-key planted',
                       '--api_key planted', '--apikey planted', '--auth planted', '--cookie planted',
                       'tool --password planted --verbose'):
            with self.subTest(source=source):
                self.assertDropped(source)
        # The bare label a compositor prints keeps its pressed key.
        for source in ('key: Escape', 'key: KEY_LEFTMETA', 'key: XF86AudioRaiseVolume', '--keyboard us'):
            with self.subTest(source=source):
                self.assertEqual(self.redact(source)[1], source)

    def test_the_key_alone_drops_the_line_and_continuations_go_with_it(self):
        # RF7: a value that starts with a delimiter, or no value at all, still
        # names a credential; a value that goes on to the next line takes it.
        for source, lines in (('key=,plantedtail', 1), ('key_id=]plantedtail', 1), ('api_key: ;plantedtail', 1),
                              ('password=', 1), ('"token":', 1), ('tool --password', 1), ("setenv 'secret'", 1),
                              ('export PASSWORD', 1), ('declare -x API_TOKEN', 1),
                              ('tool --password "head\nplantedtail"', 2), ('password="head"\'\nplantedtail\'', 2),
                              ('mysql -p"head\nplantedtail"', 2), ('token=head\\\nplantedtail', 2),
                              ('Authorization: Bearer head\n plantedtail', 2), ('Cookie: a=head;\n b=planted\n\tc=tail', 3),
                              ('Cookie:\n a=plantedtail', 2)):
            with self.subTest(source=source):
                self.assertDropped(source, lines)
        # The line after a closed continuation stays, and so do innocent keys.
        self.assertEqual(self.redact('token="head\nplanted" x\nafter\n')[1], 'after\n')
        self.assertEqual(self.redact('Authorization: Bearer head\n plantedtail\nafter')[1], 'after')
        for source in ('export first_pass', 'export PWD=/srv', 'first_pass=,done', 'key: Escape',
                       'mounted readonly passthrough', 'we declare key bindings'):
            with self.subTest(source=source):
                self.assertEqual(self.redact(source)[1], source)
        # Each value is read once: values inside an earlier one and open quotes
        # on many lines stay linear.
        for source in ('password=a"' * 20000, 'tool --password "x\n' * 15000, "token=a'b\n" * 15000,
                       'Authorization: Bearer x\n' * 15000):
            with self.subTest(source=source[:20]):
                started = time.monotonic()
                self.assertEqual(self.redact(source)[1], '')
                self.assertLess(time.monotonic() - started, 3.0)

    def test_continuations_are_found_before_any_line_is_refused(self):
        # RF8: the opening line of a value is refused for its encoding or for
        # its key in the same pass that finds its continuation lines, and a
        # header's next or folded line is read like a value of its own.
        for source in ('token="head%20\nPLANTEDTAIL\nend"\nKEPT', 'token%3D%22head\nPLANTEDTAIL\nend%22\nKEPT',
                       'Authorization: Bearer head%20\n PLANTEDTAIL\nKEPT', 'token=head%20\\\nPLANTEDTAIL\nKEPT',
                       'Authorization: Bearer\n"alpha\nPLANTEDTAIL\n"\nKEPT',
                       'Authorization: foo\n alpha\\\nPLANTEDTAIL\nKEPT',
                       'Authorization: foo\n "alpha\nPLANTEDTAIL\n"\nKEPT', 'password=head%5C\nPLANTEDTAIL\nKEPT',
                       '%41uthorization: Bearer\n"alpha\nPLANTEDTAIL"\nKEPT'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, 'KEPT')
                self.assertEqual(redactor.removed['lines'], source.count('\n'))
        # An encoded line that is not a credential still goes alone.
        self.assertEqual(self.redact('a 10%2E0%2E0%2E7 b\nnext')[1], 'next')
        # A closed value leaves the line after it, and a bare scheme does not
        # take an empty next line.
        self.assertEqual(self.redact('token="head\nPLANTEDTAIL"\nJOURNAL_AFTER')[1], 'JOURNAL_AFTER')
        self.assertEqual(self.redact('Authorization: Bearer\n\nafter')[1], '\nafter')
        # Each value is read once, also in the decoded reading.
        for source in ('token=%22a\n' * 15000, 'Authorization: Bearer\n "x\n' * 10000):
            with self.subTest(source=source[:20]):
                started = time.monotonic()
                self.assertEqual(self.redact(source)[1], '')
                self.assertLess(time.monotonic() - started, 3.0)

    def test_credential_arguments_lose_their_whole_shell_word(self):
        # A credential argument drops its whole line: no suffix after a comma,
        # a closing quote or an escape survives, also for a quoted option or
        # setenv name.
        for source in ('tool --key KEY_head,tailvalue', 'setenv key KEY_head,tailvalue', 'tool --auth "head"tailvalue',
                       'tool --key=KEY_head,tailvalue', 'tool --password "a\\"b c"d --verbose', 'tool --token a\\ b more',
                       'tool "--key" KEY_probevalue123', "tool '--auth' 'head'tailvalue next",
                       'setenv "key" KEY_probevalue123', "setenv 'key' 'a b'tailvalue x",
                       'tool "--key=head"tailvalue next', 'tool "--key=head value" next',
                       "tool '--auth=head tailvalue' next"):
            with self.subTest(source=source):
                self.assertDropped(source)
        # An open quote carries the value on to the line that closes it; one that
        # never closes takes the rest of the text, as a shell would read it.
        self.assertEqual(self.redact('tool --key "head tailvalue\nnext" line\nkept')[1], 'kept')
        self.assertEqual(self.redact('tool --key "head tailvalue\nnext line\nmore')[1], '')

    def test_quoted_option_name_before_equals_loses_the_whole_word(self):
        for source in ("tool '--api_key'=alpha]tailvalue --verbose", 'tool "--key"=a]tailvalue x',
                       "tool '--password'=a\\ tailvalue x", "tool '--auth'=head]tailvalue",
                       'tool "--api-key"="a b"tailvalue x', "tool '--token'='a]b'tailvalue x",
                       "tool '--key'=a,tailvalue x"):
            with self.subTest(source=source):
                self.assertDropped(source)
        # A quoted name that is not a credential keeps its value.
        self.assertEqual(self.redact("'--keyboard'=us")[1], "'--keyboard'=us")

    def test_assignment_keeps_no_suffix_after_a_bracket_or_an_escaped_space(self):
        for source in ('"api_key"=a]tailvalue x', 'api_key=a]tailvalue x', 'password=a]tailvalue',
                       "'api_key'=a\\ tailvalue x", 'export PASSWORD=a\\ tailvalue x', 'opts=[api_key=abc] next',
                       # RF6: repeated delimiters, empty quoted parts, punctuation-prefixed suffixes.
                       'export PASSWORD=a""plantedtail', 'api_key=a]]plantedtail', 'api_key=a,-plantedtail',
                       "password=a''plantedtail", 'token=a}/plantedtail', 'secret=a\\]plantedtail'):
            with self.subTest(source=source):
                self.assertDropped(source)

    def test_no_character_of_a_credential_value_survives_the_grid(self):
        # The property: a line that names a credential leaves nothing at all,
        # whatever the separator, quoting or glued suffix of its value.
        grids = all_grids()
        self.assertGreater(len(grids[0][1]), 9000)
        self.assertGreater(len(grids[1][1]), 70000)
        for grid, lines in grids:
            for line in lines:
                redactor, result = self.redact(line)
                if result != '':
                    self.fail(f'{grid}: {line!r} left {result!r}')
                self.assertEqual(redactor.removed['lines'], len(line.split('\n')), (grid, line))
            # Mixed with innocent lines, only the innocent lines remain. A quote
            # that never closes runs on over the lines after it, so those lines
            # are checked alone above.
            lines = [line for line in lines if closed(line)]
            text = ''.join(line + '\n' + GRID_INNOCENT[index % len(GRID_INNOCENT)] + '\n'
                           for index, line in enumerate(lines))
            redactor, result = self.redact(text)
            with self.subTest(grid=grid):
                self.assertEqual(result, ''.join(GRID_INNOCENT[index % len(GRID_INNOCENT)] + '\n'
                                                 for index in range(len(lines))))
                self.assertEqual(redactor.removed['lines'], sum(len(line.split('\n')) for line in lines))

    def test_innocent_keys_are_one_short_list_and_exempt_only_their_field(self):
        from emaki_report_redact import _INNOCENT_KEYS
        self.assertEqual(_INNOCENT_KEYS, {'first_pass', 'second_pass', 'render_pass', 'pass_count',
                                          'pw_backend', 'pw_node'})
        for source in ('FIRST_PASS=done', 'first-pass=done', 'render_pass: shadow', '"pw_node": "alsa_output"',
                       'sudo: x : TTY=pts/0 ; PWD=/srv ; USER=root ; COMMAND=/bin/true', 'key-mgmt=wpa-psk',
                       'key: Escape', 'warning: libsecret: local (0.21.8.2-1) is newer than core (0.21.8.1-1)'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, source)
                self.assertFalse(any(redactor.removed.values()))
        # An innocent field does not keep a credential beside it on its line.
        for source in ('pw_backend=pipewire pw_token=weak', 'pass_count=3 pass=weak', 'PWD=weak'):
            with self.subTest(source=source):
                self.assertDropped(source)
        # The pacman phrase must be complete; an appended disclosure refuses the line.
        self.assertEqual(self.redact('warning: libsecret: local (1-1) is newer than core (1-0) and more')[1], '')

    def test_credential_name_scan_is_linear(self):
        from emaki_report_redact import _ARGUMENT, _ASSIGNMENT, _COLON_LABEL, _SETENV
        for name, source in {'repeated word': 'pass' * 25000, 'components': 'a_' * 50000,
                             'key components': 'key_' * 12500, 'options': '--' + 'pw-' * 30000,
                             'setenv': 'setenv ' + 'pass' * 25000, 'quoted words': '--key ' + '"a"b' * 25000,
                             'escapes': '--key "' + '\\a' * 50000, 'unclosed': '"--key=' + 'a\\' * 50000,
                             'quoted names': '"--key' * 20000, 'escaped value': 'password=' + '\\a' * 50000,
                             'quoted name escapes': "'--key'=" + '\\' * 100001}.items():
            for pattern in (_ASSIGNMENT, _ARGUMENT, _SETENV, _COLON_LABEL):
                with self.subTest(shape=name, pattern=pattern.pattern[:40]):
                    started = time.monotonic()
                    list(pattern.finditer(source))
                    self.assertLess(time.monotonic() - started, 1.0)

    def test_pgpassword_without_a_value_form_is_refused(self):
        for source in ('PGPASSWORD weak', 'PGPASSWORD=planted; PGPASSWORD other', 'export PGPASSWORD weak',
                       'psql: PGPASSWORD is not set hunter2', 'hint: set PGPASSWORD or use .pgpass PGPASSWORD weak',
                       'PGPASSWORD weak psql: PGPASSWORD is not set'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, '')
                self.assertEqual(redactor.removed['lines'], 1)

    def test_recognized_mysql_message_is_not_a_command(self):
        source = 'mysql client started -pending'
        redactor, result = self.redact(source)
        self.assertEqual(result, source)
        self.assertFalse(any(redactor.removed.values()))
        # The message must be the whole rest of the line.
        for source in ('mysql client started -pending; mysql -pplanted', 'mysql client started -pplanted',
                       'mysql client -pplanted', 'mysql client started -pending db'):
            with self.subTest(source=source):
                self.assertDropped(source)

    def test_lines_that_only_name_pgpassword_are_kept(self):
        for source in ('psql: PGPASSWORD is not set', 'hint: set PGPASSWORD or use .pgpass',
                       'pg_dump: PGPASSWORD environment variable ignored'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, source)
                self.assertFalse(any(redactor.removed.values()))

    def test_source_location_exemption_does_not_hide_path_secrets(self):
        blob = 'Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MEFCQ0RFRkdISUo'
        for prefix in ('https://cdn.example.com/u/', 'http://localhost:8080/', 'file:///opt/', 'qrc:/', '@/',
                       'at onClicked@file:///opt/'):
            source = 'WARN ' + prefix + blob + '/app.js:12 failed'
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, '')
                self.assertEqual(redactor.removed['lines'], 1)
        # Each directory is judged alone; a removed user name leaves an empty one.
        source = ('WARN qml: file:///home/testperson/Projects/EmakiShell2/Settings/Modules/'
                  'NotificationPopupWindow.qml:42:5: TypeError: x')
        redactor, result = self.redact(source)
        self.assertEqual(result, source.replace('testperson', '[redacted user]'))
        self.assertEqual(redactor.removed['lines'], 0)

    def test_qml_credential_word_errors_keep_only_complete_diagnostics(self):
        for source in ("WARN qml: TypeError: Cannot read property 'token' of undefined",
                       'WARN scene: file:///opt/shell/Lock.qml:42: ReferenceError: auth is not defined'):
            with self.subTest(source=source):
                redactor, result = self.redact(source)
                self.assertEqual(result, source)
                self.assertFalse(any(redactor.removed.values()))
                self.assertEqual(self.redact(source + ' auth malformed planted123')[1], '')
        # A name long enough to be an encoded secret is not a short script identifier.
        long_name = 'QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0MTIz'
        self.assertEqual(self.redact(f'WARN qml: ReferenceError: {long_name} is not defined')[1], '')

    def test_client_password_scan_is_linear(self):
        shapes = {'client names': 'mysql ' * 16384,
                  'escaped JSON': 'mysql -e ' + '{\\"key\\":\\"value\\",' * 6000,
                  'escaped quotes': '\\"' * 48000,
                  'unclosed quotes': 'mysql ' + 'a" ' * 32000 + "b' " * 16000}
        for name, source in shapes.items():
            with self.subTest(shape=name):
                started = time.monotonic()
                self.redact(source)
                self.assertLess(time.monotonic() - started, 2.0)

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
        self.assertEqual(result, "")
        self.assertEqual(redactor.removed["lines"], 20)
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
        self.assertDropped('password=planted auth malformed other-value\n')

    def test_innocent_diagnostics_survive_residual_check(self):
        source = ('key: XF86AudioRaiseVolume\nPassword: Required\n'
                  'polkit: Authorization: denied\nconnector HDMI-1: connected\n'
                  'request https://example.com/a%20b\n')
        redactor, result = self.redact(source)
        self.assertEqual(result, source)
        self.assertFalse(any(redactor.removed.values()))


if __name__ == "__main__":
    unittest.main()
