#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise recorder validation and decode every frame around a delayed lock event."""
import json
import os
from pathlib import Path
import subprocess
import select
import signal
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
import importlib.util
import errno
from types import SimpleNamespace
from unittest.mock import patch
import threading
import time

SPEC = importlib.util.spec_from_file_location('capture_recorder', ROOT / 'capture/recorder.py')
RECORDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECORDER)


class NativeCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='capture-native-test-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)

    def run_native(self, *arguments):
        try:
            if arguments[0] == 'parse':
                RECORDER.parse_options([*map(str, arguments[1:]), '--node', '1'])
            elif arguments[0] == 'reserved':
                os.close(RECORDER.open_reserved_output(arguments[1]))
            else:
                mode, sw, sh, lw, lh, x, y, width, height = arguments
                options = SimpleNamespace(mode=mode, logical_width=float(lw), logical_height=float(lh),
                                          x=float(x), y=float(y), width=float(width), height=float(height))
                geometry = RECORDER.calculate_geometry(options, int(sw), int(sh))
                fields = ('crop_left', 'crop_top', 'width', 'height', 'requested_width', 'requested_height')
                return SimpleNamespace(returncode=0, stdout=json.dumps([geometry[k] for k in fields]), stderr='')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        except (RECORDER.CaptureError, ValueError, OSError, SystemExit) as error:
            return SimpleNamespace(returncode=1, stdout='', stderr=str(error))

    def geometry(self, sw, sh, lw, lh, x, y, width, height, mode='area'):
        result = self.run_native(mode, sw, sh, lw, lh, x, y, width, height)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_fractional_selection_and_alignment_only_remove_selected_pixels(self):
        self.assertEqual(self.geometry(150, 75, 100, 50, .1, .2, 10.2, 10.5),
                         [1, 1, 12, 14, 14, 15])
        self.assertEqual(self.geometry(1000, 750, 800, 600, 1, 3, 20, 24),
                         [2, 4, 24, 28, 24, 29])
        self.assertEqual(self.geometry(1600, 1200, 800, 600, 1, 3, 20, 24),
                         [2, 6, 40, 48, 40, 48])

    def test_extreme_finite_ratios_do_not_underflow_start_coordinates(self):
        self.assertEqual(self.geometry(1000, 1000, 1e308, 1e308, 1e-300, 1e-300, 1e308, 1e308),
                         [1, 1, 996, 998, 999, 999])

    def test_transformed_and_fractional_sources_stay_within_all_four_selected_edges(self):
        for sw, sh in [(1000, 750), (750, 1000), (1280, 800), (1537, 867)]:
            for x, y, width, height in [(0, 0, 800, 600), (.25, 2.75, 91.5, 77.25),
                                         (631.5, 502.25, 168.5, 97.75)]:
                with self.subTest(source=(sw, sh), origin=(x, y)):
                    left, top, cw, ch, rw, rh = self.geometry(sw, sh, 800, 600, x, y, width, height)
                    self.assertGreaterEqual(left, x / 800 * sw)
                    self.assertGreaterEqual(top, y / 600 * sh)
                    self.assertLessEqual(left + cw, (x + width) / 800 * sw)
                    self.assertLessEqual(top + ch, (y + height) / 600 * sh)
                    self.assertEqual(cw % 4, 0)
                    self.assertEqual(ch % 2, 0)
                    self.assertLess(rw - cw, 4)
                    self.assertLess(rh - ch, 2)

    def test_source_dimensions_determine_canvas_and_tiny_or_clipped_area_fails(self):
        self.assertEqual(self.geometry(1537, 867, 0, 0, 0, 0, 0, 0, mode='window'),
                         [0, 0, 1536, 866, 1537, 867])
        for arguments in [('area', 150, 75, 100, 50, 99, 0, 3, 20),
                          ('area', 150, 75, 100, 50, 0, 0, 1, 20),
                          ('screen', 16385, 900, 0, 0, 0, 0, 0, 0),
                          ('screen', 0, 900, 0, 0, 0, 0, 0, 0)]:
            with self.subTest(arguments=arguments):
                self.assertEqual(self.run_native(*arguments).returncode, 1)

    def test_argument_validation_rejects_nonfinite_overflow_and_outside_rectangles(self):
        valid = ['--output', 'reserved', '--mode', 'area', '--logical-width', '100',
                 '--logical-height', '50', '--x', '.25', '--y', '.5', '--width', '10.5', '--height', '20']
        self.assertEqual(self.run_native('parse', *valid).returncode, 0)
        for name, values in {
            '--logical-width': ['nan', 'inf', '0', '-1', '1e-320'],
            '--logical-height': ['nan', '0', '-1'],
            '--x': ['nan', 'inf', '-1', '100', '1e308'],
            '--y': ['nan', '-1', '50'],
            '--width': ['nan', 'inf', '0', '-1', '100', '1e309'],
            '--height': ['nan', '0', '-1', '50'],
        }.items():
            for value in values:
                with self.subTest(argument=name, value=value):
                    arguments = valid.copy()
                    arguments[arguments.index(name) + 1] = value
                    self.assertEqual(self.run_native('parse', *arguments).returncode, 1)
        overflow = valid.copy()
        for name in ['--x', '--width', '--logical-width']:
            overflow[overflow.index(name) + 1] = '1e308'
        self.assertEqual(self.run_native('parse', *overflow).returncode, 1)
        self.assertEqual(self.run_native('parse', '--output', 'x', '--mode', 'unknown').returncode, 1)
        self.assertEqual(self.run_native('parse', *valid, 'unexpected').returncode, 1)

    def test_reserved_output_rejects_links_existing_media_and_permissive_modes(self):
        with tempfile.TemporaryDirectory(dir=self.base) as directory:
            root = Path(directory)
            reserved = root / 'reserved.webm'
            reserved.touch(mode=0o600)
            self.assertEqual(self.run_native('reserved', reserved).returncode, 0)
            symlink = root / 'symlink.webm'
            symlink.symlink_to(reserved)
            self.assertEqual(self.run_native('reserved', symlink).returncode, 1)
            hardlink = root / 'hardlink.webm'
            os.link(reserved, hardlink)
            self.assertEqual(self.run_native('reserved', reserved).returncode, 1)
            hardlink.unlink()
            reserved.chmod(0o644)
            self.assertEqual(self.run_native('reserved', reserved).returncode, 1)
            reserved.chmod(0o600)
            reserved.write_bytes(b'existing recording')
            self.assertEqual(self.run_native('reserved', reserved).returncode, 1)
            self.assertEqual(reserved.read_bytes(), b'existing recording')
            self.assertEqual(self.run_native('reserved', root / 'missing').returncode, 1)
            self.assertEqual(self.run_native('reserved', root).returncode, 1)
            fifo = root / 'fifo'
            os.mkfifo(fifo, 0o600)
            self.assertEqual(self.run_native('reserved', fifo).returncode, 1)


