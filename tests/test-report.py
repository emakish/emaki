#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline report composition, selection and command-line contract checks."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlencode, urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
TARGET = 'https://github.com/emakish/emaki/issues/new'
IDS = ['emaki', 'channel', 'kernel', 'hardware', 'logs']
INFO = dict(version='0.8.0', label='alpha', channel='testing', kernel='6.17-fixture',
            processor='Fixture CPU', graphics='Fixture GPU', memory='16 GiB',
            computer='Fixture computer', firmware='Fixture firmware',
            commit='PRIVATE_BUILD_HASH', disk='100 GiB')


class Report(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        paths = types.ModuleType('emaki_paths')
        paths.LIBDIR = str(self.root / 'lib/emaki')
        paths.LOCALSTATEDIR = str(self.root / 'state')
        loader = importlib.machinery.SourceFileLoader('report_test_helper', str(ROOT / 'scripts/emaki-report'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.report = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, emaki_paths=paths):
            loader.exec_module(self.report)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket, 'socket', side_effect=AssertionError('Network forbidden')))
        self.stack.enter_context(patch.object(self.report.about, 'details', return_value=dict(INFO)))
        self.stack.enter_context(patch.object(self.report.about, 'issue_target', return_value=TARGET))
        self.calls = []
        def command(args):
            self.calls.append(args)
            if args[0] == 'lsblk':
                return 'disk 0 nvme\ndisk 1 sata'
            self.assertEqual(args[0], 'journalctl')
            return 'fixture relevant message'
        self.stack.enter_context(patch.object(self.report.about, 'command', side_effect=command))

    def document(self):
        return self.report.compose('shell')

    def test_schema_hardware_and_private_commit_exclusion(self):
        result = self.document()
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['part'], 'shell')
        self.assertEqual([item['id'] for item in result['items']], IDS)
        for item in result['items']:
            self.assertEqual(set(item), {'id', 'label', 'text', 'default'})
            self.assertIs(item['default'], True)
            self.assertIsInstance(item['text'], str)
        self.assertEqual(set(result['removed']), {'user', 'host', 'email', 'ip', 'mac', 'serial', 'secret', 'lines'})
        hardware = result['items'][3]['text']
        for value in ('Fixture CPU', 'Fixture GPU', '16 GiB'):
            self.assertIn(value, hardware)
        self.assertNotIn('PRIVATE_BUILD_HASH', json.dumps(result))
        self.assertIn(['lsblk', '-dn', '-o', 'TYPE,ROTA,TRAN'], self.calls)

    def test_part_specific_current_boot_logs(self):
        expected = {'shell': {'emaki-shell.service', 'niri-emaki.service'},
                    'update': {'emaki-update.service'},
                    'install': {'emaki-programs-install.service'}}
        for part, units in expected.items():
            with self.subTest(part=part):
                self.calls.clear()
                self.report.compose(part)
                journals = [args for args in self.calls if args[0] == 'journalctl']
                self.assertTrue(journals)
                selected = {args[i + 1] for args in journals for i, arg in enumerate(args) if arg == '-u'}
                selected.update(arg.split('=', 1)[1] for args in journals for arg in args
                                if arg.startswith('_SYSTEMD_UNIT='))
                self.assertTrue(units <= selected)
                self.assertFalse((set().union(*expected.values()) - units) & selected)
                for args in journals:
                    self.assertIn('-b', args)
                    self.assertIn('--no-pager', args)
                    self.assertNotIn('-p', args)
                    self.assertEqual(args[args.index('-n') + 1], '200')
                    self.assertEqual('--user' in args, part == 'shell')
                if part != 'shell':
                    identifier = 'emaki-update-manager' if part == 'update' else 'emaki-programs-engine'
                    self.assertIn('SYSLOG_IDENTIFIER=' + identifier, journals[0])
                    self.assertIn('+', journals[0])

    def test_log_cap_applies_even_to_unbounded_command_output(self):
        with patch.object(self.report.about, 'command', return_value='\n'.join(f'line {n}' for n in range(500))):
            for part in ('shell', 'update', 'install', 'other'):
                document = self.report.compose(part)
                logs = next(item['text'] for item in document['items'] if item['id'] == 'logs')
                self.assertLessEqual(len(logs.splitlines()), 200)

    def test_key_opening_cut_off_by_journal_line_limit(self):
        prefix = '2026-10-13T08:20:00+0000 fixture niri[123]: '
        journal = [prefix + '-----BEGIN PRIVATE KEY-----']
        journal += [prefix + 'U3ludGhldGljUHJpdmF0ZUtleUJvZHk=' for _ in range(205)]
        journal += [prefix + '-----END PRIVATE KEY-----', prefix + 'render loop resumed']

        def command(args):
            if args[0] != 'journalctl':
                return ''
            limit = int(args[args.index('-n') + 1]) if '-n' in args else len(journal)
            return '\n'.join(journal[-limit:])

        with patch.object(self.report.about, 'command', side_effect=command):
            document = self.document()
        logs = next(item['text'] for item in document['items'] if item['id'] == 'logs')
        self.assertNotIn('U3ludGhldGljUHJpdmF0ZUtleUJvZHk=', logs)
        self.assertNotIn('END PRIVATE KEY', logs)
        self.assertIn(prefix + 'render loop resumed', logs)
        self.assertEqual(document['removed']['secret'], 1)

    def test_final_english_labels_and_markers(self):
        with patch.object(self.report.about, 'command', return_value='password=fixture-secret'):
            document = self.document()
        self.assertNotIn('[PLACEHOLDER]', json.dumps(document))
        self.assertIn('[redacted secret]', document['items'][-1]['text'])
        body = parse_qs(urlsplit(self.report.url(document, 'Failure', 'logs')['url']).query)['body'][0]
        self.assertNotIn('[PLACEHOLDER]', body)
        self.assertTrue(body.startswith('### What happened\n'))

    def test_collection_redacts_before_json_and_counts(self):
        planted = ('Authorization: Bearer C8R_SYNTHETIC_SECRET\npassword=C8R_SYNTHETIC_PASSWORD '
                   'email=fixture.person@example.invalid ip=192.0.2.55 ipv6=2001:db8::55 '
                   'mac=aa:bb:cc:dd:ee:ff serial=SERIALFIXTURE77 token=TOKENFIXTURE88')
        with patch.object(self.report.about, 'details', return_value=dict(INFO, processor=planted)), \
                patch.object(self.report.about, 'command', return_value=planted):
            document = self.document()
        encoded = json.dumps(document)
        for secret in ('C8R_SYNTHETIC_SECRET', 'C8R_SYNTHETIC_PASSWORD', 'fixture.person@example.invalid',
                       '192.0.2.55', '2001:db8::55', 'aa:bb:cc:dd:ee:ff', 'SERIALFIXTURE77', 'TOKENFIXTURE88'):
            self.assertNotIn(secret, encoded)
        for kind in ('email', 'ip', 'mac', 'serial', 'secret'):
            self.assertGreater(document['removed'][kind], 0)

    def test_selection_exact_and_description_verbatim(self):
        document = self.document()
        for item in document['items']:
            item['text'] = 'UNIQUE_' + item['id']
        what = '  Failure & context\nUnicode: café # %\n'
        result = self.report.url(document, what, 'logs,emaki')
        parsed = urlsplit(result['url'])
        self.assertEqual(parsed.scheme + '://' + parsed.netloc + parsed.path, TARGET)
        body = parse_qs(parsed.query)['body'][0]
        self.assertIn(what, body)
        self.assertLess(body.index('UNIQUE_emaki'), body.index('UNIQUE_logs'))
        for item in document['items']:
            self.assertEqual(item['text'] in body, item['id'] in ('emaki', 'logs'))
        empty = parse_qs(urlsplit(self.report.url(document, what, '')['url']).query)['body'][0]
        self.assertIn(what, empty)
        self.assertNotIn('UNIQUE_', empty)

    def test_invalid_selection_and_documents(self):
        for items in ('unknown', 'emaki,emaki', 'emaki,', ',kernel'):
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.report.url(self.document(), '', items)
        for document in ({}, [], {'version': 2, 'part': 'shell', 'items': []},
                         dict(self.document(), items=[{'id': 'emaki'}])):
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.report.url(document, '', '')
        with patch.object(self.report.about, 'issue_target', return_value=None), self.assertRaises(ValueError):
            self.report.url(self.document(), '', '')

    def test_exact_encoded_url_limit_and_clipboard_payload(self):
        document = self.document()
        baseline = self.report.url(document, '', '')['url']
        count = 8000 - len(baseline.encode('utf-8'))
        self.assertGreater(count, 0)
        accepted = self.report.url(document, 'a' * count, '')
        self.assertEqual(len(accepted['url'].encode('utf-8')), 8000)
        refused = self.report.url(document, 'a' * (count + 1), '')
        self.assertEqual(set(refused), {'too_big', 'body'})
        self.assertIs(refused['too_big'], True)
        self.assertIn('a' * (count + 1), refused['body'])
        large = self.report.url(document, 'é' * 2000, '')
        self.assertTrue(large['too_big'])
        self.assertGreater(len((TARGET + '?' + urlencode({'body': large['body']}, quote_via=quote)).encode()), 8000)

    def test_main_json_and_errors(self):
        for args, stdin, succeeds in ((['compose', '--part', 'shell', '--since-boot'], '', True),
                                      (['url', '--what', 'A failure', '--items', 'emaki'], json.dumps(self.document()), True),
                                      (['url', '--what', '--example', '--items', ''], json.dumps(self.document()), True),
                                      (['url', '--what', 'Failure', '--items', 'emaki'], '{', False),
                                      (['compose', '--part', 'invalid'], '', False),
                                      (['upload'], '', False)):
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output, \
                    patch.object(sys, 'stdin', io.StringIO(stdin)):
                code = self.report.main(args)
            payload = json.loads(output.getvalue())
            self.assertIsInstance(payload, dict)
            self.assertEqual(code == 0, succeeds)
            if succeeds and '--example' in args:
                self.assertIn('--example', parse_qs(urlsplit(payload['url']).query)['body'][0])
            if not succeeds:
                self.assertIsInstance(payload.get('error'), str)

    def invoke(self, args, stdin=''):
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors, \
                patch.object(sys, 'stdin', io.StringIO(stdin)):
            code = self.report.main(args)
        self.assertEqual(errors.getvalue(), '')
        return code, json.loads(output.getvalue())

    def cli_document(self):
        return dict(version=1, part='other', items=[
            dict(id=name, label=label, text=text, default=True)
            for name, label, text in (
                ('emaki', 'Emaki version', '0.8.0 x'),
                ('channel', 'Update channel', 'testing'),
                ('kernel', 'Linux kernel', '6.0'),
                ('hardware', 'Hardware', 'CPU: x'),
                ('logs', 'Recent logs', 'line ``` with fence\n## heading'))])

    def test_cli_invalid_commands_and_documents(self):
        document = self.cli_document()
        valid = json.dumps(document)
        base = ['url', '--what', 'x', '--items']
        cases = [(args, '', 'invalid_command') for args in (
            ['--help'], ['-h'], ['compose', '--help'], ['send'], ['compose'],
            ['compose', '--part', 'shell', '--bogus'], [],
            ['compose', '--part', 'bogus'])]
        cases.extend((args, valid, 'invalid_command') for args in (
            ['url', '--what', 'x'], ['url', '--items', 'logs']))
        cases.extend((base + [items], valid, 'invalid_items') for items in (
            'logs,bogus', 'logs,logs'))
        cases.append((base + ['logs'], json.dumps(dict(document, items=document['items'][:4])),
                      'invalid_items'))
        cases.extend((base + [''], json.dumps(bad), 'invalid_report') for bad in (
            dict(document, items=document['items'] + document['items'][:1]),
            dict(document, part='bogus'), dict(document, version=True),
            dict(document, version=2), []))
        cases.extend((base + [''], text, 'invalid_report') for text in (
            '', '{not json', '[' * 100000))
        cases.append((base + ['logs'], json.dumps(dict(document, items=document['items'][:4] +
                      [dict(document['items'][4], text=5)])), 'invalid_report'))
        cases.append((base + [''], ' ' * (2 * 1024 * 1024 + 1), 'report_too_large'))
        self.assertEqual(len(cases), 23)
        for args, stdin, error in cases:
            with self.subTest(args=args, error=error, input_length=len(stdin)):
                self.assertEqual(self.invoke(args, stdin), (2, {'error': error}))

    def test_cli_valid_selection_fences_and_dash_descriptions(self):
        document = self.cli_document()
        for what, chosen in (('hello', ','.join(IDS)), ('hello', ''), ('--help', 'logs'),
                             ('--items', '')):
            with self.subTest(what=what, chosen=chosen):
                code, payload = self.invoke(['url', '--what', what, '--items', chosen],
                                            json.dumps(document))
                self.assertEqual(code, 0)
                self.assertEqual(set(payload), {'url'})
                sections = ['### What happened\n\n' + what]
                for item in document['items']:
                    if item['id'] in chosen.split(','):
                        fence = '````' if item['id'] == 'logs' else '```'
                        sections.append('### ' + item['label'] + '\n\n' + fence + '\n' +
                                        item['text'] + '\n' + fence)
                expected = '\n\n'.join(sections) + '\n'
                self.assertEqual(payload['url'], TARGET + '?' + urlencode({'body': expected}, quote_via=quote))

    def test_cli_compose_schema_and_exact_url_boundaries(self):
        code, payload = self.invoke(['compose', '--part', 'shell', '--since-boot'])
        self.assertEqual(code, 0)
        self.assertEqual(set(payload), {'version', 'part', 'items', 'removed'})
        self.assertEqual((payload['version'], payload['part']), (1, 'shell'))
        self.assertEqual([item['id'] for item in payload['items']], IDS)
        self.assertTrue(all(set(item) == {'id', 'label', 'text', 'default'} for item in payload['items']))
        self.assertEqual(set(payload['removed']), {'user', 'host', 'email', 'ip', 'mac', 'serial', 'secret', 'lines'})
        small = dict(version=1, part='shell', items=[
            dict(id='emaki', label='Emaki version', text='', default=True)])
        base = len((TARGET + '?' + urlencode({'body': '### What happened\n\n\n'}, quote_via=quote)).encode())
        for size, document in ((8000 - base, small), (8001 - base, small), (9000, self.cli_document())):
            with self.subTest(size=size):
                what = 'a' * size
                code, payload = self.invoke(['url', '--what', what, '--items', ''], json.dumps(document))
                self.assertEqual(code, 0)
                body = '### What happened\n\n' + what + '\n'
                if size + base <= 8000:
                    self.assertEqual(payload, {'url': TARGET + '?' + urlencode({'body': body}, quote_via=quote)})
                    self.assertEqual(len(payload['url'].encode()), 8000)
                else:
                    self.assertEqual(payload, {'too_big': True, 'body': body})

    def test_cli_sparse_document_contract(self):
        document = dict(version=1, part='shell', items=[
            dict(id='emaki', label='Emaki version', text='0.8.0 x', default=True),
            dict(id='logs', label='Recent logs of the shell', text='a ``` b\nline2', default=True),
            dict(id='kernel', label='Linux kernel', text='7.2', default=True)])
        raw = json.dumps(document)
        base = ['url', '--what', 'x', '--items']
        failures = [(args, raw, 'invalid_command') for args in (
            ['--help'], ['compose', '--help'], ['compose'], ['url', '--what', 'x'],
            ['url', '--items', ''], ['upload'], ['compose', '--part', 'shell', 'extra'])]
        failures.extend((base + [selection], raw, 'invalid_items')
                        for selection in ('emaki,', 'hardware'))
        failures.extend((base + [''], json.dumps(invalid), 'invalid_report') for invalid in (
            dict(document, items=[{'id': 'emaki'}]), dict(document, part='nope'),
            dict(document, version=True)))
        failures.extend((base + [''], text, error) for text, error in (
            (' ' * (2 * 1024 * 1024 + 1), 'report_too_large'),
            ('[' * 100000, 'invalid_report'), ('', 'invalid_report')))
        self.assertEqual(len(failures), 15)
        for args, stdin, error in failures:
            with self.subTest(args=args, error=error, input_length=len(stdin)):
                self.assertEqual(self.invoke(args, stdin), (2, {'error': error}))
        for what, selection, expected in (
            ('Boom -x', 'logs,emaki', '### What happened\n\nBoom -x\n\n'
             '### Emaki version\n\n```\n0.8.0 x\n```\n\n'
             '### Recent logs of the shell\n\n````\na ``` b\nline2\n````\n'),
            ('', '', '### What happened\n\n\n')):
            with self.subTest(what=what, selection=selection):
                self.assertEqual(self.invoke(['url', '--what', what, '--items', selection], raw),
                                 (0, {'url': TARGET + '?' + urlencode({'body': expected}, quote_via=quote)}))
        with patch.object(self.report.about, 'issue_target', return_value=None):
            self.assertEqual(self.invoke(base + [''], raw), (2, {'error': 'issue_target_unavailable'}))


