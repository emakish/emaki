#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline resume-continuity failures and explicit capabilities-image fixtures."""
import ast
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('encrypt_check', ROOT / 'tests/vm/iso-encrypt-check.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ResumeContinuity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name) / 'resume-proof.json'
        self.now = 0
        self.before = {'boot_id': 'saved-boot', 'volatile_marker': 'unique-marker',
                       'services': {'niri-emaki.service': {'pid': 123, 'started': '70'},
                                    'emaki-shell.service': {'pid': 124, 'started': '75'}}}

    def sleep(self, seconds):
        self.now += seconds

    def observe(self, probe):
        CHECK.observe_resume(self.before, probe, self.evidence,
                             clock=lambda: self.now, sleep=self.sleep)

    def test_alongside_uses_the_same_resume_observer_and_identity_probe(self):
        spec = importlib.util.spec_from_file_location('alongside', ROOT / 'tests/vm/iso-alongside-check.py')
        alongside = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(alongside)
        self.assertEqual(alongside.resume.RESUME_PROBE, CHECK.RESUME_PROBE)
        def probe():
            if self.now >= 10:
                raise RuntimeError('desktop disconnected')
            return copy.deepcopy(self.before)
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            alongside.resume.observe_resume(self.before, probe, self.evidence,
                                            clock=lambda: self.now, sleep=self.sleep)
        self.assertEqual(json.loads(self.evidence.read_text())['status'], 'FAIL')

    def test_resume_frame_rejects_missing_corrupt_and_black_output(self):
        spec = importlib.util.spec_from_file_location('shot', ROOT / 'tests/vm/iso-shot.py')
        shot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shot)
        vm = self.evidence.parent
        output = vm / 'resume.png'
        for contents in (None, b'\x89PNG\r\n\x1a\n', 'black', 'white'):
            with self.subTest(contents=contents):
                def capture(*args):
                    if isinstance(contents, bytes):
                        output.write_bytes(contents)
                    elif contents is not None:
                        Image.new('RGB', (1280, 800), contents).save(output)
                    return 'No surface' if contents is None else '(qemu)'
                class Loader:
                    def create_module(self, spec): return shot
                    def exec_module(self, module): pass
                fixture_spec = importlib.util.spec_from_loader('shot_fixture', Loader())
                with patch.object(CHECK.importlib.util, 'spec_from_file_location', return_value=fixture_spec), \
                        patch.object(shot.monitor, 'command', side_effect=capture), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    if contents == 'white':
                        self.assertEqual(CHECK.capture_frame(vm, 'resume'), output)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Host display capture failed'):
                            CHECK.capture_frame(vm, 'resume')
                        self.assertFalse(output.exists())

    def test_stable_resume_passes_only_after_full_observation(self):
        def probe():
            current = json.loads(self.evidence.read_text())
            self.assertEqual(current['status'], 'NOT TESTED')
            return copy.deepcopy(self.before)
        self.observe(probe)
        record = json.loads(self.evidence.read_text())
        self.assertEqual(record['status'], 'PASS')
        self.assertGreaterEqual(self.now, 60)
        self.assertGreaterEqual(len(record['samples']), 13)
        self.assertGreaterEqual(record['samples'][-1]['elapsed_seconds'], 60)

    def test_cold_boot_and_delayed_boot_marker_or_process_loss_fail(self):
        for at in (0, 10, 60):
            for field in ('boot_id', 'volatile_marker', 'services'):
                with self.subTest(at=at, field=field):
                    self.now = 0
                    broken = copy.deepcopy(self.before)
                    if field == 'services':
                        # A reused PID with a different start time is a restarted session.
                        broken[field]['niri-emaki.service']['started'] = '999'
                    else:
                        broken[field] = 'changed'
                    def probe():
                        return copy.deepcopy(broken if self.now >= at else self.before)
                    with self.assertRaisesRegex(RuntimeError, 'changed after resume'):
                        self.observe(probe)
                    record = json.loads(self.evidence.read_text())
                    self.assertEqual(record['status'], 'FAIL')
                    self.assertEqual(record['samples'][-1]['identity'], broken)
                    self.assertEqual(record['samples'][-1]['elapsed_seconds'], at)

    def test_ssh_loss_is_not_hidden_by_a_later_recovery(self):
        def probe():
            if self.now == 10:
                raise subprocess.TimeoutExpired(['ssh'], 15)
            return copy.deepcopy(self.before)
        with self.assertRaises(subprocess.TimeoutExpired):
            self.observe(probe)
        record = json.loads(self.evidence.read_text())
        self.assertEqual(record['status'], 'FAIL')
        self.assertEqual(self.now, 10)
        self.assertEqual(len(record['samples']), 3)
        self.assertIn('timed out', record['error'])
        self.assertEqual(record['samples'][0]['identity'], self.before)

    def test_inactive_resume_display_is_not_tested_after_continuity(self):
        output = self.evidence.parent / 'resume.png'
        words = [{'text': 'Display output is not active.'}]
        events = []
        def capture(label):
            self.assertEqual(json.loads(self.evidence.read_text())['status'], 'PASS')
            events.append(label)
            return output
        original = CHECK.observe_resume
        def observed(before, probe, evidence):
            events.append('continuity')
            original(before, probe, evidence, clock=lambda: self.now, sleep=self.sleep)
        with patch.object(CHECK, 'observe_resume', side_effect=observed), \
                patch.object(CHECK.frames, 'read_words', return_value=words), \
                patch.object(CHECK, 'assess_frame') as assess:
            visual = CHECK.complete_resume(self.before, lambda: self.before, capture, self.evidence)
        self.assertEqual(events, ['continuity', 'resume-after-observation'])
        self.assertEqual(visual['status'], 'NOT TESTED')
        self.assertEqual(json.loads(output.with_suffix('.assessment.json').read_text()), visual)
        assess.assert_not_called()

    def test_unknown_resume_frame_still_fails_and_retains_continuity(self):
        original = CHECK.observe_resume
        with patch.object(CHECK, 'observe_resume', side_effect=lambda before, probe, evidence:
                          original(before, probe, evidence, clock=lambda: self.now, sleep=self.sleep)), \
                patch.object(CHECK.frames, 'read_words', return_value=[{'text': 'boot error'}]), \
                patch.object(CHECK, 'assess_frame', side_effect=RuntimeError('wrong screen')):
            with self.assertRaisesRegex(RuntimeError, 'wrong screen'):
                CHECK.complete_resume(self.before, lambda: self.before,
                                      lambda label: self.evidence.parent / 'resume.png', self.evidence)
        self.assertEqual(json.loads(self.evidence.read_text())['status'], 'PASS')

    def test_resume_unlock_defers_greeter_assessment(self):
        tree = ast.parse(Path(CHECK.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        unlock = next(node for node in main.body if isinstance(node, ast.FunctionDef) and node.name == 'unlock')
        import types
        assessed = []
        namespace = dict(CHECK.__dict__, vm=self.evidence.parent, user='fixture', disk_password='fixture',
                         time=types.SimpleNamespace(sleep=lambda seconds: None),
                         monitor=types.SimpleNamespace(command=lambda *args: None),
                         type_text=lambda text: None, capture=lambda label: label,
                         assess_frame=lambda *args, **kwargs: assessed.append((args, kwargs)),
                         run=lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout=b'', stderr=b''))
        exec(compile(ast.Module(body=[unlock], type_ignores=[]), '<unlock>', 'exec'), namespace)
        namespace['unlock']('resume', assess_greeter=False)
        self.assertEqual(assessed, [(('resume-prompt', 'prompt'), {})])
        assessed.clear()
        namespace['unlock']('snapshot', {'indices': [2, 2, 1]})
        menu_rows = [(args, kwargs) for args, kwargs in assessed if args[1] == 'menu']
        self.assertEqual(menu_rows, [(('snapshot-menu', 'menu'), {}),
                                    (('snapshot-menu-0', 'menu'), {'selected_index': 2}),
                                    (('snapshot-menu-1', 'menu'), {'selected_index': 2}),
                                    (('snapshot-menu-2', 'menu'), {'selected_index': 1})])

    def test_inactive_result_has_no_pass_marker(self):
        tree = ast.parse(Path(CHECK.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        cleanup = next(node for node in main.body if isinstance(node, ast.Try) and node.finalbody)
        outcome = next(node for node in cleanup.body if isinstance(node, ast.If)
                       and ast.unparse(node.test) == "visual['status'] == 'NOT TESTED'")
        following = cleanup.body[cleanup.body.index(outcome) + 1]
        namespace = dict(CHECK.__dict__, vm=self.evidence.parent,
                         visual={'status': 'NOT TESTED'}, iso_record='ISO: fixture\n')
        code = compile(ast.Module(body=[outcome, following], type_ignores=[]), '<result>', 'exec')
        with self.assertRaises(SystemExit) as result:
            exec(code, namespace)
        self.assertEqual(result.exception.code, 77)
        self.assertTrue((self.evidence.parent / 'NOT-TESTED').exists())
        self.assertFalse((self.evidence.parent / 'PASS').exists())


class SnapshotMenuSelection(unittest.TestCase):
    MAIN = """function load_video {
  insmod all_video
}
menuentry 'Emaki' {
 linux /boot/vmlinuz-linux rootflags=subvol=@
}
submenu 'Advanced options' {
 menuentry 'Fallback' {
  linux /boot/vmlinuz-linux rootflags=subvol=@
 }
}
submenu 'Recovery snapshots' {
 if [ -f ${prefix}/grub-btrfs.cfg ]; then
  source ${prefix}/grub-btrfs.cfg
 fi
}
"""
    SNAPSHOTS = """menuentry '| Date | Snapshot |' { echo }
submenu '| earlier | 7 |' {
 submenu '| description |' { echo }
 menuentry 'LTS' {
  linux /@snapshots/7/snapshot/boot/vmlinuz-linux-lts rootflags=subvol="@snapshots/7/snapshot"
  initrd /@snapshots/7/snapshot/boot/initramfs-linux-lts.img
 }
}
submenu '| latest | 42 |' {
 submenu '| description |' { echo }
 menuentry 'Linux' {
  linux "/@snapshots/42/snapshot/boot/vmlinuz-linux" root=UUID=123 rootflags=compress=zstd,subvol="@snapshots/42/snapshot"
  initrd "/@snapshots/42/snapshot/boot/initramfs-linux.img"
 }
}
"""

    def test_real_sibling_indices_include_headers_but_not_nested_main_entries(self):
        selected = CHECK.snapshot_selection(self.MAIN, self.SNAPSHOTS)
        self.assertEqual(selected, {'indices': [2, 2, 1], 'snapshot': '42', 'title': 'Linux',
                                    'titles': ['Recovery snapshots', '| latest | 42 |', 'Linux']})
        commands, frames = [], []
        CHECK.select_snapshot_menu(selected, commands.append, frames.append, sleep=lambda _: None)
        self.assertEqual(commands, ['sendkey home', 'sendkey down', 'sendkey down', 'sendkey ret',
                                    'sendkey home', 'sendkey down', 'sendkey down', 'sendkey ret',
                                    'sendkey home', 'sendkey down', 'sendkey ret'])
        self.assertEqual(frames, ['snapshot-menu-0', 'snapshot-menu-1', 'snapshot-menu-2'])

    def test_menu_without_display_only_rows_uses_first_kernel(self):
        menu = '\n'.join(line for line in self.SNAPSHOTS.splitlines() if '{ echo }' not in line)
        self.assertEqual(CHECK.snapshot_selection(self.MAIN, menu)['indices'], [2, 1, 0])

    def test_wrong_snapshot_or_normal_boot_is_not_success(self):
        selection = CHECK.snapshot_selection(self.MAIN, self.SNAPSHOTS)
        CHECK.verify_snapshot(selection, {'mode': 'snapshot', 'snapshot': '42'})
        for status in ({'mode': 'normal'}, {'mode': 'snapshot', 'snapshot': '7'}, {}):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                CHECK.verify_snapshot(selection, status)

    def test_snapshot_boot_drives_menu_and_checks_identity(self):
        # Exercise the real nested orchestration without starting a guest.
        tree = ast.parse(Path(CHECK.__file__).read_text())
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        boot = next(node for node in main.body if isinstance(node, ast.FunctionDef) and node.name == 'snapshot_boot')
        with tempfile.TemporaryDirectory() as directory:
            selected = []
            def unlock(label, selection):
                self.assertEqual(label, 'snapshot')
                self.assertIsInstance(selection, dict, 'Snapshot boot must select the generated menu')
                selected.append(selection)
            def sudo(command):
                return {'cat /boot/grub/grub.cfg': self.MAIN.encode(),
                        'cat /boot/grub/grub-btrfs.cfg': self.SNAPSHOTS.encode(),
                        'findmnt -n -o UUID /': b'123',
                        'test -d /sys/firmware/efi && echo efi || echo bios': firmware[0]}.get(command, b'')
            firmware = [b'efi\n']
            namespace = dict(CHECK.__dict__, vm=Path(directory), sudo=sudo, stop=lambda: None,
                             start=lambda live: None, unlock=unlock, user='fixture',
                             ssh=lambda *args, **kwargs: b'{"mode":"snapshot","snapshot":"7"}')
            exec(compile(ast.Module(body=[boot], type_ignores=[]), '<snapshot-boot>', 'exec'), namespace)
            with self.assertRaisesRegex(RuntimeError, 'selected snapshot did not boot'):
                namespace['snapshot_boot']()
            self.assertEqual(selected[0]['snapshot'], '42')
            # Under BIOS the UEFI-only firmware entry is absent and every menu index would shift.
            firmware[0] = b'bios\n'
            selected.clear()
            with self.assertRaisesRegex(RuntimeError, 'UEFI-only'):
                namespace['snapshot_boot']()
            self.assertEqual(selected, [])

    def test_missing_link_kernel_initrd_or_snapshot_identity_fails(self):
        variants = [('', self.SNAPSHOTS), (self.MAIN, ''),
                    (self.MAIN, self.SNAPSHOTS.replace('initrd ', 'echo ')),
                    (self.MAIN, self.SNAPSHOTS.replace('subvol=', 'subvolid=')),
                    (self.MAIN, self.SNAPSHOTS + "submenu 'truncated' {\n")]
        for main, menu in variants:
            with self.subTest(main=main, menu=menu), self.assertRaises(ValueError):
                CHECK.snapshot_selection(main, menu)


class CapabilitiesImage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / 'tests/vm'
        scripts.mkdir(parents=True)
        self.script = scripts / 'iso-encrypt-capabilities.sh'
        shutil.copyfile(ROOT / 'tests/vm/iso-encrypt-capabilities.sh', self.script)
        for name in ('run-iso.sh', 'iso-wait-ssh.sh', 'iso-ssh.sh', 'iso-stop.sh'):
            target = scripts / name
            target.write_text(
                '#!/bin/sh\n'
                'record="$CALLS.${0##*/}"\n'
                'printf "%s\\n" "$0" "$@" > "$record.tmp"\n'
                'mv -- "$record.tmp" "$record"\n'
                + ('while [ ! -f "$CALLS.run-iso.sh" ]; do sleep 0.01; done\n'
                   if name == 'iso-wait-ssh.sh' else ''))
            target.chmod(0o700)
        self.iso = self.root / 'candidate with spaces.iso'
        self.iso.write_bytes(b'disposable ISO fixture\n')
        self.env = dict(os.environ, VMDIR=str(self.root / 'runs'),
                        CALLS=str(self.root / 'calls'), EMAKI_CHECK_ISO=str(self.iso))

    def run_check(self):
        return subprocess.run(['bash', str(self.script), 'fixture'], cwd=self.root,
                              env=self.env, capture_output=True, timeout=10)

    def test_no_image_or_missing_image_refused_before_vm_start(self):
        for value in ('', str(self.root / 'missing.iso')):
            with self.subTest(value=value):
                self.env['EMAKI_CHECK_ISO'] = value
                result = self.run_check()
                self.assertEqual(result.returncode, 2)
                self.assertIn(b'readable candidate ISO', result.stderr)
                self.assertEqual(list(self.root.glob('calls.*')), [])

    def test_explicit_image_is_booted_and_digest_recorded(self):
        import hashlib
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = (self.root / 'runs/capabilities-fixture/iso.txt').read_text()
        self.assertIn(str(self.iso), record)
        self.assertIn(hashlib.sha256(self.iso.read_bytes()).hexdigest(), record)
        self.assertEqual((self.root / 'calls.run-iso.sh').read_text().splitlines(),
                         ['tests/vm/run-iso.sh', '--ssh-port', '2231', '--iso', str(self.iso)])
        # Existing evidence must never be mixed with another invocation.
        self.assertNotEqual(self.run_check().returncode, 0)


if __name__ == '__main__':
    unittest.main()
