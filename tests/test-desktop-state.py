#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The desktop state interface: the backend writes and answers it, the session reads only it.

Writer: `emaki-session-update` records an update from the hook's targets and answers `state`.
Reader: `emaki-session-files` turns the answer into "a new session is needed". Mutants prove
that a stale record is ignored and that a missing record means no update. No root, no network,
no installed-system writes.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
BOOT = '11111111-1111-4111-8111-111111111111'


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(ROOT / 'scripts' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


update = load('emaki-session-update')
files = load('emaki-session-files')


class Fixture(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='desktop-state-', dir=evidence)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.packages = self.base / 'local'
        self.packages.mkdir()
        self.marker = self.base / 'marker'
        self.boot = BOOT
        for module, name, value in ((update.arch, 'PACKAGES', self.packages),
                                    (update.arch, 'LOCK', self.base / 'db.lck'),
                                    (update, 'MARKER', self.marker),
                                    (update.state, 'LIVE', self.base / 'live')):
            fixture = patch.object(module, name, value)
            fixture.start()
            self.addCleanup(fixture.stop)
        for module, name, value in ((update.state, 'boot_id', lambda: self.boot),
                                    (update.arch.session, 'readonly_root', lambda: False)):
            fixture = patch.object(module, name, side_effect=value)
            fixture.start()
            self.addCleanup(fixture.stop)
        self.busy = False
        running = patch.object(update.arch, 'transaction_running', side_effect=lambda: self.busy)
        running.start()
        self.addCleanup(running.stop)

    def install(self, entry):
        (self.packages / entry).mkdir()
        (self.packages / entry / 'desc').write_text('%NAME%\n' + entry.rsplit('-', 2)[0] + '\n')

    def state(self):
        return update.desktop_state()


class Writer(Fixture):
    def test_hook_records_its_session_targets_with_time_and_action(self):
        stdin = io.StringIO('niri\nqt6-base\nfirefox\n')
        stdin.isatty = lambda: False
        with patch.object(update.sys, 'stdin', stdin):
            components = update.targets()
        self.assertEqual(components, ['niri', 'qt6-base'])
        token = update.mark_update(self.marker, components)
        answer = self.state()
        self.assertEqual(answer['schema'], 1)
        self.assertIs(answer['busy'], False)
        self.assertRegex(answer['update'].pop('at'), r'\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00\Z')
        self.assertEqual(answer['update'], dict(id=token, components=['niri', 'qt6-base'], action='sign-out'))

    def test_hook_without_targets_records_an_update_with_no_components(self):
        with patch.object(update.sys, 'stdin', None):
            self.assertEqual(update.targets(), [])

    def test_missing_record_means_no_update(self):
        self.assertIsNone(self.state()['update'])
        self.marker.write_text('')
        self.assertIsNone(self.state()['update'])

    def test_stale_record_of_another_boot_or_root_is_ignored(self):
        update.mark_update(self.marker, ['niri'])
        self.assertIsNotNone(self.state()['update'])
        self.boot = '22222222-2222-4222-8222-222222222222'
        self.assertIsNone(self.state()['update'])
        self.boot = BOOT
        with patch.object(update.state, 'root_id', return_value=[0, 0]):
            self.assertIsNone(self.state()['update'])
        for malformed in ('not json', '{}', 'null', '[]', json.dumps(dict(root=[0, 0], boot=BOOT, transaction=1))):
            with self.subTest(malformed=malformed):
                self.marker.write_text(malformed)
                self.assertIsNone(self.state()['update'])

    def test_previous_release_record_still_asks_for_a_new_session(self):
        record = dict(root=update.state.root_id(), boot=BOOT, transaction='old-token')
        self.marker.write_text(json.dumps(record))
        self.assertEqual(self.state()['update'], dict(id='old-token', at=None, components=[], action='sign-out'))

    def test_desktop_identity_follows_desktop_packages_only(self):
        self.install('qt6-base-6.10.0-1')
        self.install('firefox-140.0-1')
        first = self.state()['desktop']
        self.install('htop-3.4-1')
        self.assertEqual(self.state()['desktop'], first, 'an unrelated package changed the desktop')
        (self.packages / 'qt6-base-6.10.0-1/desc').write_text('%NAME%\nqt6-base\n\n%REASON%\n1\n')
        self.assertEqual(self.state()['desktop'], first, 'an install reason changed the desktop')
        (self.packages / 'qt6-base-6.10.0-1').rename(self.packages / 'qt6-base-6.10.0-2')
        second = self.state()['desktop']
        self.assertNotEqual(second, first)
        self.install('niri-emaki-26.04-12')
        self.assertNotEqual(self.state()['desktop'], second)
        shutil.rmtree(self.packages / 'niri-emaki-26.04-12')
        self.assertEqual(self.state()['desktop'], second)

    def test_busy_is_the_backend_transaction_answer(self):
        self.busy = True
        self.assertIs(self.state()['busy'], True)

    def test_state_command_prints_json_for_an_ordinary_user(self):
        # The system map state command, run for real against this machine (read-only).
        result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/emaki-session-update'), 'state'],
                                capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(sorted(answer), ['busy', 'desktop', 'schema', 'update'])
        self.assertEqual(answer['schema'], 1)
        self.assertIsInstance(answer['busy'], bool)
        self.assertRegex(answer['desktop'], r'\A[0-9a-f]{24}\Z')


class Reader(Fixture):
    def setUp(self):
        super().setUp()
        self.source = self.base / 'installed'
        self.source.mkdir()
        (self.source / 'shell.qml').write_text('QML')
        self.binary = self.base / 'emaki'
        self.binary.write_text('core')
        self.answer = lambda: json.dumps(self.state())
        for fixture in (patch.object(files, 'query_state', side_effect=lambda: self.answer()),
                        patch.object(files.state, 'LIVE', self.base / 'live'),
                        patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.base / 'runtime'), NIRI_SOCKET='session')):
            fixture.start()
            self.addCleanup(fixture.stop)

    def login(self):
        return files.prepare(self.source, self.binary).parent

    def test_a_new_update_asks_for_a_new_session(self):
        generation = self.login()
        self.assertFalse(files.changed(generation))
        update.mark_update(self.marker, ['quickshell-emaki'])
        self.assertTrue(files.changed(generation))
        self.assertTrue(files.changed_installed(self.source))

    def test_missing_record_means_no_update(self):
        generation = self.login()
        self.assertFalse(self.marker.exists())
        self.assertFalse(files.changed(generation))
        self.assertFalse(files.changed_installed(self.source))

    def test_record_from_before_login_is_stale(self):
        update.mark_update(self.marker, ['niri'])
        generation = self.login()
        self.assertFalse(files.changed(generation))
        self.assertFalse(files.changed_installed(self.source))

    def test_one_failed_answer_at_login_is_not_an_update(self):
        update.mark_update(self.marker, ['niri'])
        answers = iter(['not json'])
        self.answer = lambda: next(answers, None) or json.dumps(self.state())
        generation = self.login()
        self.assertTrue((generation.parent / 'session.json').exists())
        self.assertFalse(files.changed(generation))
        update.mark_update(self.marker, ['niri'])
        self.assertTrue(files.changed(generation))

    def test_record_of_another_boot_is_stale(self):
        generation = self.login()
        update.mark_update(self.marker, ['niri'])
        self.boot = '22222222-2222-4222-8222-222222222222'
        self.assertFalse(files.changed(generation))

    def test_backend_action_none_is_not_a_reason_to_sign_out(self):
        generation = self.login()
        token = update.mark_update(self.marker, ['niri'])
        answer = self.state()
        answer['update'] = dict(id=token, at=None, components=['niri'], action='none')
        self.answer = lambda: json.dumps(answer)
        self.assertFalse(files.changed(generation))

    def test_no_notice_while_busy_or_without_an_answer(self):
        generation = self.login()
        update.mark_update(self.marker, ['niri'])
        self.busy = True
        self.assertFalse(files.changed(generation))
        self.busy = False
        for broken in ('', 'not json', '{}', json.dumps(dict(schema=2, busy=False, desktop='x', update=None)),
                       json.dumps(dict(schema=1, busy='no', desktop='x', update=None))):
            with self.subTest(answer=broken):
                self.answer = lambda: broken
                self.assertIsNone(files.answer())
                self.assertFalse(files.changed(generation))

    def test_lost_record_after_login_is_not_an_update(self):
        update.mark_update(self.marker, ['niri'])
        generation = self.login()
        for lost in (None, '', 'not json', '{}', json.dumps(dict(root=[0, 0], boot=BOOT, transaction='x'))):
            with self.subTest(record=lost):
                if lost is None:
                    self.marker.unlink()
                else:
                    self.marker.write_text(lost)
                self.assertIsNone(self.state()['update'])
                self.assertFalse(files.changed(generation))
                self.assertFalse(files.changed_installed(self.source))
        update.mark_update(self.marker, ['niri'])
        self.assertTrue(files.changed(generation))
        self.assertTrue(files.changed_installed(self.source))

    def unanswered_login(self):
        def failed():
            raise subprocess.CalledProcessError(75, ['emaki-session-update', 'state'])
        self.answer = failed
        generation = self.login()
        self.assertFalse((generation.parent / 'session.json').exists())
        return generation

    def watch(self, generation, installed_source, polls=2):
        """The printed messages of `polls` watcher rounds."""
        with patch.object(files.time, 'sleep', side_effect=[None] * (polls - 1) + [StopIteration]), \
                patch('builtins.print') as output:
            try:
                files.watch(generation, installed_source)
            except StopIteration:
                pass
        return [json.loads(call.args[0]) for call in output.call_args_list]

    def test_backend_recovery_without_a_baseline_is_not_an_update(self):
        for installed_source in (True, False):
            with self.subTest(installed_source=installed_source), \
                    patch.dict(os.environ, NIRI_SOCKET='session-' + str(installed_source)):
                update.mark_update(self.marker, ['niri'])
                generation = self.unanswered_login()
                watched = self.source if installed_source else generation
                self.assertEqual(self.watch(watched, installed_source), [])
                self.answer = lambda: json.dumps(self.state())
                self.assertEqual(self.watch(watched, installed_source), [])
                self.assertTrue((generation.parent / 'session.json').exists())
                self.install('niri-emaki-26.04-' + str(int(installed_source)))
                self.assertEqual(self.watch(watched, installed_source), [dict(schema=1, restart=True)])

    def test_corrupt_baseline_is_replaced_not_reported(self):
        generation = self.login()
        baseline = generation.parent / 'session.json'
        for corrupt in ('', 'not json', '[]', '{}', json.dumps(dict(desktop=1)),
                        json.dumps(dict(desktop='x', transaction=1))):
            with self.subTest(baseline=corrupt):
                baseline.write_text(corrupt)
                self.assertFalse(files.changed(generation))
                self.assertFalse(files.changed_installed(self.source))
                self.assertEqual(json.loads(baseline.read_text()), files.installed(self.state()))
        update.mark_update(self.marker, ['niri'])
        self.assertTrue(files.changed(generation))
        self.assertTrue(files.changed_installed(self.source))

    def test_changed_files_are_reported_without_a_baseline(self):
        generation = self.login()
        (generation.parent / 'session.json').unlink()
        (self.source / 'shell.qml').write_text('new QML')
        self.assertTrue(files.changed(generation))

    def test_state_command_by_name_unless_installed_beside(self):
        directory = self.base / 'generation'
        directory.mkdir()
        shutil.copy2(ROOT / 'scripts/emaki-session-files', directory / 'watch.py')
        loader = importlib.machinery.SourceFileLoader('watch_copy', str(directory / 'watch.py'))
        copy = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(copy)
        with patch.object(copy.subprocess, 'run') as run:
            run.return_value.stdout = '{}'
            copy.query_state()
            self.assertEqual(run.call_args.args[0], ['emaki-session-update', 'state'])
            beside = directory / 'emaki-session-update'
            beside.write_text('#!/bin/sh\n')
            beside.chmod(0o700)
            copy.query_state()
            self.assertEqual(run.call_args.args[0], [str(beside), 'state'])

    def test_session_side_never_reads_the_package_manager(self):
        for path in (ROOT / 'scripts/emaki-session-files', ROOT / 'scripts/emaki_session_state.py',
                     ROOT / 'shell/SessionUpdateNotice.qml'):
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_text(), r'pacman|/var/lib/pacman|vercmp')


