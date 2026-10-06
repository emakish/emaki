#!/usr/bin/env python3
"""tests/vm/check-graphics-fallback.py against a fake guest. Never starts QEMU or ssh.

The fake guest stands in for QEMU (VNC frames, QMP keys) and test-mode ssh: its snapshots are
built by the real guest helper (tests/vm/guest-graphics-state.py) from recorded console bytes
(/dev/vcsa1 header, /dev/vcsu1 cells) and journalctl JSON lines, and a small text-login state
machine answers the keys the check types. One passing guest; each fault the plan names (greetd
restarted, VT not in text mode, message missing, journal line missing, frozen frame) and a few
more must end the check FAILED with that reason.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load('guest_graphics_state', VM / 'guest-graphics-state.py')
check = load('check_graphics_fallback', VM / 'check-graphics-fallback.py')
from PIL import Image, ImageDraw  # noqa: E402  (after the check, which needs it too)

MESSAGE = (ROOT / 'greetd/graphics-failed.txt').read_text().splitlines()
ISSUE = ['Arch Linux 7.2.7-arch1-1 (tty1)', '']
TEXT_SESSION = ['Text session - the graphical desktop did not start.',
                'Type "exit" to try the graphical login again.',
                'Details: journalctl -b -t emaki-greeter-compositor -t niri-emaki-session']
ACCOUNT = {'live': ('live', ''), 'installed': ('emaki', 'emaki-vm-test-only')}
HOST = {'live': 'emaki-live', 'installed': 'emaki-vm'}
CELLS = {'1920x1080': (240, 67, 8, 16), '2560x1600': (160, 50, 16, 32)}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.01)


class World:
    """What one test run's guests do: the clock, the faults and every guest created."""

    def __init__(self, faults=()):
        self.clock = Clock()
        self.faults = set(faults)
        self.guests = []

    def factory(self, directory, **options):
        guest = FakeGuest(self, directory, **options)
        self.guests.append(guest)
        return guest


