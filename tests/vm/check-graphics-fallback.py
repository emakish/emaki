#!/usr/bin/env python3
"""HOST ONLY: graphics fallback check on a TEST image (no-GL and GL boots, live and installed).

Boots its own QEMU guests one after another and checks the text screen that
emaki-greeter-compositor shows when niri-emaki cannot draw (patch 0006 exits with status 3).
Input is QMP send-key, pictures are VNC grabs of the real scan-out (tests/vm/eyes/eyes.py),
state comes over test-mode ssh from tests/vm/guest-graphics-state.py run as root.

  python3 tests/vm/check-graphics-fallback.py --iso <test iso> --out <new dir> \\
      [--installed-disk <installed qcow2>] [--fixture erase-btrfs] [--ssh-port 2261] [--part NAME ...]

Parts (each its own boot; about 40 minutes for all):
  live-std-1920x1080, live-std-2560x1600, installed-std-1920x1080, installed-std-2560x1600
      std VGA, no GL. Within 90 s of boot (guest clock) all of:
      (a) greetd active, NRestarts 0 (and one InvocationID for the rest of the part)
      (b) tty1 active, KDGETMODE = KD_TEXT, KDGKBMODE = K_XLATE or K_UNICODE
      (c) tty1 starts with the message's first line, has the cause line, ends with "login: "
      (d) this boot's journal has the fork's "no primary GPU renderer" line and the wrapper's
          "showing graphics-failed message (status 3)" line
      (e) change: the screen differs from the last frame grabbed at least 1 s before the
          wrapper's journal line (a frozen boot log fails); reference: its message rows match
          the frame a person accepted for this part (NOT TESTED until one is committed)
      (f) 300 s later (a)-(c) still hold
      login: the account at "login: " -> the text session's lines and a shell prompt
      retry: "exit" -> a second wrapper line, the message and "login: " again
      wrong: five wrong logins (root on the live image) -> "Login incorrect" each time
      eof: six Ctrl+D at "login: " within 20 s -> agreety starts again each time
      greetd as in (a) after each of these.
  live-gl-1920x1080, installed-gl-1920x1080
      virtio-vga-gl: the session (live) or the greeter (installed) runs, no wrapper line, no
      fork line, no message on tty1, greetd as in (a); on the installed disk the password is
      typed as soon as the greeter shows (inside the wrapper's 15 s window) and the session
      must start without the message.

The installed parts boot a copy-on-write overlay of --installed-disk (an install made from
the same test image, for example by the release gate's accept-erase-btrfs job) with the
OVMF_VARS.4m.fd found next to it; the disk itself is never written. Without --installed-disk
those parts are NOT TESTED.

Each check prints one line: PASS, FAIL or NOT TESTED, never mixed. Pictures are SHOT lines and
are never a pass. The last line is "RESULT: SCRIPTS PASSED" (exit 0, writes OUT/PASS),
"RESULT: FAILED at <part> <check>" (exit 1) or "RESULT: NOT TESTED at <part> <check>" (exit 3).
Refusals before any guest starts exit 2. OUT must not exist; it is never reused.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import tomllib

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import socket_runtime
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE / 'eyes'))
import eyes  # noqa: E402  (QMP keys and VNC grabs of a guest whose sockets live in one directory)
from PIL import Image, ImageChops  # noqa: E402

# What the system prints (greetd/graphics-failed.txt, scripts/emaki-greeter-compositor,
# scripts/emaki-text-session, niri-emaki patch 0006); tests/test-graphics-fallback-check.py
# keeps these equal to those files.
MESSAGE_FIRST = 'Emaki could not start its graphical desktop.'
MESSAGE_LAST = 'Log in below for a text console. Type "exit" there to try graphics again.'
CAUSE = 'Cause: no hardware 3D renderer was found.'
TEXT_SESSION = ('Text session - the graphical desktop did not start.',
                'Type "exit" to try the graphical login again.')
FORK_LINE = 'no primary GPU renderer (software EGL rejected or driver failed)'
WRAPPER_LINE = 'showing graphics-failed message (status 3)'
WRAPPER_TAG = 'emaki-greeter-compositor'

KD_TEXT = 0
KB_NAMES = {0: 'K_RAW', 1: 'K_XLATE', 2: 'K_MEDIUMRAW', 3: 'K_UNICODE', 4: 'K_OFF'}
KB_WORKING = (1, 3)

PARTS = ('live-std-1920x1080', 'live-std-2560x1600', 'installed-std-1920x1080', 'installed-std-2560x1600',
         'live-gl-1920x1080', 'installed-gl-1920x1080')
STD_CHECKS = ('ssh', '(a)', '(b)', '(c)', '(d)', '(e) change', '(e) reference', '(f)', 'login', 'retry',
              'wrong', 'eof')
GL_CHECKS = {'live': ('ssh', 'graphics', 'no message'),
             'installed': ('ssh', 'graphics', 'login', 'login window', 'no message')}

MESSAGE_DEADLINE = 90.0   # guest seconds since boot (plan section 6: "within 90 s")
POLL_UNTIL = 150.0        # guest seconds: keep looking after the deadline to tell late from never
HOLD = 300.0              # (f)
SSH_TIMEOUT = 300.0       # host seconds from the QEMU start
BEFORE_MARGIN = 1.0       # the boot-log frame is grabbed at least this long before the wrapper's line
PIXEL_STEP = 16           # a pixel differs when one channel differs by more than this
CHANGED_SHARE = 0.001     # (e) change: more than 0.1 % of the pixels (a blinking cursor is far less)
REFERENCE_SHARE = 0.005   # (e) reference: at most 0.5 % of the message-row pixels may differ
SETTLE = 20.0             # GL: the compositor has run longer than the wrapper's 15 s window
WINDOW = 15.0             # the wrapper treats a non-zero exit within 15 s as a failure
EOF_PACE = 20.0           # six Ctrl+D within this many seconds

HELPER = HERE / 'guest-graphics-state.py'
VERIFY = ROOT / 'iso/verify-image.py'
REFERENCES = HERE / 'fixtures/graphics-fallback'
REFERENCE_DOC = 'tests/vm/fixtures/graphics-fallback'
OVMF = Path(os.environ.get('EYES_OVMF_DIR', '/usr/share/edk2/x64'))
RENDER = os.environ.get('EYES_RENDER_NODE', '/dev/dri/renderD128')
GL_LOCK = Path(os.environ.get('EYES_ROOT', Path.home() / 'VMs/eyes')) / '.gl.lock'

now = time.monotonic
sleep = time.sleep


class Guest:
    """One QEMU guest of this check, started and stopped here; its sockets have links in its evidence directory."""

    def __init__(self, directory, *, system, display, res, iso, disk, port, account, password):
        self.dir = Path(directory)
        self.system, self.display, self.res = system, display, res
        self.iso, self.disk, self.port = iso, disk, port
        self.account, self.password = account, password
        self.process = None
        self.vm = None

    def qemu_args(self):
        x, y = self.res.split('x')
        d = self.dir
        args = ['qemu-system-x86_64', '-machine', 'q35', '-enable-kvm', '-cpu', 'host', '-smp', '4', '-m', '4G',
                '-drive', f'if=pflash,format=raw,readonly=on,file={OVMF / "OVMF_CODE.4m.fd"}',
                '-drive', f'if=pflash,format=raw,file={d / "OVMF_VARS.4m.fd"}']
        if self.disk:
            args += ['-drive', f'file={d / "overlay.qcow2"},if=none,id=target,format=qcow2',
                     '-device', 'virtio-blk-pci,drive=target,serial=emaki-target,bootindex=1']
        else:
            args += ['-drive', f'if=none,id=cd,media=cdrom,readonly=on,format=raw,file={self.iso}',
                     '-device', 'ide-cd,drive=cd,bootindex=1']
        if self.display == 'std':
            args += ['-device', f'VGA,xres={x},yres={y},vgamem_mb=64', '-display', 'none']
        else:
            args += ['-device', f'virtio-vga-gl,xres={x},yres={y}', '-display', f'egl-headless,rendernode={RENDER}']
        return args + ['-vnc', f'unix:{socket_runtime.runtime(d) / "vnc.sock"}',
                       '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{self.port}-:22',
                       '-device', 'virtio-net-pci,netdev=net0',
                       '-qmp', f'unix:{socket_runtime.runtime(d) / "qmp.sock"},server=on,wait=off',
                       '-serial', f'file:{d / "serial.log"}', '-pidfile', str(d / 'qemu.pid')]

    def start(self):
        self.dir.mkdir()
        variables = OVMF / 'OVMF_VARS.4m.fd'
        if self.disk:
            beside = Path(self.disk).parent / 'OVMF_VARS.4m.fd'
            if beside.is_file():
                variables = beside  # the boot entries the installer registered
            subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-b', str(self.disk), '-F', 'qcow2',
                            str(self.dir / 'overlay.qcow2')], check=True, capture_output=True)
        shutil.copyfile(variables, self.dir / 'OVMF_VARS.4m.fd')
        (self.dir / 'res').write_text(self.res + '\n')
        args = self.qemu_args()
        (self.dir / 'qemu-cmdline.txt').write_text(shlex.join(args) + '\n')
        with open(self.dir / 'qemu.log', 'ab') as log:
            self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                            start_new_session=True)
        deadline = now() + 30
        while not (self.dir / 'qmp.sock').exists():
            if self.process.poll() is not None:
                raise RuntimeError(f'QEMU exited with status {self.process.returncode} (log {self.dir / "qemu.log"})')
            if now() > deadline:
                raise RuntimeError('QEMU opened no QMP socket within 30 s')
            sleep(0.2)
        self.vm = eyes.Pass(self.dir)

    def stop(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            if self.vm is not None:
                with contextlib.suppress(OSError, RuntimeError):
                    self.vm.cmd('quit')
            try:
                self.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)
        if self.vm is not None:
            self.vm.close()
        socket_runtime.cleanup(self.dir)

    def grab(self):
        return self.vm.grab()[0]

    def keys(self, *combos):
        self.vm.keys(*combos)

    def type_text(self, text):
        self.vm.type_text(text)

    def ssh(self, command, data=b'', timeout=60):
        argv = [str(HERE / 'iso-ssh.sh'), '--dir', str(self.dir), '--ssh-port', str(self.port),
                '--user', self.account, '--', command]
        return subprocess.run(argv, input=data, capture_output=True, timeout=timeout)

    def ssh_ready(self):
        try:
            return self.ssh('true', timeout=30).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def upload_helper(self):
        result = self.ssh(f'umask 077; cat > "$HOME/{HELPER.name}"', data=HELPER.read_bytes())
        if result.returncode != 0:
            raise RuntimeError(f'helper upload failed: {tail(result.stderr)}')

    def snapshot(self):
        if self.password is None:
            sudo, data = 'sudo -n', b''
        else:
            sudo, data = 'sudo -k -S -p ""', (self.password + '\n').encode()
        started = now()
        result = self.ssh(f'{sudo} python3 "$HOME/{HELPER.name}"', data=data)
        ended = now()
        if result.returncode != 0:
            raise RuntimeError(f'guest helper exit {result.returncode}: {tail(result.stderr)}')
        snap = json.loads(result.stdout)
        snap['host'] = [started, ended]
        return snap


GUEST = Guest


def tail(data):
    lines = data.decode(errors='replace').strip().splitlines() if data else []
    return lines[-1] if lines else 'no output'


class Results:
    """One row per check of every part; every line also goes to OUT/summary.txt."""

    def __init__(self, out):
        self.out = out
        self.rows = []

    def line(self, text):
        print(text, flush=True)
        with (self.out / 'summary.txt').open('a') as stream:
            stream.write(text + '\n')

    def add(self, part, name, word, detail):
        self.rows.append((part, name, word))
        self.line(f'{word}: {part} {name}: {detail}')


class Part:
    def __init__(self, name, results, out):
        self.name = name
        self.system, self.display, self.res = name.split('-')
        self.results = results
        self.dir = out / name
        self.checks = STD_CHECKS if self.display == 'std' else GL_CHECKS[self.system]
        self.done = []

    def record(self, name, word, detail):
        assert name in self.checks and name not in self.done, name
        self.done.append(name)
        self.results.add(self.name, name, word, detail)

    def passed(self, name, detail):
        self.record(name, 'PASS', detail)

    def failed(self, name, detail):
        self.record(name, 'FAIL', detail)

    def untested(self, name, detail):
        self.record(name, 'NOT TESTED', detail)

    def rest(self, reason):
        for name in self.checks:
            if name not in self.done:
                self.untested(name, reason)

    def shot(self, guest, filename, what):
        path = self.dir / filename
        try:
            guest.grab().save(path)
        except (OSError, RuntimeError) as error:
            self.results.line(f'no picture {path}: the grab failed ({error}); {what}')
            return
        self.results.line(f'SHOT: {path} (not judged); {what}')


class Frames:
    """Frames grabbed while the check waits; one is kept on disk each time the screen changes."""

    def __init__(self, directory):
        self.dir = directory
        self.kept = []  # (host time the grab returned, path)
        self.last = None

    def grab(self, guest):
        try:
            img = guest.grab()
        except (OSError, RuntimeError):
            return
        at = now()
        digest = hashlib.sha256(img.tobytes()).hexdigest()
        if digest == self.last:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f'{len(self.kept):03d}.png'
        img.save(path, compress_level=1)
        self.kept.append((at, path))
        self.last = digest
        with (self.dir / 'frames.jsonl').open('a') as stream:
            stream.write(json.dumps({'host': at, 'file': path.name, 'sha256': digest}) + '\n')

    def before(self, guest_time, probes):
        """The last kept frame grabbed while the guest clock was surely below guest_time.

        Each probe (host t0, host t1, guest g) says the guest read g between t0 and t1, so the
        host-minus-guest offset is at least t0 - g; host instant h is then at most h - offset.
        """
        if not probes:
            return None
        offset = max(t0 - g for t0, _t1, g in probes)
        found = [(at, path) for at, path in self.kept if at - offset < guest_time]
        return found[-1] if found else None


def changed_pixels(a, b):
    if a.size != b.size:
        return a.size[0] * a.size[1]
    red, green, blue = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).split()
    most = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return most.point(lambda value: 255 if value > PIXEL_STEP else 0).histogram()[255]


def lit(guest):
    """The screen shows something other than black (a failed grab counts as black)."""
    try:
        return eyes.stats(guest.grab())[1] > eyes.BLACK
    except (OSError, RuntimeError):
        return False


# -- what a snapshot shows ------------------------------------------------------------------
def row_text(screen, y):
    lines = screen['lines']
    return lines[y] if 0 <= y < len(lines) else ''


def prompt_problem(screen, ending='login:'):
    y, x = screen['y'], screen['x']
    row = row_text(screen, y)
    if not row.endswith(ending) or x != len(row) + 1 or any(screen['lines'][y + 1:]):
        return f'tty1 does not end with "{ending} " (cursor row {row[-60:]!r}, column {x})'
    return None


def message_problem(screen):
    first = row_text(screen, 0)
    if first != MESSAGE_FIRST:
        return f'message missing on tty1 (first row {first[:60]!r})'
    if CAUSE not in screen['lines']:
        return 'cause line missing on tty1'
    return prompt_problem(screen)


def text_session_problem(screen):
    lines = screen['lines']
    if not all(line in lines for line in TEXT_SESSION):
        return 'the text session lines are not on tty1'
    y, x = screen['y'], screen['x']
    row = row_text(screen, y)
    if not row or row[-1] not in '$#%>' or x != len(row) + 1 or lines.index(TEXT_SESSION[0]) > y:
        return f'no shell prompt below the text session lines (cursor row {row[-60:]!r})'
    return None


def asks_password(snap):
    return prompt_problem(snap['screen'], 'Password:') is None


def greetd_problem(snap, invocation=None):
    unit = snap['greetd']
    if unit.get('ActiveState') != 'active':
        return f'greetd is {unit.get("ActiveState")}/{unit.get("SubState")}'
    if unit.get('NRestarts') != '0':
        return f'greetd restarted (NRestarts {unit.get("NRestarts")})'
    if invocation and unit.get('InvocationID') != invocation:
        return 'greetd was started again (another InvocationID)'
    return None


def vt_problem(snap):
    if snap['active_vt'] != 'tty1':
        return f'the active VT is {snap["active_vt"]}, not tty1'
    if snap['kd_mode'] != KD_TEXT:
        return f'tty1 is not in text mode (KDGETMODE {snap["kd_mode"]})'
    if snap['kb_mode'] not in KB_WORKING:
        return f'tty1 keyboard mode is {KB_NAMES.get(snap["kb_mode"], snap["kb_mode"])}'
    return None


def wrapper_lines(snap):
    return [entry for entry in snap['journal'] if entry['tag'] == WRAPPER_TAG and WRAPPER_LINE in entry['message']]


def fork_lines(snap):
    return [entry for entry in snap['journal'] if FORK_LINE in entry['message']]


def journal_problem(snap):
    if not fork_lines(snap):
        return f'journal line missing: "{FORK_LINE}" (niri-emaki patch 0006)'
    if not wrapper_lines(snap):
        return f'journal line missing: "{WRAPPER_LINE}" from {WRAPPER_TAG}'
    return None


def std_problems(snap, invocation=None, letters='abcd'):
    found = {'(a)': greetd_problem(snap, invocation), '(b)': vt_problem(snap),
             '(c)': message_problem(snap['screen']), '(d)': journal_problem(snap)}
    return {name: reason for name, reason in found.items() if name[1] in letters and reason}


def process_age(snap, name, user):
    ages = [entry['age'] for entry in snap['processes'] if entry['name'] == name and entry['user'] == user]
    return max(ages) if ages else -1.0


def agreety_pids(snap):
    return {entry['pid'] for entry in snap['processes'] if entry['name'] == 'agreety'}


# -- waiting --------------------------------------------------------------------------------
def take(guest, part):
    snap = guest.snapshot()
    with (part.dir / 'snapshots.jsonl').open('a') as stream:
        stream.write(json.dumps(snap) + '\n')
    return snap


def wait_for(guest, part, done, timeout):
    deadline = now() + timeout
    while True:
        snap = take(guest, part)
        if done(snap):
            return snap, True
        if now() >= deadline:
            return snap, False
        sleep(1)


def wait_ssh(guest, part, frames):
    deadline = now() + SSH_TIMEOUT
    while True:
        frames.grab(guest)
        if guest.ssh_ready():
            part.passed('ssh', f'{guest.account} answers over test-mode ssh')
            guest.upload_helper()
            return True
        if now() >= deadline:
            part.failed('ssh', f'test-mode ssh did not answer within {SSH_TIMEOUT:.0f} s '
                               f'(serial log {part.dir / "serial.log"})')
            part.rest('not run: no ssh')
            return False
        sleep(2)


# -- the parts ------------------------------------------------------------------------------
def run_std(part, guest):
    frames = Frames(part.dir / 'frames')
    guest.start()
    if not wait_ssh(guest, part, frames):
        return
    probes = []
    while True:
        frames.grab(guest)
        snap = take(guest, part)
        probes.append((snap['host'][0], snap['host'][1], snap['monotonic']))
        problems = std_problems(snap)
        if not problems or snap['monotonic'] >= POLL_UNTIL:
            break
        sleep(1)
    held_at = now()
    if problems:
        for name in ('(a)', '(b)', '(c)', '(d)'):
            if name in problems:
                part.failed(name, f'{problems[name]} (at {snap["monotonic"]:.0f} s of guest uptime)')
            else:
                part.passed(name, 'held in the last snapshot')
        part.rest('not run: ' + ', '.join(problems) + ' failed')
        return
    invocation = snap['greetd'].get('InvocationID')
    line = wrapper_lines(snap)[0]
    part.passed('(a)', 'greetd active, NRestarts 0')
    part.passed('(b)', f'tty1 active, KD_TEXT, keyboard {KB_NAMES[snap["kb_mode"]]}')
    part.passed('(c)', 'tty1: the message from the top row, the cause line, a "login: " prompt')
    if line['mono'] > MESSAGE_DEADLINE:
        part.failed('(d)', f'message printed {line["mono"]:.1f} s after boot (limit {MESSAGE_DEADLINE:.0f} s)')
    else:
        part.passed('(d)', f'fork and wrapper lines in the journal; message printed {line["mono"]:.1f} s after boot')

    sleep(2)
    final = guest.grab().convert('RGB')
    picture = part.dir / 'message.png'
    final.save(picture)
    part.results.line(f'SHOT: {picture} (not judged); S1/S3: the failure message')
    before = frames.before(line['mono'] - BEFORE_MARGIN, probes)
    if before is None:
        part.untested('(e) change', f'no frame was grabbed at least {BEFORE_MARGIN:.0f} s before the message')
    else:
        changed = changed_pixels(final, Image.open(before[1]).convert('RGB'))
        share = changed / (final.width * final.height)
        if share <= CHANGED_SHARE:
            part.failed('(e) change', f'frozen frame: the screen after the message equals {before[1]}, grabbed '
                                      f'before it ({changed} pixels differ)')
        else:
            part.passed('(e) change', f'{share:.1%} of the pixels differ from {before[1]}, the last frame '
                                      f'grabbed before the message')
    part.record('(e) reference', *reference_result(part, final, snap['screen']))

    sleep(max(0.0, held_at + HOLD - now()))
    snap = take(guest, part)
    problems = std_problems(snap, invocation, 'abc')
    if problems:
        part.failed('(f)', f'after {HOLD:.0f} s: ' + '; '.join(problems.values()))
    else:
        part.passed('(f)', f'after {HOLD:.0f} s (a)-(c) still hold')
    part.shot(guest, 'message-t300.png', 'S2: the message 300 s later')

    if not interactive(part, guest, snap, invocation):
        part.rest('not run: an earlier step of this part failed')


def interactive(part, guest, snap, invocation):
    user, password = guest.account, guest.password or ''
    guest.type_text(user)
    guest.keys('ret')
    snap, _ = wait_for(guest, part, lambda s: asks_password(s) or not text_session_problem(s['screen']), 30)
    if asks_password(snap):
        if password:
            guest.type_text(password)
        guest.keys('ret')
        snap, _ = wait_for(guest, part, lambda s: not text_session_problem(s['screen']), 30)
    problem = text_session_problem(snap['screen'])
    if problem:
        part.failed('login', f'after logging in as {user}: {problem}')
        return False
    part.passed('login', f'logged in as {user}: the text session lines and a shell prompt on tty1')
    part.shot(guest, 'text-session.png', 'S4: the text session')

    count = len(wrapper_lines(snap))

    def retried(s):
        return len(wrapper_lines(s)) == count + 1 and not std_problems(s, invocation, 'abc')
    guest.type_text('exit')
    guest.keys('ret')
    snap, ok = wait_for(guest, part, retried, 30)
    if not ok:
        reasons = list(std_problems(snap, invocation, 'abc').values())
        if len(wrapper_lines(snap)) != count + 1:
            reasons.insert(0, 'no second "showing graphics-failed message" line in the journal')
        part.failed('retry', 'after exit: ' + '; '.join(reasons))
        return False
    part.passed('retry', 'exit: graphics tried again; the message and "login: " again, greetd unchanged')
    part.shot(guest, 'retry.png', 'S4b: the message after exit')

    wrong_user = 'root' if part.system == 'live' else user

    def wrong_problem(s):
        lines, y = s['screen']['lines'], s['screen']['y']
        return (greetd_problem(s, invocation) or prompt_problem(s['screen'])
                or (None if y >= 2 and lines[y - 2] == 'Login incorrect' else 'no "Login incorrect" above the prompt'))
    for attempt in range(1, 6):
        guest.type_text(wrong_user)
        guest.keys('ret')
        snap, ok = wait_for(guest, part, lambda s: asks_password(s) or greetd_problem(s, invocation), 15)
        if not ok or not asks_password(snap):
            part.failed('wrong', f'attempt {attempt}: no "Password:" prompt after {wrong_user} '
                                 f'({greetd_problem(snap, invocation) or prompt_problem(snap["screen"], "Password:")})')
            return False
        guest.type_text(f'wrong-password-{attempt}')
        guest.keys('ret')
        snap, ok = wait_for(guest, part, lambda s: wrong_problem(s) is None, 20)
        if not ok:
            part.failed('wrong', f'attempt {attempt}: {wrong_problem(snap)}')
            return False
    part.passed('wrong', f'five wrong logins as {wrong_user}: "Login incorrect" each time, greetd active with '
                         'NRestarts 0, a "login: " prompt')
    part.shot(guest, 'wrong.png', 'S4c: after five wrong logins')

    def eof_problem(s, before):
        pids = agreety_pids(s)
        return (greetd_problem(s, invocation) or prompt_problem(s['screen'])
                or (None if pids and not pids & before else 'agreety did not start again'))
    started = now()
    for press in range(1, 7):
        before = agreety_pids(snap)
        guest.keys('ctrl-d')
        snap, ok = wait_for(guest, part, lambda s: eof_problem(s, before) is None, 10)
        if not ok:
            part.failed('eof', f'Ctrl+D {press}: {eof_problem(snap, before)}')
            return False
    took = now() - started
    if took > EOF_PACE:
        part.untested('eof', f'the six Ctrl+D took {took:.0f} s; the plan asks for six within {EOF_PACE:.0f} s')
    else:
        part.passed('eof', f'six Ctrl+D at "login: " in {took:.0f} s: agreety started again each time, greetd '
                           'active with NRestarts 0, a "login: " prompt')
    part.shot(guest, 'eof.png', 'S4c: after six Ctrl+D')
    return True


def run_gl(part, guest):
    frames = Frames(part.dir / 'frames')
    guest.start()
    if not wait_ssh(guest, part, frames):
        return
    invocation = take(guest, part)['greetd'].get('InvocationID')
    if part.system == 'live':
        snap, ok = wait_for(guest, part, lambda s: process_age(s, 'niri-emaki', 'live') >= SETTLE, 180)
        if not ok:
            part.failed('graphics', f'no niri-emaki of live running for {SETTLE:.0f} s within 180 s')
            part.rest('not run: no live session')
            return
        part.passed('graphics', f'niri-emaki of live running for {process_age(snap, "niri-emaki", "live"):.0f} s')
    else:
        account = guest.account
        snap, ok = wait_for(guest, part, lambda s: process_age(s, 'qs', 'greeter') >= 3
                            and process_age(s, 'niri-emaki', 'greeter') >= 0, 180)
        if not ok:
            part.failed('graphics', 'no greeter (niri-emaki and qs of greeter) within 180 s')
            part.rest('not run: no greeter')
            return
        part.passed('graphics', 'the greeter runs (niri-emaki and qs of greeter)')
        deadline = now() + 20
        while now() < deadline and not lit(guest):
            sleep(0.5)
        age = process_age(snap, 'niri-emaki', 'greeter') + (now() - snap['host'][1])
        for attempt in (1, 2):
            guest.type_text(guest.password)
            guest.keys('ret')
            snap, ok = wait_for(guest, part, lambda s: process_age(s, 'niri-emaki', account) >= 0, 30)
            if ok:
                break
        if not ok:
            part.failed('login', f'no session of {account} (niri-emaki of {account}) within 30 s after each of '
                                 'two password attempts at the greeter')
            part.rest('not run: no graphical login')
            return
        part.passed('login', f'graphical login: niri-emaki of {account} runs')
        if attempt == 1 and age <= WINDOW:
            part.passed('login window', f'the password was sent {age:.0f} s after the greeter compositor started '
                                        f'(inside the {WINDOW:.0f} s window)')
        else:
            part.untested('login window', f'attempt {attempt}, {age:.0f} s after the greeter compositor started: '
                                          f'the {WINDOW:.0f} s window was not exercised')
        snap, _ = wait_for(guest, part, lambda s: process_age(s, 'niri-emaki', account) >= SETTLE, 60)
    reasons = []
    if wrapper_lines(snap):
        reasons.append(f'the wrapper printed the message on a GL boot (journal line at '
                       f'{wrapper_lines(snap)[0]["mono"]:.1f} s)')
    if fork_lines(snap):
        reasons.append('niri-emaki logged "no primary GPU renderer" on a GL boot')
    if MESSAGE_FIRST in snap['screen']['lines']:
        reasons.append('the message is in the text of tty1')
    reasons.append(greetd_problem(snap, invocation))
    reasons = [reason for reason in reasons if reason]
    if reasons:
        part.failed('no message', '; '.join(reasons))
    else:
        part.passed('no message', 'no wrapper or fork line in the journal, no message on tty1, greetd active with '
                                  'NRestarts 0')
    part.shot(guest, 'screen.png', 'S5: the GL boot')


def reference_result(part, final, screen):
    key = part.name
    how = (f'no reference a person accepted for {key}: read {part.dir / "message.png"}; if it shows the whole '
           f'message legibly, copy it to {REFERENCE_DOC}/{key}.png and add [{key}] (file, sha256, accepted_by, '
           f'accepted_on) to {REFERENCE_DOC}/accepted.toml, see tests/vm/README.md "Graphics fallback check"')
    record = REFERENCES / 'accepted.toml'
    entry = tomllib.loads(record.read_text()).get(key) if record.is_file() else None
    if not entry:
        return 'NOT TESTED', how
    path = REFERENCES / str(entry.get('file', ''))
    if not entry.get('accepted_by') or not path.is_file():
        return 'FAIL', f'reference record [{key}] in {record} names no person or no existing file'
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('sha256'):
        return 'FAIL', f'{path} does not match its recorded sha256'
    if MESSAGE_LAST not in screen['lines']:
        return 'FAIL', 'the last message line is not on tty1'
    reference = Image.open(path).convert('RGB')
    if reference.size != final.size:
        return 'FAIL', f'{path} is {reference.size[0]}x{reference.size[1]}, the screen {final.width}x{final.height}'
    height = final.height // screen['rows'] * (screen['lines'].index(MESSAGE_LAST) + 1)
    box = (0, 0, final.width, height)
    share = changed_pixels(final.crop(box), reference.crop(box)) / (final.width * height)
    if share > REFERENCE_SHARE:
        return 'FAIL', (f'the message rows differ from the accepted reference {path} ({share:.2%} of their pixels; '
                        f'accepted by {entry["accepted_by"]})')
    return 'PASS', (f'the message rows match {path} ({share:.3%} of their pixels differ), accepted by '
                    f'{entry["accepted_by"]} on {entry.get("accepted_on", "?")}')


# -- the run --------------------------------------------------------------------------------
def missing_prerequisites(display, installed):
    for command in ('qemu-system-x86_64', 'qemu-img') if installed else ('qemu-system-x86_64',):
        if not shutil.which(command):
            return f'{command} is not installed'
    for path in (OVMF / 'OVMF_CODE.4m.fd', OVMF / 'OVMF_VARS.4m.fd'):
        if not os.access(path, os.R_OK):
            return f'{path} is not readable'
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        return '/dev/kvm is not usable'
    if display == 'gl' and not os.access(RENDER, os.R_OK | os.W_OK):
        return f'{RENDER} is not usable'
    return None


@contextlib.contextmanager
def gl_lock():
    """One GL guest at a time on this host (tests/vm/eyes/eyes-vm.sh holds the same lock)."""
    GL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(GL_LOCK, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def run_part(part, *, iso, disk, port, login):
    lock = gl_lock() if part.display == 'gl' else contextlib.nullcontext(True)
    with lock as held:
        if not held:
            part.rest(f'not run: another GL guest holds {GL_LOCK}')
            return
        account, password = login if part.system == 'installed' else ('live', None)
        guest = GUEST(part.dir, system=part.system, display=part.display, res=part.res, iso=iso,
                      disk=disk if part.system == 'installed' else None, port=port, account=account,
                      password=password)
        try:
            (run_std if part.display == 'std' else run_gl)(part, guest)
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
            current = next((name for name in part.checks if name not in part.done), None)
            if current:
                part.failed(current, f'error: {error}')
        finally:
            guest.stop()
        part.rest('not run: an earlier check of this part failed')


def refuse(text):
    print(f'check-graphics-fallback: refused: {text}', file=sys.stderr)
    return 2


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def fixture_login(name):
    for folder in (ROOT / 'installer/fixtures', HERE / 'fixtures'):
        path = folder / f'plan-{name}.json'
        if path.is_file():
            data = json.loads(path.read_text())
            user = data.get('config', data)['user']
            return user['login'], user['password']
    raise FileNotFoundError(f'no fixture plan-{name}.json')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--iso', required=True, help='the TEST image (iso/verify-image.py --test accepts it)')
    parser.add_argument('--out', required=True, help='new directory for the evidence')
    parser.add_argument('--installed-disk', help='qcow2 of an install made from this test image')
    parser.add_argument('--fixture', default='erase-btrfs', help='installer fixture of that install (its account)')
    parser.add_argument('--ssh-port', type=int, default=2261)
    parser.add_argument('--part', action='append', choices=PARTS, default=[], help='run only these parts')
    args = parser.parse_args(argv)

    out = Path(args.out).absolute()
    iso = Path(args.iso).absolute()
    disk = Path(args.installed_disk).absolute() if args.installed_disk else None
    if out.exists():
        return refuse(f'output directory exists, never reused: {out}')
    if any(',' in str(path) for path in (out, iso, disk or '')):
        return refuse('QEMU paths must not contain commas')
    if not 1024 <= args.ssh_port <= 65535:
        return refuse('--ssh-port must be 1024-65535')
    if not iso.is_file():
        return refuse(f'no image file {iso}')
    checksum = Path(f'{iso}.sha256')
    if not checksum.is_file():
        return refuse(f'missing {checksum} next to the image')
    sha = file_sha256(iso)
    if checksum.read_text().split()[:1] != [sha]:
        return refuse(f'{iso} does not match {checksum}')
    verified = subprocess.run([sys.executable, str(VERIFY), str(iso), '--test'], capture_output=True, text=True)
    if verified.returncode != 0:
        return refuse(f'iso/verify-image.py --test refused {iso}: {(verified.stdout + verified.stderr).strip()[-300:]}')
    if disk is not None and not disk.is_file():
        return refuse(f'no installed disk file {disk}')
    try:
        login = fixture_login(args.fixture)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return refuse(f'fixture {args.fixture}: {error}')

    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()
    results = Results(out)
    results.line(f'image: {iso} sha256 {sha}')
    results.line(f'installed disk: {disk} (booted through an overlay; the disk is not written)' if disk
                 else 'installed disk: none')
    for name in PARTS:
        part = Part(name, results, out)
        if args.part and name not in args.part:
            part.rest('not selected in this run (--part)')
            continue
        if part.system == 'installed' and disk is None:
            part.rest('no installed disk (--installed-disk, an install made from this test image)')
            continue
        missing = missing_prerequisites(part.display, part.system == 'installed')
        if missing:
            part.rest(f'not run: {missing}')
            continue
        results.line(f'== {name} on {iso.name} (sha256 {sha[:12]})')
        run_part(part, iso=iso, disk=disk, port=args.ssh_port, login=login)

    failed = [row for row in results.rows if row[2] == 'FAIL']
    untested = [row for row in results.rows if row[2] == 'NOT TESTED']
    if failed:
        results.line(f'RESULT: FAILED at {failed[0][0]} {failed[0][1]}')
        return 1
    if untested:
        results.line(f'RESULT: NOT TESTED at {untested[0][0]} {untested[0][1]}')
        return 3
    (out / 'PASS').write_text(f'{iso} sha256 {sha}\n')
    results.line('RESULT: SCRIPTS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
