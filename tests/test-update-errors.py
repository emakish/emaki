#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Update explanations preserve diagnostics, prompts, and transaction results."""
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer.update_errors import DETAIL_LIMIT, details, explain


class UpdateErrors(unittest.TestCase):
    def test_refusals_keep_the_original_detail_in_brackets(self):
        cases = {
            "installing qt6-base breaks dependency 'qt6-base<6.12' required by quickshell-emaki": 'do not yet match this Emaki release',
            'could not satisfy dependencies': 'different versions of each other',
            'failed to commit transaction (failed to run transaction hooks)': 'safety check stopped',
            'example version 2 was rolled back (pacman).': 'held after a rollback',
            'Package versions could not be checked (pacman).': 'could not check packages held',
            'The holds file is unreadable (pacman).': 'could not check packages held',
            'error: unable to lock database': 'Another package operation',
            'invalid or corrupted package (PGP signature)': 'could not verify a download',
            '/example exists in filesystem': 'file is in the way',
            'not enough free disk space': 'not enough free space',
            'failed retrieving file: could not resolve host': 'could not reach a package source',
            'error: command failed to execute correctly': 'Some packages may already have changed',
            'unrecognized failure': 'Resolve the reported problem',
        }
        for technical, plain in cases.items():
            with self.subTest(technical=technical):
                if not ('(pacman)' in technical or technical.startswith('error:')):
                    technical = 'error: ' + technical
                result = explain(technical)
                self.assertIn(plain, result)
                self.assertTrue(result.endswith('[' + technical + ']'))

    def test_keywords_in_ordinary_output_do_not_select_an_explanation(self):
        noise = ('checking package signature and unknown trust\n'
                 'installing quickshell-emaki; breaks dependency\n'
                 'emaki-guard: snapshot failed\n')
        result = explain(noise + 'error: unknown failure')
        self.assertEqual(result, 'The update could not finish. Resolve the reported problem before trying again. [error: unknown failure]')
        self.assertIn('[pacman returned no details]', explain(noise))

    def test_dependency_and_conflict_continuations_are_retained(self):
        for output, expected in (
            ("error: failed to prepare transaction (could not satisfy dependencies)\n"
             ":: installing qt6-base (6.12) breaks dependency 'qt6-base<6.12' required by quickshell-emaki", 'do not yet match'),
            ('error: failed to commit transaction (conflicting files)\n'
             'example: /usr/bin/example exists in filesystem', 'file is in the way'),
        ):
            result = explain(output)
            self.assertIn(expected, result)
            self.assertTrue(result.endswith('[' + output + ']'))

    def test_diagnostics_are_bounded_and_progress_is_excluded(self):
        result = explain('progress signature\n' * 10000 + 'error: unable to lock database')
        self.assertTrue(result.endswith('[error: unable to lock database]'))
        result = explain('error: ' + 'x' * 10000)
        detail = result.split(' [', 1)[1][:-1]
        self.assertEqual(len(detail), DETAIL_LIMIT)
        self.assertTrue(detail.endswith('…'))

    def test_real_rollback_hook_notices(self):
        import runpy
        hook = runpy.run_path(str(ROOT / 'upkeep/emaki-rollback-holds'))
        for detail in (hook['UNREADABLE'], hook['UNCONFIRMED'],
                       hook['notices']({'example': '2-1'})[0]):
            result = explain(detail + '\nerror: failed to commit transaction (failed to run transaction hooks)')
            self.assertIn('held after a rollback', result)
            self.assertIn(detail, result)

    def test_process_group_interrupt_allows_child_cleanup_and_preserves_status(self):
        source = ("import os,signal,time\n"
                  "assert signal.getsignal(signal.SIGINT) != signal.SIG_IGN\n"
                  "def finish(signum, frame):\n"
                  "    time.sleep(0.05)\n"
                  "    os.write(1, b'x' * 262144)\n"
                  "    os.write(1, b'clean finish\\n')\n"
                  "    raise SystemExit(23)\n"
                  "signal.signal(signal.SIGINT, finish)\n"
                  "print('ready', flush=True)\n"
                  "while True: signal.pause()\n")
        code = ('import runpy,sys; m=runpy.run_path(sys.argv[1]); '
                'sys.exit(m["run"]([sys.executable,"-c",sys.argv[2]]))')
        with subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'scripts/emaki-update'), source],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), 'child did not become ready')
                self.assertEqual(process.stdout.readline(), b'ready\n')
                os.killpg(process.pid, signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 23)
                self.assertTrue(stdout.endswith(b'clean finish\n'))
                self.assertEqual(stdout.count(b'x'), 262144)
                self.assertNotIn(b'Traceback', stderr)
                self.assertNotIn(b'cancelled', stderr)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()

    def invoke(self, source, input=''):
        code = ('import runpy,sys; m=runpy.run_path(sys.argv[1]); '
                'sys.exit(m["run"]([sys.executable,"-c",sys.argv[2]]))')
        return subprocess.run([sys.executable, '-c', code, str(ROOT / 'scripts/emaki-update'), source],
                              input=input, text=True, capture_output=True)

    def test_stdin_prompts_locale_and_original_failure_status(self):
        result = self.invoke('import os,sys; print("Continue?", flush=True); '
                             'assert input() == "yes"; assert os.environ["LC_ALL"] == "C"; '
                             'print("error: unable to lock database", file=sys.stderr); sys.exit(7)', 'yes\n')
        self.assertEqual(result.returncode, 7)
        self.assertIn('Continue?', result.stdout)
        self.assertIn('Another package operation', result.stderr)
        self.assertIn('error: unable to lock database]', result.stderr)

    def test_success_is_not_reported_as_failure(self):
        result = self.invoke('print("there is nothing to do; previous error: command failed to execute correctly")')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')

    def test_failed_post_transaction_hook_is_visible_even_with_zero_status(self):
        result = self.invoke('print("error: command failed to execute correctly")')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Some packages may already have changed', result.stderr)

    def test_installer_details_have_no_plain_explanation(self):
        self.assertEqual(details('error: command failed to execute correctly'),
                         '[error: command failed to execute correctly]')
        self.assertEqual(details('ordinary progress'), '[pacman returned no details]')
        self.assertEqual(len(details('error: ' + 'x' * 10000)), DETAIL_LIMIT + 2)

    def test_decline_at_the_installation_prompt(self):
        result = self.invoke('import sys; print(":: Proceed with installation? [Y/n] ", end="", flush=True); '
                             'assert input() == "n"; sys.exit(1)', 'n\n')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.strip(),
                         'The update was cancelled. No installed packages were changed.')

    def test_terminal_interrupt_before_transaction_with_pacman_exit_status(self):
        source = ('import signal,sys; '
                  'signal.signal(signal.SIGINT, lambda *args: sys.exit(1)); '
                  'print(":: Proceed with installation? [Y/n]", flush=True); signal.pause()')
        code = ('import runpy,sys; m=runpy.run_path(sys.argv[1]); '
                'sys.exit(m["run"]([sys.executable,"-c",sys.argv[2]]))')
        with subprocess.Popen([sys.executable, '-c', code, str(ROOT / 'scripts/emaki-update'), source],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              start_new_session=True) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), 'child did not reach the prompt')
                self.assertIn(b'Proceed with installation?', process.stdout.readline())
                os.killpg(process.pid, signal.SIGINT)
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(stderr.strip(),
                                 b'The update was cancelled. No installed packages were changed.')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()

    def test_interrupted_before_package_changes(self):
        result = self.invoke('import os,signal; print(":: Synchronizing package databases...", flush=True); '
                             'os.kill(os.getpid(), signal.SIGINT)')
        self.assertEqual(result.returncode, 130)
        self.assertIn('The update was cancelled.', result.stderr)

    def test_interruption_after_hooks_never_claims_nothing_changed(self):
        for boundary in (':: Running pre-transaction hooks...', ':: Processing package changes...'):
            with self.subTest(boundary=boundary):
                result = self.invoke('import os,signal; print(' + repr(boundary) + ', flush=True); '
                                     'print("x" * 100000, flush=True); '
                                     'os.kill(os.getpid(), signal.SIGINT)')
                self.assertEqual(result.returncode, 130)
                self.assertNotIn('No installed packages were changed', result.stderr)

    def test_failure_and_post_prompt_work_are_not_declines(self):
        for output in ('', ':: Proceed with installation? [Y/n] \n:: Retrieving packages...',
                       ':: Proceed with installation? [Y/n] \nerror: unable to lock database'):
            with self.subTest(output=output):
                result = self.invoke('import sys; print(' + repr(output) + '); sys.exit(1)')
                self.assertNotIn('cancelled', result.stderr)

    def test_no_partial_upgrade_or_bypass_arguments(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/emaki-update'), '--nodeps'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
