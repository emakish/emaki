#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Software PipeWire recorder with an exact, retrospective lock boundary."""
import argparse
import bisect
import errno
import json
import importlib.util
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from array import array
from fractions import Fraction


class CaptureError(Exception):
    pass


def parse_options(arguments):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=('screen', 'window', 'area'), required=True)
    parser.add_argument('--node', type=int, required=True)
    parser.add_argument('--cursor', action='store_true')
    for name in ('logical-width', 'logical-height', 'x', 'y', 'width', 'height'):
        parser.add_argument('--' + name, type=float, default=0)
    options = parser.parse_args(arguments)
    if options.node < 0 or options.node > 0xffffffff:
        raise CaptureError('invalid_arguments')
    if options.mode == 'area':
        values = (options.logical_width, options.logical_height, options.x, options.y,
                  options.width, options.height, options.x + options.width, options.y + options.height)
        if (not all(math.isfinite(value) for value in values) or
                options.logical_width <= 0 or options.logical_height <= 0 or
                options.x < 0 or options.y < 0 or options.width <= 0 or options.height <= 0 or
                options.x + options.width > options.logical_width or
                options.y + options.height > options.logical_height):
            raise CaptureError('invalid_arguments')
    return options


def calculate_geometry(options, sw, sh):
    if not 0 < sw <= 16384 or not 0 < sh <= 16384:
        raise CaptureError('invalid_geometry')
    left = top = 0
    rw, rh = sw, sh
    if options.mode == 'area':
        # Exact float ratios avoid overflow and round inward even at extreme scales.
        x, y, width, height, lw, lh = map(Fraction, (
            options.x, options.y, options.width, options.height,
            options.logical_width, options.logical_height))
        left, top = math.ceil(x * sw / lw), math.ceil(y * sh / lh)
        right, bottom = math.floor((x + width) * sw / lw), math.floor((y + height) * sh / lh)
        if left < 0 or top < 0 or right > sw or bottom > sh:
            raise CaptureError('invalid_geometry')
        rw, rh = right - left, bottom - top
    width, height = rw & ~3, rh & ~1
    if width < 4 or height < 2:
        raise CaptureError('invalid_geometry')
    return dict(source_width=sw, source_height=sh, width=width, height=height,
                crop_left=left, crop_top=top, crop_width=width, crop_height=height,
                requested_width=rw, requested_height=rh)


def open_reserved_output(path):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    status = os.fstat(fd)
    if (not stat.S_ISREG(status.st_mode) or status.st_uid != os.getuid() or
            stat.S_IMODE(status.st_mode) != 0o600 or status.st_nlink != 1 or status.st_size != 0):
        os.close(fd)
        raise CaptureError('invalid_output')
    return fd


def gst_runtime():
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
    except (ImportError, ValueError) as error:
        raise CaptureError('modules_unavailable') from error
    for name in ('pipewiresrc', 'appsrc', 'appsink', 'videocrop', 'videoconvert', 'videoscale', 'openh264enc',
                 'h264parse', 'mp4mux', 'fdsink'):
        if not Gst.ElementFactory.find(name):
            raise CaptureError('modules_unavailable')
    return Gst


class FrameBoundary:
    """One arrival stamp per source frame; equality belongs to the locked interval."""
    def __init__(self):
        self.arrivals = array('Q')
        self.cutoff = None
        self.mutex = threading.RLock()

    def append(self, stamp):
        with self.mutex:
            if self.arrivals and stamp <= self.arrivals[-1]:
                raise CaptureError('clock_changed')
            if self.cutoff is not None and stamp >= self.cutoff:
                return None
            self.arrivals.append(stamp)
            return stamp - self.arrivals[0]

    def stop(self, stamp):
        with self.mutex:
            self.cutoff = stamp if self.cutoff is None else min(stamp, self.cutoff)

    def kept(self):
        with self.mutex:
            return (len(self.arrivals) if self.cutoff is None else
                    bisect.bisect_left(self.arrivals, self.cutoff))