class FakeGuest:
    BOOT = 5.0       # the guest kernel starts this long after QEMU
    SSH = 30.0       # guest seconds until test-mode ssh answers
    MESSAGE = 40.0   # guest seconds when the wrapper prints the message
    GREETER = 28.0   # GL, installed: guest seconds when the greeter compositor starts (just before ssh)

    def __init__(self, world, directory, *, system, display, res, iso, disk, port, account, password):
        self.world, self.clock, self.faults = world, world.clock, world.faults
        self.dir, self.system, self.display, self.res = Path(directory), system, display, res
        self.account, self.password = account, password
        self.cols, self.rows, self.cell_w, self.cell_h = CELLS[res]
        self.width, self.height = (int(v) for v in res.split('x'))
        self.started = None
        self.typed = ''
        self.state = 'message'
        self.pending = None
        self.restarts = 0
        self.invocation = 'a1'
        self.agreety = 300
        self.blink = False
        self.keys_sent = []
        self.message_at = 95.0 if 'late' in self.faults else self.MESSAGE
        if 'greeter-early' in self.faults:  # the greeter runs 20 s before test-mode ssh answers
            self.GREETER = 10.0
        self.prints = [self.message_at]
        self.session_at = None
        self.lines = []

    # -- QEMU ------------------------------------------------------------------------------
    def start(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started = self.clock.t

    def stop(self):
        self.stopped = True

    def g(self):
        return self.clock.t - self.started - self.BOOT

    def message_shown(self):
        return self.display == 'std' and self.g() >= self.message_at

    def frame(self, boxes):
        img = Image.new('RGB', (self.width, self.height))
        draw = ImageDraw.Draw(img)
        for row, length in boxes:
            draw.rectangle((0, row * self.cell_h + 2, length * self.cell_w, row * self.cell_h + self.cell_h - 3),
                           fill=(170, 170, 170))
        return img

    def grab(self):
        self.clock.sleep(0.2)
        if self.display == 'gl':
            if self.system == 'installed' and self.g() >= self.GREETER + 1:
                return Image.new('RGB', (self.width, self.height), (200, 120, 60))
            return Image.new('RGB', (self.width, self.height))
        if not self.message_shown() or 'frozen' in self.faults:
            return self.frame([(row, 40 + row % 7 * 9) for row in range(self.rows)])
        lines = self.screen_lines()
        img = self.frame([(row, len(text)) for row, text in enumerate(lines) if text])
        self.blink = not self.blink
        if self.blink:  # the text cursor blinks: a few pixels change between grabs
            y = len(lines) - 1
            ImageDraw.Draw(img).rectangle((len(lines[-1]) * self.cell_w + self.cell_w, y * self.cell_h + 12,
                                           len(lines[-1]) * self.cell_w + 2 * self.cell_w - 1, y * self.cell_h + 14),
                                          fill=(255, 255, 255))
        return img

    def keys(self, *combos):
        self.keys_sent.extend(combos)
        for combo in combos:
            self.clock.sleep(0.15)
            if combo == 'ret':
                self.enter()
            elif combo == 'ctrl-d':
                self.eof()

    def type_text(self, text):
        self.keys_sent.append(('type', text))
        self.clock.sleep(0.1 * len(text))
        self.typed += text

    # -- ssh -------------------------------------------------------------------------------
    def ssh_ready(self):
        self.clock.sleep(0.3)
        return self.g() >= self.SSH

    def upload_helper(self):
        self.uploaded = True

    def snapshot(self):
        t0 = self.clock.t
        self.clock.sleep(0.2)
        g = self.g()
        self.clock.sleep(0.2)
        lines = self.screen_lines()
        cells = ''.join(text.ljust(self.cols) for text in lines).ljust(self.cols * self.rows)
        header = bytes([self.rows, self.cols, len(lines[-1]) + 1 if lines else 0, max(len(lines) - 1, 0)])
        screen = helper.parse_screen(header, struct.pack(f'={len(cells)}I', *map(ord, cells)), 4)
        restarted = self.restarts + ('restarted' in self.faults) + \
            ('restart-during-hold' in self.faults and g > self.message_at + 100)
        greetd = {'ActiveState': 'active', 'SubState': 'running', 'NRestarts': str(restarted),
                  'InvocationID': self.invocation + ('b' if restarted else '')}
        return {'monotonic': g, 'virt': 'kvm', 'active_vt': 'tty1',
                'kd_mode': 1 if 'graphics' in self.faults else 0, 'kb_mode': 4 if 'kb-off' in self.faults else 3,
                'screen': screen, 'greetd': greetd, 'journal': helper.journal_entries(self.journal(g)),
                'processes': self.processes(g), 'host': [t0, self.clock.t]}

    def journal(self, g):
        entries = []
        if self.display == 'std':
            if 'no-fork-line' not in self.faults and g >= self.message_at - 3:
                tag = 'niri-emaki-session' if self.system == 'live' else 'emaki-greeter-compositor'
                text = '2026-10-05T01:00:00.000000Z ERROR niri: ' + check.FORK_LINE
                entries.append((tag, self.message_at - 3, list(text.encode())))  # journald keeps ANSI-free bytes too
            entries += [('emaki-greeter-compositor', at, check.WRAPPER_LINE) for at in self.prints if g >= at]
        elif 'gl-flash' in self.faults and g >= 20:
            entries.append(('emaki-greeter-compositor', 20.0, check.WRAPPER_LINE))
        entries.append(('emaki-greeter-compositor', 1.0, 'dbus-run-session: unrelated line'))
        return [json.dumps({'SYSLOG_IDENTIFIER': tag, '__MONOTONIC_TIMESTAMP': str(int(at * 1e6)), 'MESSAGE': text})
                for tag, at, text in entries]

    def processes(self, g):
        found = []
        if self.display == 'gl':
            if self.system == 'live' and g >= 15:
                found.append({'name': 'niri-emaki', 'pid': 900, 'user': 'live', 'age': round(g - 15, 1)})
            if self.system == 'installed' and g >= self.GREETER and self.session_at is None:
                found.append({'name': 'niri-emaki', 'pid': 700, 'user': 'greeter', 'age': round(g - self.GREETER, 1)})
                if g >= self.GREETER + 1:
                    found.append({'name': 'qs', 'pid': 710, 'user': 'greeter', 'age': round(g - self.GREETER - 1, 1)})
            if self.session_at is not None:
                found.append({'name': 'niri-emaki', 'pid': 800, 'user': self.account,
                              'age': round(g - self.session_at, 1)})
        elif self.state in ('message', 'password'):
            found.append({'name': 'agreety', 'pid': self.agreety, 'user': 'greeter', 'age': 1.0})
        return found

    # -- the VT ----------------------------------------------------------------------------
    def message_screen(self):
        lines = [MESSAGE[0]]
        if 'no-cause' not in self.faults:
            lines.append(check.CAUSE)
        lines += MESSAGE[1:-1]
        if self.system == 'live':
            lines.append('User: live, no password.')
        lines.append(MESSAGE[-1])
        return lines + ISSUE + [f'{HOST[self.system]} login:']

    def screen_lines(self):
        if self.display == 'gl' or not self.message_shown() or 'no-message' in self.faults:
            return [f'[  {row}.000000] boot log line {row}' for row in range(self.rows)]
        if not self.lines:
            self.lines = self.message_screen()
        return self.lines[-self.rows:]

    def add(self, *rows):
        self.lines[-1] = self.lines[-1] + ' ' + self.typed if self.typed else self.lines[-1]
        self.lines += list(rows)
        self.typed = ''

    def enter(self):
        if self.display == 'gl':
            if self.typed == self.password and 'gl-login-lost' not in self.faults:
                self.session_at = self.g()
            self.typed = ''
            return
        typed = self.typed
        if self.state == 'message':
            if typed == self.account and not self.password:
                self.add(*TEXT_SESSION, f'[{typed}@{HOST[self.system]} ~]$')
                self.state = 'session'
            else:
                self.add('Password:')
                self.state, self.pending = 'password', typed
        elif self.state == 'password':
            self.typed = ''
            if self.pending == self.account and typed == self.password:
                self.add(*TEXT_SESSION, f'[{self.pending}@{HOST[self.system]} ~]$')
                self.state = 'session'
            else:
                if 'wrong-restarts' in self.faults:
                    self.restarts += 1
                self.add('Login incorrect', '', f'{HOST[self.system]} login:')
                self.state = 'message'
        elif self.state == 'session' and typed == 'exit':
            self.typed = ''
            self.state = 'message'
            if 'retry-silent' not in self.faults:
                self.prints.append(self.g())
                self.lines = self.message_screen()
            else:
                self.lines = ['']

    def eof(self):
        if self.display == 'std' and self.state == 'message':
            if 'eof-restarts' in self.faults:
                self.restarts += 1
            self.agreety += 1
            self.add('error: no input', *ISSUE, f'{HOST[self.system]} login:')


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='gfx-', dir=CACHE)
        self.base = Path(self.work.name)
        self.iso = self.base / 'isos/emaki-0.2.0-x86_64.iso'
        self.iso.parent.mkdir()
        self.iso.write_bytes(b'test image emaki.test=1\n')
        self.sha = hashlib.sha256(self.iso.read_bytes()).hexdigest()
        Path(f'{self.iso}.sha256').write_text(f'{self.sha}  {self.iso.name}\n')
        self.disk = self.base / 'accept/target.qcow2'
        self.disk.parent.mkdir()
        self.disk.write_bytes(b'qcow2 fixture\n')
        self.references = self.base / 'references'
        self.verify = self.base / 'verify-image.py'
        self.verify.write_text('import sys\nsys.exit(0 if "--test" in sys.argv else 1)\n')
        self.runs = 0

    def tearDown(self):
        self.work.cleanup()

    def run_check(self, *extra, faults=(), parts=('live-std-1920x1080',), disk=True, verify=None):
        self.runs += 1
        world = World(faults)
        out = self.base / f'out{self.runs}'
        argv = ['--iso', str(self.iso), '--out', str(out)]
        if disk:
            argv += ['--installed-disk', str(self.disk)]
        for part in parts:
            argv += ['--part', part]
        stdout = io.StringIO()
        with mock.patch.object(check, 'GUEST', world.factory), \
                mock.patch.object(check, 'now', world.clock.now), \
                mock.patch.object(check, 'sleep', world.clock.sleep), \
                mock.patch.object(check, 'VERIFY', verify or self.verify), \
                mock.patch.object(check, 'REFERENCES', self.references), \
                mock.patch.object(check, 'missing_prerequisites', lambda display, installed: None), \
                mock.patch.object(check, 'gl_lock', lambda: contextlib.nullcontext(True)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
            code = check.main(argv + list(extra))
        self.out, self.world, self.text = out, world, stdout.getvalue()
        self.lines = self.text.splitlines()
        return code

    def row(self, part, name):
        prefix = f'{part} {name}: '
        found = [line for line in self.lines if line.split(': ', 1)[-1].startswith(prefix)]
        self.assertEqual(len(found), 1, f'{part} {name}\n{self.text}')
        return found[0]

    def assert_row(self, part, name, word, text=''):
        line = self.row(part, name)
        self.assertTrue(line.startswith(word + ': '), line)
        self.assertIn(text, line)

    def assert_result(self, code, last):
        self.assertEqual(self.lines[-1], last, self.text)
        self.assertEqual(code, {'RESULT: SCRIPTS PASSED': 0}.get(last, 1 if last.startswith('RESULT: FAILED') else 3))
        self.assertEqual((self.out / 'PASS').exists(), code == 0)
        self.assertTrue((self.out / 'summary.txt').read_text().endswith(last + '\n'))

    def accept_reference(self, part, image=None, sha=None):
        """What a person does after reading the frame: copy it and record who accepted it."""
        self.references.mkdir(exist_ok=True)
        image = image or self.reference_source(part)
        target = self.references / f'{part}.png'
        shutil.copy(image, target)
        digest = sha or hashlib.sha256(target.read_bytes()).hexdigest()
        with (self.references / 'accepted.toml').open('a') as stream:
            stream.write(f'[{part}]\nfile = "{part}.png"\nsha256 = "{digest}"\naccepted_by = "a reviewer"\n'
                         f'accepted_on = "2026-10-05"\n')

    def reference_source(self, part):
        # A first run makes the candidate frame, as on the VM; it is not judged here.
        self.run_check(parts=(part,))
        return self.out / part / 'message.png'

    # -- the passing guest -----------------------------------------------------------------
    def test_passing_guest_passes_everything_but_the_missing_reference(self):
        code = self.run_check()
        part = 'live-std-1920x1080'
        for name in ('ssh', '(a)', '(b)', '(c)', '(d)', '(e) change', '(f)', 'login', 'retry', 'wrong', 'eof'):
            self.assert_row(part, name, 'PASS')
        self.assert_row(part, '(d)', 'PASS', '40.0 s')
        self.assert_row(part, '(e) reference', 'NOT TESTED', 'no reference')
        self.assertIn(f'tests/vm/fixtures/graphics-fallback/{part}.png', self.row(part, '(e) reference'))
        self.assertIn(f'SHOT: {self.out}/{part}/message.png (not judged)', self.text)
        self.assert_row('installed-std-1920x1080', '(a)', 'NOT TESTED', 'not selected')
        self.assert_result(code, f'RESULT: NOT TESTED at {part} (e) reference')
        self.assertIn(f'image: {self.iso} sha256 {self.sha}', self.text)
        guest = self.world.guests[0]
        self.assertTrue(guest.stopped)
        # The login, the retry, five wrong logins (root on the live image) and six Ctrl+D.
        typed = [k[1] for k in guest.keys_sent if isinstance(k, tuple)]
        self.assertEqual(typed[:2], ['live', 'exit'])
        self.assertEqual(typed[2:12], ['root', 'wrong-password-1', 'root', 'wrong-password-2', 'root',
                                       'wrong-password-3', 'root', 'wrong-password-4', 'root', 'wrong-password-5'])
        self.assertEqual(guest.keys_sent.count('ctrl-d'), 6)
        self.assertEqual(guest.agreety, 306)
        self.assertEqual(guest.prints, [40.0, guest.prints[1]])

    def test_every_part_passes_with_accepted_references(self):
        for part in check.PARTS:
            if '-std-' in part:
                self.accept_reference(part)
        code = self.run_check(parts=())
        for part in check.PARTS:
            for line in self.lines:
                if line.split(': ', 1)[-1].startswith(part + ' '):
                    self.assertTrue(line.startswith('PASS: '), line)
        self.assert_row('installed-std-2560x1600', '(e) reference', 'PASS', 'a reviewer')
        self.assert_row('installed-gl-1920x1080', 'login window', 'PASS', 'inside the 15 s window')
        self.assert_result(code, 'RESULT: SCRIPTS PASSED')
        installed = [g for g in self.world.guests if g.system == 'installed' and g.display == 'std']
        typed = [k[1] for k in installed[0].keys_sent if isinstance(k, tuple)]
        self.assertEqual(typed[:3], ['emaki', 'emaki-vm-test-only', 'exit'])
        self.assertEqual(typed[3:5], ['emaki', 'wrong-password-1'])

    # -- each fault the plan names ends FAILED with its reason --------------------------------
    def assert_fails(self, faults, name, reason, part='live-std-1920x1080'):
        code = self.run_check(faults=faults, parts=(part,))
        self.assert_row(part, name, 'FAIL', reason)
        self.assert_result(code, f'RESULT: FAILED at {part} {name}')
        return code

    def test_greetd_restarted(self):
        self.assert_fails({'restarted'}, '(a)', 'greetd restarted (NRestarts 1)')
        self.assert_row('live-std-1920x1080', 'login', 'NOT TESTED', 'not run')

    def test_vt_not_in_text_mode(self):
        self.assert_fails({'graphics'}, '(b)', 'tty1 is not in text mode (KDGETMODE 1)')

    def test_keyboard_off(self):
        self.assert_fails({'kb-off'}, '(b)', 'tty1 keyboard mode is K_OFF')

    def test_message_missing(self):
        self.assert_fails({'no-message'}, '(c)', 'message missing on tty1')

    def test_cause_line_missing(self):
        self.assert_fails({'no-cause'}, '(c)', 'cause line missing on tty1')

    def test_journal_line_missing(self):
        self.assert_fails({'no-fork-line'}, '(d)', 'journal line missing: "no primary GPU renderer')

    def test_message_later_than_90_seconds(self):
        self.assert_fails({'late'}, '(d)', 'message printed 95.0 s after boot (limit 90 s)')

    def test_frozen_frame(self):
        self.assert_fails({'frozen'}, '(e) change', 'frozen frame')
        self.assert_row('live-std-1920x1080', '(f)', 'PASS')

    def test_greetd_restart_during_the_hold(self):
        self.assert_fails({'restart-during-hold'}, '(f)', 'greetd restarted (NRestarts 1)')

    def test_exit_without_a_new_message(self):
        self.assert_fails({'retry-silent'}, 'retry', 'no second "showing graphics-failed message" line')

    def test_wrong_logins_that_restart_greetd(self):
        self.assert_fails({'wrong-restarts'}, 'wrong', 'greetd restarted (NRestarts 1)', part='installed-std-1920x1080')

    def test_ctrl_d_that_restarts_greetd(self):
        self.assert_fails({'eof-restarts'}, 'eof', 'greetd restarted (NRestarts 1)')

    def test_reference_that_does_not_match(self):
        part = 'live-std-2560x1600'
        other = self.base / 'other.png'
        Image.new('RGB', (2560, 1600), (90, 90, 90)).save(other)
        self.accept_reference(part, image=other)
        self.assert_fails((), '(e) reference', 'differ from the accepted reference', part=part)

    def test_reference_whose_file_was_changed(self):
        part = 'live-std-1920x1080'
        self.accept_reference(part, sha='0' * 64)
        self.assert_fails((), '(e) reference', 'does not match its recorded sha256', part=part)

    def test_gl_boot_that_shows_the_message(self):
        self.assert_fails({'gl-flash'}, 'no message', 'the wrapper printed the message', part='live-gl-1920x1080')

    def test_gl_login_after_the_window_is_not_tested(self):
        """When ssh answers late, the password reaches the greeter after the wrapper's 15 s window."""
        code = self.run_check(faults={'greeter-early'}, parts=('installed-gl-1920x1080',))
        part = 'installed-gl-1920x1080'
        self.assert_row(part, 'login', 'PASS')
        self.assert_row(part, 'login window', 'NOT TESTED', 'the 15 s window was not exercised')
        self.assert_row(part, 'no message', 'PASS')
        self.assert_result(code, 'RESULT: NOT TESTED at live-std-1920x1080 ssh')

    def test_gl_login_whose_first_password_is_lost(self):
        code = self.run_check(faults={'gl-login-lost'}, parts=('installed-gl-1920x1080',))
        self.assert_row('installed-gl-1920x1080', 'login', 'FAIL', 'no session of emaki')
        self.assertEqual(code, 1)

    # -- what the check refuses before any guest starts --------------------------------------
    def assert_refused(self, code, text):
        self.assertEqual(code, 2, self.text)
        self.assertIn(text, self.text)
        self.assertEqual(self.world.guests, [])

    def test_output_directory_is_never_reused(self):
        (self.base / 'out1').mkdir()
        self.assert_refused(self.run_check(), 'never reused')

    def test_image_that_does_not_match_its_checksum(self):
        Path(f'{self.iso}.sha256').write_text('0' * 64 + f'  {self.iso.name}\n')
        self.assert_refused(self.run_check(), 'does not match')

    def test_image_without_test_access(self):
        refusing = self.base / 'refuse.py'
        refusing.write_text('import sys\nsys.exit(1)\n')
        self.assert_refused(self.run_check(verify=refusing), 'verify-image.py --test refused')

    def test_output_path_too_long_for_sockets(self):
        long = self.base / ('x' * 90)
        code = self.run_check('--out', str(long))
        self.assert_refused(code, 'too long')

    def test_no_installed_disk_is_not_tested(self):
        code = self.run_check(disk=False, parts=('installed-std-1920x1080',))
        self.assert_row('installed-std-1920x1080', '(a)', 'NOT TESTED', 'no installed disk')
        self.assert_result(code, 'RESULT: NOT TESTED at live-std-1920x1080 ssh')


class GuestTests(unittest.TestCase):
    """The real Guest's commands, with subprocess replaced: nothing is started."""

    def guest(self, system, display='std', res='1920x1080'):
        directory = CACHE / 'gfx-guest'
        return check.Guest(directory, system=system, display=display, res=res, iso=Path('/i/emaki.iso'),
                           disk=Path('/d/target.qcow2') if system == 'installed' else None, port=2261,
                           account=ACCOUNT[system][0], password=ACCOUNT[system][1] or None)

    def test_qemu_command_line(self):
        std = self.guest('live').qemu_args()
        joined = ' '.join(std)
        self.assertIn('VGA,xres=1920,yres=1080,vgamem_mb=64', std)
        self.assertEqual(std[std.index('-display') + 1], 'none')
        self.assertNotIn('virtio-vga-gl', joined)
        self.assertIn('file=/i/emaki.iso', joined)
        self.assertIn('user,id=net0,hostfwd=tcp:127.0.0.1:2261-:22', std)
        self.assertNotRegex(joined, r'\bmodel\b')
        self.assertIn(f'unix:{CACHE}/gfx-guest/vnc.sock', std)
        gl = self.guest('installed', 'gl', '1920x1080').qemu_args()
        joined = ' '.join(gl)
        self.assertIn('virtio-vga-gl,xres=1920,yres=1080', gl)
        self.assertIn('egl-headless,rendernode=', joined)
        self.assertNotIn('emaki.iso', joined)
        # The installed disk is used through an overlay in the part directory, never written.
        self.assertIn(f'file={CACHE}/gfx-guest/overlay.qcow2,if=none,id=target,format=qcow2', gl)
        self.assertNotIn('file=/d/target.qcow2', joined)

    def test_snapshot_runs_the_helper_as_root_and_passes_no_password_in_argv(self):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs.get('input')))
            return subprocess.CompletedProcess(argv, 0, b'{"monotonic": 12.5}\n', b'')
        for system, sudo, data in (('live', 'sudo -n python3', b''),
                                   ('installed', 'sudo -k -S -p "" python3', b'emaki-vm-test-only\n')):
            calls.clear()
            with mock.patch.object(check.subprocess, 'run', run):
                snapshot = self.guest(system).snapshot()
            argv, given = calls[0]
            self.assertEqual(argv[:2], [str(VM / 'iso-ssh.sh'), '--dir'])
            self.assertIn('--ssh-port', argv)
            self.assertIn(ACCOUNT[system][0], argv)
            self.assertTrue(argv[-1].startswith(sudo), argv[-1])
            self.assertTrue(argv[-1].endswith('guest-graphics-state.py"'))
            self.assertEqual(given, data)
            self.assertNotIn('emaki-vm-test-only', ' '.join(argv))
            self.assertEqual(snapshot['monotonic'], 12.5)
            self.assertEqual(len(snapshot['host']), 2)


