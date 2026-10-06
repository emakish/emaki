#!/usr/bin/env python3
"""Offline tests of the eyes harness: fake QMP and VNC servers, stub QEMU. Never starts a VM."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE))
import eyes  # noqa: E402

from PIL import Image  # noqa: E402


def short_tempdir():
    # Unix socket paths are limited to about 107 bytes; keep the fixture paths short.
    return tempfile.TemporaryDirectory(prefix='e-', dir=CACHE)


class FakeQMP(socketserver.ThreadingUnixStreamServer):
    """Records every QMP command; answers screendump by writing the current fake frame."""
    daemon_threads = True

    def __init__(self, path, frames):
        self.calls = []
        self.frames = frames
        super().__init__(str(path), FakeQMPHandler)


class FakeQMPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'{"QMP": {"version": {}, "capabilities": []}}\n')
        for line in self.rfile:
            request = json.loads(line)
            self.server.calls.append(request)
            if request['execute'] == 'screendump':
                self.server.frames.current().save(request['arguments']['filename'])
            self.wfile.write(b'{"return": {}}\n')


class FakeVNC(socketserver.ThreadingUnixStreamServer):
    """RFB 3.8, security none, raw encoding: serves the current fake frame on each request."""
    daemon_threads = True

    def __init__(self, path, frames):
        self.frames = frames
        super().__init__(str(path), FakeVNCHandler)


class FakeVNCHandler(socketserver.BaseRequestHandler):
    def read(self, n):
        data = b''
        while len(data) < n:
            part = self.request.recv(n - len(data))
            if not part:
                raise EOFError
            data += part
        return data

    def handle(self):
        w, h = self.server.frames.size
        self.request.sendall(b'RFB 003.008\n')
        self.read(12)
        self.request.sendall(b'\x01\x01')
        self.read(1)
        self.request.sendall(struct.pack('>I', 0))
        self.read(1)
        self.request.sendall(struct.pack('>HH', w, h) + bytes(16) + struct.pack('>I', 4) + b'fake')
        self.read(20)  # SetPixelFormat
        self.read(8)  # SetEncodings with one encoding
        try:
            while True:
                if self.read(1) != b'\x03':
                    return
                self.read(9)
                time.sleep(self.server.frames.delay)
                img = self.server.frames.current()
                data = img.convert('RGB').tobytes('raw', 'BGRX')
                self.request.sendall(b'\x00\x00' + struct.pack('>H', 1) + struct.pack('>HHHHi', 0, 0, w, h, 0) + data)
        except (EOFError, OSError):
            return


class Frames:
    """A scripted screen: each grab returns the next colour, the last one repeats."""

    def __init__(self, colours, size=(64, 40)):
        self.colours = list(colours)
        self.size = size
        self.index = 0
        self.delay = 0.0  # seconds per VNC frame, roughly a real grab
        self.lock = threading.Lock()

    def current(self):
        with self.lock:
            colour = self.colours[min(self.index, len(self.colours) - 1)]
            self.index += 1
        return Image.new('RGB', self.size, colour)


class FakeGuest:
    """A pass directory with fake QMP and VNC sockets, as eyes-vm.sh would leave it."""

    def __init__(self, directory, colours, vnc=True, res='1920x1080'):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / 'res').write_text(res + '\n')
        self.frames = Frames(colours)
        self.qmp = FakeQMP(self.dir / 'qmp.sock', self.frames)
        self.servers = [self.qmp]
        if vnc:
            self.servers.append(FakeVNC(self.dir / 'vnc.sock', self.frames))
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()


class MetricsTests(unittest.TestCase):
    def test_black_frozen_and_change_points(self):
        timeline = [
            {'t': 0.0, 'luminance': 0.0, 'changed': True},
            {'t': 1.0, 'luminance': 0.0, 'changed': False},
            {'t': 2.0, 'luminance': 0.01, 'changed': True},
            {'t': 3.0, 'luminance': 0.5, 'changed': True},
            {'t': 4.0, 'luminance': 0.5, 'changed': False},
            {'t': 5.0, 'luminance': 0.5, 'changed': False},
            {'t': 6.0, 'luminance': 0.5, 'changed': False},
        ]
        self.assertEqual(eyes.metrics(timeline), {'t_first_change': 2.0, 't_stable': 3.0,
                                                  'longest_black': 2.0, 'longest_frozen': 3.0})

    def test_unknown_characters_are_refused_before_typing(self):
        self.assertEqual(eyes.qcode('A'), 'shift-a')
        self.assertEqual(eyes.qcode('7'), '7')
        self.assertEqual(eyes.qcode('|'), 'shift-backslash')
        for character in ('ж', 'é', '\x00'):
            with self.assertRaises(ValueError):
                eyes.qcode(character)


class PassTests(unittest.TestCase):
    def setUp(self):
        self.work = short_tempdir()
        self.base = Path(self.work.name)

    def tearDown(self):
        self.work.cleanup()

    def guest(self, colours, vnc=True):
        guest = FakeGuest(self.base / 'p', colours, vnc)
        self.addCleanup(guest.close)
        vm = eyes.Pass(guest.dir, {'secret': 'Ab1|', 'marker': 'm1'})
        self.addCleanup(vm.close)
        return guest, vm

    def test_input_is_qmp_only_and_scaled_to_the_pass_resolution(self):
        guest, vm = self.guest(['black'])
        vm.click(960, 540)
        vm.type_input('secret')
        vm.keys('meta_l-l')
        names = [call['execute'] for call in guest.qmp.calls]
        self.assertEqual(names[0], 'qmp_capabilities')
        pointer = [c['arguments']['events'] for c in guest.qmp.calls if c['execute'] == 'input-send-event']
        self.assertEqual(pointer[1], [{'type': 'abs', 'data': {'axis': 'x', 'value': 16383}},
                                      {'type': 'abs', 'data': {'axis': 'y', 'value': 16383}}])
        self.assertEqual([e[0]['data'] for e in pointer[2:]], [{'down': True, 'button': 'left'},
                                                               {'down': False, 'button': 'left'}])
        typed = [[k['data'] for k in c['arguments']['keys']] for c in guest.qmp.calls if c['execute'] == 'send-key']
        self.assertEqual(typed, [['shift', 'a'], ['b'], ['1'], ['shift', 'backslash'], ['meta_l', 'l']])
        log = (guest.dir / 'timeline.log').read_text()
        self.assertIn('type ****', log)
        self.assertNotIn('Ab1', log)

    def test_vnc_grab_reads_the_scan_out(self):
        _, vm = self.guest(['red'])
        img, source = vm.grab()
        self.assertEqual(source, 'vnc')
        self.assertEqual(img.getpixel((5, 5)), (255, 0, 0))

    def test_screendump_only_without_vnc(self):
        guest, vm = self.guest(['blue'], vnc=False)
        img, source = vm.grab()
        self.assertEqual(source, 'screendump')
        self.assertEqual(img.getpixel((1, 1)), (0, 0, 255))
        self.assertIn('screendump', [c['execute'] for c in guest.qmp.calls])

    def test_series_keeps_change_points_and_stops_when_stable(self):
        _, vm = self.guest(['black', 'black', 'white', 'white', 'grey', 'grey'])
        saved = []

        def save(img, t):
            saved.append(img.getpixel((0, 0)))
            return f'f{len(saved)}.png'

        timeline, result = vm.series(save, interval=0.01, timeout=5, stable=0.05)
        self.assertEqual(saved, [(0, 0, 0), (255, 255, 255), (128, 128, 128)])
        self.assertFalse(result['timed_out'])
        self.assertGreater(result['longest_black'], 0)
        self.assertEqual(sum(1 for e in timeline if e['file']), 3)

    def test_series_reports_a_timeout(self):
        colours = [(i, i, i) for i in range(200)]
        _, vm = self.guest(colours)
        timeline, result = vm.series(lambda img, t: 'x.png', interval=0.01, timeout=0.2, stable=5)
        self.assertTrue(result['timed_out'])
        self.assertTrue(timeline[-1]['file'])

    def test_burst_keeps_every_frame_and_measures_the_interval(self):
        guest, vm = self.guest(['black'])
        saved = []
        typed = threading.Event()

        def during(stop):
            vm.key('a', 10)
            typed.set()
            stop.wait(5)

        timeline, interval = vm.burst(lambda img, t: saved.append(t) or 'f.png', duration=0.3, during=during)
        self.assertTrue(typed.is_set())
        self.assertEqual(len(saved), len(timeline))
        self.assertGreater(len(saved), 2)
        self.assertGreater(interval['grab_interval_ms'], 0)
        self.assertIn('send-key', [c['execute'] for c in guest.qmp.calls])


STUB_QEMU = '''#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STUB_LOG"
'''
STUB_QEMU_IMG = '''#!/usr/bin/env bash
printf 'qemu-img %s\\n' "$*" >>"$STUB_LOG"
: >"${@: -2:1}"
'''
STUB_VERIFY = '''#!/usr/bin/env python3
import os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write('verify-image ' + ' '.join(sys.argv[1:]) + '\\n')
sys.exit(int(os.environ.get('STUB_VERIFY_RC', '0')))
'''


class LauncherTests(unittest.TestCase):
    """eyes-vm.sh against stub QEMU and a stub iso/verify-image.py in a copied tree."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='eyes-vm-')  # no sockets here; short real paths
        self.base = Path(self.work.name)
        self.repo = self.base / 'r'
        (self.repo / 'tests/vm/eyes').mkdir(parents=True)
        (self.repo / 'iso').mkdir()
        shutil.copy(HERE / 'eyes-vm.sh', self.repo / 'tests/vm/eyes/eyes-vm.sh')
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for path, text in ((self.bin / 'qemu-system-x86_64', STUB_QEMU), (self.bin / 'qemu-img', STUB_QEMU_IMG),
                           (self.repo / 'iso/verify-image.py', STUB_VERIFY)):
            path.write_text(text)
            path.chmod(0o755)
        self.ovmf = self.base / 'ovmf'
        self.ovmf.mkdir()
        for name in ('OVMF_CODE.4m.fd', 'OVMF_VARS.4m.fd'):
            (self.ovmf / name).write_bytes(b'fw')
        self.render = self.base / 'render'
        self.render.write_bytes(b'')
        self.log = self.base / 'calls.log'
        self.log.touch()
        self.iso = self.base / 'isos/emaki.iso'
        self.iso.parent.mkdir()
        self.iso.write_bytes(b'release image\n')
        self.sha = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        (self.base / 'isos/emaki.iso.sha256').write_text(f'{self.sha}  emaki.iso\n')
        self.walk = self.base / 'eyes' / self.sha[:12] / 'w'
        self.walk.mkdir(parents=True)
        (self.walk / 'iso.sha256').write_text(self.sha + '\n')

    def tearDown(self):
        self.work.cleanup()

    def launch(self, *args, verify_rc=0):
        env = dict(os.environ, HOME=str(self.base / 'home'), PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                   STUB_LOG=str(self.log), STUB_VERIFY_RC=str(verify_rc), EYES_OVMF_DIR=str(self.ovmf),
                   EYES_RENDER_NODE=str(self.render), EYES_ROOT=str(self.base / 'eyes'))
        result = subprocess.run(['bash', str(self.repo / 'tests/vm/eyes/eyes-vm.sh'), '--walk', str(self.walk),
                                 '--run', 'W1', *args], env=env, text=True, capture_output=True, timeout=60)
        return result, self.log.read_text().splitlines()

    def qemu_calls(self, calls):
        return [c for c in calls if not c.startswith(('qemu-img', 'verify-image'))]

    def test_release_image_starts_one_isolated_gl_pass(self):
        result, calls = self.launch('--display', 'gl', '--res', '1920x1080', '--iso', str(self.iso))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'verify-image {self.iso}', calls)
        self.assertFalse(any('--test' in c for c in calls))
        [qemu] = self.qemu_calls(calls)
        passdir = self.walk / 'W1/gl-1920x1080-uefi-01'
        for part in ('-m 4G', '-smp 2', 'nvme,drive=target', 'usb-tablet', 'virtio-vga-gl,xres=1920,yres=1080',
                     f'-vnc unix:{passdir}/vnc.sock', f'unix:{passdir}/qmp.sock', f'file={self.iso}'):
            self.assertIn(part, qemu)
        self.assertNotIn('hostfwd', qemu)
        self.assertEqual((self.walk / 'current-pass').read_text().strip(), str(passdir))
        self.assertEqual((passdir / 'res').read_text().strip(), '1920x1080')
        self.assertIn(f'EYES_PASS={passdir}', result.stdout)
        self.assertTrue((passdir / 'qemu.log').is_file())
        # A second pass never reuses the first one's directory.
        result, calls = self.launch('--display', 'fw', '--res', '2560x1600', '--iso', str(self.iso))
        self.assertEqual(result.returncode, 0, result.stderr)
        qemu = self.qemu_calls(calls)[-1]
        self.assertIn('VGA,xres=2560,yres=1600,vgamem_mb=64', qemu)
        self.assertTrue((self.walk / 'W1/fw-2560x1600-uefi-01').is_dir())

    def test_bios_and_installed_disk_passes(self):
        result, calls = self.launch('--display', 'fw', '--res', '1920x1080', '--firmware', 'bios', '--iso', str(self.iso))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('pflash', self.qemu_calls(calls)[-1])
        result, calls = self.launch('--display', 'gl', '--res', '1920x1080', '--no-cd')
        self.assertEqual(result.returncode, 0, result.stderr)
        qemu = self.qemu_calls(calls)[-1]
        self.assertNotIn('cdrom', qemu)
        self.assertIn('pflash', qemu)

    def assert_refused(self, result, calls, words):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(words, result.stderr)
        self.assertEqual(self.qemu_calls(calls), [])

    def test_test_image_is_refused(self):
        result, calls = self.launch('--display', 'gl', '--res', '1920x1080', '--iso', str(self.iso), verify_rc=1)
        self.assert_refused(result, calls, 'release mode')

    def test_image_that_does_not_match_its_checksum_is_refused(self):
        (self.base / 'isos/emaki.iso.sha256').write_text('0' * 64 + '  emaki.iso\n')
        result, calls = self.launch('--display', 'gl', '--res', '1920x1080', '--iso', str(self.iso))
        self.assert_refused(result, calls, 'does not match')

    def test_image_of_another_walk_is_refused(self):
        other = self.base / 'isos/other.iso'
        other.write_bytes(b'another image\n')
        digest = hashlib.sha256(other.read_bytes()).hexdigest()
        (self.base / 'isos/other.iso.sha256').write_text(f'{digest}  other.iso\n')
        result, calls = self.launch('--display', 'gl', '--res', '1920x1080', '--iso', str(other))
        self.assert_refused(result, calls, "is not the walk's")

    def test_running_build_and_second_gl_guest_are_refused(self):
        for lock, words in ((self.base / 'home/VMs/iso/.build.lock', 'ISO build'),
                            (self.base / 'eyes/.gl.lock', 'another GL guest')):
            lock.parent.mkdir(parents=True, exist_ok=True)
            with open(lock, 'w') as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                result, calls = self.launch('--display', 'gl', '--res', '1920x1080', '--iso', str(self.iso))
                self.assert_refused(result, calls, words)

    def test_shellcheck(self):
        if not shutil.which('shellcheck'):
            self.skipTest('shellcheck is not installed')
        subprocess.run(['shellcheck', str(HERE / 'eyes-vm.sh')], check=True)