class BoundaryTests(unittest.TestCase):
    def test_marker_boundary_is_strict_even_when_stop_arrives_late(self):
        boundary = RECORDER.FrameBoundary()
        for stamp in (100, 200, 300, 400):
            boundary.append(stamp)
        boundary.stop(300)
        self.assertEqual(boundary.kept(), 2)
        self.assertIsNone(boundary.append(500))
        boundary.stop(350)
        self.assertEqual(boundary.kept(), 2)
        boundary.stop(100)
        self.assertEqual(boundary.kept(), 0)

    def test_clock_backwards_fails_closed(self):
        boundary = RECORDER.FrameBoundary()
        boundary.append(200)
        with self.assertRaisesRegex(RECORDER.CaptureError, 'clock_changed'):
            boundary.append(199)

    def test_realtime_discontinuity_between_frames_fails_closed(self):
        guard = object.__new__(RECORDER.ClockGuard)
        guard.fd = -1
        with patch.object(RECORDER.os, 'read', side_effect=OSError(errno.ECANCELED, 'clock changed')):
            with self.assertRaisesRegex(RECORDER.CaptureError, 'clock_changed'):
                guard.check()
        with patch.object(RECORDER.os, 'read', side_effect=BlockingIOError()):
            guard.check()

    def test_source_count_mismatch_and_empty_prefix_are_never_saved(self):
        boundary = RECORDER.FrameBoundary()
        boundary.append(100)
        with patch.object(RECORDER, 'media_frames', return_value=2):
            with self.assertRaisesRegex(RECORDER.CaptureError, 'finalization_failed'):
                RECORDER.finalize(-1, boundary, '.')
        boundary.stop(100)
        with patch.object(RECORDER, 'media_frames', return_value=1):
            with self.assertRaisesRegex(RECORDER.CaptureError, 'no_frames'):
                RECORDER.finalize(-1, boundary, '.')


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.Gst = RECORDER.gst_runtime()
            for program in ('ffmpeg', 'ffprobe'):
                subprocess.run([program, '-version'], check=True, capture_output=True, timeout=5)
        except (RECORDER.CaptureError, OSError, subprocess.SubprocessError) as error:
            raise unittest.SkipTest('Recorder runtime unavailable (no SDK required): ' + str(error)) from error

    def test_delayed_lock_cuts_every_forbidden_frame_including_exact_boundary(self):
        self.check_cutoff()

    def test_signal_stop_waits_for_workers_earlier_lock_boundary(self):
        self.check_cutoff(handshake=True)

    def test_resized_window_keeps_initial_canvas_and_exact_cutoff(self):
        self.check_cutoff(resize=True)

    def test_process_group_termination_preserves_real_encoded_recording(self):
        with tempfile.TemporaryDirectory(prefix='capture-group-runtime-') as directory:
            root = Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir(mode=0o700)
            home = root / 'home'
            home.mkdir(mode=0o700)
            evidence = root / 'recorder-signal'
            child = root / 'synthetic-recorder.py'
            child.write_text("""# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util, pathlib, sys, threading, time
spec = importlib.util.spec_from_file_location('recorder', %r)
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
class Synthetic(recorder.Recorder):
    def __init__(self, options, fd):
        Gst = recorder.gst_runtime()
        source = Gst.ElementFactory.make('appsrc')
        source.set_property('caps', Gst.Caps.from_string(
            'video/x-raw,format=RGB,width=64,height=48,framerate=30/1'))
        source.set_property('format', Gst.Format.TIME)
        source.set_property('is-live', True)
        source.set_property('block', True)
        source.set_property('max-buffers', 2)
        super().__init__(options, fd, source=source)

    def request_stop(self, stamp=None):
        caller = sys._getframe(1)
        if caller.f_code.co_name == 'stop' and caller.f_code.co_filename == recorder.__file__:
            pathlib.Path(%r).write_text('production signal handler reached')
        super().request_stop(stamp)

    def run(self, *args, **kwargs):
        done = threading.Event()
        def produce():
            while not done.is_set() and self.boundary.cutoff is None:
                buffer = self.Gst.Buffer.new_allocate(None, 64 * 48 * 3, None)
                buffer.fill(0, bytes((0, 255, 0)) * (64 * 48))
                buffer.duration = self.Gst.SECOND // 30
                if self.source.emit('push-buffer', buffer) != self.Gst.FlowReturn.OK:
                    break
                done.wait(1 / 30)
        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        try:
            return super().run(*args, **kwargs)
        finally:
            done.set()
            producer.join(1)
recorder.Recorder = Synthetic
sys.exit(recorder.main())
""" % (str(ROOT / 'capture/recorder.py'), str(evidence)))
            worker = root / 'worker.py'
            worker.write_text("""# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location('worker', %r)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
class CompositorFixture(worker.Recorder):
    def __init__(self, selected, path):
        self.selected, self.path = selected, path
        self.connection = None
        self.session = self.subscription = self.native = self.lock_watch = None
        self.native_buffer = b''
        self.node = None
        self.stop_requested = self.started = self.native_saved = False
    def connect(self):
        self.node = 1
    def pump(self):
        pass
    def target_exists(self):
        return True
worker.Recorder = CompositorFixture
worker.native_recorder = lambda: pathlib.Path(%r)
result = worker.record(dict(target='screen', output=dict(name='fixture', width=64, height=48)))
worker.emit('saved', **result)
""" % (str(ROOT / 'shell/helpers/capture.py'), str(child)))
            environment = dict(os.environ, HOME=str(home), XDG_RUNTIME_DIR=str(runtime),
                               XDG_CONFIG_HOME=str(home / '.config'), WAYLAND_DISPLAY='fixture')
            process = subprocess.Popen([sys.executable, '-I', '-B', str(worker)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=environment,
                                       start_new_session=True)
            try:
                pending = b''
                deadline = time.monotonic() + 5
                while pending.count(b'\n') < 2 and time.monotonic() < deadline:
                    if select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        pending += chunk
                self.assertGreaterEqual(pending.count(b'\n'), 2, 'No complete recording event')
                # The shell learns its own cast's node before the recorder reads it.
                first, second, pending = pending.split(b'\n', 2)
                self.assertEqual(json.loads(first), dict(event='stream', node=1))
                event = json.loads(second)
                self.assertEqual(event['event'], 'recording')
                # Allow several frames through the real encoder before terminating
                # the same group that contains both worker and recorder processes.
                time.sleep(.15)
                os.killpg(process.pid, signal.SIGTERM)
                output, errors = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, errors.decode())
                events = [json.loads(line) for line in (pending + output).splitlines()]
                self.assertEqual(events[-1]['event'], 'saved')
                self.assertTrue(evidence.is_file(), 'Recorder did not receive the group signal')
                saved = Path(events[-1]['path'])
                self.assertTrue(saved.is_file())
                self.assertFalse(list(saved.parent.glob('.*.part')))
                decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(saved), '-f', 'rawvideo',
                                          '-pix_fmt', 'rgb24', '-fps_mode', 'passthrough', '-'],
                                         capture_output=True, timeout=5, check=True).stdout
                frame_size = 64 * 48 * 3
                self.assertGreaterEqual(len(decoded) // frame_size, 2)
                self.assertEqual(len(decoded) % frame_size, 0)
                for index in range(len(decoded) // frame_size):
                    frame = decoded[index * frame_size:(index + 1) * frame_size]
                    self.assertLess(max(frame[0::3]), 20, index)
                    self.assertGreater(min(frame[1::3]), 230, index)
                    self.assertLess(max(frame[2::3]), 20, index)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if process.poll() is None:
                    process.communicate(timeout=3)

    def check_cutoff(self, handshake=False, resize=False):
        # appsrc uses the production crop/convert/encode/mux/finalize path. Frames
        # delivered after the lock are intentionally already encoded before stop.
        Gst = self.Gst
        with tempfile.TemporaryDirectory(prefix='capture-pipeline-') as directory:
            path = Path(directory) / '.recording.part'
            path.touch(mode=0o600)
            fd = RECORDER.open_reserved_output(path)
            self.addCleanup(os.close, fd)
            options = RECORDER.parse_options(['--output', str(path), '--mode', 'screen', '--node', '1'])
            source = Gst.ElementFactory.make('appsrc')
            source.set_property('caps', Gst.Caps.from_string('video/x-raw,format=RGB,width=64,height=48,framerate=0/1'))
            source.set_property('format', Gst.Format.TIME)
            source.set_property('is-live', True)
            stamps = iter(1_000_000_000 + index * 100_000_000 for index in range(8))
            recorder = RECORDER.Recorder(options, fd, source=source, clock=lambda: next(stamps, 1_900_000_000))
            events, failures = [], []
            read_fd, write_fd = os.pipe() if handshake else (None, None)
            if read_fd is not None:
                self.addCleanup(os.close, read_fd)

            def emit(event):
                events.append(event)
                if handshake and event['event'] == 'stopping':
                    os.write(write_fd, b'{"stop_at_ns":1400000000}\n')
                    os.close(write_fd)

            def run():
                try:
                    recorder.run(emit, control_fd=read_fd, startup_timeout=3, stop_timeout=3)
                except Exception as error:
                    failures.append(error)

            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            for index in range(8):
                width, height = (80, 64) if resize and index >= 2 else (64, 48)
                source.set_property('caps', Gst.Caps.from_string(
                    f'video/x-raw,format=RGB,width={width},height={height},framerate=0/1'))
                color = bytes((255, 0, 0) if index >= 4 else (0, 255, 0))
                buffer = Gst.Buffer.new_allocate(None, width * height * 3, None)
                buffer.fill(0, color * (width * height))
                buffer.duration = 100_000_000
                self.assertEqual(source.emit('push-buffer', buffer), Gst.FlowReturn.OK)
            deadline = time.monotonic() + 4
            while len(recorder.boundary.arrivals) < 8 and not failures and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(len(recorder.boundary.arrivals), 8, failures)
            recorder.request_stop(1_800_000_000 if handshake else 1_400_000_000)
            thread.join(8)
            self.assertFalse(thread.is_alive(), 'Recorder did not honor the bounded stop')
            self.assertEqual(failures, [])
            self.assertEqual(events[-1]['event'], 'saved')
            self.assertEqual(events[-1]['frames'], 4)
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                                      '-pix_fmt', 'rgb24', '-fps_mode', 'passthrough', '-'], capture_output=True,
                                     timeout=5, check=True).stdout
            frame_size = 64 * 48 * 3
            self.assertEqual(len(decoded), frame_size * 4)
            for index in range(4):
                frame = decoded[index * frame_size:(index + 1) * frame_size]
                # Every pixel in every retained frame must be the pre-lock green.
                self.assertLess(max(frame[0::3]), 20, index)
                if resize and index >= 2:
                    self.assertGreater(frame[(24 * 64 + 32) * 3 + 1], 230, index)
                else:
                    self.assertGreater(min(frame[1::3]), 230, index)
                self.assertLess(max(frame[2::3]), 20, index)


if __name__ == '__main__':
    unittest.main()