class HelperTests(unittest.TestCase):
    def test_screen_from_console_bytes(self):
        rows, cols = 3, 10
        text = 'Emaki'.ljust(cols) + ''.ljust(cols) + 'h login: '.ljust(cols)
        header = bytes([rows, cols, 9, 2])
        for width, cells in ((4, struct.pack(f'={rows * cols}I', *map(ord, text))), (1, text.encode())):
            screen = helper.parse_screen(header, cells, width)
            self.assertEqual(screen, {'rows': 3, 'cols': 10, 'x': 9, 'y': 2, 'lines': ['Emaki', '', 'h login:']})

    def test_journal_messages_as_text_or_bytes(self):
        lines = [json.dumps({'SYSLOG_IDENTIFIER': 'niri-emaki-session', '__MONOTONIC_TIMESTAMP': '41500000',
                             'MESSAGE': list(b'\x1b[31mERROR\x1b[0m niri: no primary GPU renderer (x)')}),
                 json.dumps({'SYSLOG_IDENTIFIER': 'emaki-greeter-compositor', '__MONOTONIC_TIMESTAMP': '42000000',
                             'MESSAGE': 'showing graphics-failed message (status 3)'}),
                 json.dumps({'SYSLOG_IDENTIFIER': 'emaki-greeter-compositor', '__MONOTONIC_TIMESTAMP': '1',
                             'MESSAGE': 'something else'}), '']
        found = helper.journal_entries(lines)
        self.assertEqual([(e['tag'], e['mono']) for e in found],
                         [('niri-emaki-session', 41.5), ('emaki-greeter-compositor', 42.0)])
        self.assertIn('no primary GPU renderer', found[0]['message'])


