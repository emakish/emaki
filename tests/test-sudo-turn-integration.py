#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check opt-in turn adapters without a broker or personal configuration."""
import importlib.machinery
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader('turn_adapter', str(ROOT / 'scripts/emaki-sudo-turn'))
spec = importlib.util.spec_from_loader(loader.name, loader)
adapter = importlib.util.module_from_spec(spec)
loader.exec_module(adapter)


class TurnIntegration(unittest.TestCase):
    def call(self, kind, payload):
        raw = json.dumps(payload)
        with patch.object(adapter.subprocess, 'run') as run:
            try:
                adapter.report([kind] + ([raw] if kind == 'codex' else []), io.StringIO(raw))
            except (ValueError, TypeError):
                pass
        return run

    def test_start_end_minimal_identity(self):
        for event, operation in [('UserPromptSubmit', 'turn-start'), ('Stop', 'turn-end')]:
            run = self.call('claude', {'hook_event_name': event, 'session_id': 'session',
                                     'prompt': 'private prompt', 'transcript_path': '/private/path'})
            command = run.call_args.args[0]
            self.assertEqual(command, ['@EMAKI_BINDIR@/emaki-sudo', operation, adapter.identity('session'), '--json'])
            self.assertNotIn('private', repr(run.call_args))
            self.assertEqual(run.call_args.kwargs['stdin'], adapter.subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['stdout'], adapter.subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['stderr'], adapter.subprocess.DEVNULL)

    def test_complete_minimal_identity(self):
        run = self.call('codex', {'type': 'agent-turn-complete', 'thread-id': 'thread',
                                 'turn-id': 'turn', 'last-assistant-message': 'private reply'})
        command = run.call_args.args[0]
        self.assertEqual(command, ['@EMAKI_BINDIR@/emaki-sudo', 'turn-complete',
                         adapter.identity('thread') + ':' + adapter.identity('turn'), '--json'])
        self.assertLessEqual(len(command[2]), 128)
        self.assertTrue(command[2].isascii())
        self.assertNotIn('private', repr(run.call_args))

    def test_invalid_or_unknown_events_do_not_report(self):
        for kind, payload in [('claude', None), ('codex', []), ('claude', {}),
                              ('claude', {'hook_event_name': 'SessionStart', 'session_id': 'id'}),
                              ('codex', {'type': 'other', 'thread-id': 'id', 'turn-id': 'id'})]:
            self.assertFalse(self.call(kind, payload).called)
        for bad in (None, '', 0, False, [], {}, 'a' * 4097):
            self.assertFalse(self.call('claude', {'hook_event_name': 'Stop', 'session_id': bad}).called)
            for field in ('thread-id', 'turn-id'):
                payload = {'type': 'agent-turn-complete', 'thread-id': 'id', 'turn-id': 'id'}
                payload[field] = bad
                self.assertFalse(self.call('codex', payload).called)

    def test_config_is_untouched_and_failure_is_quiet(self):
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / '.config'
            config.mkdir()
            personal = config / 'settings.json'
            personal.write_text('{"keep": true}')
            before = {str(p): p.read_bytes() for p in Path(home).rglob('*') if p.is_file()}
            with patch.dict(os.environ, HOME=home), patch.object(adapter.sys, 'argv',
                    ['emaki-sudo-turn', 'claude']), patch.object(adapter.sys, 'stdin',
                    io.StringIO('{"hook_event_name":"Stop","session_id":"id"}')), \
                    patch.object(adapter.subprocess, 'run', side_effect=OSError):
                self.assertEqual(adapter.main(), 0)
            after = {str(p): p.read_bytes() for p in Path(home).rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_payload_limit_and_bad_json(self):
        for raw in ('not-json', 'x' * (adapter.MAX_PAYLOAD + 1)):
            with patch.object(adapter.sys, 'argv', ['emaki-sudo-turn', 'claude']), \
                    patch.object(adapter.sys, 'stdin', io.StringIO(raw)), \
                    patch.object(adapter.subprocess, 'run') as run:
                self.assertEqual(adapter.main(), 0)
                self.assertFalse(run.called)

    def test_installed_paths_ignore_search_path_and_python_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = root / 'prefix'
            interpreter = str(Path(sys.executable).resolve())
            subprocess.run(['make', '--no-print-directory', 'install-sudo-turn-integration',
                            'PREFIX=' + str(prefix), 'EMAKI_PYTHON=' + interpreter],
                           cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            helper = prefix / 'bin/emaki-sudo-turn'
            self.assertEqual(helper.read_text().splitlines()[0], '#!' + interpreter + ' -I')
            self.assertNotIn('@EMAKI_', helper.read_text())
            marker = root / 'called'
            cli = prefix / 'bin/emaki-sudo'
            cli.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > ' + shlex.quote(str(marker)) + '\n')
            cli.chmod(0o755)
            hostile = root / 'hostile'
            hostile.mkdir()
            for name in ('python3', 'emaki-sudo', 'emaki-sudo-turn'):
                path = hostile / name
                path.write_text('#!/bin/sh\nexit 97\n')
                path.chmod(0o755)
            (hostile / 'sitecustomize.py').write_text('raise SystemExit(98)\n')
            environment = dict(os.environ, PATH=str(hostile), PYTHONPATH=str(hostile),
                               PYTHONHOME=str(hostile))
            result = subprocess.run([str(helper), 'claude'], text=True,
                                    input=json.dumps({'hook_event_name': 'UserPromptSubmit',
                                                      'session_id': 'installed-session'}),
                                    env=environment, cwd=hostile, capture_output=True, check=True)
            self.assertEqual(result.stdout + result.stderr, '')
            self.assertEqual(marker.read_text().splitlines(),
                             ['turn-start', adapter.identity('installed-session'), '--json'])
            integrations = prefix / 'share/emaki/sudo/integrations'
            hooks = json.loads((integrations / 'claude-hooks.json').read_text())['hooks']
            for entries in hooks.values():
                self.assertEqual(entries[0]['hooks'][0]['command'], str(helper) + ' claude')
            self.assertIn('notify = ["' + str(helper) + '", "codex"]',
                          (integrations / 'codex-notify.toml').read_text())

    def test_guide_describes_typed_turn_bounds(self):
        guide = (ROOT / 'sudo/turn-reporting.md').read_text()
        self.assertIn("Type a whole number from 1 to 1000", guide)
        self.assertIn("there are no preset counts", guide)

    def test_snippets_and_explicit_settings_action(self):
        hooks = json.loads((ROOT / 'sudo/integrations/claude-hooks.json').read_text())['hooks']
        self.assertEqual(set(hooks), {'UserPromptSubmit', 'Stop'})
        for entries in hooks.values():
            self.assertEqual(entries[0]['hooks'], [{'type': 'command', 'command': '@EMAKI_BINDIR@/emaki-sudo-turn claude'}])
        self.assertIn('notify = ["@EMAKI_BINDIR@/emaki-sudo-turn", "codex"]',
                      (ROOT / 'sudo/integrations/codex-notify.toml').read_text())
        settings = (ROOT / 'shell/SudoKeySettingsPage.qml').read_text()
        self.assertIn('onClicked: Qt.openUrlExternally(', settings)
        self.assertIn('Platform.dataDir + "/sudo/turn-reporting.md"', settings)
        self.assertNotIn('Component.onCompleted', settings)


if __name__ == '__main__':
    unittest.main()