class CommandLine(unittest.TestCase):
    def test_isolated_executables_no_network_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('emaki-report', 'emaki-settings-about', 'emaki_report_redact.py'):
                shutil.copy(ROOT / 'scripts' / name, root / name)
            library = root / 'lib/emaki'
            library.mkdir(parents=True)
            (library / 'os-release').write_text(
                'VARIANT_ID=emaki\nBUG_REPORT_URL="https://github.com/emakish/emaki/issues"\n')
            (library.parent / 'emaki-release').write_text('VERSION=0.8.0\nCHANNEL=testing\n')
            (root / 'emaki_paths.py').write_text(
                f'LIBDIR = {str(library)!r}\nLOCALSTATEDIR = {str(root / "state")!r}\n')
            fake = """import os, sys
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]
if name == 'journalctl':
    assert '-b' in args and '--no-pager' in args and '-p' not in args
    if '--user' in args:
        print('SHELL_FIXTURE')
    elif '_SYSTEMD_UNIT=emaki-update.service' in args:
        print('UPDATE_FIXTURE')
    elif '_SYSTEMD_UNIT=emaki-programs-install.service' in args:
        print('INSTALL_FIXTURE')
    else:
        raise SystemExit(2)
    print('Authorization: Bearer C8R_SYNTHETIC_SECRET password=C8R_SYNTHETIC_PASSWORD')
elif name == 'lsblk':
    assert args == ['-dn', '-o', 'TYPE,ROTA,TRAN']
    print('disk 0 nvme')
elif name == 'lspci':
    print('00:01.0 "VGA compatible controller" "Fixture" "GPU"')
elif name == 'niri':
    print('niri fixture')
else:
    raise SystemExit(2)
"""
            for name in ('journalctl', 'lsblk', 'lspci', 'niri'):
                path = root / name
                path.write_text('#!' + sys.executable + '\n' + fake)
                path.chmod(0o700)
            runner = root / 'runner.py'
            runner.write_text("""import os, runpy, sys
sys.dont_write_bytecode = True
def audit(event, args):
    if event.startswith('socket.') and event != 'socket.gethostname':
        raise AssertionError('Network forbidden')
    if event == 'open':
        mode, flags = args[1:3]
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)):
            raise AssertionError('Writes forbidden')
    if event in ('os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.system'):
        raise AssertionError('Mutation forbidden')
    if event == 'subprocess.Popen' and args[0] not in ('journalctl', 'lsblk', 'lspci', 'niri'):
        raise AssertionError('Unexpected command')
sys.addaudithook(audit)
helper = os.path.join(os.path.dirname(__file__), 'emaki-report')
sys.argv = [helper] + sys.argv[1:]
runpy.run_path(helper, run_name='__main__')
""")
            env = dict(os.environ, PATH=str(root), HOME=str(root), XDG_STATE_HOME=str(root / 'state'),
                       PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(root))
            before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            for part in ('shell', 'update', 'install'):
                result = subprocess.run([sys.executable, str(runner), 'compose', '--part', part],
                                        cwd=root, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                document = json.loads(result.stdout)
                logs = next(item['text'] for item in document['items'] if item['id'] == 'logs')
                self.assertIn(part.upper() + '_FIXTURE', logs)
                self.assertNotIn('C8R_SYNTHETIC', result.stdout)
                result = subprocess.run([sys.executable, str(runner), 'url', '--what', 'Failure',
                                         '--items', 'logs'], input=result.stdout, cwd=root, env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Failure', parse_qs(urlsplit(json.loads(result.stdout)['url']).query)['body'][0])
            after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            self.assertEqual(before, after)
            for name in ('journalctl', 'lsblk', 'lspci', 'niri'):
                (root / name).unlink()
            result = subprocess.run([sys.executable, str(runner), 'compose', '--part', 'shell'],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['version'], 1)


if __name__ == '__main__':
    unittest.main()
