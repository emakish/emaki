#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for host display evidence."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import struct
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.cache/evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)

spec = importlib.util.spec_from_file_location('iso_shot', ROOT / 'tests/vm/iso-shot.py')
shot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shot)

class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s-', dir=EVIDENCE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'frame.png'
        self.args = argparse.Namespace(dir=str(self.root), output=str(self.output), monitor=False)

    def capture(self, contents):
        def monitor(*args):
            if isinstance(contents, bytes):
                self.output.write_bytes(contents)
            elif contents is not None:
                Image.new('RGB', (1280, 800), contents).save(self.output)
            return '(qemu) '
        with patch.object(shot.monitor, 'command', side_effect=monitor), \
                patch.object(shot.subprocess, 'run', side_effect=AssertionError('guest execution')), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return shot.screenshot(self.args)

    def test_valid_host_frame_is_saved_but_not_judged(self):
        self.assertTrue(self.capture('white'))
        info = json.loads(self.output.with_suffix('.png.json').read_text())
        self.assertEqual(info['judgment'], 'NOT TESTED')
        self.assertEqual(info['source'], 'QEMU screendump')
        self.assertEqual((info['width'], info['height']), (1280, 800))
        self.assertEqual(info['nonblack_fraction'], 1)

    def test_signature_only_corrupt_black_and_missing_frames_fail(self):
        for broken in (b'\x89PNG\r\n\x1a\n', 'black', None):
            with self.subTest(broken=broken):
                self.assertFalse(self.capture(broken))
                self.assertFalse(self.output.exists())

    def test_stale_previous_frame_cannot_hide_missing_capture(self):
        self.assertTrue(self.capture('white'))
        self.assertFalse(self.capture(None))
        self.assertFalse(self.output.with_suffix('.png.json').exists())

    def test_nonblack_share_counts_dim_rgb_pixels_without_rounding(self):
        frame = Image.new('RGB', (2, 2), 'black')
        frame.putpixel((0, 0), (0, 0, 1))
        frame.save(self.output)
        self.assertEqual(shot.frame_metrics(self.output)['nonblack_fraction'], 0.25)

    def test_boot_checker_requires_decodable_nonblack_frames(self):
        spec = importlib.util.spec_from_file_location('boot_check', ROOT / 'tests/vm/iso-boot-check.py')
        boot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(boot)
        for contents in (b'\x89PNG\r\n\x1a\n', 'black', None, 'white'):
            with self.subTest(contents=contents):
                self.output.unlink(missing_ok=True)
                if isinstance(contents, bytes):
                    self.output.write_bytes(contents)
                elif contents is not None:
                    Image.new('RGB', (1280, 800), contents).save(self.output)
                log = io.StringIO()
                self.assertEqual(boot.valid_boot_frame(self.output, log), contents == 'white')
                if contents != 'white':
                    self.assertIn('Invalid GRUB frame', log.getvalue())

    def test_vnc_uses_host_scanout_without_guest_commands(self):
        (self.root / 'vnc.sock').touch()
        class Loader:
            def create_module(self, spec): return None
            def exec_module(self, module):
                module.grab = lambda socket, out: Image.new('RGB', (2560, 1600), 'white').save(out)
        spec = importlib.util.spec_from_loader('vncshot_fixture', Loader())
        with patch.object(shot.importlib.util, 'spec_from_file_location', return_value=spec), \
                patch.object(shot.monitor, 'command', side_effect=AssertionError('unexpected fallback')), \
                patch.object(shot.subprocess, 'run', side_effect=AssertionError('guest execution')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(shot.screenshot(self.args))
        self.assertEqual(json.loads(self.output.with_suffix('.png.json').read_text())['source'], 'VNC')


class LegacyCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s-', dir=EVIDENCE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helpers = self.root / 'helpers'
        self.helpers.mkdir()
        (self.helpers / 'eyes').mkdir()
        for name in ('shot.sh', 'iso-shot.py', 'iso-monitor.py', 'eyes/vncshot.py'):
            shutil.copy(ROOT / 'tests/vm' / name, self.helpers / name)
        # A regression must never contact a real guest.
        ssh = self.helpers / 'ssh.sh'
        ssh.write_text('#!/bin/sh\ntouch "$EMAKI_VM_DIR/guest-called"\nexit 1\n')
        ssh.chmod(0o755)
        self.env = dict(os.environ, EMAKI_VM_DIR=str(self.root))
        self.output = self.root / 'frame.png'

    def capture(self, colour):
        (self.root / 'vnc.sock').touch()
        w, h = 64, 40
        pixels = Image.new('RGB', (w, h), colour).tobytes('raw', 'BGRX')
        wire = io.BytesIO(b'RFB 003.008\n' + b'\x01\x01' + struct.pack('>I', 0)
                         + struct.pack('>HH', w, h) + bytes(16)
                         + struct.pack('>I', 4) + b'fake'
                         + b'\x00\x00' + struct.pack('>H', 1)
                         + struct.pack('>HHHHi', 0, 0, w, h, 0) + pixels)
        # Fragment the wire stream to exercise the real client's exact reads.
        with patch('socket.socket') as factory, \
                patch.object(shot.monitor, 'command', side_effect=AssertionError('unexpected fallback')), \
                patch.object(shot.subprocess, 'run', side_effect=AssertionError('guest execution')), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            connection = factory.return_value
            connection.recv.side_effect = lambda count: wire.read(min(count, 7))
            result = shot.screenshot(argparse.Namespace(
                dir=str(self.root), output=str(self.output), monitor=False))
            connection.connect.assert_called_once_with(str(self.root / 'vnc.sock'))
            connection.close.assert_called_once()
            self.assertEqual(connection.sendall.call_args_list[-1].args[0],
                             struct.pack('>BBHHHH', 3, 0, 0, 0, w, h))
        return result, out.getvalue(), err.getvalue()

    def test_legacy_helper_forwards_output_and_failure_through_target(self):
        binary = self.root / 'python3'
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$EMAKI_VM_DIR/args"\nexit 19\n')
        binary.chmod(0o755)
        env = dict(self.env, PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        result = subprocess.run(['bash', str(self.helpers / 'shot.sh'), str(self.output)],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertFalse((self.root / 'guest-called').exists())
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual((self.root / 'args').read_text().splitlines(),
                         [str(self.helpers / 'glass-target.py'), 'shot', str(self.output)])

    def test_legacy_launcher_exposes_local_vnc(self):
        binary = self.root / 'qemu-system-x86_64'
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$EMAKI_VM_DIR/args"\n')
        binary.chmod(0o755)
        env = dict(self.env, PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        self.addCleanup(subprocess.run, ['python3', str(ROOT / 'tests/vm/socket_runtime.py'),
                                         'cleanup', str(self.root)], check=True, timeout=10)
        subprocess.run(['bash', str(ROOT / 'tests/vm/run.sh')], env=env, check=True, timeout=10)
        args = (self.root / 'args').read_text().splitlines()
        self.assertIn('-vnc', args)
        self.assertEqual(args[args.index('-vnc') + 1], 'unix:' + str((self.root / 'vnc.sock').resolve()))
        self.assertLess(len(os.fsencode((self.root / 'vnc.sock').resolve())), 108)

    def test_legacy_capture_reads_vnc_and_reports_actual_pixels(self):
        for colour in ((255, 0, 0), (0, 0, 255)):
            result, out, err = self.capture(colour)
            self.assertTrue(result, err)
            self.assertIn('SHOT: VNC:', out)
            self.assertIn('FRAME: 64x40 nonblack=1.000000', out)
            with Image.open(self.output) as frame:
                self.assertEqual(frame.getpixel((0, 0)), colour)
            info = json.loads(self.output.with_suffix('.png.json').read_text())
            self.assertEqual(info['source'], 'VNC')
            self.assertEqual(info['judgment'], 'NOT TESTED')

    def test_black_vnc_frame_cannot_reuse_previous_evidence(self):
        self.assertTrue(self.capture('white')[0])
        result, _, err = self.capture('black')
        self.assertFalse(result)
        self.assertIn('host display is completely black', err)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.png.json').exists())


if __name__ == '__main__':
    unittest.main()
