#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The privileged AUR helper: reviewed plans only, sandboxed builds, exact installs."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path
import sys
import tempfile
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_aur_build as helper

TOKEN = 'a' * 64
COMMIT = 'c' * 40
PLAN = dict(kind='aur', target='paint', token=TOKEN, fullUpgrade=True,
            packages=[dict(package='gtk3', purpose='run'), dict(package='cmake', purpose='build')],
            builds=[dict(packageBase='libbrush', commit='d' * 40, packages=[dict(package='libbrush', version='1-1')]),
                    dict(packageBase='paint', commit=COMMIT, packages=[dict(package='paint', version='2-1')])])


class StreamTests(unittest.TestCase):
    def measured_stream(self, script, relay):
        with patch.object(helper, 'environment', return_value=dict(os.environ)), \
                patch.object(helper, 'progress', new=relay):
            tracemalloc.start()
            try:
                result = helper.stream([sys.executable, '-c', script], prefix='build: ')
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        return result, peak

    def test_twenty_thousand_lines_have_bounded_memory_and_keep_early_hook_failure(self):
        count = 0

        def relay(line):
            nonlocal count
            count += 1
            self.assertTrue(line.startswith('build: '))
            self.assertLessEqual(len(line), 2055)

        (status, lines), peak = self.measured_stream(
            "import sys\n"
            "sys.stdout.write('ERROR: command failed to execute correctly\\n')\n"
            "for i in range(20000):\n"
            " sys.stdout.write(str(i).zfill(5) + 'x' * 2042 + '\\n')\n", relay)
        self.assertEqual(status, 1)
        self.assertEqual(count, 20001)
        self.assertEqual(lines, [str(i).zfill(5) + 'x' * 2042 for i in range(19800, 20000)])
        self.assertLess(peak, 2 * 1024 * 1024, f'peak memory: {peak} bytes')

    def test_huge_unterminated_line_is_truncated_without_buffering_it(self):
        relayed = []
        (status, lines), peak = self.measured_stream(
            "import sys\n"
            "sys.stdout.buffer.write(b'\\t\\x00' + 'caf\u00e9'.encode() + b'\\xff')\n"
            "for i in range(10000):\n"
            " sys.stdout.buffer.write(b'x' * 4096)\n", relayed.append)
        expected = '\tcaf\u00e9\ufffd' + 'x' * (2048 - 6)
        self.assertEqual((status, lines), (0, [expected]))
        self.assertEqual(relayed, ['build: ' + expected])
        self.assertLess(peak, 2 * 1024 * 1024, f'peak memory: {peak} bytes')


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_strict_arguments_refuse_before_any_work(self):
        for argv in ([], ['paint'], ['--root', TOKEN], ['paint', 'short'], ['--run', 'paint'],
                     ['--build', 'paint', 'HEAD'], ['--build', '../x', COMMIT], ['paint', TOKEN, 'extra']):
            with self.subTest(argv=argv), patch.object(helper.subprocess, 'run') as run, \
                    patch.object(helper.subprocess, 'Popen') as popen:
                self.assertEqual(helper.main(argv), 1)
                run.assert_not_called()
                popen.assert_not_called()

    def test_ordinary_users_never_reach_the_transaction(self):
        with patch.object(helper.os, 'geteuid', return_value=1000), patch.object(helper, 'request') as request, \
                patch.object(helper, 'worker') as worker:
            self.assertEqual(helper.main(['paint', TOKEN]), 1)
            request.assert_not_called()
            worker.assert_not_called()

    def test_a_recipe_never_builds_as_root(self):
        with patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                helper.build('paint', COMMIT)
            run.assert_not_called()

    def test_signing_keys_come_only_from_the_reviewed_recipe_metadata(self):
        text = ('pkgbase = paint\n\tvalidpgpkeys = 0123456789ABCDEF0123456789ABCDEF01234567\n'
                '\tvalidpgpkeys = not-a-key\n\tvalidpgpkeys = 0123456789abcdef0123456789abcdef01234567\n')
        self.assertEqual(helper.srcinfo_keys(text), ['0123456789ABCDEF0123456789ABCDEF01234567'])

    def test_each_build_runs_as_a_throwaway_user_without_a_home_folder(self):
        lines = ['==> Making package: paint', json.dumps(dict(type='built', files=['paint-2-1-x86_64.pkg.tar.zst']))]
        with patch.object(helper, 'stream', return_value=(0, lines)) as stream:
            unit, files = helper.sandbox_build('paint', COMMIT)
        command = stream.call_args.args[0]
        for value in ('DynamicUser=yes', 'ProtectHome=yes', 'NoNewPrivileges=yes', 'ProtectSystem=strict',
                      'PrivateTmp=yes', 'CacheDirectory=' + helper.BUILD_CACHE):
            self.assertIn('--property=' + value, command)
        self.assertEqual(command[-3:], ['--build', 'paint', COMMIT])
        self.assertTrue(unit.startswith('emaki-programs-aur-build-'))
        self.assertEqual(files, ['paint-2-1-x86_64.pkg.tar.zst'])
        with patch.object(helper, 'stream', return_value=(1, lines)), self.assertRaises(RuntimeError):
            helper.sandbox_build('paint', COMMIT)

    def test_only_the_planned_package_names_are_installed(self):
        with tempfile.TemporaryDirectory(prefix='aur-collect-') as temporary:
            output = Path(temporary) / 'private' / helper.BUILD_CACHE / 'build' / 'out'
            output.mkdir(parents=True)
            for name in ('paint-2-1-x86_64.pkg.tar.zst', 'paint-debug-2-1-x86_64.pkg.tar.zst', 'notes.txt'):
                (output / name).write_text(name)
            names = {'paint-2-1-x86_64.pkg.tar.zst': 'paint 2-1', 'paint-debug-2-1-x86_64.pkg.tar.zst': 'paint-debug 2-1'}

            def query(arguments, timeout=None):
                return SimpleNamespace(returncode=0, stdout=names[Path(arguments[-1]).name] + '\n')
            real_path = Path

            def located(value, *rest):
                if str(value) == '/var/cache/private':
                    return real_path(temporary) / 'private'
                return real_path(value, *rest)
            with patch.object(helper, 'run', side_effect=query), patch.object(helper, 'Path', side_effect=located), \
                    patch.object(helper, 'PACKAGES', Path(temporary) / 'root'):
                directory, chosen = helper.collect('paint', sorted(names) + ['notes.txt'], ['paint'])
                self.assertEqual(list(chosen), ['paint'])
                self.assertEqual(chosen['paint'][1], '2-1')
                self.assertTrue(chosen['paint'][0].startswith(str(directory)))
                with self.assertRaises(RuntimeError):
                    helper.collect('paint', sorted(names), ['paint', 'paint-docs'])

    def worker(self, plan=PLAN, statuses=None, failing_build=None, cleanup_error=None, snapshot_error=None,
               leave_tools=False, receipt_error=None, unrelated_during_build=False, installed=None, external_changes=None, snapshot_errors=None, discard_ok=True):
        calls = []
        statuses = dict(statuses or {})
        state = dict({'base': '1'}, **(installed or {}))

        def stream(arguments, prefix=''):
            calls.append(list(arguments))
            status = statuses.get(arguments[0] if arguments[0] != 'systemd-inhibit' else 'update', 0)
            if not status:
                if arguments[0] == 'systemd-inhibit':
                    state['base'] = '2'
                    state['updated-dependency'] = '2'
                elif '-S' in arguments:
                    state.update(gtk3='1', cmake='1')
                elif '-U' in arguments:
                    for value in arguments[arguments.index('--') + 1:]:
                        state[Path(value).name.split('.')[0]] = '9-1'
                elif '-Rn' in arguments:
                    for name in arguments[arguments.index('--') + 1:]:
                        state.pop(name, None)
            return status, []

        def query(arguments, timeout=None):
            targets = arguments[arguments.index('--') + 1:]
            names = [Path(value).name.split('.')[0] if '/' in value else value for value in targets]
            return SimpleNamespace(returncode=0, stdout=''.join(name + ' 9-1\n' for name in names), stderr='')

        def build(base, commit):
            calls.append(['build', base, commit])
            if unrelated_during_build:
                state['unrelated'] = '1'
            for name, version in (external_changes or {}).items():
                if version is None:
                    state.pop(name, None)
                else:
                    state[name] = version
            if base == failing_build:
                raise RuntimeError('build failed')
            return 'unit', [base + '.pkg.tar.zst']

        def collect(base, files, wanted):
            return Path('/nonexistent'), {name: ('/pkg/' + name + '.pkg.tar.zst', '9-1') for name in wanted}

        def cleanup(before, additions):
            if cleanup_error:
                raise RuntimeError(cleanup_error)
            if leave_tools:
                return []
            state.pop('cmake', None)
            return ['cmake']
        receipts = SimpleNamespace(record=Mock(return_value={'id': 'r'}, side_effect=receipt_error),
                                   snapshot_states=[], discarded=[])

        def create_snapshot():
            receipts.snapshot_states.append(dict(state))
            error = (snapshot_errors or {}).get(len(receipts.snapshot_states), snapshot_error)
            if error:
                raise error
            number = str(11 + len(receipts.snapshot_states))
            return number, number + ':' + TOKEN

        def discard(number):
            receipts.discarded.append(number)
            return discard_ok
        modules = (SimpleNamespace(BINDIR='/bin', ENV={'PATH': '/bin'}), SimpleNamespace(Refused=ValueError),
                   receipts, None)
        apply = SimpleNamespace(changed=Mock(return_value=(False, False)))
        lock = tempfile.TemporaryFile(mode='w')
        self.addCleanup(lock.close)
        with patch.object(helper, 'modules', return_value=modules), \
                patch.object(helper.os, 'open', return_value=10), \
                patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), \
                patch.object(helper.fcntl, 'flock'), patch.object(helper, 'require_update_root'), \
                patch.object(helper, 'reviewed_plan', return_value=plan), \
                patch.object(helper, 'preinstall_snapshot', side_effect=create_snapshot) as snapshot, \
                patch.object(helper, 'discard_snapshot', side_effect=discard, create=True), \
                patch.object(helper, 'versions', side_effect=lambda: dict(state)), \
                patch.object(helper, 'run', side_effect=query), patch.object(helper, 'stream', side_effect=stream), \
                patch.object(helper, 'sandbox_build', side_effect=build), \
                patch.object(helper, 'collect', side_effect=collect), \
                patch.object(helper, 'remove_leftovers', side_effect=cleanup) as leftovers, \
                patch.object(helper, 'update_backend', return_value=apply):
            status = helper.worker('paint', TOKEN)
        return status, calls, receipts, leftovers, state

    def test_reviewed_plan_updates_first_builds_in_order_and_records_the_install(self):
        status, calls, receipts, leftovers, state = self.worker()
        self.assertEqual(status, 0)
        self.assertEqual(calls[0][0], 'systemd-inhibit')
        self.assertEqual(calls[0][-2:], ['/bin/emaki-update', '--noninteractive'])
        self.assertEqual(calls[1:], [
            ['pacman', '-S', '--needed', '--noconfirm', '--', 'gtk3', 'cmake'],
            ['pacman', '-D', '--asdeps', '--', 'cmake', 'gtk3'],
            ['build', 'libbrush', 'd' * 40],
            ['pacman', '-U', '--needed', '--noconfirm', '--', '/pkg/libbrush.pkg.tar.zst'],
            ['pacman', '-D', '--asdeps', '--', 'libbrush'],
            ['build', 'paint', COMMIT],
            ['pacman', '-U', '--needed', '--noconfirm', '--', '/pkg/paint.pkg.tar.zst'],
            ['pacman', '-D', '--asdeps', '--', 'paint'],
            ['pacman', '-D', '--asexplicit', '--', 'paint']])
        leftovers.assert_called_once_with({'base': '2', 'updated-dependency': '2'}, {'gtk3', 'cmake', 'libbrush', 'paint'})
        self.assertEqual(receipts.record.call_args.args[:4], ('aur', 'aur:paint', 'paint', 'paint'))
        self.assertEqual(receipts.record.call_args.args[4], {'base': '2', 'updated-dependency': '2'})
        self.assertEqual(receipts.record.call_args.args[6], '13')
        self.assertEqual(receipts.record.call_args.kwargs, {'snapshot_token': '13:' + TOKEN})
        result = self.events()[-1]
        self.assertTrue(result['ok'])
        self.assertEqual((result['receipt'], result['removedAfterBuild']), ('r', ['cmake']))

    def test_snapshot_preflight_is_discarded_and_install_point_follows_update(self):
        status, calls, receipts, leftovers, state = self.worker()
        self.assertEqual(status, 0)
        self.assertEqual(receipts.snapshot_states,
                         [{'base': '1'}, {'base': '2', 'updated-dependency': '2'}])
        self.assertEqual(receipts.discarded, ['12'])

    def test_preflight_cleanup_failure_refuses_before_update(self):
        status, calls, receipts, leftovers, state = self.worker(discard_ok=False)
        self.assertEqual(status, 1)
        self.assertEqual(calls, [])
        self.assertTrue(self.events()[-1]['notStarted'])

    def test_second_snapshot_failure_keeps_committed_update_and_installs_nothing(self):
        status, calls, receipts, leftovers, state = self.worker(
            snapshot_errors={2: RuntimeError('snapshot failed')})
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})
        receipts.record.assert_not_called()
        self.assertEqual(receipts.discarded, ['12'])

    def test_build_failure_without_package_changes_discards_install_point(self):
        status, calls, receipts, leftovers, state = self.worker(
            plan=dict(PLAN, packages=[]), failing_build='libbrush')
        self.assertEqual(status, 1)
        self.assertEqual(receipts.discarded, ['12', '13'])
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})

    def test_a_failed_system_update_builds_nothing(self):
        status, calls, receipts, leftovers, state = self.worker(statuses={'update': 1})
        self.assertEqual(status, 1)
        self.assertEqual(len(calls), 1)
        receipts.record.assert_not_called()
        leftovers.assert_not_called()

    def test_failed_build_removes_exact_additions_and_keeps_committed_update(self):
        status, calls, receipts, leftovers, state = self.worker(failing_build='paint')
        self.assertEqual(status, 1)
        self.assertEqual(calls[-1], ['pacman', '-Rn', '--noconfirm', '--', 'cmake', 'gtk3', 'libbrush'])
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})
        receipts.record.assert_not_called()
        self.assertEqual(self.events()[-1]['code'], 'failed')

    def test_failed_build_rollback_preserves_an_unrelated_concurrent_addition(self):
        status, calls, receipts, leftovers, state = self.worker(failing_build='libbrush', unrelated_during_build=True)
        self.assertEqual(status, 1)
        self.assertEqual(calls[-1], ['pacman', '-Rn', '--noconfirm', '--', 'cmake', 'gtk3'])
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2', 'unrelated': '1'})
        self.assertEqual(self.events()[-1]['code'], 'failed')
        self.assertNotIn('snapshot', self.events()[-1]['message'])
        receipts.record.assert_not_called()

    def test_unrelated_addition_during_build_is_kept_out_of_the_receipt(self):
        status, calls, receipts, leftovers, state = self.worker(unrelated_during_build=True)
        self.assertEqual(status, 0)
        self.assertEqual(state['unrelated'], '1')
        self.assertEqual(state['paint'], '9-1')
        self.assertNotIn('unrelated', receipts.record.call_args.args[5])

    def test_unrelated_upgrade_and_removal_during_build_are_not_recorded(self):
        status, calls, receipts, leftovers, state = self.worker(
            installed={'other': '1'}, external_changes={'base': '3', 'other': None})
        self.assertEqual(status, 0)
        self.assertEqual(state['base'], '3')
        self.assertNotIn('other', state)
        self.assertEqual(receipts.record.call_args.args[5]['base'], '2')
        self.assertEqual(receipts.record.call_args.args[5]['other'], '1')

    def test_failed_install_after_dependency_upgrade_offers_its_snapshot(self):
        status, calls, receipts, leftovers, state = self.worker(
            installed={'libbrush': '1-1'}, failing_build='paint')
        self.assertEqual(status, 1)
        self.assertEqual(state['libbrush'], '9-1')
        self.assertEqual(state['base'], '2')
        self.assertEqual(self.events()[-1]['code'], 'partial')
        self.assertIn('pre-install snapshot', self.events()[-1]['message'])
        self.assertEqual(self.events()[-1].get('restoreToken'), '13:' + TOKEN)
        self.assertEqual(self.events()[-1].get('snapshot'), '13')
        self.assertEqual(receipts.snapshot_states[1]['base'], '2')
        self.assertEqual(receipts.discarded, ['12'])
        receipts.record.assert_not_called()

    def test_installed_program_and_aur_dependency_are_upgraded(self):
        import programs_receipts
        for installed in ({'paint': '1-1'}, {'libbrush': '1-1'},
                          {'paint': '1-1', 'libbrush': '1-1'}):
            with self.subTest(installed=installed):
                status, calls, receipts, leftovers, state = self.worker(installed=installed)
                self.assertEqual(status, 0)
                for name in installed:
                    self.assertEqual(state[name], '9-1')
                with tempfile.TemporaryDirectory(prefix='aur-upgrade-receipt-') as directory:
                    receipt = programs_receipts.record(*receipts.record.call_args.args,
                                                       **receipts.record.call_args.kwargs, directory=directory)
                self.assertEqual(set(receipt['upgraded']) & set(installed), set(installed))
                self.assertTrue(programs_receipts.needs_restore(receipt))

    def test_cleanup_never_removes_an_unrelated_new_orphan(self):
        replies = [SimpleNamespace(returncode=0, stdout='git\nunrelated\n', stderr=''),
                   SimpleNamespace(returncode=0, stdout='unrelated\n', stderr='')]
        with patch.object(helper, 'run', side_effect=replies), \
                patch.object(helper, 'stream', return_value=(0, [])) as stream:
            self.assertEqual(helper.remove_leftovers({'base': '1'}, {'git'}), ['git'])
        self.assertEqual(stream.call_args.args[0], ['pacman', '-Rn', '--noconfirm', '--', 'git'])

    def test_failed_dependency_install_is_rolled_back_against_post_update_state(self):
        status, calls, receipts, leftovers, state = self.worker(statuses={'pacman': 1})
        self.assertEqual(status, 1)
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})
        receipts.record.assert_not_called()
        self.assertFalse(any(call[0] == 'build' for call in calls))
        self.assertEqual(self.events()[-1]['code'], 'failed')

    def test_cleanup_failure_never_reports_success_and_rolls_back_install(self):
        status, calls, receipts, leftovers, state = self.worker(cleanup_error='cleanup failed')
        self.assertEqual(status, 1)
        self.assertFalse(self.events()[-1]['ok'])
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})
        receipts.record.assert_not_called()

    def test_cleanup_empty_orphan_result_cannot_hide_remaining_planned_tools(self):
        status, calls, receipts, leftovers, state = self.worker(leave_tools=True)
        self.assertEqual(status, 1)
        self.assertEqual(leftovers.call_count, 2)
        self.assertIn('Build tools could not be removed', self.events()[-1]['message'])
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})
        receipts.record.assert_not_called()

    def test_build_only_verification_excludes_promoted_runtime_and_tracks_aur_tools(self):
        plan = dict(packages=[dict(package='tool', purpose='build'), dict(package='shared', purpose='build')],
                    builds=[dict(purpose='run', packages=[dict(package='shared'), dict(package='paint')]),
                            dict(purpose='build', packages=[dict(package='aur-tool')])])
        self.assertEqual(helper.build_only_packages(plan), {'tool', 'aur-tool'})

    def test_receipt_write_failure_rolls_back_exact_additions_and_keeps_update(self):
        status, calls, receipts, leftovers, state = self.worker(receipt_error=OSError('disk full'))
        self.assertEqual(status, 1)
        self.assertEqual(receipts.record.call_count, 1)
        self.assertEqual(calls[-1], ['pacman', '-Rn', '--noconfirm', '--', 'gtk3', 'libbrush', 'paint'])
        self.assertEqual(state, {'base': '2', 'updated-dependency': '2'})
        self.assertEqual(self.events()[-1]['code'], 'failed')
        self.assertIn('could not be saved for Undo', self.events()[-1]['message'])
        self.assertEqual(self.events()[-1]['receipt'], '')

    def test_snapshot_failure_prevents_first_package_mutation(self):
        status, calls, receipts, leftovers, state = self.worker(snapshot_error=RuntimeError('snapshot failed'))
        self.assertEqual(status, 1)
        self.assertEqual(calls, [])
        self.assertEqual(state, {'base': '1'})
        self.assertTrue(self.events()[-1]['notStarted'])

    def test_build_only_packages_go_and_older_orphans_stay(self):
        # base-devel and git stay optional dependencies of installed tools; gcc is freed after base-devel goes.
        rounds = iter(['polkit-kde-agent\nbase-devel\ngit\n', 'polkit-kde-agent\ngcc\n', 'polkit-kde-agent\n'])
        def run(arguments, timeout=None):
            self.assertEqual(arguments, ['pacman', '-Qdttq'])
            return SimpleNamespace(returncode=0, stdout=next(rounds), stderr="")
        with patch.object(helper, 'run', side_effect=run), \
                patch.object(helper, 'stream', return_value=(0, [])) as stream:
            removed = helper.remove_leftovers({'base': '1', 'polkit-kde-agent': '6'}, {'base-devel', 'git', 'gcc'})
        self.assertEqual(removed, ['base-devel', 'git', 'gcc'])
        self.assertEqual([call.args[0] for call in stream.call_args_list],
                         [['pacman', '-Rn', '--noconfirm', '--', 'base-devel', 'git'], ['pacman', '-Rn', '--noconfirm', '--', 'gcc']])

    def test_changed_plan_is_refused_before_any_package_change(self):
        lock = tempfile.TemporaryFile(mode='w')
        self.addCleanup(lock.close)
        modules = (None, SimpleNamespace(Refused=ValueError), None, None)
        with patch.object(helper, 'modules', return_value=modules), \
                patch.object(helper.os, 'open', return_value=10), \
                patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), \
                patch.object(helper.fcntl, 'flock'), patch.object(helper, 'require_update_root'), \
                patch.object(helper, 'reviewed_plan', side_effect=ValueError('changed')), \
                patch.object(helper, 'stream') as stream:
            self.assertEqual(helper.worker('paint', TOKEN), 1)
            stream.assert_not_called()
        self.assertTrue(self.events()[-1]['notStarted'])

    def test_request_relays_the_service_journal(self):
        records = [dict(__CURSOR='1', MESSAGE=json.dumps(dict(type='progress', message='Building paint'))),
                   dict(__CURSOR='2', MESSAGE='==> Finished making: paint'),
                   dict(__CURSOR='3', MESSAGE=json.dumps(dict(type='finished', ok=True, code='success',
                                                              action='install', kind='aur')))]
        starter = Mock()
        starter.poll.return_value = 0
        starter.__enter__ = Mock(return_value=starter)
        starter.__exit__ = Mock(return_value=False)
        journal = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, records)))
        with patch.object(helper, 'require_update_root'), patch.object(helper, 'reviewed_plan'), \
                patch.object(helper, 'require_consent', return_value='1000'), \
                patch.object(helper, 'environment', return_value={'PATH': '/bin'}), \
                patch.object(helper.subprocess, 'Popen', return_value=starter) as popen, \
                patch.object(helper, 'run', return_value=journal) as run:
            self.assertEqual(helper.request('paint', TOKEN), 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[-3:], ['--run', 'paint', TOKEN])
        self.assertIn('--unit=' + helper.SERVICE, command)
        self.assertTrue(any(word.startswith('EMAKI_AUR_REQUEST=') for word in run.call_args_list[0].args[0]))
        events = self.events()
        self.assertEqual([event.get('message') for event in events[1:3]], ['Building paint', '==> Finished making: paint'])
        self.assertTrue(events[-1]['ok'])

    def test_split_outputs_build_once_and_only_the_target_is_explicit(self):
        plan = dict(PLAN, builds=[dict(packageBase='paint', commit=COMMIT,
                                      packages=[dict(package='paint'), dict(package='paint-lib')])])
        status, calls, receipts, leftovers, state = self.worker(plan=plan)
        self.assertEqual(status, 0)
        self.assertEqual([call for call in calls if call[0] == 'build'], [['build', 'paint', COMMIT]])
        installs = [call for call in calls if '-U' in call]
        self.assertEqual(len(installs), 1)
        self.assertNotIn('--asdeps', installs[0])
        self.assertIn(['pacman', '-D', '--asdeps', '--', 'paint', 'paint-lib'], calls)
        self.assertEqual(installs[0][-2:], ['/pkg/paint.pkg.tar.zst', '/pkg/paint-lib.pkg.tar.zst'])
        self.assertEqual([call for call in calls if '--asexplicit' in call], [['pacman', '-D', '--asexplicit', '--', 'paint']])

    def test_cleanup_retries_query_exceptions(self):
        with patch.object(helper, 'run', side_effect=[OSError('failed'),
                          SimpleNamespace(returncode=1, stdout='', stderr='')]) as query:
            self.assertEqual(helper.remove_leftovers({'base': '1'}, {'git'}), [])
        self.assertEqual(query.call_count, 2)

    def test_unsupported_and_unreadable_roots_are_refused(self):
        import emaki_update_root
        for result in (SimpleNamespace(returncode=0, stdout='{"mode":"unsupported"}'),
                       SimpleNamespace(returncode=0, stdout='{}'),
                       SimpleNamespace(returncode=1, stdout='{"mode":"normal"}')):
            with self.subTest(result=result), patch.object(helper, 'run', return_value=result), \
                    patch.object(helper, 'modules', return_value=(None, None, None, emaki_update_root)):
                with self.assertRaises(RuntimeError):
                    helper.require_update_root()

    def test_empty_failed_or_unverified_snapshot_is_refused(self):
        import restore
        for status, number in ((0, ''), (1, '12'), (0, '0'), (0, '12\n13')):
            with self.subTest(status=status, number=number), \
                    patch.object(helper, 'run', return_value=SimpleNamespace(returncode=status, stdout=number)) as run:
                with self.assertRaises(RuntimeError):
                    helper.preinstall_snapshot()
                self.assertEqual(run.call_count, 1)
        for metadata, readonly in ((ValueError('missing'), 'ro=true'),
                                   ({'kind': 'post', 'digest': TOKEN}, 'ro=true'),
                                   ({'kind': 'pre', 'digest': TOKEN}, 'ro=false')):
            replies = [SimpleNamespace(returncode=0, stdout='12'),
                       SimpleNamespace(returncode=0, stdout='subvolume'),
                       SimpleNamespace(returncode=0, stdout=readonly),
                       SimpleNamespace(returncode=0, stdout='')]
            with self.subTest(metadata=metadata, readonly=readonly), \
                    patch.object(helper, 'run', side_effect=replies), \
                    patch.object(restore, 'receipt', side_effect=metadata if isinstance(metadata, Exception) else None,
                                 return_value=metadata):
                with self.assertRaises(RuntimeError):
                    helper.preinstall_snapshot()

    def test_snapshot_verifies_receipt_subvolume_and_readonly(self):
        import restore
        replies = [SimpleNamespace(returncode=0, stdout='12'),
                   SimpleNamespace(returncode=0, stdout='subvolume'),
                   SimpleNamespace(returncode=0, stdout='ro=true\n')]
        with patch.object(helper, 'run', side_effect=replies) as run, \
                patch.object(restore, 'receipt', return_value={'kind': 'pre', 'digest': TOKEN}):
            self.assertEqual(helper.preinstall_snapshot(), ('12', '12:' + TOKEN))
        self.assertEqual(run.call_args_list[1].args[0], ['btrfs', 'subvolume', 'show', '/.snapshots/12/snapshot'])
        self.assertEqual(run.call_args_list[2].args[0], ['btrfs', 'property', 'get', '-ts', '/.snapshots/12/snapshot', 'ro'])

    def test_privileged_plan_checks_current_callers_consent_before_planning(self):
        import programs_aur
        with tempfile.TemporaryDirectory(prefix='aur-consent-') as directory:
            person = SimpleNamespace(pw_uid=os.getuid(), pw_dir=directory)
            path = programs_aur.consent_path(Path(directory) / '.config')
            modules = (SimpleNamespace(ENV={}), programs_aur, None, None)
            with patch.dict(os.environ, {'PKEXEC_UID': str(person.pw_uid)}, clear=True), \
                    patch.object(helper.pwd, 'getpwuid', return_value=person), \
                    patch.object(helper, 'modules', return_value=modules), \
                    patch.object(programs_aur, 'build_plan', return_value={'token': TOKEN}) as planner:
                with self.assertRaises(ValueError):
                    helper.reviewed_plan('paint', TOKEN)
                planner.assert_not_called()
                programs_aur.set_consent(path, True)
                self.assertEqual(helper.reviewed_plan('paint', TOKEN), {'token': TOKEN})
                programs_aur.set_consent(path, False)
                with self.assertRaises(ValueError):
                    helper.reviewed_plan('paint', TOKEN)
                self.assertEqual(planner.call_count, 1)

    def test_service_rechecks_revoked_consent_and_does_not_use_root_home(self):
        with patch.dict(os.environ, {'EMAKI_AUR_UID': '1000', 'HOME': '/root'}, clear=True), \
                patch.object(helper.pwd, 'getpwuid', return_value=SimpleNamespace(pw_uid=1000, pw_dir='/missing-person')):
            with self.assertRaises(ValueError):
                helper.require_consent()

    def test_cleanup_retries_remove_and_queries_again(self):
        replies = [SimpleNamespace(returncode=0, stdout='git\n', stderr=''),
                   SimpleNamespace(returncode=0, stdout='git\n', stderr=''),
                   SimpleNamespace(returncode=1, stdout='', stderr='')]
        with patch.object(helper, 'run', side_effect=replies), \
                patch.object(helper, 'stream', side_effect=[(1, []), (0, [])]) as stream:
            self.assertEqual(helper.remove_leftovers({'base': '1'}, {'git'}), ['git'])
        self.assertEqual(stream.call_count, 2)
        self.assertIn('Trying again', self.events()[-1]['message'])

    def test_cleanup_repeated_query_or_removal_failure_is_an_error(self):
        for query in (SimpleNamespace(returncode=1, stdout='', stderr='database error'),
                      SimpleNamespace(returncode=0, stdout='git\n', stderr='')):
            with self.subTest(query=query), patch.object(helper, 'run', return_value=query) as run, \
                    patch.object(helper, 'stream', return_value=(1, [])):
                with self.assertRaises(RuntimeError):
                    helper.remove_leftovers({'base': '1'}, {'git'})
                self.assertEqual(run.call_count, 2)

    def test_install_allows_a_previewed_upgrade(self):
        changed = set()
        with patch.object(helper, 'versions', side_effect=[{'base': '2'}, {'base': '3'}]), \
                patch.object(helper, 'run', return_value=SimpleNamespace(returncode=0, stdout='base 3\n')), \
                patch.object(helper, 'stream', return_value=(0, [])) as stream:
            self.assertEqual(helper.add_only(['pacman', '-U', '--', '/pkg/base.pkg.tar.zst'],
                                             {'base': '2'}, changed), 0)
        self.assertEqual(changed, {'base'})
        stream.assert_called_once()

    def test_committed_install_with_hook_failure_marks_only_new_dependencies(self):
        changed = set()
        with patch.object(helper, 'versions', side_effect=[{'base': '2'}, {'base': '3', 'tool': '1'}]), \
                patch.object(helper, 'run', return_value=SimpleNamespace(returncode=0, stdout='base 3\ntool 1\n')), \
                patch.object(helper, 'stream', side_effect=[(1, []), (0, [])]) as stream:
            self.assertEqual(helper.add_only(['pacman', '-S', '--asdeps', '--', 'tool'],
                                             {'base': '2'}, changed), 1)
        self.assertEqual(changed, {'base', 'tool'})
        self.assertNotIn('--asdeps', stream.call_args_list[0].args[0])
        self.assertEqual(stream.call_args_list[1].args[0], ['pacman', '-D', '--asdeps', '--', 'tool'])

    def test_rollback_detects_an_install_upgrade_after_removing_additions(self):
        states = [{'base': '3', 'paint': '1'}, {'base': '3'}, {'base': '3'}]
        with patch.object(helper, 'versions', side_effect=states), \
                patch.object(helper, 'stream', return_value=(0, [])) as stream:
            with self.assertRaisesRegex(RuntimeError, 'could not be restored'):
                helper.rollback_additions({'base': '2'}, {'paint', 'base'})
        self.assertEqual(stream.call_args.args[0], ['pacman', '-Rn', '--noconfirm', '--', 'paint'])

    def test_rollback_ignores_unrelated_concurrent_upgrades_and_removals(self):
        states = [{'base': '3', 'paint': '1'}, {'base': '3'}, {'base': '3'}]
        with patch.object(helper, 'versions', side_effect=states), \
                patch.object(helper, 'stream', return_value=(0, [])):
            helper.rollback_additions({'base': '2', 'other': '1'}, {'paint'})

    @unittest.skipUnless(shutil.which('pacman') and shutil.which('fakeroot'), 'pacman and fakeroot required')
    def test_real_pacman_noconfirm_refuses_conflicting_local_package(self):
        # Only an isolated database is writable; the archive has no files or scripts.
        with tempfile.TemporaryDirectory(prefix='aur-conflict-') as temporary:
            root = Path(temporary)
            database = root / 'db'
            local = database / 'local' / 'old-program-1-1'
            local.mkdir(parents=True)
            (database / 'local' / 'ALPM_DB_VERSION').write_text('9\n')
            (local / 'desc').write_text('%NAME%\nold-program\n\n%VERSION%\n1-1\n\n%ARCH%\nany\n')
            (local / 'files').write_text('%FILES%\n\n')
            config = root / 'pacman.conf'
            config.write_text('[options]\nArchitecture = auto\nSigLevel = Never\n')
            package = root / 'new-program.pkg.tar'
            def archive_package(conflict=''):
                metadata = ('pkgname = new-program\npkgver = 1-1\npkgdesc = fixture\narch = any\nsize = 0\n'
                            + ('conflict = ' + conflict + '\n' if conflict else '')).encode()
                with tarfile.open(package, 'w') as archive:
                    member = tarfile.TarInfo('.PKGINFO')
                    member.size = len(metadata)
                    archive.addfile(member, io.BytesIO(metadata))
            archive_package('old-program')
            for name in ('hooks', 'cache', 'gpg'):
                (root / name).mkdir()
            command = ['fakeroot', '--', 'pacman', '--root', str(root), '--dbpath', str(database),
                       '--config', str(config), '--logfile', str(root / 'pacman.log'),
                       '--cachedir', str(root / 'cache'), '--gpgdir', str(root / 'gpg'),
                       '--hookdir', str(root / 'hooks'), '-U', '--dbonly', '--noconfirm', '--', str(package)]
            def execute():
                return subprocess.run(command, text=True, capture_output=True, timeout=20,
                                      env=dict(os.environ, LC_ALL='C'))

            def assert_conflict():
                result = execute()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('conflict', result.stdout + result.stderr)
                self.assertIn('[y/N]', result.stdout + result.stderr)
                self.assertEqual(sorted(path.name for path in (database / 'local').iterdir()),
                                 ['ALPM_DB_VERSION', 'old-program-1-1'])

            assert_conflict()
            archive_package()
            with (local / 'desc').open('a') as desc:
                desc.write('\n%CONFLICTS%\nnew-program\n')
            assert_conflict()
            (local / 'desc').write_text('%NAME%\nold-program\n\n%VERSION%\n1-1\n\n%ARCH%\nany\n')
            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sorted(path.name for path in (database / 'local').iterdir()),
                             ['ALPM_DB_VERSION', 'new-program-1-1', 'old-program-1-1'])


if __name__ == '__main__':
    unittest.main()