class ClockGuard:
    """Fail closed if realtime steps between a marker and a source buffer."""
    def __init__(self):
        self.fd = os.timerfd_create(time.CLOCK_REALTIME, flags=os.TFD_NONBLOCK | os.TFD_CLOEXEC)
        os.timerfd_settime_ns(self.fd, flags=os.TFD_TIMER_ABSTIME | os.TFD_TIMER_CANCEL_ON_SET,
                             initial=time.time_ns() + 7 * 24 * 3600 * 1_000_000_000)

    def check(self):
        try:
            os.read(self.fd, 8)
        except BlockingIOError:
            return
        except OSError as error:
            if error.errno != errno.ECANCELED:
                raise
        raise CaptureError('clock_changed')

    def close(self):
        os.close(self.fd)


def media_frames(fd):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_packets',
         '-show_entries', 'stream=nb_read_packets,has_b_frames', '-of', 'json',
         f'/proc/self/fd/{fd}'],
        pass_fds=(fd,), capture_output=True, text=True, timeout=30, check=True,
        start_new_session=True)
    try:
        stream, = json.loads(result.stdout)['streams']
        frames = int(stream['nb_read_packets'])
        if frames <= 0 or stream['has_b_frames'] != 0:
            raise ValueError()
        return frames
    except (ValueError, KeyError, TypeError) as error:
        raise CaptureError('finalization_failed') from error


def finalize(fd, boundary, directory):
    """Publish only the prefix whose source buffers preceded the marker.

    The encoder never skips or reorders frames. Count its muxed access units before
    trimming, so any broken correspondence fails closed. Stream-copying exactly
    N I/P frames retains dependencies only on earlier, permitted frames.
    """
    total = media_frames(fd)
    if total != len(boundary.arrivals):
        raise CaptureError('finalization_failed')
    kept = boundary.kept()
    if not kept:
        raise CaptureError('no_frames')
    if kept != total:
        with tempfile.TemporaryFile(dir=directory, prefix='.capture-trim-') as trimmed:
            subprocess.run(
                ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', f'/proc/self/fd/{fd}',
                 '-map', '0:v:0', '-c:v', 'copy', '-frames:v', str(kept), '-f', 'mp4',
                 f'/proc/self/fd/{trimmed.fileno()}'],
                pass_fds=(fd, trimmed.fileno()), capture_output=True, timeout=30, check=True,
                start_new_session=True)
            if media_frames(trimmed.fileno()) != kept:
                raise CaptureError('finalization_failed')
            # Keep the original validated inode, including during finalization.
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            trimmed.seek(0)
            while chunk := trimmed.read(1024 * 1024):
                pending = memoryview(chunk)
                while pending:
                    pending = pending[os.write(fd, pending):]
    os.fsync(fd)
    return kept


