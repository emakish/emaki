#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for host display evidence."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('boot_menu', ROOT / 'tests/vm/check-boot-menu.py')
menu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(menu)


class BootMenuTests(unittest.TestCase):
    def test_recorded_modes_follow_configuration_without_loader_or_size_assumptions(self):
        spec = importlib.util.spec_from_file_location('menu_mode', ROOT / 'tests/vm/menu_mode.py')
        modes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modes)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / 'loader.cfg'
            for directive, size in [('set gfxmode="800x600,640x480"', '800x600'),
                                    ('GRUB_GFXMODE=1280x800', '1280x800'),
                                    ('set gfxmode=1920x1080', '1920x1080'),
                                    ('set gfxmode=auto', '2560x1600')]:
                with self.subTest(directive=directive):
                    source.write_text(directive + '\n')
                    item = {'menu_mode': dict(size=size, source=source.name,
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest())}
                    self.assertEqual(modes.configured_size(base, item, (2560, 1600)),
                                     tuple(map(int, size.split('x'))))

    def test_synthetic_grub_requires_each_requested_mode(self):
        spec = importlib.util.spec_from_file_location('grub_layout', ROOT / 'tests/vm/check-grub-layout.py')
        layout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout)
        for requested in menu.SIZES:
            layout.require_fixture_mode(requested, requested)
            with self.subTest(size=requested), self.assertRaisesRegex(RuntimeError, 'requested mode'):
                layout.require_fixture_mode((800, 600), requested)

    def test_display_matrix_keeps_existing_sizes_and_owner_size(self):
        self.assertEqual(set(menu.SIZES), {(1366, 768), (1920, 1080), (3840, 2160), (1280, 800), (2560, 1600), (1024, 768)})

    def test_actual_candidate_iso_is_verified_and_booted(self):
        with tempfile.TemporaryDirectory() as temporary:
            iso = Path(temporary) / 'candidate.iso'
            iso.write_bytes(b'candidate image fixture')
            digest = hashlib.sha256(iso.read_bytes()).hexdigest()
            self.assertEqual(menu.checked_iso(iso, digest), (iso, digest))
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                menu.checked_iso(iso, '0' * 64)
            command = menu.qemu_command(iso, Path('/tmp/vars.fd'), Path('/tmp/boot'), (2560, 1600))
            self.assertEqual(command[command.index('-cdrom') + 1], str(iso))
            self.assertIn('virtio-gpu-pci,xres=2560,yres=1600', command)
            self.assertFalse(any('grub-mkstandalone' in part or 'fat:' in part for part in command))

    def test_firmware_matrix_exposes_requested_display_modes(self):
        for firmware, size in menu.CASES:
            with self.subTest(firmware=firmware, size=size):
                command = menu.qemu_command(Path('/candidate.iso'), Path('/vars.fd'),
                                            Path('/evidence'), size, firmware)
                width, height = size
                device = (f'virtio-gpu-pci,xres={width},yres={height}' if firmware == 'uefi'
                          else f'VGA,xres={width},yres={height},vgamem_mb=64')
                self.assertIn(device, command)

    def test_synthetic_fixture_exposes_requested_firmware_mode(self):
        spec = importlib.util.spec_from_file_location('grub_layout', ROOT / 'tests/vm/check-grub-layout.py')
        layout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout)
        with tempfile.TemporaryDirectory() as temporary:
            argv = ['check-grub-layout.py', '--grub-root', '/fixture', '--out', temporary]
            with patch.object(layout.sys, 'argv', argv), patch.object(layout.shutil, 'copy'), \
                    patch.object(layout.subprocess, 'run') as build, \
                    patch.object(layout.subprocess, 'Popen', side_effect=RuntimeError('stop before VM')) as start:
                with self.assertRaisesRegex(RuntimeError, 'stop before VM'):
                    layout.main()
            self.assertIn('virtio-gpu-pci,xres=1366,yres=768', start.call_args.args[0])
            command = start.call_args.args[0]
            monitor = Path(command[command.index('-monitor') + 1].split(':', 1)[1].split(',')[0])
            self.assertEqual(monitor.parent.parent, Path('/tmp'))
            self.assertFalse(monitor.parent.exists(), 'failed QEMU startup leaked its socket runtime')
            self.assertFalse((Path(temporary) / '.socket-runtime').is_symlink())
            self.assertIn('boot/grub/menu.pf2=' + temporary + '/fat/boot/grub/menu.pf2', build.call_args.args[0])
            self.assertEqual(set(layout.SIZES), set(menu.SIZES))

    def test_synthetic_fixture_waits_after_kill_before_runtime_cleanup(self):
        spec = importlib.util.spec_from_file_location('grub_layout', ROOT / 'tests/vm/check-grub-layout.py')
        layout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout)
        with tempfile.TemporaryDirectory() as temporary:
            events = []
            runtime = None

            class Process:
                def poll(self): return None
                def terminate(self): events.append('terminate')
                def kill(self): events.append('kill')
                def wait(process, timeout=None):
                    self.assertTrue(runtime.is_dir())
                    events.append('wait')
                    if timeout is not None:
                        raise layout.subprocess.TimeoutExpired('qemu', timeout)

            def start(command, **kwargs):
                nonlocal runtime
                monitor = command[command.index('-monitor') + 1].split(':', 1)[1].split(',')[0]
                runtime = Path(monitor).parent
                return Process()

            argv = ['check-grub-layout.py', '--grub-root', '/fixture', '--out', temporary]
            with patch.object(layout.sys, 'argv', argv), patch.object(layout.shutil, 'copy'), \
                    patch.object(layout.subprocess, 'run'), \
                    patch.object(layout.subprocess, 'Popen', side_effect=start), \
                    patch.object(layout.time, 'sleep', side_effect=RuntimeError('monitor unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'monitor unavailable'):
                    layout.main()
            self.assertEqual(events, ['terminate', 'wait', 'kill', 'wait'])
            self.assertFalse(runtime.exists())
            self.assertFalse((Path(temporary) / '.socket-runtime').is_symlink())

    def test_good_capture_matrix_remains_not_tested(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            config = output / 'candidate-grub.cfg'
            config.write_text('set gfxmode=1024x768\n')
            def start(command, **kwargs):
                monitor = command[command.index('-monitor') + 1].split(':', 1)[1].split(',')[0]
                (Path(kwargs['cwd']) / monitor).touch()
                class Process:
                    def poll(self): return None
                    def terminate(self): pass
                    def wait(self, **kwargs): pass
                return Process()
            def capture(args):
                size = tuple(map(int, Path(args.output).stem.split('-')[1].split('x')))
                if Path(args.output).name.startswith('bios-'):
                    size = (640, 480)
                Image.new('RGB', size, 'white').save(args.output)
                return True
            with patch.object(menu, 'detect_bootloader', return_value='grub'), \
                    patch.object(menu.shutil, 'copy'), patch.object(menu.subprocess, 'Popen', side_effect=start), \
                    patch.object(menu.time, 'sleep'), patch.object(menu.shot, 'screenshot', side_effect=capture), \
                    contextlib.redirect_stdout(io.StringIO()) as log:
                self.assertEqual(menu.capture_matrix(Path('/tmp/candidate.iso'), 'fixture', output, config, '1024x768'), 3)
            evidence = json.loads((output / 'evidence.json').read_text())
            self.assertEqual(len(evidence['frames']), 21)
            for frame in evidence['frames']:
                self.assertEqual(frame['menu_mode']['size'], '1024x768')
                self.assertEqual(frame['observed_mode'], f"{frame['width']}x{frame['height']}")
                if frame['firmware'] == 'bios':
                    self.assertEqual(frame['observed_mode'], '640x480')
                    self.assertIn('Syslinux', frame['mode_note'])
                source = output / frame['menu_mode']['source']
                self.assertEqual(source.read_bytes(), config.read_bytes())
                self.assertEqual(frame['menu_mode']['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(frame['sha256'], hashlib.sha256((output / frame['file']).read_bytes()).hexdigest())
            self.assertEqual(evidence['judgment'], 'NOT TESTED')
            self.assertIn('NOT TESTED', log.getvalue())

    def test_systemd_boot_capture_uses_only_legacy_uefi_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            commands = []
            def start(command, **kwargs):
                commands.append(command)
                (Path(kwargs['cwd']) / 'mon.sock').touch()
                class Process:
                    def poll(self): return None
                    def terminate(self): pass
                    def wait(self, **kwargs): pass
                return Process()
            def capture(args):
                Image.new('RGB', (1280, 800), 'white').save(args.output)
                return True
            with patch.object(menu, 'detect_bootloader', return_value='systemd-boot'), \
                    patch.object(menu.shutil, 'copy'), patch.object(menu.subprocess, 'Popen', side_effect=start), \
                    patch.object(menu.time, 'sleep'), patch.object(menu.shot, 'screenshot', side_effect=capture), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(menu.capture_matrix(Path('/candidate.iso'), 'fixture', output), 3)
            evidence = json.loads((output / 'evidence.json').read_text())
            self.assertEqual(evidence['bootloader'], 'systemd-boot')
            self.assertEqual(len(evidence['frames']), 15)
            self.assertEqual(len(commands), 5)
            self.assertEqual({(f['firmware'], tuple(f['advertised_size'])) for f in evidence['frames']},
                             {('uefi', size) for size in menu.SIZES if size != (1024, 768)})
            self.assertTrue(all(any('pflash' in arg for arg in command) for command in commands))

    def test_broken_host_capture_fails_the_matrix(self):
        with tempfile.TemporaryDirectory() as temporary:
            def start(command, **kwargs):
                monitor = command[command.index('-monitor') + 1].split(':', 1)[1].split(',')[0]
                (Path(kwargs['cwd']) / monitor).touch()
                class Process:
                    def poll(self): return None
                    def terminate(self): pass
                    def wait(self, **kwargs): pass
                return Process()
            with patch.object(menu, 'detect_bootloader', return_value='grub'), \
                    patch.object(menu.shutil, 'copy'), patch.object(menu.subprocess, 'Popen', side_effect=start), \
                    patch.object(menu.time, 'sleep'), patch.object(menu.shot, 'screenshot', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                    menu.capture_matrix(Path('/tmp/candidate.iso'), 'fixture', Path(temporary))

    def test_wrong_grub_frame_fails_with_observed_mode_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            def start(command, **kwargs):
                (Path(kwargs['cwd']) / 'mon.sock').touch()
                class Process:
                    def poll(self): return None
                    def terminate(self): pass
                    def wait(self, **kwargs): pass
                return Process()
            def capture(args):
                Image.new('RGB', (800, 600), 'white').save(args.output)
                return True
            with patch.object(menu, 'detect_bootloader', return_value='grub'), \
                    patch.object(menu, 'CASES', (('uefi', (2560, 1600)),)), \
                    patch.object(menu.shutil, 'copy'), patch.object(menu.subprocess, 'Popen', side_effect=start), \
                    patch.object(menu.time, 'sleep'), patch.object(menu.shot, 'screenshot', side_effect=capture):
                with self.assertRaisesRegex(RuntimeError, 'requested mode'):
                    menu.capture_matrix(Path('/candidate.iso'), 'fixture', Path(temporary))
            evidence = json.loads((Path(temporary) / 'evidence.json').read_text())
            self.assertEqual(evidence['frames'][0]['observed_mode'], '800x600')
            self.assertEqual(evidence['frames'][0]['advertised_size'], [2560, 1600])

    def test_bios_boots_candidate_without_pflash(self):
        command = menu.qemu_command(Path('/candidate.iso'), None, Path('/evidence'), (1024, 768), 'bios')
        self.assertIn('/candidate.iso', command)
        self.assertFalse(any('pflash' in arg for arg in command))
        self.assertIn(('bios', (1024, 768)), menu.CASES)

    def test_physical_cap_measurement_rejects_tiny_text(self):
        from PIL import ImageDraw
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'frame.png'
            for height, passes in [(12, False), (24, True)]:
                picture = Image.new('RGB', (2560, 1600), 'white')
                draw = ImageDraw.Draw(picture)
                samples = []
                for x in (10, 60, 110):
                    draw.rectangle((x, 10, x + 3, 10 + height - 1), fill='black')
                    for y in (10, 10 + height // 2, 10 + height - 3):
                        draw.rectangle((x, y, x + 14, y + 2), fill='black')
                    samples.append(dict(letter='E', box=[x - 2, 8, x + 17, height + 12],
                                        foreground=[0, 0, 0]))
                picture.save(path)
                frame = dict(advertised_size=[2560, 1600], capitals=samples)
                if passes:
                    result = menu.measure_caps(path, frame)
                    self.assertAlmostEqual(result['capitals'][0]['mm'], 2.6857, places=3)
                    frame['capitals'][1] = frame['capitals'][0]
                    with self.assertRaisesRegex(ValueError, 'separate'):
                        menu.measure_caps(path, frame)
                else:
                    with self.assertRaisesRegex(ValueError, 'below 2.4'):
                        menu.measure_caps(path, frame)

    def test_missing_iso_fails_before_any_vm(self):
        with patch.object(menu.subprocess, 'Popen', side_effect=AssertionError('unexpected VM')):
            with self.assertRaises(FileNotFoundError):
                menu.checked_iso('/missing/candidate.iso', '0' * 64)


if __name__ == '__main__':
    unittest.main()