# Each mutant breaks one promise; the named test must then fail on an assertion.
MUTANTS = (
    ('scripts/emaki_session_state.py', "value['root'] == root_id(ROOT) and value['boot'] == boot_id()",
     "value['root'] == root_id(ROOT)", 'Writer.test_stale_record_of_another_boot_or_root_is_ignored'),
    ('scripts/emaki_session_state.py', "value['root'] == root_id(ROOT) and value['boot'] == boot_id()",
     "value['root'] == root_id(ROOT)", 'Reader.test_record_of_another_boot_is_stale'),
    ('scripts/emaki-session-files', "    before = installed(value)\n    if value['busy']:",
     "    before = dict(desktop=value['desktop'])\n    if value['busy']:",
     'Reader.test_record_from_before_login_is_stale'),
    ('scripts/emaki-session-files', '    remember(baseline, after)\n', '',
     'Reader.test_one_failed_answer_at_login_is_not_an_update'),
    ('scripts/emaki-session-update', '    update = None\n    if record is not None:',
     "    update = dict(id='missing', at=None, components=[], action=SIGN_OUT)\n    if record is not None:",
     'Writer.test_missing_record_means_no_update'),
    ('scripts/emaki-session-files', "    return None if state.LIVE.exists() or value is None or value['busy'] else value",
     "    value = value or dict(schema=1, busy=False, desktop='', update=None)\n"
     "    return None if state.LIVE.exists() or value['busy'] else value",
     'Reader.test_backend_recovery_without_a_baseline_is_not_an_update'),
    ('scripts/emaki-session-files', "    if update is not None and update['action'] != 'none':",
     '    if update is not None:', 'Reader.test_backend_action_none_is_not_a_reason_to_sign_out'),
    ('scripts/emaki-session-files', "            or 'transaction' in now and now['transaction'] != before.get('transaction'))",
     "            or now.get('transaction') != before.get('transaction'))",
     'Reader.test_lost_record_after_login_is_not_an_update'),
    ('scripts/emaki-session-files', '    stale = before is not None and differs(before, installed(value))\n',
     '    stale = before is None or differs(before, installed(value))\n',
     'Reader.test_backend_recovery_without_a_baseline_is_not_an_update'),
    ('scripts/emaki-session-files', '        remember(baseline, value)\n', '        pass\n',
     'Reader.test_backend_recovery_without_a_baseline_is_not_an_update'),
    ('scripts/emaki-session-files', '        baseline.unlink(missing_ok=True)\n', '',
     'Reader.test_corrupt_baseline_is_replaced_not_reported'),
)


class Mutants(unittest.TestCase):
    def test_each_mutant_fails_its_test(self):
        for relative, old, new, test in MUTANTS:
            with self.subTest(mutant=relative + ': ' + test), \
                    tempfile.TemporaryDirectory(prefix='desktop-state-mutant-') as directory:
                tree = Path(directory)
                for name in ('scripts/emaki-session-files', 'scripts/emaki-session-update', 'scripts/emaki_paths.py',
                             'scripts/emaki_session_state.py', 'scripts/emaki_session_arch.py',
                             'update-manager/catalog.py', 'shell/SessionUpdateNotice.qml',
                             'tests/test-desktop-state.py'):
                    (tree / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / name, tree / name)
                path = tree / relative
                source = path.read_text()
                self.assertEqual(source.count(old), 1, (relative, old))
                path.write_text(source.replace(old, new))
                result = subprocess.run([sys.executable, '-B', str(tree / 'tests/test-desktop-state.py'), test],
                                        capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0, (relative, test, 'mutant survived'))
                self.assertIn('FAIL:', result.stderr, (relative, test, result.stderr[-2000:]))


if __name__ == '__main__':
    unittest.main()
