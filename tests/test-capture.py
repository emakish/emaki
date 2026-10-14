#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture selection, private output and recorder cleanup without a desktop session."""
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import select
import errno
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('capture', ROOT / 'shell/helpers/capture.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)

OUTPUT = dict(name='TEST-1', x=-100, y=30, width=800, height=600, scale=1.25, transform='Normal')
AVAILABLE = dict(outputs=[OUTPUT], windows=[dict(id=17, title='Example', app_id='example')])


class CaptureTests(unittest.TestCase):
    def setUp(self):
        previous = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('capture test deadline exceeded')))
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        signal.alarm(8)
        self.temporary = tempfile.TemporaryDirectory(prefix='capture-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.environment = patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime),
                                      HOME=str(self.base), WAYLAND_DISPLAY='fixture', WAYLAND_SOCKET='87654')
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_requires_running_secure_compositor_not_cli(self):
        for compositor in ('26.04', '26.04 (v26.04+emaki.14)', 'garbage',
                           '27.04 (v27.04+emaki.99)', '26.04+emaki.15'):
            with self.subTest(compositor=compositor), patch.object(C, 'niri', return_value={
                    'compositor': compositor, 'cli': '26.04 (v26.04+emaki.99)'}):
                with self.assertRaisesRegex(C.CaptureError, 'compositor_unsupported'):
                    C.supported()
        for compositor in ('26.04 (v26.04+emaki.15)', '26.04 (v26.04+emaki.16)'):
            with patch.object(C, 'niri', return_value={'compositor': compositor, 'cli': '26.04'}):
                C.supported()

    def test_inventory_keeps_logical_dimensions_and_omits_disabled_output(self):
        data = {'TEST-1': dict(logical=OUTPUT, current_mode=0), 'OFF': dict(logical=None, current_mode=None)}
        with patch.object(C, 'supported'), patch.object(C, 'guard'), patch.object(C, 'niri', side_effect=[data, AVAILABLE['windows']]):
            actual = C.inventory()
        self.assertEqual(actual['outputs'][0]['width'], 800)
        self.assertEqual(len(actual['outputs']), 1)
        self.assertEqual(actual['windows'][0]['id'], 17)

    def test_selection_rejects_stale_ids_and_invalid_geometry(self):
        requests = [dict(target='window', window_id=True), dict(target='window', window_id=18),
                    dict(target='screen', output='MISSING'), dict(target='unknown'),
                    dict(target='area', output='TEST-1', area=dict(x=-1, y=0, width=20, height=20)),
                    dict(target='area', output='TEST-1', area=dict(x=790, y=0, width=20, height=20)),
                    dict(target='area', output='TEST-1', area=dict(x=0, y=float('nan'), width=20, height=20)),
                    dict(target='area', output='TEST-1', area=dict(x=0, y=0, width=True, height=20))]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(C.CaptureError):
                C.selection(request, AVAILABLE)
        self.assertEqual(C.selection(dict(target='window', window_id=17), AVAILABLE), dict(target='window', window_id=17))

    def test_fractional_crop_stays_inside_selected_edges(self):
        selected = C.selection(dict(target='area', output='TEST-1',
                               area=dict(x=1, y=3, width=20, height=24)), AVAILABLE)
        self.assertEqual(C.crop_box(selected, 1000, 750), (2, 4, 26, 33))
        # The actual transformed framebuffer dimensions determine the ratio independently.
        self.assertEqual(C.crop_box(selected, 1600, 1200), (2, 6, 42, 54))

    def test_children_never_receive_private_wayland_descriptor(self):
        with patch.object(C.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'ok')) as run:
            self.assertEqual(C.command(['fixed', 'one value']), b'ok')
        self.assertNotIn('WAYLAND_SOCKET', run.call_args.kwargs['env'])
        self.assertTrue(run.call_args.kwargs['close_fds'])
        self.assertEqual(run.call_args.args[0], ['fixed', 'one value'])

    def test_runtime_directory_and_lease_fail_closed(self):
        with C.RecordingLease():
            with self.assertRaisesRegex(C.CaptureError, 'recording_busy'):
                with C.RecordingLease():
                    pass
        self.runtime.chmod(0o755)
        with self.assertRaisesRegex(C.CaptureError, 'runtime_unavailable'):
            C.runtime_directory()

    def test_publish_never_overwrites_and_keeps_private_mode(self):
        with patch.object(C, 'save_directory', return_value=self.base):
            partial = C.reserve('picture')
        partial.write_bytes(b'data')
        expected = partial.with_name(partial.name[1:-5])
        expected.write_bytes(b'old')
        with self.assertRaises(FileExistsError):
            C.publish(partial)
        self.assertEqual(expected.read_bytes(), b'old')
        expected.unlink()
        final = C.publish(partial)
        self.assertEqual(stat.S_IMODE(final.stat().st_mode), 0o600)
        self.assertFalse(partial.exists())

    def test_recording_reserves_private_mp4(self):
        with patch.object(C, 'save_directory', return_value=self.base):
            partial = C.reserve('recording')
        self.assertTrue(partial.name.endswith('.mp4.part'))
        self.assertEqual(stat.S_IMODE(partial.stat().st_mode), 0o600)
        self.assertEqual(partial.stat().st_size, 0)
        self.assertTrue(C.publish(partial).name.endswith('.mp4'))

    def test_picture_crops_before_clipboard_and_uses_selected_output(self):
        from PIL import Image
        calls = []
        def fake_command(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0] == 'grim':
                image = Image.new('RGB', (1000, 750), 'red')
                image.paste('green', (125, 125, 250, 250))
                image.save(argv[-1])
            return b''
        selected = C.selection(dict(target='area', output='TEST-1',
                         area=dict(x=100, y=100, width=100, height=100)), AVAILABLE)
        with patch.object(C, 'command', side_effect=fake_command), patch.object(C, 'guard'), patch.object(C, 'save_directory', return_value=self.base):
            result = C.picture(selected)
        self.assertEqual(calls[0][0][:5], ['grim', '-o', 'TEST-1', '-s', '1.25'])
        self.assertEqual(calls[-1][0], ['wl-copy', '--type', 'image/png'])
        copied = Image.open(io.BytesIO(calls[-1][1]['data']))
        self.assertEqual(copied.size, (125, 125))
        self.assertEqual(copied.getpixel((0, 0)), (0, 128, 0))
        self.assertEqual(Path(result['path']).read_bytes(), calls[-1][1]['data'])
        self.assertEqual(stat.S_IMODE(Path(result['path']).stat().st_mode), 0o600)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_locked_picture_never_invokes_capture(self):
        with patch.object(C, 'locked', return_value=True), patch.object(C, 'command') as run:
            with self.assertRaisesRegex(C.CaptureError, 'session_locked'):
                C.picture(dict(target='window', window_id=17))
        run.assert_not_called()

    def test_window_picture_waits_for_decodable_native_output(self):
        from PIL import Image
        calls = []
        def fake_command(argv, **_):
            calls.append(argv)
            if argv[0] == 'niri':
                self.assertTrue(Path(argv[-1]).name.startswith('.Screenshot-'))
                self.assertTrue(argv[-1].endswith('.part'))
                Image.new('RGB', (72, 55), 'blue').save(argv[-1], format='PNG')
            elif argv[0] == 'wl-copy':
                raise C.CaptureError('dependency_missing')
            return b''
        with patch.object(C, 'command', side_effect=fake_command), patch.object(C, 'guard'), patch.object(C, 'save_directory', return_value=self.base):
            result = C.picture(dict(target='window', window_id=17))
        self.assertEqual(calls[0][:6], ['niri', 'msg', 'action', 'screenshot-window', '--id', '17'])
        self.assertFalse(result['clipboard'])
        with Image.open(result['path']) as saved:
            self.assertEqual(saved.size, (72, 55))

    def test_failed_window_picture_never_publishes_and_cleans_after_grace(self):
        def failed_decode(path, deadline):
            self.assertEqual(list(self.base.glob('Screenshot-*')), [])
            self.assertTrue(path.exists())
            raise C.CaptureError('capture_timeout')
        with patch.object(C, 'command'), patch.object(C, 'guard'), \
                patch.object(C, 'save_directory', return_value=self.base), \
                patch.object(C, 'png_ready', side_effect=failed_decode), patch.object(C.time, 'sleep') as sleep:
            with self.assertRaisesRegex(C.CaptureError, 'capture_timeout'):
                C.picture(dict(target='window', window_id=17))
        sleep.assert_called_once_with(2)
        self.assertEqual(list(self.base.glob('*Screenshot-*')), [])

    def test_missing_xdg_user_directories_fall_back_to_named_folders(self):
        with patch.object(C, 'command', return_value=os.fsencode(self.base)):
            self.assertEqual(C.save_directory('picture'), self.base / 'Pictures/Screenshots')
            self.assertEqual(C.save_directory('recording'), self.base / 'Videos/Screencasts')

    def test_truncated_picture_is_not_reported_as_saved(self):
        source = self.base / 'bad.png'
        source.write_bytes(b'\x89PNG\r\n\x1a\n')
        with patch.object(C, 'guard'):
            with self.assertRaisesRegex(C.CaptureError, 'capture_timeout'):
                C.png_ready(source, time.monotonic() + .06)

    def test_recording_finalizes_then_publishes_and_cleans_errors(self):
        for failing in (False, True):
            with self.subTest(failing=failing):
                seen = []
                publish = C.publish
                def publish_saved(path):
                    seen.append('publish')
                    return publish(path)
                class FakeRecorder:
                    def __init__(self, selected, path):
                        self.path = path
                    def run(self):
                        self.path.write_bytes(b'encoded-stream')
                        seen.append('run')
                        if failing:
                            raise C.CaptureError('recorder_failed')
                    def close(self):
                        seen.append('close')
                with patch.object(C, 'Recorder', FakeRecorder), patch.object(C, 'guard'), \
                        patch.object(C, 'save_directory', return_value=self.base), \
                        patch.object(C, 'publish', side_effect=publish_saved):
                    if failing:
                        with self.assertRaisesRegex(C.CaptureError, 'recorder_failed'):
                            C.record(dict(target='window', window_id=17))
                    else:
                        result = C.record(dict(target='window', window_id=17))
                        self.assertEqual(Path(result['path']).read_bytes(), b'encoded-stream')
                        Path(result['path']).unlink()
                self.assertEqual(seen, ['run', 'close'] if failing else ['run', 'publish', 'close'])
                self.assertEqual(list(self.base.glob('*.mp4')), [])
                self.assertEqual(list(self.base.glob('.*.part')), [])

    def test_close_stops_session_even_if_pipeline_shutdown_fails(self):
        recorder = C.Recorder.__new__(C.Recorder)
        recorder.native = Mock()
        recorder.lock_watch = Mock()
        recorder.session = '/session'
        recorder.subscription = 4
        recorder.connection = Mock()
        recorder.call = Mock()
        with patch.object(C, 'stop_child', side_effect=RuntimeError('failed')) as stop, self.assertRaises(RuntimeError):
            recorder.close()
        self.assertEqual(stop.call_args_list[0].args, (recorder.native,))
        self.assertEqual(stop.call_args_list[0].kwargs, dict(timeout=35))
        recorder.call.assert_called_once_with('/session', recorder.SESSION, 'Stop')
        recorder.connection.signal_unsubscribe.assert_called_once_with(4)

    def test_close_tolerates_lost_session_and_subscription(self):
        from gi.repository import GLib
        for stop_fails, unsubscribe_fails in ((True, False), (False, True), (True, True)):
            with self.subTest(stop_fails=stop_fails, unsubscribe_fails=unsubscribe_fails):
                recorder = C.Recorder.__new__(C.Recorder)
                recorder.GLib = GLib
                recorder.native = None
                recorder.lock_watch = Mock()
                watch = recorder.lock_watch
                recorder.session, recorder.subscription = '/session', 4
                recorder.call = Mock(side_effect=GLib.Error('session gone') if stop_fails else None)
                recorder.connection = Mock()
                recorder.connection.signal_unsubscribe.side_effect = GLib.Error('bus gone') if unsubscribe_fails else None
                recorder.close()
                recorder.close()
                watch.close.assert_called_once_with()
                recorder.call.assert_called_once_with('/session', recorder.SESSION, 'Stop')
                recorder.connection.signal_unsubscribe.assert_called_once_with(4)
                self.assertIsNone(recorder.session)
                self.assertIsNone(recorder.subscription)

    def test_dbus_window_id_and_subscription_precede_start(self):
        recorder = C.Recorder.__new__(C.Recorder)
        recorder.selected = dict(target='window', window_id=2 ** 40)
        recorder.GLib = SimpleNamespace(Variant=lambda signature, value: (signature, value))
        recorder.Gio = SimpleNamespace(DBusSignalFlags=SimpleNamespace(NONE=0))
        recorder.connection = Mock()
        order = []
        recorder.connection.signal_subscribe.side_effect = lambda *_: order.append('subscribe') or 42
        def call(path, interface, method, *args):
            order.append(method)
            if method == 'CreateSession':
                return ('/session',)
            if method == 'RecordWindow':
                self.assertEqual(args[1][0]['window-id'], ('t', 2 ** 40))
                return ('/stream',)
            return ()
        recorder.call = call
        with patch.object(C, 'guard'):
            recorder.connect()
        self.assertEqual(order, ['CreateSession', 'RecordWindow', 'subscribe', 'Start'])

    def test_stdin_eof_and_stop_both_stop_recorder(self):
        recorder = C.Recorder.__new__(C.Recorder)
        recorder.stop_requested = False
        for data in (b'', b'stop\n'):
            read, write = os.pipe()
            if data:
                os.write(write, data)
            os.close(write)
            with os.fdopen(read) as stream, patch.object(C.sys, 'stdin', stream):
                self.assertTrue(recorder.input_stopped())

    def test_killed_launcher_still_stops_worker_with_shell_stdin_open(self):
        # The fixture replaces capture only; the launcher, descriptor inheritance,
        # worker argument parsing, lifecycle loop and finalization are production code.
        fixture = self.base / 'worker.py'
        fixture.write_text('''import importlib.util, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location("capture", %r)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
base = pathlib.Path(%r)
class Recorder:
    def __init__(self, selected, path):
        self.path = path
        self.stop_requested = False
    def run(self):
        (base / "worker.pid").write_text(str(os.getpid()))
        deadline = time.monotonic() + 6
        while not C.Recorder.input_stopped(self):
            if time.monotonic() > deadline:
                raise RuntimeError("lifetime did not close")
            time.sleep(.01)
        self.path.write_bytes(b"finalized")
    def close(self):
        (base / "session-stopped").write_text("yes")
if "--record-worker" in sys.argv:
    Original = C.Recorder
    Recorder.input_stopped = Original.input_stopped
    C.Recorder = Recorder
    C.inventory = lambda: {"windows": [{"id": 17}], "outputs": []}
    C.guard = lambda: None
    C.save_directory = lambda _: base
    sys.exit(C.main())
sys.exit(C.launch_record('{"target":"window","window_id":17}', __file__))
''' % (str(ROOT / 'shell/helpers/capture.py'), str(self.base)))
        log = self.base / 'worker.log'
        with log.open('wb') as output:
            launcher = subprocess.Popen([os.sys.executable, '-I', '-B', str(fixture)],
                                         stdin=subprocess.PIPE, stdout=output, stderr=output,
                                         env=C.child_environment())
            try:
                deadline = time.monotonic() + 4
                while not (self.base / 'worker.pid').exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((self.base / 'worker.pid').exists(), log.read_text())
                launcher.kill()
                launcher.wait(timeout=3)
                # Retain launcher.stdin: shell-side EOF cannot explain the worker stopping.
                self.assertFalse(launcher.stdin.closed)
                deadline = time.monotonic() + 3
                while '"event":"saved"' not in log.read_text() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((self.base / 'session-stopped').exists(), log.read_text())
                self.assertIn('"event":"saved"', log.read_text())
                self.assertEqual(list(self.base.glob('.*.part')), [])
            finally:
                launcher.stdin.close()
                if launcher.poll() is None:
                    launcher.kill()
                    launcher.wait(timeout=3)

    def test_main_machine_error_does_not_leak_diagnostics(self):
        output = io.StringIO()
        with patch.object(C.sys, 'argv', ['capture', '--inventory']), patch.object(C.sys, 'stdout', output), patch.object(C, 'inventory', side_effect=RuntimeError('private title and path')):
            self.assertEqual(C.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), dict(event='error', reason='capture_failed'))

    def test_owned_native_finalization_lock_and_failures(self):
        from gi.repository import GLib
        for behavior in ('stop', 'lock', 'logout', 'error', 'error-logout', 'empty', 'unsaved', 'failed-saved'):
            with self.subTest(behavior=behavior):
                native = self.base / 'native'
                report = self.base / 'native-environment.json'
                native.write_text('''#!/usr/bin/env python3
import json, os, pathlib, select, signal, sys
args = sys.argv
path = pathlib.Path(args[args.index('--output') + 1])
pathlib.Path(%r).write_text(json.dumps({"bus": os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
                                      "private_fd": os.environ.get("WAYLAND_SOCKET")}))
behavior = %r
stopped = False
def stop(*_):
    global stopped
    stopped = True
signal.signal(signal.SIGINT, stop)
print(json.dumps({"event": "recording"}), flush=True)
if behavior in ('error', 'error-logout'):
    path.write_bytes(b'partial-frames')
    print(json.dumps({"event": "error", "code": "target_changed"}), flush=True)
    sys.exit(1)
while not stopped:
    if select.select([0], [], [], .01)[0] and not os.read(0, 4096):
        break
path.write_bytes(b'encoded-frames')
if behavior != 'unsaved':
    print(json.dumps({"event": "saved", "frames": 0 if behavior == 'empty' else 5}), flush=True)
sys.exit(1 if behavior == 'failed-saved' else 0)
''' % (str(report), behavior))
                native.chmod(0o700)
                real_recorder = C.Recorder
                instances = []
                begin = time.monotonic()
                class FixtureRecorder(real_recorder):
                    def __init__(self, selected, path):
                        self.selected, self.path = selected, path
                        self.GLib = GLib
                        self.connection = Mock()
                        self.call = Mock()
                        if behavior in ('logout', 'error-logout'):
                            self.call.side_effect = GLib.Error('session gone')
                            self.connection.signal_unsubscribe.side_effect = GLib.Error('bus gone')
                        self.session, self.subscription = '/fixture-session', 1
                        self.native = self.lock_watch = None
                        self.native_buffer = b''
                        self.node = 123
                        self.stop_requested = self.started = self.native_saved = False
                        instances.append(self)

                    def connect(self):
                        pass

                    def pump(self):
                        pass

                    def input_stopped(self):
                        return behavior != 'lock' and time.monotonic() - begin > .15

                    def target_exists(self):
                        return True

                class FixtureWatch:
                    def __init__(self):
                        self.fd, self.write = os.pipe()
                    def cutoff(self):
                        return time.time_ns() if behavior == 'lock' and time.monotonic() - begin > .15 else None
                    def close(self):
                        os.close(self.fd)
                        os.close(self.write)

                with patch.object(C, 'Recorder', FixtureRecorder), patch.object(C, 'LockWatch', FixtureWatch), \
                        patch.object(C, 'native_recorder', return_value=native), \
                        patch.object(C, 'guard'), patch.object(C, 'save_directory', return_value=self.base), \
                        patch.object(C, 'emit'):
                    if behavior in ('error', 'error-logout', 'empty', 'unsaved', 'failed-saved'):
                        with self.assertRaises(C.CaptureError):
                            C.record(dict(target='screen', output=OUTPUT))
                    else:
                        result = C.record(dict(target='screen', output=OUTPUT))
                        self.assertEqual(Path(result['path']).read_bytes(), b'encoded-frames')
                        Path(result['path']).unlink()
                recorder = instances[0]
                self.assertIsNotNone(recorder.native.poll())
                recorder.call.assert_called_once_with('/fixture-session', recorder.SESSION, 'Stop')
                recorder.connection.signal_unsubscribe.assert_called_once_with(1)
                self.assertEqual(json.loads(report.read_text()),
                                 dict(bus=os.environ.get('DBUS_SESSION_BUS_ADDRESS'), private_fd=None))
                self.assertEqual(list(self.base.glob('.*.part')), [])
                self.assertEqual(list(self.base.glob('*.mp4')), [])

    def test_native_stop_timeout_reaps_only_owned_child(self):
        child = Mock()
        child.poll.return_value = None
        child.stdin.closed = False
        child.wait.side_effect = [subprocess.TimeoutExpired('owned', .01),
                                 subprocess.TimeoutExpired('owned', 2), 0]
        C.stop_child(child, timeout=.01)
        child.stdin.close.assert_called_once()
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_args_list[-1].kwargs, dict(timeout=2))

    def test_lock_events_retain_marker_time_after_delayed_dispatch(self):
        with patch.object(C, 'guard'):
            observer = C.LockWatch()
        self.addCleanup(observer.close)
        observer.directory.mkdir(mode=0o700)
        observer.marker.write_text('{}')
        observer.marker.chmod(0o600)
        stamp = observer.marker.stat().st_mtime_ns
        self.assertIn(observer.fd, select.select([observer.fd], [], [], 1)[0])
        time.sleep(.05)
        cutoff = observer.cutoff()
        self.assertLessEqual(cutoff, stamp)
        # Filesystem birth timestamps may have coarser precision than time_ns().
        self.assertLess(cutoff, time.time_ns())
        observer.marker.unlink()
        self.assertEqual(observer.cutoff(), cutoff)

    def test_short_lived_lock_event_cuts_at_previous_safe_observation(self):
        with patch.object(C, 'guard'):
            observer = C.LockWatch()
        self.addCleanup(observer.close)
        checkpoint = observer.checkpoint_ns
        observer.directory.mkdir(mode=0o700)
        observer.marker.write_text('{}')
        observer.marker.unlink()
        self.assertEqual(observer.cutoff(), checkpoint)

    def test_existing_lock_directory_watches_atomic_marker_replacement(self):
        with patch.object(C, 'guard'):
            setup = C.LockWatch()
            setup.directory.mkdir(mode=0o700)
            setup.close()
            observer = C.LockWatch()
        self.addCleanup(observer.close)
        self.assertIsNone(observer.cutoff())
        self.assertEqual(select.select([observer.fd], [], [], .03)[0], [])
        temporary = observer.directory / '.pending'
        temporary.write_text('{}')
        temporary.chmod(0o600)
        temporary.rename(observer.marker)
        self.assertLessEqual(observer.cutoff(), observer.marker.stat().st_mtime_ns)

    def test_rewritten_marker_cannot_advance_the_first_lock_boundary(self):
        with patch.object(C, 'guard'):
            observer = C.LockWatch()
        self.addCleanup(observer.close)
        observer.directory.mkdir(mode=0o700)
        observer.marker.write_text('{}')
        observer.marker.chmod(0o600)
        original = observer.marker.stat().st_mtime_ns
        time.sleep(.02)
        observer.marker.write_text('{}')
        self.assertLessEqual(observer.cutoff(), original)

    def test_lock_observer_rejects_clock_step_before_recorder_start(self):
        with patch.object(C, 'guard'):
            observer = C.LockWatch()
        self.addCleanup(observer.close)
        with patch.object(C.os, 'read', side_effect=OSError(errno.ECANCELED, 'clock changed')):
            with self.assertRaises(C.CaptureError):
                observer.cutoff()

    def test_group_termination_waits_for_recorder_saved_result(self):
        native = self.base / 'native-group.py'
        native.write_text('''import json, os, pathlib, select, signal, sys, time
signal.signal(signal.SIGTERM, lambda *_: None)
signal.signal(signal.SIGINT, lambda *_: None)
print('{"event":"recording"}', flush=True)
line = sys.stdin.buffer.readline()
cutoff = json.loads(line)['stop_at_ns']
time.sleep(.15)
path = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
path.write_bytes(str(cutoff).encode())
print('{"event":"saved","frames":5}', flush=True)
''')
        worker = self.base / 'group-worker.py'
        worker.write_text('''import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('capture', %r)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
C.guard = lambda: None
C.native_recorder = lambda: pathlib.Path(%r)
C.save_directory = lambda _: pathlib.Path(%r)
class Fixture(C.Recorder):
    def __init__(self, selected, path):
        self.selected, self.path = selected, path
        self.native = self.lock_watch = None
        self.native_buffer = b''
        self.session = self.subscription = None
        self.node = 123
        self.stop_requested = self.started = self.native_saved = False
    def connect(self): pass
    def pump(self): pass
    def target_exists(self): return True
C.Recorder = Fixture
C.emit('saved', **C.record(dict(target='screen')))
''' % (str(ROOT / 'shell/helpers/capture.py'), str(native), str(self.base)))
        child = subprocess.Popen([os.sys.executable, '-I', str(worker)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True, env=C.child_environment())
        try:
            self.assertTrue(select.select([child.stdout], [], [], 3)[0], 'recorder startup deadline')
            self.assertEqual(json.loads(child.stdout.readline()), dict(event='stream', node=123))
            self.assertEqual(json.loads(child.stdout.readline()), dict(event='recording'))
            os.killpg(child.pid, signal.SIGTERM)
            stdout, stderr = child.communicate(timeout=4)
            self.assertEqual(child.returncode, 0, stderr.decode())
            saved = json.loads(stdout)
            self.assertEqual(saved['event'], 'saved')
            self.assertGreater(int(Path(saved['path']).read_bytes()), 0)
            self.assertEqual(list(self.base.glob('.*.part')), [])
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=1)

    def test_native_area_arguments_preserve_logical_geometry(self):
        recorder = C.Recorder.__new__(C.Recorder)
        recorder.selected = C.selection(dict(target='area', output='TEST-1',
            area=dict(x=3.5, y=5, width=80.5, height=72)), AVAILABLE)
        recorder.path = self.base / 'reserved.part'
        recorder.node = 123
        with patch.object(C, 'native_recorder', return_value=Path('/fixture/native')), \
                patch.object(C.subprocess, 'Popen') as start, patch.object(C.os, 'set_blocking'):
            recorder.start_native()
        argv = start.call_args.args[0]
        self.assertEqual(argv[argv.index('--x') + 1], '3.5')
        self.assertEqual(argv[argv.index('--logical-width') + 1], '800')
        self.assertEqual(argv[argv.index('--node') + 1], '123')
        self.assertNotIn('WAYLAND_SOCKET', start.call_args.kwargs['env'])



if __name__ == '__main__':
    unittest.main()
