#!/usr/bin/env python3
"""Restart policy of the session units. The sleep guard must always come back. The shell
and the idle policy come back after a crash, but not after an exit no restart can heal,
and a tight crash loop of the shell stops. Reads the unit files; runs the start scripts
only in ways that fail before anything starts (no shell window, no swayidle)."""
import configparser
import ctypes
import itertools
import math
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from runtime_fixture import short_runtime
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parents[1]


def unit_file(path):
    unit = configparser.ConfigParser(interpolation=None)
    unit.optionxform = str
    assert unit.read(path) == [str(path)], path
    return unit


class UnitChecks:
    """Checks every session unit passes; mixed into one TestCase per unit."""
    UNIT = SCRIPT = None

    def setUp(self):
        self.unit = unit_file(self.UNIT)

    def test_restart_delay_cannot_grow(self):
        # systemd's restart counter is reset only by a manual start, reset-failed or a
        # RESTART_RESET=1 notification (systemd 262 src/core/service.c: service_start,
        # service_reset_failed, service_notify_message). With RestartSteps= the delay
        # therefore reaches RestartMaxDelaySec= for the rest of the session: for the
        # guard, a lid close in that gap would sleep with no inhibitor and no lock; for
        # the shell, the bar and the launcher stay away that long after every crash.
        # Either the exponential settings are absent, or the service resets the counter.
        service = self.unit['Service']
        exponential = 'RestartSteps' in service or 'RestartMaxDelaySec' in service
        resets = service.get('Type') == 'notify' and 'RESTART_RESET=1' in self.SCRIPT.read_text()
        self.assertTrue(not exponential or resets,
                        'the restart delay grows with RestartSteps/RestartMaxDelaySec and nothing resets the counter')

    @unittest.skipUnless(shutil.which('systemd-analyze'), 'systemd-analyze is not installed')
    def test_systemd_knows_every_key(self):
        # The exit code stays 0 for a misspelt key; only the message tells. A non-zero
        # exit means the unit was not checked at all (no runtime directory, e.g. a CI
        # job running as root: "Failed to initialize manager"): never a silent pass.
        result = subprocess.run(['systemd-analyze', '--user', 'verify', str(self.UNIT)],
                                text=True, capture_output=True)
        if result.returncode and 'Failed to initialize manager' in result.stderr:
            self.skipTest('systemd-analyze could not start a user manager here, the unit was not checked: '
                          + result.stderr.strip().splitlines()[-1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Unknown key', result.stderr)
        self.assertNotIn('Failed to parse', result.stderr)

    def prevented(self):
        return set(self.unit['Service'].get('RestartPreventExitStatus', '').split())

    def exit_status(self, argv, **environ):
        """Run a start script with private directories and without any display."""
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as private, short_runtime() as runtime:
            for name in ('run', 'cache', 'state', 'data', 'config'):
                os.mkdir(os.path.join(private, name), 0o700)
            env = {key: value for key, value in os.environ.items()
                   if key not in ('WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'DISPLAY', 'NIRI_SOCKET', 'EMAKI_LOGIN_HANDOFF')}
            env.update(XDG_RUNTIME_DIR=runtime, XDG_CACHE_HOME=private + '/cache',
                       XDG_STATE_HOME=private + '/state', XDG_DATA_HOME=private + '/data',
                       XDG_CONFIG_HOME=private + '/config', QT_QPA_PLATFORM='offscreen',
                       QS_DISABLE_CRASH_HANDLER='1')
            env.update({key: value.replace('{private}', private) for key, value in environ.items()})
            if self.SCRIPT.name == 'emaki-shell':
                checker = Path(private, 'run/emaki-qt-check')
                checker.write_text('#!/bin/sh\nexit 0\n')
                checker.chmod(0o700)
                env['PATH'] = private + '/run:' + env.get('PATH', '')
            if callable(argv):
                argv = argv(private)
            done = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            return done.returncode, done.stdout + done.stderr


# The start limit as systemd 262 applies it: an automatic restart enqueues a JOB_START
# (src/core/service.c:3109 service_enter_restart), unit_start() asks the unit type's
# test_startable (src/core/unit.c:2012), service_test_startable() calls
# unit_test_start_limit() for every start that is not already starting
# (src/core/service.c:6380), and that asks ratelimit_below() (src/core/unit.c:1873,
# src/basic/ratelimit.c:9-31). The manager itself cannot be asked safely here: a private
# `systemd --user` takes the cgroup it was started in as its root, creates init.scope there
# and moves every other process of that cgroup into it (src/core/cgroup.c:3293-3370,
# manager_setup_cgroup), i.e. it rearranges a cgroup of the running session.
def ratelimit_below(window, now, interval, burst):
    """A copy of systemd 262 src/basic/ratelimit.c:9-31 ratelimit_below() on a simulated
    clock; StartLimitBurst/StartLimitIntervalSec fill its burst and interval."""
    if window['begin'] is None or now - window['begin'] > interval:
        window.update(begin=now, num=1)
        return True
    window['num'] += 1
    return window['num'] <= burst


class ModelLimiter:
    def __init__(self, interval, burst):
        self.window, self.interval, self.burst = {'begin': None, 'num': 0}, interval, burst

    def below(self, now):
        return ratelimit_below(self.window, now, self.interval, self.burst)


class RateLimit(ctypes.Structure):
    """systemd 262 src/basic/ratelimit.h: struct RateLimit."""
    _fields_ = [('interval', ctypes.c_uint64), ('burst', ctypes.c_uint),
                ('num', ctypes.c_uint), ('begin', ctypes.c_uint64)]


def installed_ratelimit_below():
    """The ratelimit_below() the running user manager calls: libsystemd-core imports it from
    libsystemd-shared (`nm -D`: "U ratelimit_below@SD_SHARED"). None where it is not there."""
    for library in sorted(Path('/usr/lib/systemd').glob('libsystemd-shared-*.so')):
        try:
            function = ctypes.CDLL(str(library)).ratelimit_below
        except (OSError, AttributeError):
            continue
        function.argtypes = [ctypes.POINTER(RateLimit)]
        function.restype = ctypes.c_bool
        return function
    return None


class SystemdLimiter:
    """The installed ratelimit_below() on a simulated clock. It reads CLOCK_BOOTTIME itself,
    so before each call the window's begin is moved to where the simulated time puts it."""

    def __init__(self, function, interval, burst):
        self.function = function
        self.limit = RateLimit(interval=interval * 1000000, burst=burst, num=0, begin=0)
        self.begin = None

    def below(self, now):
        if self.begin is not None:
            elapsed = round((now - self.begin) * 1000000)
            self.limit.begin = time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 1000 - elapsed
        below = self.function(ctypes.byref(self.limit))
        if self.limit.num == 1:
            # The call opened a new window at `now`.
            self.begin = now
        return below


def restart_delay(service, n_restarts_next):
    """A copy of systemd 262 src/core/service.c:375-407 service_restart_usec_next() in seconds,
    for the RestartSec=, RestartSteps= and RestartMaxDelaySec= of a [Service] section (plain
    seconds; absent keys take systemd's defaults: 100 ms, no steps, no maximum)."""
    usec = float(service.get('RestartSec', '0.1'))
    steps = int(service.get('RestartSteps', '0'))
    maximum = float(service.get('RestartMaxDelaySec', 'inf'))
    if n_restarts_next <= 1 or steps == 0 or usec == 0 or maximum == math.inf or usec >= maximum:
        return usec
    if n_restarts_next > steps:
        return maximum
    return usec * (maximum / usec) ** ((n_restarts_next - 1) / steps)


def restart_delays(service, runs):
    """The delay before the restart after each run; a run is True when it sent READY=1 with
    RESTART_RESET=1 before it ended. The delay is computed while the counter still lacks this
    restart (service_restart_usec_next adds 1), service_enter_restart then counts it
    (service.c:3146), and RESTART_RESET=1 sets it to 0 (service_notify_message, :5776-5784)."""
    n_restarts = 0
    for ready in runs:
        if ready:
            n_restarts = 0
        yield restart_delay(service, n_restarts + 1)
        n_restarts += 1


def starts_per_hour(service, runs, lifetime):
    now, starts = 0.0, 0
    for delay in restart_delays(service, runs):
        if now >= 3600:
            break
        starts += 1
        now += lifetime + delay
    return starts


class FakeCompositor(threading.Thread):
    """Just enough of a Wayland compositor for a Qt client to start: wl_compositor, wl_shm and
    xdg_wm_base in the registry and wl_display.sync answered; every other request is ignored.
    Once `act` is set it posts a protocol error on the registry (what a compositor does for
    any protocol violation) or closes the connection (a compositor that went away)."""
    GLOBALS = ((1, 'wl_compositor', 4), (2, 'wl_shm', 1), (3, 'xdg_wm_base', 1))

    def __init__(self, path, how, act):
        super().__init__(daemon=True)
        self.how, self.act = how, act
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(1)
        self.server.settimeout(30)

    @staticmethod
    def string(text):
        data = text.encode() + b'\0'
        return struct.pack('<I', len(data)) + data + b'\0' * (-len(data) % 4)

    def run(self):
        try:
            connection, _ = self.server.accept()
        except OSError:
            return
        finally:
            self.server.close()
        with connection:
            try:
                self.serve(connection)
            except OSError:
                pass

    def serve(self, connection):
        def send(target, opcode, payload=b''):
            connection.sendall(struct.pack('<II', target, (8 + len(payload)) << 16 | opcode) + payload)

        def receive(timeout):
            if not select.select([connection], [], [], timeout)[0]:
                return b''
            data, descriptors, _flags, _address = socket.recv_fds(connection, 65536, 32)
            for descriptor in descriptors:
                os.close(descriptor)
            if not data:
                raise ConnectionResetError('the client went away')
            return data
        pending, registry = b'', None
        while not (self.act.is_set() and registry):
            pending += receive(0.05)
            while len(pending) >= 8:
                target, word = struct.unpack('<II', pending[:8])
                size, opcode = word >> 16, word & 0xffff
                if len(pending) < size:
                    break
                body, pending = pending[8:size], pending[size:]
                if target == 1 and opcode == 1:  # wl_display.get_registry(new_id)
                    registry, = struct.unpack('<I', body[:4])
                    for name, interface, version in self.GLOBALS:
                        send(registry, 0, struct.pack('<I', name) + self.string(interface) + struct.pack('<I', version))
                elif target == 1 and opcode == 0:  # wl_display.sync(new_id): done, delete_id
                    callback, = struct.unpack('<I', body[:4])
                    send(callback, 0, struct.pack('<I', 0))
                    send(1, 1, struct.pack('<I', callback))
        if self.how == 'protocol error':
            # wl_display.error(object, code, message); the connection stays open, as in a
            # compositor that goes on, until the client is gone.
            send(1, 0, struct.pack('<II', registry, 0) + self.string('emaki test: protocol error'))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                receive(0.1)


class SleepGuardRestart(UnitChecks, unittest.TestCase):
    UNIT = ROOT / 'systemd/emaki-sleep-guard.service'
    SCRIPT = ROOT / 'scripts/emaki-sleep-guard'

    def test_unit_is_restarted_without_a_start_limit(self):
        # Only 0 disables rate limiting; a missing key falls back to the manager default.
        self.assertEqual(self.unit['Unit']['StartLimitIntervalSec'], '0')
        self.assertNotIn('StartLimitBurst', self.unit['Unit'])
        self.assertEqual(self.unit['Service']['RestartSec'], '1')

    def test_a_clean_exit_is_restarted_too(self):
        # The guard exits 0 on SIGTERM, which Restart=on-failure counts as success: one
        # `systemctl --user kill emaki-sleep-guard` left the session without its sleep delay
        # for good (VM check f6, 2026-10-05). A stop job is never restarted, whatever Restart=
        # says (systemd.service(5), Restart=): `systemctl --user stop` and the end of the
        # session through PartOf= still stop it.
        self.assertEqual(self.unit['Service']['Restart'], 'always')
        self.assertEqual(self.unit['Unit']['PartOf'], 'graphical-session.target')

    def test_every_failure_is_retried(self):
        # Without the guard a lid close sleeps with no inhibitor and no lock: no exit
        # status may keep it down.
        self.assertEqual(self.prevented(), set())

    def test_shutdown_stops_the_compositor_before_the_guard(self):
        # The inhibitor and polling children must outlive the compositor stop.
        # Stop ordering reverses Before/After; After=graphical-session.target
        # would instead hold up the compositor while the guard waits for it.
        before = set(self.unit['Unit'].get('Before', '').split())
        after = set(self.unit['Unit'].get('After', '').split())
        self.assertTrue({'niri-emaki.service', 'niri.service', 'graphical-session.target'} <= before)
        self.assertNotIn('graphical-session.target', after)

    def test_start_outside_a_graphical_session_is_skipped(self):
        # ExecCondition exit 1..254 skips activation, including Restart=always.
        # Requisite needs After=, which would reverse the required shutdown order.
        self.assertNotIn('Requisite', self.unit['Unit'])
        condition = shlex.split(self.unit['Service']['ExecCondition'])
        self.assertEqual(condition[:2], ['/bin/sh', '-c'])
        self.assertEqual(len(condition), 3)
        command = condition[2]
        self.assertEqual(command.count('/usr/bin/systemctl'), 1)
        # systemd turns $$ into a literal $ before handing the command to sh.
        self.assertNotIn('$', command.replace('$$', ''))
        command = command.replace('$$', '$')
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as private:
            fake = Path(private) / 'systemctl'
            args = Path(private) / 'args'
            fake.write_text('#!/bin/sh\n'
                            'printf "%s\\n" "$@" > "$QUERY_ARGS"\n'
                            'printf "%s\\n" "$QUERY_STATE"\n'
                            'exit "$QUERY_STATUS"\n')
            fake.chmod(0o700)
            command = command.replace('/usr/bin/systemctl', shlex.quote(str(fake)))
            cases = [(state, 0, 0 if state in ('active', 'activating') else 1)
                     for state in ('active', 'activating', 'inactive', 'deactivating',
                                   'failed', 'reloading', 'unknown', '', 'active\nactivating')]
            # Query errors must skip activation, even with misleading stdout, and
            # must never return 255 (which makes ExecCondition fail the service).
            cases.extend((state, status, 1) for state in ('', 'active', 'activating')
                         for status in (1, 4, 255))
            for state, query_status, expected in cases:
                with self.subTest(state=state, query_status=query_status):
                    result = subprocess.run(
                        condition[:2] + [command],
                        env={**os.environ, 'QUERY_ARGS': str(args), 'QUERY_STATE': state,
                             'QUERY_STATUS': str(query_status)},
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(args.read_text().splitlines(),
                                     ['--user', 'show', '--property=ActiveState', '--value',
                                      'graphical-session.target'])

    def test_a_guard_that_cannot_start_backs_off(self):
        # A guard that can never start (no system bus, no logind) was restarted every second
        # for the rest of the session: about 3,400 starts an hour of 60 ms CPU each (5.7 % of
        # a core) and their journal lines. It now backs off in five steps to one start every
        # 30 s. A start fails in about 60 ms (python3 -I up to the failed bus connection).
        service = self.unit['Service']
        self.assertLessEqual(starts_per_hour(service, itertools.repeat(False), lifetime=0.06), 130)
        self.assertEqual((service.get('RestartSteps'), service.get('RestartMaxDelaySec')), ('5', '30'))
        self.assertEqual(list(restart_delays(service, [False] * 7))[-2:], [30, 30])

    def test_a_guard_that_held_its_inhibitor_comes_back_after_a_second(self):
        # The gap without a sleep delay after an exit stays one second whenever the guard got
        # as far as its inhibitor (REV-S1: without RESTART_RESET=1 the counter never goes back
        # in a session), after a run of failed starts too, and for a guard killed again and
        # again (VM check f6).
        service = self.unit['Service']
        self.assertEqual(list(restart_delays(service, [False] * 50 + [True] * 3))[-3:], [1, 1, 1])
        self.assertEqual(set(restart_delays(service, [True] * 20)), {1})

    def test_systemd_takes_the_reset_with_the_readiness(self):
        # systemd 262 handles READY=1 of a message first (service.c:5660), which takes a
        # starting Type=notify service to running unless an ExecStartPost= runs
        # (service_enter_start_post, :2867), and takes RESTART_RESET=1 only from a running or
        # stopping service (:5777). The guard sends both in one message from its main process,
        # python3 itself, which NotifyAccess=main (the default for Type=notify, :1250) needs.
        service = self.unit['Service']
        self.assertEqual(service.get('Type'), 'notify')
        self.assertNotIn('ExecStartPost', service)
        self.assertIn(service.get('NotifyAccess', 'main'), ('main', 'all'))
        self.assertTrue(service['ExecStart'].startswith('/usr/bin/python3 '), service['ExecStart'])


class ShellRestart(UnitChecks, unittest.TestCase):
    # Five crashes in 30 s used to leave the session without bar, launcher, dock and
    # notifications until the next login, without a word (SH-07). A shell that can never
    # start was restarted about 3,000 times an hour, each start leaving its run directory
    # in /run/user and, on a signal, a core dump.
    UNIT = ROOT / 'systemd/emaki-shell.service'
    SCRIPT = ROOT / 'scripts/emaki-shell'
    BROKEN = 'import Quickshell\n\nShellRoot {\n    NoSuchType {}\n}\n'

    def test_a_crash_restarts_after_a_second(self):
        self.assertEqual(self.unit['Service']['Restart'], 'on-failure')
        self.assertEqual(self.unit['Service']['RestartSec'], '1')

    def test_exits_no_restart_can_heal_are_not_retried(self):
        # 127 (qs is not installed) needs repair; a Qt mismatch only warns and the panel still starts. 255 is a fatal Wayland error of a shell
        # that was running fine (test_a_lost_compositor_is_255_and_restarted); a shell that
        # cannot load is stopped by the start limit instead.
        self.assertEqual(self.prevented(), {'127'})

    def test_qs_missing_is_127(self):
        status, output = self.exit_status(['/bin/sh', str(self.SCRIPT)], PATH='{private}/run',
                                          EMAKI_SHELL_DIR=str(ROOT / 'shell'))
        self.assertEqual(status, 127, output)
        self.assertIn(str(status), self.prevented())

    @unittest.skipUnless(shutil.which('qs'), 'qs is not installed')
    def test_a_shell_qs_cannot_load_is_255(self):
        # Quickshell 0.3.1 exits with -1 when the first load fails (src/core/rootwrapper.cpp)
        # and when the config file cannot be opened (src/launch/launch.cpp). The same status
        # ends a running shell whose compositor connection fails, so it is restarted, and
        # the start limit ends this loop (test_start_limit_stops_a_tight_loop_only).
        def broken(private):
            Path(private, 'shell').mkdir()
            Path(private, 'shell/shell.qml').write_text(self.BROKEN)
            return ['/bin/sh', str(self.SCRIPT)]
        for name, argv, shell in (('QML type error', broken, '{private}/shell'),
                                  ('no shell directory', ['/bin/sh', str(self.SCRIPT)], '{private}/missing')):
            with self.subTest(name):
                status, output = self.exit_status(argv, EMAKI_SHELL_DIR=shell)
                self.assertEqual(status, 255, output)
                self.assertNotIn(str(status), self.prevented())

    @unittest.skipUnless(shutil.which('qs'), 'qs is not installed')
    def test_a_lost_compositor_is_255_and_restarted(self):
        # Qt 6.11's Wayland client ends the process with _exit(-1) on every fatal error of
        # its connection (qtbase src/plugins/platforms/wayland/qwaylanddisplay.cpp,
        # QWaylandDisplay::checkWaylandError): a protocol error the compositor posts, and a
        # compositor connection that breaks (QT_WAYLAND_RECONNECT unset). The journal of a
        # session showed exactly this: "The Wayland connection broke", then status=255. A
        # shell that had loaded fine dies this way, and a restart heals it.
        for how in ('protocol error', 'connection lost'):
            with self.subTest(how):
                status, output = self.lost_compositor(how)
                self.assertIn('Configuration Loaded', output)
                self.assertIn('The Wayland connection', output)
                self.assertEqual(status, 255, output)
                self.assertNotIn(str(status), self.prevented())

    def lost_compositor(self, how):
        """emaki-shell with a minimal shell on a fake compositor that, once the shell has
        loaded, posts a protocol error or hangs up. The exit status and the output."""
        # /tmp: the Wayland and qs IPC sockets must fit in sun_path (108 bytes).
        with tempfile.TemporaryDirectory(prefix='emaki-wl-', dir='/tmp') as private:
            for name in ('run', 'cache', 'state', 'data', 'config', 'shell'):
                os.mkdir(os.path.join(private, name), 0o700)
            checker = Path(private, 'run/emaki-qt-check')
            checker.write_text('#!/bin/sh\nexit 0\n')
            checker.chmod(0o700)
            Path(private, 'shell/shell.qml').write_text('import Quickshell\n\nShellRoot {}\n')
            loaded = threading.Event()
            compositor = FakeCompositor(os.path.join(private, 'run/wayland-test'), how, loaded)
            compositor.start()
            env = {key: value for key, value in os.environ.items()
                   if key not in ('WAYLAND_SOCKET', 'DISPLAY', 'NIRI_SOCKET', 'EMAKI_LOGIN_HANDOFF',
                                  'QT_WAYLAND_RECONNECT', 'QT_QPA_PLATFORMTHEME')}
            env.update(XDG_RUNTIME_DIR=private + '/run', XDG_CACHE_HOME=private + '/cache',
                       XDG_STATE_HOME=private + '/state', XDG_DATA_HOME=private + '/data',
                       XDG_CONFIG_HOME=private + '/config', EMAKI_SHELL_DIR=private + '/shell',
                       WAYLAND_DISPLAY='wayland-test', QT_QPA_PLATFORM='wayland',
                       QT_QUICK_BACKEND='software', PATH=private + '/run:' + env.get('PATH', ''))
            shell = subprocess.Popen(['/bin/sh', str(self.SCRIPT)], env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            output = []

            def read():
                for line in shell.stdout:
                    output.append(line)
                    if 'Configuration Loaded' in line:
                        loaded.set()
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            try:
                status = shell.wait(timeout=30)
            except subprocess.TimeoutExpired:
                shell.kill()
                status = shell.wait()
            finally:
                loaded.set()
                compositor.join(timeout=5)
                reader.join(timeout=5)
                shell.stdout.close()
            return status, ''.join(output)

    def test_start_limit_stops_a_tight_loop_only(self):
        self.check_start_limit(ModelLimiter)

    @unittest.skipUnless(installed_ratelimit_below(), "systemd's libsystemd-shared is not installed")
    def test_the_installed_systemd_limits_the_same_way(self):
        # The same starts through the rate limiter the running manager calls, not a copy.
        function = installed_ratelimit_below()
        self.check_start_limit(lambda interval, burst: SystemdLimiter(function, interval, burst))

    def check_start_limit(self, limiter):
        unit = self.unit['Unit']
        interval, burst = int(unit['StartLimitIntervalSec']), int(unit['StartLimitBurst'])
        delay = float(self.unit['Service']['RestartSec'])

        def first_refused(lifetimes, until=3600.0):
            """Start, live `lifetime`, restart after RestartSec; the time of the first refused start."""
            window, now = limiter(interval, burst), 0.0
            for lifetime in lifetimes:
                if now > until:
                    return None
                if not window.below(now):
                    return now
                now += lifetime + delay
            return None

        def forever(lifetime):
            while True:
                yield lifetime
        # Repeated failures during fast or slow loading stop within the first window.
        for lifetime in (0.3, 2.1, 2.5, 5):
            stopped = first_refused(forever(lifetime))
            self.assertIsNotNone(stopped, lifetime)
            self.assertLess(stopped, interval, lifetime)
        self.assertEqual(first_refused(forever(2.5)), 70.0)
        # Occasional crashes still recover without accumulating across windows.
        for lifetime in (10, 60, 1800):
            self.assertIsNone(first_refused(forever(lifetime)), lifetime)
        # The window starts over by itself: bursts of quick crashes with calm in between
        # never add up, unlike RestartSteps=, whose counter lives as long as the session.
        bursts = ([0.3] * 15 + [600]) * 6
        self.assertIsNone(first_refused(iter(bursts)))

    def test_a_terminal_opens_without_the_shell(self):
        # With the shell stopped the person still has Mod+T (a niri bind, not the shell) to
        # read `journalctl --user -u emaki-shell` and run `emaki-shell restart`.
        # The helper resolves the selected terminal and falls back to kitty if removed.
        binds = (ROOT / 'niri/default.kdl').read_text()
        self.assertRegex(binds, re.compile(r'^\s*Mod\+T [^{\n]*\{ spawn "emaki-terminal"; \}', re.M))


class IdleRestart(UnitChecks, unittest.TestCase):
    UNIT = ROOT / 'systemd/emaki-idle.service'
    SCRIPT = ROOT / 'scripts/emaki-idle'

    def test_unit_is_restarted_without_a_start_limit(self):
        # The idle lock is protection: no start limit, as for the sleep guard.
        self.assertEqual(self.unit['Unit']['StartLimitIntervalSec'], '0')
        self.assertNotIn('StartLimitBurst', self.unit['Unit'])
        self.assertEqual(self.unit['Service']['Restart'], 'on-failure')
        self.assertEqual(self.unit['Service']['RestartSec'], '2')

    def test_exits_no_restart_can_heal_are_not_retried(self):
        self.assertEqual(self.prevented(), {'2', '127', '255'})

    def test_the_scripts_refusals_are_2_and_a_missing_swayidle_127(self):
        # PATH holds what the script runs before `exec swayidle` (rm), and nothing else.
        def script(*arguments):
            def argv(private):
                os.symlink(shutil.which('rm'), os.path.join(private, 'run', 'rm'))
                return ['/bin/sh', str(self.SCRIPT), *arguments]
            return argv
        for name, argv, environ in (('timing not a number', script(), {'EMAKI_IDLE_DIM': 'abc'}),
                                    ('unknown argument', script('--bogus'), {}),
                                    ('every step off', script(), {'EMAKI_IDLE_DIM': '0', 'EMAKI_IDLE_LOCK': '0',
                                                                  'EMAKI_IDLE_OFF': '0'})):
            with self.subTest(name):
                status, output = self.exit_status(argv, PATH='{private}/run', **environ)
                self.assertEqual(status, 2, output)
                self.assertIn(str(status), self.prevented())
        status, output = self.exit_status(script(), PATH='{private}/run')
        self.assertEqual(status, 127, output)
        self.assertIn('swayidle', output)
        self.assertIn(str(status), self.prevented())

    @unittest.skipUnless(shutil.which('swayidle'), 'swayidle is not installed')
    def test_a_broken_swayidle_config_is_255(self):
        # emaki-idle execs swayidle, which also reads the person's own swayidle config and
        # exits with -1 on an error in it, before it connects to anything (swayidle 1.9).
        def broken(private):
            Path(private, 'config/swayidle').mkdir()
            Path(private, 'config/swayidle/config').write_text('timeout abc true\n')
            return ['swayidle', '-w']
        status, output = self.exit_status(broken, WAYLAND_DISPLAY='emaki-test-no-display')
        self.assertEqual(status, 255, output)
        self.assertIn(str(status), self.prevented())

    @unittest.skipUnless(shutil.which('swayidle'), 'swayidle is not installed')
    def test_a_compositor_swayidle_cannot_use_is_restarted(self):
        # 255 is kept above only because swayidle 1.9 exits with -1 nowhere but in argument
        # and config parsing (main.c: parse_args, the parse_* commands, load_config). A
        # compositor it cannot reach exits -3, one without ext-idle-notify -4, no seat -5.
        argv = ['swayidle', '-w', 'timeout', '300', 'true']
        status, output = self.exit_status(argv, WAYLAND_DISPLAY='emaki-test-no-display')
        self.assertEqual(status, 253, output)
        self.assertNotIn(str(status), self.prevented())
        with tempfile.TemporaryDirectory(prefix='emaki-wl-', dir='/tmp') as private:
            done = threading.Event()
            compositor = FakeCompositor(private + '/wayland-test', 'connection lost', done)
            compositor.start()
            try:
                status, output = self.exit_status(argv, WAYLAND_DISPLAY=private + '/wayland-test')
            finally:
                done.set()
                compositor.join(timeout=5)
        self.assertEqual(status, 252, output)
        self.assertNotIn(str(status), self.prevented())


if __name__ == '__main__':
    unittest.main(verbosity=2)