class Recorder:
    def __init__(self, options, fd, source=None, clock=time.time_ns):
        self.Gst = Gst = gst_runtime()
        self.options, self.fd, self.clock = options, fd, clock
        self.boundary = FrameBoundary()
        self.clock_guard = ClockGuard()
        self.geometry = None
        self.failure = None
        self.ready = threading.Event()
        self.pipeline = Gst.Pipeline.new('capture')
        self.capture_pipeline = None
        self.importer = None
        self.source = source or Gst.ElementFactory.make('appsrc', 'capture-source')
        if source is None:
            self.source.set_property('format', Gst.Format.TIME)
            self.source.set_property('is-live', True)
            self.source.set_property('block', True)
            self.source.set_property('max-buffers', 2)
            self.build_capture_source()
        self.crop = Gst.ElementFactory.make('videocrop', 'capture-crop')
        convert = Gst.ElementFactory.make('videoconvert')
        scale = Gst.ElementFactory.make('videoscale')
        self.canvas = Gst.ElementFactory.make('capsfilter')
        encoder = Gst.ElementFactory.make('openh264enc')
        encoder.set_property('enable-frame-skip', False)
        encoder.set_property('usage-type', 1)
        encoder.set_property('complexity', 0)
        encoder.set_property('multi-thread', 2)
        encoder.set_property('bitrate', 6000000)
        parser = Gst.ElementFactory.make('h264parse')
        mux = Gst.ElementFactory.make('mp4mux')
        sink = Gst.ElementFactory.make('fdsink')
        sink.set_property('fd', fd)
        sink.set_property('sync', False)
        elements = (self.source, self.crop, convert, scale, self.canvas, encoder, parser, mux, sink)
        for element in elements:
            self.pipeline.add(element)
        for first, second in zip(elements, elements[1:]):
            if not first.link(second):
                raise CaptureError('initialization_failed')
        self.source.get_static_pad('src').add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM | Gst.PadProbeType.BUFFER, self.source_probe)
        encoder.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, self.encoded_probe)

    def build_capture_source(self):
        Gst = self.Gst
        import gi
        gi.require_version('GstVideo', '1.0')
        gi.require_version('GstAllocators', '1.0')
        from gi.repository import GstVideo, GstAllocators
        self.GstVideo, self.GstAllocators = GstVideo, GstAllocators
        spec = importlib.util.spec_from_file_location('capture_dmabuf', Path(__file__).with_name('dmabuf.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.importer = module.Importer()
        self.capture_pipeline = Gst.Pipeline.new('capture-import')
        source = Gst.ElementFactory.make('pipewiresrc')
        source.set_property('path', str(self.options.node))
        source.set_property('do-timestamp', True)
        source.set_property('keepalive-time', 0)
        source.set_property('resend-last', False)
        caps = Gst.ElementFactory.make('capsfilter')
        # Legacy caps preserve an implicit modifier. Explicit DMA_DRM remains an
        # alternative; neither path substitutes a linear layout for an unknown one.
        caps.set_property('caps', Gst.Caps.from_string(
            'video/x-raw(memory:DMABuf),format=(string){ BGRx, BGRA };'
            'video/x-raw(memory:DMABuf),format=DMA_DRM'))
        sink = Gst.ElementFactory.make('appsink')
        sink.set_property('emit-signals', True)
        sink.set_property('sync', False)
        sink.set_property('max-buffers', 2)
        sink.set_property('drop', False)
        sink.connect('new-sample', self.import_sample)
        for element in (source, caps, sink):
            self.capture_pipeline.add(element)
        if not source.link(caps) or not caps.link(sink):
            raise CaptureError('initialization_failed')

    def import_sample(self, sink):
        Gst, GstVideo, GstAllocators = self.Gst, self.GstVideo, self.GstAllocators
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.EOS
        if self.boundary.cutoff is not None:
            return Gst.FlowReturn.OK
        try:
            caps = sample.get_caps()
            structure = caps.get_structure(0)
            width, height = structure.get_value('width'), structure.get_value('height')
            format_name = structure.get_value('format')
            if format_name == 'DMA_DRM':
                drm_format = structure.get_value('drm-format')
                fourcc_name, _, modifier_text = drm_format.partition(':')
                if len(fourcc_name) != 4:
                    raise CaptureError('source_unavailable')
                fourcc = int.from_bytes(fourcc_name.encode('ascii'), 'little')
                modifier = int(modifier_text, 0) if modifier_text else 0
                video = GstVideo.VideoInfoDmaDrm.new_from_caps(caps).vinfo
            else:
                fourcc = int.from_bytes({'BGRx': b'XR24', 'BGRA': b'AR24'}[format_name], 'little')
                modifier = 0x00ffffffffffffff
                video = GstVideo.VideoInfo.new_from_caps(caps)
            buffer = sample.get_buffer()
            meta = GstVideo.buffer_get_video_meta(buffer)
            plane_count = meta.n_planes if meta else video.finfo.n_planes
            offsets, strides = (meta.offset, meta.stride) if meta else (video.offset, video.stride)
            if buffer.n_memory() > plane_count:
                # Auxiliary modifier planes need strides absent from VideoMeta.
                # Never guess a compression plane's layout.
                raise CaptureError('source_unavailable')
            planes = []
            for index in range(plane_count):
                found, memory_index, length, skip = buffer.find_memory(offsets[index], 1)
                if not found or length != 1:
                    raise CaptureError('source_unavailable')
                memory = buffer.peek_memory(memory_index)
                if not GstAllocators.is_dmabuf_memory(memory):
                    raise CaptureError('source_unavailable')
                _, memory_offset, _ = memory.get_sizes()
                planes.append((GstAllocators.dmabuf_memory_get_fd(memory), memory_offset + skip, strides[index]))
            pixels = self.importer.read_frame(width, height, fourcc, modifier, planes)
            output = Gst.Buffer.new_allocate(None, len(pixels), None)
            output.fill(0, pixels)
            output.duration = buffer.duration
            self.source.set_property('caps', Gst.Caps.from_string(
                f'video/x-raw,format=RGBA,width={width},height={height},framerate=0/1'))
            return self.source.emit('push-buffer', output)
        except Exception as error:
            print(f'Capture buffer import failed: {error}', file=sys.stderr)
            self.failure = 'source_unavailable'
            return Gst.FlowReturn.ERROR

    def encoded_probe(self, _pad, _info):
        self.ready.set()
        return self.Gst.PadProbeReturn.OK

    def source_probe(self, _pad, info):
        Gst = self.Gst
        try:
            if info.type & Gst.PadProbeType.EVENT_DOWNSTREAM:
                event = info.get_event()
                if event.type == Gst.EventType.CAPS:
                    structure = event.parse_caps().get_structure(0)
                    sw, sh = structure.get_value('width'), structure.get_value('height')
                    geometry = calculate_geometry(self.options, sw, sh)
                    if self.geometry and self.geometry != geometry and self.options.mode == 'area':
                        raise CaptureError('target_changed')
                    if self.geometry is None:
                        self.geometry = geometry
                        self.canvas.set_property('caps', Gst.Caps.from_string(
                            f"video/x-raw,width={geometry['width']},height={geometry['height']},pixel-aspect-ratio=1/1"))
                    self.crop.set_property('left', geometry['crop_left'])
                    self.crop.set_property('top', geometry['crop_top'])
                    self.crop.set_property('right', sw - geometry['crop_left'] - geometry['width'])
                    self.crop.set_property('bottom', sh - geometry['crop_top'] - geometry['height'])
            elif info.type & Gst.PadProbeType.BUFFER:
                self.clock_guard.check()
                stamp = self.boundary.append(self.clock())
                if stamp is None:
                    return Gst.PadProbeReturn.DROP
                buffer = info.get_buffer()
                buffer.pts = buffer.dts = stamp
            return Gst.PadProbeReturn.OK
        except (CaptureError, TypeError, ValueError) as error:
            self.failure = str(error) if isinstance(error, CaptureError) else 'invalid_geometry'
            return Gst.PadProbeReturn.DROP

    def request_stop(self, stamp=None):
        self.boundary.stop(self.clock() if stamp is None else stamp)

    def run(self, emit, control_fd=None, startup_timeout=15, stop_timeout=30):
        Gst = self.Gst
        self.pipeline.set_state(Gst.State.PLAYING)
        if self.capture_pipeline is not None:
            self.capture_pipeline.set_state(Gst.State.PLAYING)
        bus = self.pipeline.get_bus()
        deadline = time.monotonic() + startup_timeout
        stop_deadline = None
        announced = False
        incoming = b''
        source_ended = None
        if control_fd is not None:
            os.set_blocking(control_fd, False)
        try:
            while True:
                if self.failure:
                    raise CaptureError(self.failure)
                if control_fd is not None:
                    try:
                        chunk = os.read(control_fd, 4096)
                    except BlockingIOError:
                        chunk = None
                    if chunk == b'':
                        self.request_stop()
                        control_fd = None
                    elif chunk:
                        incoming += chunk
                        if len(incoming) > 4096:
                            raise CaptureError('invalid_control')
                        while b'\n' in incoming:
                            line, incoming = incoming.split(b'\n', 1)
                            try:
                                value = json.loads(line)
                                cutoff = value['stop_at_ns']
                                if type(cutoff) is not int or cutoff <= 0:
                                    raise ValueError()
                            except (ValueError, TypeError, KeyError) as error:
                                raise CaptureError('invalid_control') from error
                            self.request_stop(cutoff)
                if self.ready.is_set() and not announced:
                    emit(dict(event='recording', **self.geometry))
                    announced = True
                if self.boundary.cutoff is not None and stop_deadline is None:
                    emit(dict(event='stopping'))
                    # Deliver EOS downstream after every buffer already accepted by the source.
                    if self.capture_pipeline is not None:
                        self.capture_pipeline.set_state(Gst.State.NULL)
                    self.source.emit('end-of-stream')
                    stop_deadline = time.monotonic() + stop_timeout
                if stop_deadline is not None and time.monotonic() > stop_deadline:
                    raise CaptureError('finalization_failed')
                if not announced and stop_deadline is None and time.monotonic() > deadline:
                    raise CaptureError('source_unavailable')
                if source_ended is not None:
                    if control_fd is None:
                        break
                    if time.monotonic() >= source_ended + 3:
                        raise CaptureError('finalization_failed')
                    time.sleep(.01)
                    continue
                if self.capture_pipeline is not None and stop_deadline is None:
                    capture_message = self.capture_pipeline.get_bus().pop_filtered(
                        Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    if capture_message is not None:
                        if capture_message.type == Gst.MessageType.ERROR:
                            error, debug = capture_message.parse_error()
                            print(f'{error}: {debug}', file=sys.stderr)
                        self.request_stop()
                message = bus.timed_pop_filtered(20 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message is not None:
                    if message.type == Gst.MessageType.ERROR:
                        error, debug = message.parse_error()
                        print(f'{error}: {debug}', file=sys.stderr)
                        raise CaptureError('recorder_failed')
                    # The whole process group may receive SIGTERM before the worker
                    # can forward an earlier lock marker. Keep accepting that final
                    # control message until its pipe closes, with a bounded grace.
                    source_ended = time.monotonic()
                    if self.boundary.cutoff is None:
                        self.request_stop()
            self.pipeline.set_state(Gst.State.NULL)
            self.clock_guard.check()
            frames = finalize(self.fd, self.boundary, Path(self.options.output).parent)
            emit(dict(event='saved', frames=frames, width=self.geometry['width'], height=self.geometry['height']))
        finally:
            if self.capture_pipeline is not None:
                self.capture_pipeline.set_state(Gst.State.NULL)
            self.pipeline.set_state(Gst.State.NULL)
            if self.importer is not None:
                self.importer.close()
            self.clock_guard.close()


def main():
    fd = None
    recorder = None
    pending_stop = None

    def emit(value):
        try:
            print(json.dumps(value), flush=True)
        except BrokenPipeError:
            if recorder is not None:
                recorder.request_stop()

    def stop(_number, _frame):
        nonlocal pending_stop
        stamp = time.time_ns()
        pending_stop = stamp if pending_stop is None else min(stamp, pending_stop)
        if recorder is not None:
            recorder.request_stop(pending_stop)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    os.umask(0o077)
    os.environ.pop('WAYLAND_SOCKET', None)
    try:
        options = parse_options(sys.argv[1:])
        fd = open_reserved_output(options.output)
        recorder = Recorder(options, fd)
        if pending_stop is not None:
            recorder.request_stop(pending_stop)
        recorder.run(emit, sys.stdin.fileno())
        return 0
    except (CaptureError, OSError, subprocess.SubprocessError) as error:
        emit(dict(event='error', code=str(error) if isinstance(error, CaptureError) else 'finalization_failed'))
        return 1
    finally:
        if fd is not None:
            os.close(fd)


if __name__ == '__main__':
    sys.exit(main())
