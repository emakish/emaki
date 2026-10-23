#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private local screen recognition, including real decoder fixtures when installed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('capture', ROOT / 'shell/helpers/capture.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
OUTPUT = dict(name='TEST-1', width=800, height=400, scale=1)
SELECTED = dict(target='area', output=OUTPUT, area=dict(x=20, y=20, width=500, height=180))


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='recognition-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700)
        env = patch.dict(os.environ, HOME=str(self.base), XDG_RUNTIME_DIR=str(self.runtime))
        env.start()
        self.addCleanup(env.stop)
        self.seen = []

    def screenshot(self, argv, **kwargs):
        if argv[0] == 'grim':
            path = Path(argv[-1])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.seen.append(path)
            Image.new('RGB', (800, 400), 'white').save(path)
        else:
            self.assertEqual(argv, ['wl-copy', '--type', 'text/plain;charset=utf-8'])
        return b''

    def assert_clean(self):
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertFalse((self.base / 'Pictures').exists())
        self.assertTrue(all(not path.exists() for path in self.seen))

    def test_locale_preference(self):
        for language, expected in [('en_US', 'eng'), ('es_MX', 'spa+eng'), ('pt_BR', 'por+eng'),
                                   ('de_DE', 'deu+eng'), ('fr_FR', 'fra+eng'), ('ru_RU', 'rus+eng'),
                                   ('C', 'eng'), ('unknown', 'eng')]:
            self.assertEqual(C.recognition_languages({'LANG': language + '.UTF-8'}), expected)
        self.assertEqual(C.recognition_languages({'LANG': 'es', 'LC_MESSAGES': 'ru', 'LC_ALL': 'fr'}), 'fra+eng')
        self.assertEqual(C.recognition_languages({'LANG': 'es', 'LC_MESSAGES': 'pt_BR'}), 'por+eng')

    def test_success_private_cropped_and_removed(self):
        def recognize(path, kind):
            with Image.open(path) as image:
                self.assertEqual(image.size, (500, 180))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            return 'Hello world'
        for kind in ('text', 'qr'):
            with patch.object(C, 'guard'), patch.object(C.shutil, 'which', return_value='decoder'), \
                    patch.object(C, 'command', side_effect=self.screenshot) as command, \
                    patch.object(C, 'recognize_image', side_effect=recognize):
                self.assertEqual(C.recognition(SELECTED, kind), dict(kind=kind, text='Hello world', clipboard=True))
                self.assertEqual(command.call_args.kwargs['data'], b'Hello world')
            self.assert_clean()

    def test_decoder_uses_only_installed_selected_languages(self):
        for installed, expected in [('eng\nosd\n', 'eng'), ('rus\neng\n', 'rus+eng'),
                                    ('rus\n', 'rus')]:
            with self.subTest(installed=installed):
                listing = ('List of available languages (2):\n' + installed).encode()
                with patch.dict(os.environ, LC_ALL='ru_RU.UTF-8'), \
                        patch.object(C.subprocess, 'run', side_effect=[
                            subprocess.CompletedProcess([], 0, listing),
                            subprocess.CompletedProcess([], 0, b'recognized')]) as run:
                    self.assertEqual(C.recognize_image('image.png', 'text'), 'recognized')
                self.assertEqual(run.call_args_list[0].args[0], ['tesseract', '--list-langs'])
                self.assertEqual(run.call_args_list[1].args[0],
                                 ['tesseract', 'image.png', 'stdout', '-l', expected])

    def test_no_installed_selected_language_refuses_decode(self):
        with patch.dict(os.environ, LC_ALL='ru_RU.UTF-8'), \
                patch.object(C.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, b'List of available languages (1):\nosd\n')) as run:
            with self.assertRaisesRegex(C.CaptureError, 'capture_failed'):
                C.recognize_image('image.png', 'text')
            self.assertEqual(run.call_count, 1)

    def test_startup_removes_only_stale_private_recognition_directories(self):
        stale = self.runtime / 'emaki-recognize-stale'
        fresh = self.runtime / 'emaki-recognize-fresh'
        other = self.runtime / 'emaki-capture-old'
        shared = self.runtime / 'emaki-recognize-shared'
        target = self.base / 'keep'
        for directory in (stale, fresh, other, shared, target):
            directory.mkdir(mode=0o700)
            (directory / 'image.png').write_bytes(b'private pixels')
        shared.chmod(0o755)
        link = self.runtime / 'emaki-recognize-link'
        link.symlink_to(target, target_is_directory=True)
        (stale / 'link').symlink_to(target, target_is_directory=True)
        plain = self.runtime / 'emaki-recognize-file'
        plain.write_bytes(b'keep')
        for path in (stale, other, shared, link, plain):
            os.utime(path, (time.time() - 120,) * 2, follow_symlinks=False)
        output = io.StringIO()
        with patch.object(C, 'inventory', return_value={}), \
                patch.object(C.sys, 'argv', ['capture.py', '--inventory']), patch('sys.stdout', output):
            self.assertEqual(C.main(), 0)
        self.assertFalse(stale.exists())
        for path in (fresh, other, shared, link, plain, target / 'image.png'):
            self.assertTrue(path.exists(), str(path))

    def test_startup_preserves_foreign_recognition_directory(self):
        foreign = self.runtime / 'emaki-recognize-foreign'
        foreign.mkdir(mode=0o700)
        os.utime(foreign, (time.time() - 120,) * 2)
        original_stat = C.os.stat
        def foreign_stat(path, *args, **kwargs):
            info = original_stat(path, *args, **kwargs)
            if os.fspath(path) == foreign.name:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info
        with patch.object(C.os, 'stat', side_effect=foreign_stat), \
                patch.object(C, 'inventory', return_value={}), \
                patch.object(C.sys, 'argv', ['capture.py', '--inventory']), patch('sys.stdout', io.StringIO()):
            self.assertEqual(C.main(), 0)
        self.assertTrue(foreign.exists())

    def test_empty_does_not_change_clipboard(self):
        for kind in ('text', 'qr'):
            with patch.object(C, 'guard'), patch.object(C.shutil, 'which', return_value='decoder'), \
                    patch.object(C, 'command', side_effect=self.screenshot) as command, \
                    patch.object(C, 'recognize_image', return_value=''):
                self.assertEqual(C.recognition(SELECTED, kind), dict(kind=kind, text='', clipboard=False))
                self.assertEqual(command.call_count, 1)
            self.assert_clean()

    def test_failures_and_termination_remove_image(self):
        def terminate(*_):
            os.kill(os.getpid(), signal.SIGTERM)
        for effect in (C.CaptureError('capture_timeout'), C.CaptureError('capture_failed'), terminate):
            with patch.object(C, 'guard'), patch.object(C.shutil, 'which', return_value='decoder'), \
                    patch.object(C, 'command', side_effect=self.screenshot), \
                    patch.object(C, 'recognize_image', side_effect=effect):
                previous = signal.getsignal(signal.SIGTERM)
                with self.assertRaises(C.CaptureError):
                    C.recognition(SELECTED, 'text')
                self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
            self.assert_clean()

    def test_termination_reaps_running_decoder_and_cleans_image(self):
        executable = self.base / 'tesseract'
        marker = self.base / 'decoder.pid'
        executable.write_text('#!' + sys.executable + '\nimport os, sys, time\n'
                              + 'if "--list-langs" in sys.argv:\n print("eng"); sys.exit(0)\n'
                              + 'open(' + repr(str(marker)) + ', "w").write(str(os.getpid()))\n'
                              + 'time.sleep(60)\n')
        executable.chmod(0o700)
        driver = ("import importlib.util, sys\nfrom PIL import Image\n"
                  + "spec = importlib.util.spec_from_file_location('capture', "
                  + repr(str(ROOT / 'shell/helpers/capture.py')) + ")\n"
                  + "c = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(c)\n"
                  + "c.guard = lambda: None\n"
                  + "c.command = lambda argv, **kwargs: Image.new('RGB', (800, 400), 'white').save(argv[-1])\n"
                  + "try:\n c.recognition(" + repr(SELECTED) + ", 'text')\n"
                  + "except c.CaptureError as error:\n print(str(error), flush=True)\n sys.exit(1)\n")
        environment = dict(os.environ, PATH=str(self.base) + os.pathsep + os.environ.get('PATH', ''))
        child = subprocess.Popen([sys.executable, '-c', driver], env=environment,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline and child.poll() is None:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'decoder did not start')
            decoder_pid = int(marker.read_text())
            child.terminate()
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 1, stderr.decode())
            self.assertEqual(stdout.strip(), b'capture_cancelled')
            with self.assertRaises(ProcessLookupError):
                os.kill(decoder_pid, 0)
            self.assert_clean()
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_clipboard_failure_still_cleans_image(self):
        def command(argv, **kwargs):
            if argv[0] == 'wl-copy':
                raise C.CaptureError('dependency_missing')
            return self.screenshot(argv, **kwargs)
        with patch.object(C, 'guard'), patch.object(C.shutil, 'which', return_value='decoder'), \
                patch.object(C, 'command', side_effect=command), \
                patch.object(C, 'recognize_image', return_value='sample'):
            self.assertEqual(C.recognition(SELECTED, 'text'), dict(kind='text', text='sample', clipboard=False))
        self.assert_clean()

    def test_non_area_requests_refused(self):
        for target in ('window', 'screen'):
            with self.assertRaisesRegex(C.CaptureError, 'selection_invalid'):
                C.recognition(dict(target=target), 'text')
        self.assert_clean()

    def test_missing_dependencies(self):
        for kind, reason in [('text', 'tesseract_missing'), ('qr', 'zbar_missing')]:
            with patch.object(C.shutil, 'which', return_value=None):
                with self.assertRaisesRegex(C.CaptureError, reason):
                    C.recognition(SELECTED, kind)
            with patch.object(C.subprocess, 'run', side_effect=FileNotFoundError):
                with self.assertRaisesRegex(C.CaptureError, reason):
                    C.recognize_image(self.base / 'image.png', kind)
        self.assert_clean()

    def test_decoder_status_and_timeout(self):
        with patch.object(C.subprocess, 'run', return_value=subprocess.CompletedProcess([], 4, b'')) as run:
            self.assertEqual(C.recognize_image('image.png', 'qr'), '')
            self.assertIn('--nodbus', run.call_args.args[0])
            with self.assertRaisesRegex(C.CaptureError, 'capture_failed'):
                C.recognize_image('image.png', 'text')
        with patch.dict(os.environ, LC_ALL='C'), patch.object(C.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, b'eng\n'),
                subprocess.CompletedProcess([], 0, b' \n\f')]):
            self.assertEqual(C.recognize_image('image.png', 'text'), '')
        with patch.object(C.subprocess, 'run', side_effect=subprocess.TimeoutExpired('decoder', 30)):
            with self.assertRaisesRegex(C.CaptureError, 'capture_timeout'):
                C.recognize_image('image.png', 'text')

    def test_cli_result_events(self):
        for kind in ('text', 'qr'):
            for text in ('sample', ''):
                output = io.StringIO()
                with patch.object(C, 'inventory', return_value={}), patch.object(C, 'selection', return_value=SELECTED), \
                        patch.object(C, 'recognition', return_value=dict(kind=kind, text=text, clipboard=bool(text))), \
                        patch.object(C.sys, 'argv', ['capture.py', '--' + kind, '{}']), \
                        patch('sys.stdout', output):
                    self.assertEqual(C.main(), 0)
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                expected = dict(event='recognized', kind=kind, text=text, clipboard=True) if text else dict(event='empty', kind=kind)
                self.assertEqual(events, [dict(event='starting'), expected])

    @unittest.skipUnless(shutil.which('tesseract') and shutil.which('zbarimg'),
                         'Real decoders unavailable; mocked recognition tests cover both modes')
    def test_real_generated_text_and_barcode(self):
        text_image = Image.new('RGB', (800, 160), 'white')
        ImageDraw.Draw(text_image).text((30, 40), 'Hello Emaki 123', fill='black', font=ImageFont.load_default(size=56))
        text_path = self.base / 'text.png'
        text_image.save(text_path)
        with patch.dict(os.environ, LC_ALL='C'):
            self.assertIn('Hello Emaki 123', C.recognize_image(text_path, 'text'))
        # Generate the EAN-13 4006381333931 using its standard left/right digit patterns.
        odd = ['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011']
        even = ['0100111','0110011','0011011','0100001','0011101','0111001','0000101','0010001','0001001','0010111']
        digits = '4006381333931'
        bits = '101' + ''.join((odd if parity == 'L' else even)[int(digit)]
                               for parity, digit in zip('LGLLGG', digits[1:7])) + '01010'
        bits += ''.join(''.join('1' if bit == '0' else '0' for bit in odd[int(digit)]) for digit in digits[7:]) + '101'
        barcode = Image.new('RGB', ((len(bits) + 40) * 4, 240), 'white')
        draw = ImageDraw.Draw(barcode)
        for index, bit in enumerate(bits):
            if bit == '1':
                draw.rectangle(((index + 20) * 4, 20, (index + 21) * 4 - 1, 219), fill='black')
        barcode_path = self.base / 'barcode.png'
        barcode.save(barcode_path)
        self.assertEqual(C.recognize_image(barcode_path, 'qr'), digits)
        if shutil.which('qrencode'):
            qr_path = self.base / 'qr.png'
            subprocess.run(['qrencode', '-o', str(qr_path), 'https://example.org/capture'], check=True)
            self.assertEqual(C.recognize_image(qr_path, 'qr'), 'https://example.org/capture')
        blank = self.base / 'blank.png'
        Image.new('RGB', (400, 200), 'white').save(blank)
        self.assertEqual(C.recognize_image(blank, 'qr'), '')
        self.assertEqual(C.recognize_image(blank, 'text'), '')


if __name__ == '__main__':
    unittest.main()
