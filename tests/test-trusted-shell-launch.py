#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise private descriptor isolation and restart routing without a desktop."""
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import socket
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/emaki-shell-trusted'
RENDER = runpy.run_path(str(ROOT / 'scripts/render-paths'))
TREE = runpy.run_path(str(ROOT / 'scripts/emaki_shell_tree.py'))


class TrustedLaunch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-trusted-launch-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        HOME=str(self.base / 'home'), XDG_STATE_HOME=str(self.base / 'state'),
                        XDG_RUNTIME_DIR=str(self.base), NIRI_SOCKET='',
                        CALLS=str(self.base / 'calls'), RESULT=str(self.base / 'result'))
        self.env.pop('WAYLAND_SOCKET', None)
        self.env.pop('EMAKI_LOGIN_HANDOFF', None)
        self.helper = self.bin / 'emaki-shell-trusted'
        values = RENDER['paths'](prefix=str(self.base), libexecdir=str(self.base / 'libexec'),
                                 system={'TRUSTED_PATH': str(self.bin), 'PYTHON': sys.executable})
        self.helper.write_text(RENDER['substitute'](HELPER.read_text(), values))
        self.helper.chmod(0o700)

    def script(self, name, body):
        path = self.bin / name
        path.write_text(f'#!{sys.executable} -I\n' + body)
        path.chmod(0o700)

    def mock_compositor(self, configured=True, restart_status=0, query_status=0, stderr=''):
        self.env['NIRI_SOCKET'] = str(self.base / 'never-opened')
        self.script('niri-emaki', f'''
import json, os, sys
from pathlib import Path
with Path(os.environ['CALLS']).open('a') as out: out.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[-1] == 'trusted-shell':
    print(json.dumps({{'configured': {configured!r}, 'running': False, 'prompt_active': False}}))
    print({stderr!r}, file=sys.stderr)
    sys.exit({query_status})
if os.environ.get('CHECK_RECEIPT'):
    Path(os.environ['CHECK_RECEIPT'] + '.seen').write_text(
        str(Path(os.environ['CHECK_RECEIPT']).exists()))
sys.exit({restart_status})
''')

    def invoke(self, command):
        return subprocess.run([str(self.helper), command], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_status_reports_supported_trusted_prompt_state(self):
        self.mock_compositor()
        result = subprocess.run([str(self.helper), 'status', '--json'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(configured=True, running=False,
                                                       prompt_active=False, supported=True))

    def test_status_without_a_session_cannot_attest_a_prompt(self):
        result = subprocess.run([str(self.helper), 'status', '--json'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(configured=False, running=False,
                                                       prompt_active=False, supported=False))

    def test_restart_only_uses_fixed_compositor_request(self):
        self.mock_compositor()
        self.assertEqual(self.invoke('restart').returncode, 0)
        calls = [json.loads(line) for line in Path(self.env['CALLS']).read_text().splitlines()]
        self.assertEqual(calls, [['msg', '-j', 'trusted-shell'], ['msg', 'restart-trusted-shell']])

    def test_failed_trusted_restart_never_selects_legacy_service(self):
        self.mock_compositor(restart_status=1)
        self.assertEqual(self.invoke('restart').returncode, 75)

    def running_health_marker(self):
        self.script('emaki-shell-health', (ROOT / 'scripts/emaki-shell-health').read_text())
        (self.bin / 'emaki_paths.py').write_text((ROOT / 'scripts/emaki_paths.py').read_text())
        state = self.base / 'emaki-shell-health'
        state.mkdir(mode=0o700)
        token = 'a' * 32
        (state / 'running.json').write_text(json.dumps({
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'pid': os.getpid(), 'token': token, 'session': self.env['NIRI_SOCKET'],
        }))
        receipt = state / ('clean-' + token)
        self.env['CHECK_RECEIPT'] = str(receipt)
        return receipt

    def test_requested_restart_records_clean_intent_before_compositor_request(self):
        self.mock_compositor()
        receipt = self.running_health_marker()
        result = self.invoke('restart')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(str(receipt) + '.seen').read_text(), 'True')
        self.assertEqual(receipt.read_text().strip(), 'restart:' + str(os.getpid()))

    def test_failed_requested_restart_cancels_its_clean_intent(self):
        self.mock_compositor(restart_status=1)
        receipt = self.running_health_marker()
        result = self.invoke('restart')
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(Path(str(receipt) + '.seen').read_text(), 'True')
        self.assertFalse(receipt.exists())

    def test_optional_health_failure_does_not_block_requested_restart(self):
        self.script('emaki-shell-health', 'import sys; sys.exit(1)')
        self.mock_compositor()
        result = self.invoke('restart')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_disconnected_compositor_never_selects_legacy_service(self):
        self.mock_compositor(query_status=1, stderr='connection refused')
        self.assertEqual(self.invoke('restart').returncode, 75)
        self.assertNotEqual(self.invoke('legacy-start').returncode, 0)

    def test_old_or_unconfigured_compositor_uses_legacy_path(self):
        self.assertEqual(self.invoke('restart').returncode, 69)
        self.mock_compositor(configured=False)
        self.assertEqual(self.invoke('restart').returncode, 69)
        self.mock_compositor(query_status=1, stderr='error parsing request')
        self.assertEqual(self.invoke('restart').returncode, 69)

    def test_service_condition_refuses_a_competing_shell(self):
        self.mock_compositor()
        self.assertEqual(self.invoke('legacy-start').returncode, 1)
        self.mock_compositor(configured=False)
        self.assertEqual(self.invoke('legacy-start').returncode, 0)

    def private_socket(self):
        try:
            server, client = socket.socketpair()
        except OSError as error:
            self.skipTest(f'private sockets unavailable in this sandbox: {error}')
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        return client

    def launch_module(self, client):
        environment = dict(self.env)
        environment.pop('WAYLAND_SOCKET', None)
        with patch.dict(os.environ, environment, clear=True):
            module = runpy.run_path(str(self.helper))
        # Functions keep run_path's original globals rather than the returned copy.
        namespace = module['launch'].__globals__
        namespace['PRIVATE_SOCKET'] = client.fileno()
        os.set_inheritable(client.fileno(), False)
        return SimpleNamespace(**module), namespace

    def test_direct_helper_entry_closes_descriptor_inheritance_before_preparation(self):
        client = self.private_socket()
        os.set_inheritable(client.fileno(), True)
        with patch.dict(os.environ, dict(self.env, WAYLAND_SOCKET=str(client.fileno())), clear=True):
            module = runpy.run_path(str(self.helper))
            self.assertEqual(module['PRIVATE_SOCKET'], client.fileno())
            self.assertNotIn('WAYLAND_SOCKET', os.environ)
            self.assertFalse(os.get_inheritable(client.fileno()))

    def test_restart_never_searches_hostile_path(self):
        self.mock_compositor()
        hostile = self.base / 'hostile'
        hostile.mkdir()
        (hostile / 'niri-emaki').write_text('#!/bin/sh\nexit 99\n')
        (hostile / 'niri-emaki').chmod(0o700)
        self.env['PATH'] = str(hostile)
        self.assertEqual(self.invoke('restart').returncode, 0)
        self.assertEqual(len(Path(self.env['CALLS']).read_text().splitlines()), 2)

    def test_environment_uses_only_generation_and_fixed_runtime_paths(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        generation = self.base / 'generation'
        hostile = {key: '/hostile' for key in (
            'EMAKI_SHELL_DIR', 'EMAKI_BIN', 'EMAKI_PYTHON', 'EMAKI_SHADER_DIR',
            'EMAKI_SESSION_GENERATION', 'EMAKI_SESSION_WATCHER', 'QT_PLUGIN_PATH',
            'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH',
            'LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME', 'GCONV_PATH',
            'GTK_PATH', 'GTK_MODULES', 'LOCPATH', 'QS_CONFIG_PATH', 'QML_DISK_CACHE_PATH',
            'QV4_FORCE_INTERPRETER', 'WAYLAND_DEBUG', 'WAYLAND_SOCKET')}
        env = module.environment(generation, dict(hostile, PATH='/hostile',
                                 EMAKI_SHELL_BORDER='soft', MALLOC_CONF='custom'))
        self.assertFalse(any(value == '/hostile' for value in env.values()), env)
        self.assertEqual(env['PATH'], namespace['FIXED_PATH'])
        self.assertEqual(env['EMAKI_BIN'], str(generation / 'emaki'))
        self.assertEqual(env['EMAKI_PYTHON'], namespace['PYTHON'])
        self.assertEqual(env['EMAKI_SESSION_GENERATION'], str(generation))
        self.assertEqual(env['EMAKI_SESSION_WATCHER'], str(generation / 'watch.py'))
        self.assertEqual(env['QML_IMPORT_PATH'], str(generation / 'shell') + ':' + str(module.QMLDIR))
        self.assertEqual(env['QML2_IMPORT_PATH'], env['QML_IMPORT_PATH'])
        self.assertEqual(env['QT_PLUGIN_PATH'], str(module.QT_PLUGINSDIR))
        self.assertEqual(env['QML_DISABLE_DISK_CACHE'], '1')
        self.assertEqual(env['QT_LOGGING_RULES'], '*.debug=false')
        self.assertEqual(env['EMAKI_SHELL_ORIGINAL_MALLOC_CONF'], '1custom')

    def test_session_snapshot_removes_handoff_from_raw_and_serialized_input(self):
        module, _ = self.launch_module(self.private_socket())
        markers = {key: 'private-login-value' for key in module.HANDOFF_MARKERS}
        session = dict(markers, PATH='/personal/bin', QT_QPA_PLATFORMTHEME='personal-theme')
        for inherited in (session, dict(session, EMAKI_SHELL_SESSION_ENV=json.dumps(session)),
                          dict(session, EMAKI_SHELL_SESSION_ENV='invalid snapshot')):
            with self.subTest(snapshot=inherited.get('EMAKI_SHELL_SESSION_ENV')):
                environment = module.environment(self.base / 'generation', inherited)
                restored = json.loads(environment['EMAKI_SHELL_SESSION_ENV'])
                self.assertEqual(restored['PATH'], '/personal/bin')
                self.assertEqual(restored['QT_QPA_PLATFORMTHEME'], 'personal-theme')
                self.assertFalse(module.HANDOFF_MARKERS.intersection(restored))
                self.assertFalse(module.HANDOFF_MARKERS.intersection(environment))

    def test_preparation_cannot_inherit_socket_but_foreground_shell_can(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        generation = self.base / 'old-generation'
        tree = Mock()
        tree.select.return_value = generation
        inode = os.fstat(client.fileno()).st_ino
        real_run = subprocess.run
        preparations = []
        def prepare(argv, **kwargs):
            self.assertEqual(argv[:3], [sys.executable, '-I', '-B'])
            self.assertTrue(kwargs['close_fds'])
            self.assertNotIn('pass_fds', kwargs)
            self.assertNotIn('WAYLAND_SOCKET', kwargs['env'])
            self.assertFalse(os.get_inheritable(client.fileno()))
            preparations.append((argv, dict(kwargs['env'])))
            # Execute a real child with the production fd/environment options.
            probe = ('import os; from pathlib import Path; '
                     'assert "WAYLAND_SOCKET" not in os.environ; '
                     f'expected={inode}; '
                     '\nfor path in Path("/proc/self/fd").iterdir():\n'
                     ' try: current=os.fstat(int(path.name)).st_ino\n'
                     ' except OSError: continue\n'
                     ' assert current != expected, "private socket inherited"\n')
            result = real_run([sys.executable, '-I', '-c', probe], **kwargs)
            self.assertEqual(result.returncode, 0)
            return result
        def execute(binary, argv, env):
            self.assertEqual(binary, module.QS)
            self.assertTrue(Path(binary).is_absolute())
            self.assertEqual(argv, [module.QS, '-n', '-p', str(generation / 'shell')])
            self.assertEqual(int(env['WAYLAND_SOCKET']), client.fileno())
            self.assertTrue(os.get_inheritable(client.fileno()))
            self.assertEqual(os.fstat(client.fileno()).st_ino, inode)
            self.assertNotIn('EMAKI_SHELL_PROCESS_ID', env)
            self.assertNotIn('EMAKI_SHELL_DIR', env)
            self.assertEqual(env['QS_DISABLE_CRASH_HANDLER'], '1')
        with patch.dict(os.environ, dict(self.env, EMAKI_SHELL_DIR='/hostile'), clear=True), \
                patch.dict(namespace, bootstrap=lambda: tree), \
                patch.object(module.os, 'chdir') as chdir, \
                patch.object(module.subprocess, 'run', side_effect=prepare), \
                patch.object(module.resource, 'setrlimit') as limits, \
                patch.object(module.os, 'execve', side_effect=execute) as execve:
            module.launch()
        tree.select.assert_called_once_with(os.getppid())
        self.assertEqual([call.args[0] for call in tree.trusted_tree.call_args_list],
                         [module.QMLDIR, module.QT_PLUGINSDIR])
        scans = tree.trusted_tree.call_args_list
        self.assertTrue(all(call.kwargs['seen'] is scans[0].kwargs['seen'] for call in scans))
        self.assertIn(module.BINDIR, [call.args[0] for call in tree.trusted_path.call_args_list])
        self.assertIn(Path(module.QS), [call.args[0] for call in tree.trusted_path.call_args_list])
        self.assertEqual([Path(argv[3]).name for argv, _ in preparations],
                         ['emaki-session-files', 'emaki-shell-health', 'emaki-qt-check'])
        self.assertEqual(preparations[0][0][4], 'trusted-record')
        chdir.assert_called_once_with('/')
        limits.assert_called_once_with(module.resource.RLIMIT_CORE, (0, 0))
        execve.assert_called_once()
        self.assertFalse(os.get_inheritable(client.fileno()))

    def test_warning_and_handoff_helpers_exclude_private_connection(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        tree = Mock()
        tree.select.return_value = self.base / 'generation'
        seen = []
        token = 'a' * 32
        def prepare(argv, **kwargs):
            self.assertTrue(kwargs['close_fds'])
            self.assertNotIn('WAYLAND_SOCKET', kwargs['env'])
            self.assertFalse('EMAKI_LOGIN_HANDOFF' in os.environ, 'login capability remained inherited')
            self.assertFalse(module.HANDOFF_MARKERS.intersection(
                json.loads(kwargs['env']['EMAKI_SHELL_SESSION_ENV'])))
            if argv[-1] == 'shell-token':
                self.assertEqual(kwargs['env']['EMAKI_LOGIN_HANDOFF'], 'login-capability')
            else:
                self.assertFalse(module.HANDOFF_MARKERS.intersection(kwargs['env']))
            seen.append(list(argv))
            diagnostic = Path(argv[3]).name == 'emaki-qt-check'
            output = token if argv[-1] == 'shell-token' else ''
            return subprocess.CompletedProcess(argv, int(diagnostic), output, 'Qt mismatch' if diagnostic else '')
        def warning(argv, **kwargs):
            self.assertEqual(argv[0], 'kitty')
            self.assertTrue(kwargs['close_fds'])
            self.assertNotIn('WAYLAND_SOCKET', kwargs['env'])
            self.assertEqual(kwargs['env']['PATH'], module.FIXED_PATH)
            self.assertFalse('EMAKI_LOGIN_HANDOFF' in os.environ, 'login capability remained inherited')
            self.assertFalse(module.HANDOFF_MARKERS.intersection(kwargs['env']))
            self.assertFalse(module.HANDOFF_MARKERS.intersection(
                json.loads(kwargs['env']['EMAKI_SHELL_SESSION_ENV'])))
        with patch.dict(os.environ, dict(self.env, EMAKI_LOGIN_HANDOFF='login-capability',
                EMAKI_SHELL_SESSION_ENV=json.dumps(dict(self.env, EMAKI_LOGIN_HANDOFF='login-capability'))), clear=True), \
                patch.dict(namespace, bootstrap=lambda: tree), \
                patch.object(module.os, 'chdir'), patch.object(module.resource, 'setrlimit'), \
                patch.object(module.subprocess, 'run', side_effect=prepare), \
                patch.object(module.subprocess, 'Popen', side_effect=warning) as popen, \
                patch.object(module.os, 'execve') as execve:
            module.launch()
        self.assertEqual(sum(Path(argv[3]).name == 'emaki-qt-check' for argv in seen), 2)
        popen.assert_called_once()
        env = execve.call_args.args[2]
        self.assertEqual(env['EMAKI_SESSION_START'], token)
        self.assertEqual(env['EMAKI_SESSION_SKIP_INTRO'], '1')
        self.assertNotIn('EMAKI_LOGIN_HANDOFF', env)

    def test_unsafe_preparation_module_refuses_before_starting_any_program(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        with patch.object(module.subprocess, 'run') as run, \
                patch.object(module.os, 'execve') as execve:
            with self.assertRaisesRegex(ValueError, 'unsafe trusted preparation module'):
                module.launch()
        run.assert_not_called()
        execve.assert_not_called()
        self.assertFalse(os.get_inheritable(client.fileno()))

    def test_user_writable_import_tree_refuses_before_preparation(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        unsafe = self.base / 'imports'
        unsafe.mkdir(mode=0o777)
        tree = Mock()
        tree.trusted_tree.side_effect = TREE['trusted_tree']
        with patch.dict(namespace, bootstrap=lambda: tree, QMLDIR=unsafe), \
                patch.object(module.subprocess, 'run') as run, \
                patch.object(module.os, 'execve') as execve:
            with self.assertRaisesRegex(ValueError, 'not root-owned and protected'):
                module.launch()
        run.assert_not_called()
        execve.assert_not_called()

    def test_empty_or_relative_fixed_search_entries_refuse_before_preparation(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        for search in ('', ':', 'relative', '/safe:', ':/safe', '/safe:relative', '/safe::/other'):
            with self.subTest(search=search):
                tree = Mock()
                with patch.dict(namespace, bootstrap=lambda: tree, FIXED_PATH=search), \
                        patch.object(module.subprocess, 'run') as run, \
                        patch.object(module.os, 'execve') as execute:
                    with self.assertRaisesRegex(ValueError, 'only absolute directories'):
                        module.launch()
                tree.trusted_tree.assert_not_called()
                tree.select.assert_not_called()
                run.assert_not_called()
                execute.assert_not_called()

    def test_unsafe_helper_or_additional_search_directory_refuses_launch(self):
        client = self.private_socket()
        module, namespace = self.launch_module(client)
        unsafe = self.base / 'writable-code'
        unsafe.mkdir(mode=0o777)
        (unsafe / 'emaki_paths.py').write_text('raise RuntimeError("untrusted module")\n')
        for overrides in ({'BINDIR': unsafe}, {'FIXED_PATH': str(module.BINDIR) + ':' + str(unsafe)}):
            with self.subTest(overrides=overrides):
                tree = Mock()
                # Other system trees are fixture boundaries; the adversarial tree
                # is checked by the actual owner/mode/symlink validator.
                def validate(path, **kwargs):
                    if path == unsafe:
                        TREE['trusted_path'](path, **kwargs)
                tree.trusted_path.side_effect = validate
                with patch.dict(namespace, bootstrap=lambda: tree, **overrides), \
                        patch.object(module.subprocess, 'run') as run, \
                        patch.object(module.os, 'execve') as execute:
                    with self.assertRaisesRegex(ValueError, 'not root-owned and protected'):
                        module.launch()
                self.assertIn(unsafe, [call.args[0] for call in tree.trusted_path.call_args_list])
                tree.select.assert_not_called()
                run.assert_not_called()
                execute.assert_not_called()
                self.assertFalse(os.get_inheritable(client.fileno()))

    def test_source_wrapper_fails_closed_despite_environment_override(self):
        client = self.private_socket()
        result = subprocess.run([str(ROOT / 'scripts/emaki-shell')],
            env=dict(self.env, WAYLAND_SOCKET=str(client.fileno()), EMAKI_SHELL_DIR=str(self.base),
                     EMAKI_BIN=str(self.bin / 'emaki'), EMAKI_PYTHON=sys.executable),
            pass_fds=(client.fileno(),), capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('trusted helper install path was not rendered', result.stderr)
        self.assertFalse(Path(self.env['RESULT']).exists())


class RuntimePaths(unittest.TestCase):
    """Exercise real path traversal without needing a compositor socket or root."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='trusted-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.libexec = self.base / 'libexec'
        self.qml = self.base / 'qml'
        self.plugins = self.base / 'plugins'
        for path in (self.bin, self.libexec, self.qml, self.plugins):
            path.mkdir()
        for name in ('qs', 'python3', 'emaki-session-files', 'emaki-shell-health', 'emaki-qt-check'):
            (self.bin / name).write_text('protected executable')
        for name in ('emaki_paths.py', 'emaki_session_state.py'):
            (self.libexec / name).write_text('protected module')
        self.unsafe = None
        original = Path.lstat

        def metadata(path):
            fields = list(original(path))
            fields[4] = 0
            if path != self.unsafe and not stat.S_ISLNK(fields[0]):
                fields[0] &= ~0o022
            return os.stat_result(fields)

        self.addCleanup(patch.stopall)
        patch.object(Path, 'lstat', metadata).start()
        with patch.dict(os.environ, {}, clear=True):
            self.module = runpy.run_path(str(HELPER))
        self.namespace = self.module['validate_runtime'].__globals__
        self.namespace.update(BINDIR=self.bin, LIBEXECDIR=self.libexec,
                              QS=str(self.bin / 'qs'), PYTHON=str(self.bin / 'python3'),
                              FIXED_PATH=str(self.bin), QMLDIR=self.qml, QT_PLUGINSDIR=self.plugins)
        self.tree = SimpleNamespace(**TREE)

    def validate(self):
        self.module['validate_runtime'](self.tree)

    def test_stock_dangling_commands_and_optional_qt_links_do_not_block_startup(self):
        for name, target in (('orbd', '../lib/jvm/default-runtime/bin/orbd'),
                             ('sdpscanner6', '../lib/qt6/bin/sdpscanner'),
                             ('QtWebEngineProcess6', '../lib/qt6/bin/QtWebEngineProcess')):
            (self.bin / name).symlink_to(target)
        for directory in (self.qml, self.plugins):
            (directory / 'optional').symlink_to('../missing-package/plugin.so')
        self.validate()

    def test_dangling_import_link_cannot_cross_writable_ancestor(self):
        self.unsafe = self.base / 'writable'
        self.unsafe.mkdir(mode=0o777)
        self.unsafe.chmod(0o777)
        for directory in (self.qml, self.plugins):
            with self.subTest(directory=directory):
                link = directory / 'optional'
                link.symlink_to(self.unsafe / 'missing' / 'plugin.so')
                with self.assertRaisesRegex(ValueError, 'not root-owned and protected'):
                    self.validate()
                link.unlink()

    def test_loaded_files_must_exist_and_remain_protected(self):
        for target in (self.bin / 'qs', self.libexec / 'emaki_paths.py',
                       self.qml / 'module.qml', self.plugins / 'plugin.so'):
            with self.subTest(target=target):
                target.write_text('code')
                target.chmod(0o666)
                self.unsafe = target
                with self.assertRaisesRegex(ValueError, 'not root-owned and protected'):
                    self.validate()
                target.chmod(0o644)
        (self.bin / 'qs').unlink()
        with self.assertRaises(FileNotFoundError):
            self.validate()

    def test_generation_validation_still_rejects_dangling_links(self):
        (self.qml / 'optional').symlink_to('../missing-package/plugin.so')
        with self.assertRaisesRegex(ValueError, 'links are not allowed'):
            TREE['trusted_tree'](self.qml, links=False)


if __name__ == '__main__':
    unittest.main()