class SourceTests(unittest.TestCase):
    """The check's expected text is the text the system prints."""

    def test_message_lines_match_the_installed_files(self):
        self.assertEqual(check.MESSAGE_FIRST, MESSAGE[0])
        self.assertEqual(check.MESSAGE_LAST, MESSAGE[-1])
        wrapper = (ROOT / 'scripts/emaki-greeter-compositor').read_text()
        self.assertIn(f"'{check.CAUSE}'", wrapper)
        self.assertIn('echo "showing graphics-failed message (status $status)"', wrapper)
        self.assertEqual(check.WRAPPER_LINE, 'showing graphics-failed message (status 3)')
        patch = (ROOT / 'packaging/niri-emaki/0006-exit-without-primary-renderer.patch').read_text()
        self.assertIn(f'f.write_str("{check.FORK_LINE}")', patch)
        session = (ROOT / 'scripts/emaki-text-session').read_text()
        for line in check.TEXT_SESSION:
            self.assertIn(f"'{line}'", session)
        self.assertEqual(list(check.TEXT_SESSION), TEXT_SESSION[:2])


# The release gate's graphics-fallback job, with every script it starts replaced by a stub in a
# copied tree (as tests/test-release-gate.py does). STUB_RC_<name> sets a stub's exit status.
GATE_STUB = r'''#!/usr/bin/env bash
name=$(basename "$0")
printf '%s %s\n' "$name" "$*" >>"$STUB_LOG"
var=STUB_RC_$(printf '%s' "${name%.*}" | tr -c 'a-zA-Z0-9\n' _)
rc=${!var:-0}
case $name in
    iso-encrypt-check.sh) mkdir -p "$VMDIR/btrfs-$2"; : >"$VMDIR/btrfs-$2/PASS" ;;
    iso-alongside-check.sh) rc=${!var:-77}; mkdir -p "$VMDIR/btrfs-$2"; : >"$VMDIR/btrfs-$2/NOT-APPLICABLE" ;;
    bwrap) while (($#)) && [[ $1 != -- ]]; do shift; done; shift; exec "$@" ;;
esac
exit "$rc"
'''
GATE_PYTHON_STUBS = {
    'iso/verify-image.py': r'''import sys
test = '--test' in sys.argv
sys.exit(0 if (b'emaki.test=1' in open(sys.argv[1], 'rb').read()) == test else 1)
''',
    'tests/vm/eyes/eyes-gate.py': 'print("RESULT: PASSED (walk record)")\n',
    'tests/vm/check-graphics-fallback.py': r'''import os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write('check-graphics-fallback.py ' + ' '.join(sys.argv[1:]) + '\n')
rc = int(os.environ.get('STUB_RC_check', '0'))
out = sys.argv[sys.argv.index('--out') + 1]
os.makedirs(out)
if rc == 0 and not os.environ.get('STUB_NO_PASS'):
    open(os.path.join(out, 'PASS'), 'w').close()
print({0: 'RESULT: SCRIPTS PASSED', 3: 'RESULT: NOT TESTED at live-std-1920x1080 (e) reference'}.get(
    rc, 'RESULT: FAILED at live-std-1920x1080 (c)'))
sys.exit(rc)
''',
}