STAGES = tomllib.loads((HERE / 'stages.toml').read_text())
SEEN = 'Top centre: "Install Emaki 0.1.2" in 18 px white text; Back and Continue buttons at the bottom right.'


class GateTests(unittest.TestCase):
    """eyes-gate.py on hand-made walk records (plan 5.3a): each stop condition gives a non-zero exit."""

    def setUp(self):
        self.work = short_tempdir()
        self.base = Path(self.work.name)
        self.iso = self.base / 'release.iso'
        self.iso.write_bytes(b'release image\n')
        self.sha = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        self.walk = self.base / 'walk'
        (self.walk / 'frames').mkdir(parents=True)
        self.record = self.complete_record()
        self.signoff = {'iso_sha256': self.sha, 'signed_by': 'owner', 'date': '2026-10-05',
                        'fixed_frames_seen': True, 'picked': [], 'picked_agree': True,
                        'hardware': {}}  # filled as a list below
        self.hardware = [{'line': line, 'iso_sha256': self.sha, 'result': 'seen', 'note': 'MacBook'}
                         for line in ('H-M10 / H-N6', 'H-M11')]
        self.waivers = []

    def tearDown(self):
        self.work.cleanup()

    def frame(self, name, colour='grey'):
        path = self.walk / 'frames' / f'{name}.png'
        Image.new('RGB', (8, 8), colour).save(path)
        return f'frames/{name}.png', hashlib.sha256(path.read_bytes()).hexdigest()

    def complete_record(self):
        runs = {}
        for run, spec in STAGES['runs'].items():
            tables = runs.setdefault(run, {'stages': {}})['stages']
            for sid in spec['stages']:
                stage = STAGES['stages'][sid]
                name, digest = self.frame(f'{run}-{sid}')
                if stage.get('hardware_line'):
                    table = {'result': 'NOT TESTED', 'reason': 'Display output is not active after wake in QEMU',
                             'hardware_line': stage['hardware_line'], 'frames': [name], 'sha256': [digest]}
                else:
                    table = {'result': 'PASS', 'verdict': 'OK', 'seen': SEEN, 'frames': [name], 'sha256': [digest]}
                if stage.get('lock') and table['result'] == 'PASS':
                    burst, burst_sha = self.frame(f'{run}-{sid}-burst')
                    table['input_test'] = {'marker_absent': True, 'frames': [burst], 'sha256': [burst_sha],
                                           'grab_interval_ms': 80.5}
                tables[sid] = table
        return {'walk': {'iso_path': str(self.iso), 'iso_sha256': self.sha, 'date': '2026-10-05',
                         'walked_by': 'walker', 'reviewed_by': 'reviewer', 'qemu': ['qemu-system-x86_64 ...']},
                'runs': runs}

    def gate(self, *extra, iso=None, script=HERE / 'eyes-gate.py', verified=True):
        (self.walk / 'walk.toml').write_text(eyes.dump_toml(self.record))
        signoff = eyes.dump_toml({k: v for k, v in self.signoff.items() if k != 'hardware'})
        for block, entries in (('hardware', self.hardware), ('waive', self.waivers)):
            for entry in entries:
                signoff += f'\n[[{block}]]\n' + eyes.dump_toml(entry)
        (self.walk / 'signoff.toml').write_text(signoff)
        command = [sys.executable, str(script), '--record', str(self.walk / 'walk.toml'),
                   '--iso', str(iso or self.iso), '--signoff', str(self.walk / 'signoff.toml'), *extra]
        if verified:
            command.append('--image-verified')
        result = subprocess.run(command, text=True, capture_output=True, timeout=60)
        self.assertNotRegex(result.stdout, r'(?im)^OK\b|verified|\bDONE\b')
        return result

    def accepted(self):
        """Run once to learn the gate's five picks, put them on the sheet, run again."""
        first = self.gate()
        self.assertEqual(first.returncode, 3, first.stdout)
        picks = first.stdout.split('picked by the gate: ')[1].splitlines()[0].split(', ')
        self.assertEqual(len(picks), 5)
        self.signoff['picked'] = picks
        return picks

    def stage(self, run, sid):
        return self.record['runs'][run]['stages'][sid]

    def test_complete_record_passes(self):
        self.accepted()
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], f'RESULT: PASSED (walk record of {self.sha[:12]})')

    def test_missing_stage_fails(self):
        self.accepted()
        del self.record['runs']['W2']['stages']['19']
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn('W2/19: FAIL: stage missing from the record', result.stdout)

    def test_unjudged_stage_fails(self):
        self.accepted()
        self.stage('W1', '05')['result'] = ''
        self.assertEqual(self.gate().returncode, 1)

    def test_wrong_image_sha_fails(self):
        self.accepted()
        other = self.base / 'test.iso'
        other.write_bytes(b'test image\n')
        result = self.gate(iso=other)
        self.assertEqual(result.returncode, 1)
        self.assertIn('header: FAIL: record sha256', result.stdout)

    def test_not_tested_without_hardware_line_fails(self):
        self.accepted()
        self.stage('W1', '30')['hardware_line'] = ''
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn('W1/30: FAIL: NOT TESTED needs the reason and the hardware-sheet line', result.stdout)

    def test_fail_stage_fails(self):
        self.accepted()
        self.stage('W1', '03').update(result='FAIL', verdict='BROKEN')
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: FAILED at W1/03')

    def test_not_tested_blocks_until_the_hardware_line_is_seen(self):
        self.accepted()
        self.hardware = [h for h in self.hardware if h['line'] != 'H-M11']
        result = self.gate()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: NOT TESTED at W1/31')
        self.hardware.append({'line': 'H-M11', 'iso_sha256': '0' * 64, 'result': 'seen'})
        self.assertEqual(self.gate().returncode, 3, 'a hardware result of another image counts')
        self.hardware[-1].update(iso_sha256=self.sha, result='failed')
        self.assertEqual(self.gate().returncode, 1)

    def test_rubber_stamp_seen_fails(self):
        self.accepted()
        self.stage('W1', '05')['seen'] = 'looks fine'
        self.assertEqual(self.gate().returncode, 1)
        self.stage('W1', '05')['seen'] = STAGES['stages']['05']['expected']
        self.assertEqual(self.gate().returncode, 1)

    def test_changed_frame_fails(self):
        self.accepted()
        Image.new('RGB', (8, 8), 'red').save(self.walk / 'frames/W1-05.png')
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn('frame sha256 differs', result.stdout)

    def test_lock_stage_needs_the_input_test(self):
        self.accepted()
        self.stage('W1', '29')['input_test']['marker_absent'] = False
        self.assertEqual(self.gate().returncode, 1)
        self.stage('W1', '29')['input_test'] = {'marker_absent': True, 'frames': [], 'sha256': []}
        self.assertEqual(self.gate().returncode, 1)

    def test_only_a_confusing_frame_can_be_waived(self):
        self.accepted()
        self.stage('W1', '26').update(result='FAIL', verdict='CONFUSING')
        self.assertEqual(self.gate().returncode, 1)
        self.waivers.append({'run': 'W1', 'stage': '26', 'sentence': 'the island labels are fine for 0.2'})
        self.accepted()  # the picks come from the PASS stages, which changed
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('W1/26: FAIL, waived by name on the signed sheet', result.stdout)
        self.stage('W1', '26')['verdict'] = 'BROKEN'
        self.assertEqual(self.gate().returncode, 1)

    def test_not_applicable_needs_its_reference(self):
        self.accepted()
        self.stage('W3', '35').clear()
        self.stage('W3', '35').update(result='NOT APPLICABLE')
        self.assertEqual(self.gate().returncode, 1)
        self.stage('W3', '35')['reference'] = 'scenario parameter: 1366x768 not kept (question 7)'
        self.assertEqual(self.gate().returncode, 0)

    def test_hidden_feature_needs_the_frame_of_its_absence(self):
        self.accepted()
        self.stage('W2', '20').clear()
        self.stage('W2', '20').update(result='NOT APPLICABLE', hidden=True, reference='DECISIONS 2026-10-04 encryption hidden')
        self.assertEqual(self.gate().returncode, 1)

    def test_reviewer_must_not_be_the_walker(self):
        self.accepted()
        self.record['walk']['reviewed_by'] = 'walker'
        self.assertEqual(self.gate().returncode, 1)

    def test_signed_sheet_is_required(self):
        self.accepted()
        self.signoff['signed_by'] = ''
        self.assertEqual(self.gate().returncode, 3)
        self.signoff['signed_by'] = 'owner'
        self.signoff['picked_agree'] = False
        result = self.gate()
        self.assertEqual(result.returncode, 3)
        self.assertIn('the review counts as NOT TESTED', result.stdout)
        self.signoff['picked_agree'] = True
        self.signoff['iso_sha256'] = '0' * 64
        self.assertEqual(self.gate().returncode, 1)

    def test_walk_of_the_0_1_2_image_is_refused(self):
        """Plan step 15 / 10.0 'Done means' on a record shaped like the 2026-10-04 walk."""
        self.accepted()
        self.stage('W1', '03').update(result='FAIL', verdict='BROKEN', seen=(
            'Sidebar bottom-left reads "Install · 0.1.0" on the 0.1.2 image; a second window "Welcome to Emaki" '
            'sits behind the installer window.'))
        self.stage('W2', '19').update(result='FAIL', verdict='BLACK', seen=(
            'Black screen; one line of 16 px grey text at the top left asks for a passphrase without naming Emaki.'))
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        for line in ('W1/03: FAIL: BROKEN', 'W2/19: FAIL: BLACK'):
            self.assertIn(line, result.stdout)
        self.hardware = []
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        for where in ('W1/30', 'W1/31', 'W2/31'):
            self.assertIn(f'{where}: NOT TESTED:', result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: FAILED at W1/03')

    def test_test_image_is_refused(self):
        self.accepted()
        repo = self.base / 'r'
        (repo / 'tests/vm/eyes').mkdir(parents=True)
        (repo / 'iso').mkdir()
        for name in ('eyes-gate.py', 'stages.toml'):
            shutil.copy(HERE / name, repo / 'tests/vm/eyes' / name)
        (repo / 'iso/verify-image.py').write_text(
            'import sys\nsys.exit("ERROR: mastered image verification failed: test kernel argument in release loader")\n')
        result = self.gate(script=repo / 'tests/vm/eyes/eyes-gate.py', verified=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('image: FAIL: iso/verify-image.py (release mode) refused the image', result.stdout)


def load_walk_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('eyes_walk', HERE / 'eyes-walk.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WalkTests(unittest.TestCase):
    """eyes-walk.py: a walk directory per image sha256, frames recorded into walk.toml."""

    def setUp(self):
        self.work = short_tempdir()
        self.base = Path(self.work.name)
        self.repo = self.base / 'r'
        (self.repo / 'tests/vm/eyes').mkdir(parents=True)
        (self.repo / 'iso').mkdir()
        for name in ('eyes-walk.py', 'eyes.py', 'vncshot.py', 'stages.toml'):
            shutil.copy(HERE / name, self.repo / 'tests/vm/eyes' / name)
        (self.repo / 'iso/VERSION').write_text('0.2.0\n')
        (self.repo / 'iso/verify-image.py').write_text(STUB_VERIFY)
        self.log = self.base / 'calls.log'
        self.iso = self.base / 'emaki.iso'
        self.iso.write_bytes(b'release image\n')
        self.sha = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        Path(str(self.iso) + '.sha256').write_text(f'{self.sha}  emaki.iso\n')
        self.walk_mod = load_walk_module()

    def tearDown(self):
        self.work.cleanup()

    def init(self, verify_rc=0):
        env = dict(os.environ, STUB_LOG=str(self.log), STUB_VERIFY_RC=str(verify_rc))
        return subprocess.run([sys.executable, str(self.repo / 'tests/vm/eyes/eyes-walk.py'), 'init',
                               '--iso', str(self.iso), '--root', str(self.base / 'y'), '--walked-by', 'walker'],
                              env=env, text=True, capture_output=True, timeout=60)

    def new_walk(self):
        result = self.init()
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(result.stdout.strip().split('=', 1)[1])

    def test_init_makes_a_fresh_directory_per_image_sha(self):
        first = self.new_walk()
        self.assertEqual(first.parent.name, self.sha[:12])
        self.assertIn(f'verify-image {self.iso}', self.log.read_text())
        record = tomllib.loads((first / 'walk.toml').read_text())
        self.assertEqual(record['walk']['iso_sha256'], self.sha)
        for run, spec in STAGES['runs'].items():
            self.assertEqual(list(record['runs'][run]['stages']), spec['stages'])
        self.assertEqual(record['runs']['W1']['stages']['30']['hardware_line'], 'H-M10 / H-N6')
        self.assertIn('input_test', record['runs']['W1']['stages']['29'])
        self.assertEqual((first / 'iso.sha256').read_text().strip(), self.sha)
        self.assertIn('marker', tomllib.loads((first / 'inputs.toml').read_text()))
        self.assertEqual(tomllib.loads((first / 'signoff.toml').read_text())['iso_sha256'], self.sha)
        second = self.new_walk()
        self.assertNotEqual(first, second)
        self.assertTrue(second.name.endswith('-r2'))

    def test_init_refuses_test_and_unchecked_images(self):
        result = self.init(verify_rc=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('release mode', result.stderr)
        Path(str(self.iso) + '.sha256').write_text('0' * 64 + '  emaki.iso\n')
        self.assertNotEqual(self.init().returncode, 0)
        self.assertFalse((self.base / 'y' / self.sha[:12]).exists() and any((self.base / 'y' / self.sha[:12]).iterdir()))

    def guest_for(self, walk, colours):
        guest = FakeGuest(self.base / 'p', colours)  # short socket path; the walk may be deep
        self.addCleanup(guest.close)
        (walk / 'current-pass').write_text(str(guest.dir) + '\n')
        return guest

    def run_walk(self, walk, *argv, fast=True):
        original = self.walk_mod.Walk.series
        if fast:
            def quick(this, vm, run, stage, label, interval=1.0, timeout=60.0, stable=5.0, input_test=None):
                return original(this, vm, run, stage, label, 0.01, 0.3, 0.05, input_test)
            self.walk_mod.Walk.series = quick
        try:
            return self.walk_mod.main(['--walk', str(walk), *argv])
        finally:
            self.walk_mod.Walk.series = original

    def record(self, walk):
        return tomllib.loads((walk / 'walk.toml').read_text())

    def test_capture_records_frames_with_their_sha256(self):
        walk = self.new_walk()
        self.guest_for(walk, ['black', 'black', 'white'])
        self.run_walk(walk, 'capture', '--run', 'W1', '--stage', '05', '--label', 'welcome')
        self.run_walk(walk, 'capture', '--run', 'W1', '--stage', '02', '--label', 'kernel', '--series')
        stages = self.record(walk)['runs']['W1']['stages']
        self.assertEqual(len(stages['05']['frames']), 1)
        self.assertEqual(len(stages['02']['frames']), 2)
        for table in (stages['05'], stages['02']):
            for name, digest in zip(table['frames'], table['sha256']):
                self.assertEqual(hashlib.sha256((walk / name).read_bytes()).hexdigest(), digest)
        self.assertIn('longest_black', stages['02']['timing']['kernel'])
        self.assertEqual(len((walk / 'frames.jsonl').read_text().splitlines()), 3)
        with self.assertRaises(SystemExit):
            self.run_walk(walk, 'capture', '--run', 'W1', '--stage', '99', '--label', 'x')

    def test_lock_input_test_and_unlock(self):
        walk = self.new_walk()
        guest = self.guest_for(walk, ['grey'])
        guest.frames.delay = 0.02
        self.run_walk(walk, 'input-test', '--run', 'W1', '--stage', '29', '--trigger', 'keys:meta_l-l',
                      '--duration', '1.5')
        self.run_walk(walk, 'session-unlock', '--run', 'W1', '--stage', '29')
        test = self.record(walk)['runs']['W1']['stages']['29']['input_test']
        self.assertIs(test['marker_absent'], False, 'only the judge may set marker_absent')
        self.assertGreater(test['grab_interval_ms'], 0)
        self.assertEqual(test['trigger'], 'keys:meta_l-l')
        names = [Path(name).name for name in test['frames']]
        self.assertTrue(names[0].startswith('001-terminal-before'))
        self.assertIn('-terminal-after-', names[-1])
        self.assertTrue(any('-burst-' in name for name in names))
        keys = [''.join(k['data'] for k in c['arguments']['keys'] if k['data'] != 'shift')
                for c in guest.qmp.calls if c['execute'] == 'send-key']
        self.assertEqual(keys[0], 'meta_ll')
        typed = ''.join(k if len(k) == 1 else {'minus': '-', 'ret': '\n'}.get(k, '?') for k in keys[1:])
        self.assertIn('eyesmarker4711\n', typed)
        self.assertTrue(typed.endswith('wrong-pass-0000\nemaki-walk-pass1\n'), typed[-40:])

    def test_disk_unlock_types_three_then_wrong_then_right(self):
        walk = self.new_walk()
        guest = self.guest_for(walk, ['black'])
        self.run_walk(walk, 'disk-unlock', '--run', 'W2', '--stage', '20')
        keys = [k['data'] for c in guest.qmp.calls if c['execute'] == 'send-key' for k in c['arguments']['keys']]
        typed = ''.join(k if len(k) == 1 else {'minus': '-', 'ret': '\n', 'backspace': '<'}.get(k, '') for k in keys)
        self.assertEqual(typed, 'wro<<<wrong-disk-0000\nemaki-disk-pass1\n')
        timing = self.record(walk)['runs']['W2']['stages']['20']['timing']
        self.assertEqual(list(timing), ['after-wrong', 'after-right'])

    def test_index_lists_every_frame_and_missing_stages(self):
        walk = self.new_walk()
        self.guest_for(walk, ['grey'])
        self.run_walk(walk, 'capture', '--run', 'W1', '--stage', '01', '--label', 'menu')
        self.run_walk(walk, 'index')
        text = (walk / 'index.txt').read_text()
        frame = self.record(walk)['runs']['W1']['stages']['01']['frames'][0]
        self.assertIn(f'  frame: {walk / frame}', text)
        self.assertIn('W1/02 ', text)
        self.assertGreater(text.count('MISSING: no frame recorded'), 50)
        self.assertIn(frame, (walk / 'index.html').read_text())


if __name__ == '__main__':
    unittest.main()