class ReleaseGateJob(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='gfx-gate-', dir=CACHE)
        self.base = Path(self.work.name)
        self.repo = self.base / 'r'
        vm = self.repo / 'tests/vm'
        (vm / 'eyes').mkdir(parents=True)
        (self.repo / 'iso').mkdir()
        for name in ('release-gate.sh', 'jail.sh'):
            shutil.copy(VM / name, vm / name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        stubs = [vm / n for n in ('run-iso.sh', 'iso-wait-ssh.sh', 'iso-install.sh', 'iso-stop.sh',
                                  'iso-boot-check.sh', 'iso-encrypt-check.sh', 'iso-alongside-check.sh')]
        for path in stubs + [self.bin / 'make', self.bin / 'bwrap']:
            path.write_text(GATE_STUB)
            path.chmod(0o755)
        for name, text in GATE_PYTHON_STUBS.items():
            (self.repo / name).write_text(text)
        self.log = self.base / 'calls.log'
        self.log.touch()
        self.release = self.image('release', b'release image\n')
        self.test = self.image('test', b'test image emaki.test=1\n')
        self.walk = self.base / 'walk'
        self.walk.mkdir()

    def tearDown(self):
        self.work.cleanup()

    def image(self, folder, content):
        path = self.base / 'isos' / folder / 'emaki-0.2.0-x86_64.iso'
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        Path(f'{path}.sha256').write_text(f'{hashlib.sha256(content).hexdigest()}  {path.name}\n')
        return path

    def gate(self, *, no_pass=False, **rcs):
        env = dict(os.environ, HOME=str(self.base / 'home'), STUB_LOG=str(self.log),
                   PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}')
        env.update({f'STUB_RC_{name}': str(rc) for name, rc in rcs.items()})
        if no_pass:
            env['STUB_NO_PASS'] = '1'
        self.out = self.base / 'out'
        result = subprocess.run(['bash', str(self.repo / 'tests/vm/release-gate.sh'), '--release-iso', str(self.release),
                                 '--test-iso', str(self.test), '--out', str(self.out), '--walk', str(self.walk)],
                                env=env, text=True, capture_output=True, timeout=120)
        self.assertNotRegex(result.stdout, r'(?i)\bOK\b|\bDONE\b|verified|accepted')
        self.calls = [c for c in self.log.read_text().splitlines() if c.startswith('check-graphics-fallback.py')]
        self.job = next(line for line in result.stdout.splitlines() if line.startswith('graphics-fallback  '))
        return result

    def test_passing_check_passes_on_the_test_image_with_the_installed_disk(self):
        result = self.gate()
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: SCRIPTS PASSED', result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertRegex(self.job, r'\bPASS\b')
        sha = hashlib.sha256(self.test.read_bytes()).hexdigest()
        self.assertIn(f'graphics-fallback: PASS - ', result.stdout)
        self.assertIn(f'on test image {self.test} sha256 {sha}', result.stdout)
        self.assertEqual(self.calls, [f'check-graphics-fallback.py --iso {self.test} --out {self.out}/graphics-fallback '
                                      f'--ssh-port 2261 --installed-disk {self.out}/accept-erase-btrfs/target.qcow2 '
                                      f'--fixture erase-btrfs'])

    def test_failing_check_fails_the_gate(self):
        result = self.gate(check=1)
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: FAILED at graphics-fallback', result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertIn('exit 1: RESULT: FAILED at live-std-1920x1080 (c)', self.job)

    def test_untested_check_is_not_tested(self):
        result = self.gate(check=3)
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: NOT TESTED at graphics-fallback', result.stdout)
        self.assertEqual(result.returncode, 3)
        self.assertIn('NOT TESTED', self.job)
        self.assertIn('(e) reference', self.job)

    def test_exit_zero_without_the_pass_file_is_a_failure(self):
        result = self.gate(no_pass=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('no PASS file', self.job)

    def test_without_a_passed_install_the_check_gets_no_disk(self):
        result = self.gate(iso_boot_check=1)
        self.assertEqual(result.stdout.splitlines()[-1], 'RESULT: FAILED at accept-erase-btrfs', result.stdout)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('--installed-disk', self.calls[0])

    def test_unusable_test_image_runs_no_check(self):
        Path(f'{self.test}.sha256').write_text('0' * 64 + f'  {self.test.name}\n')
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls, [])
        self.assertIn('NOT TESTED', self.job)

    def test_shellcheck(self):
        if not shutil.which('shellcheck'):
            self.skipTest('shellcheck is not installed')
        subprocess.run(['shellcheck', '-x', str(VM / 'release-gate.sh')], check=True)


if __name__ == '__main__':
    unittest.main()
