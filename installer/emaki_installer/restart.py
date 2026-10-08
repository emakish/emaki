# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A resident restart guardian, isolated from reads of the removable live root."""
import ctypes
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import time

from .errors import Code, InstallError, require
from .runtime import safe_log

PREPARE_TIMEOUT = 125
FALLBACK_DELAY = 15
PREPARE_ERROR = 'Could not prepare to restart. Keep the USB stick connected and try again.'
UNCONFIRMED_ERROR = ('Your installation is safe. If the computer has not restarted by itself, '
                     'hold the power button to turn it off, then start it again.')
GUARD_ERROR = 'Automatic restart is unavailable. ' + UNCONFIRMED_ERROR
RESTART_ERROR = ('Could not restart. Try again, or hold the power button to turn the computer off, '
                 'then start it again. Your installation is safe.')
PACKET = struct.Struct('!cQd')
SCOPE = 'emaki-installer-restart.scope'
SCOPE_PROCS = Path('/sys/fs/cgroup/system.slice') / SCOPE / 'cgroup.procs'
PENDING_RESTART_ERROR = ('A previous restart is still pending. Wait for it to finish '
                         'before starting another installation.')
SCOPE_COMMAND_TIMEOUT = 5
SCOPE_CGROUP_TIMEOUT = 2
# Keep the original ten seconds for memory locking and scheduling after adoption.
STARTUP_TIMEOUT = SCOPE_COMMAND_TIMEOUT + SCOPE_CGROUP_TIMEOUT + 10


def isolate_guardian():
    """Move this process out of the daemon's KillMode=mixed cgroup at startup."""
    subprocess.run([
        'busctl', '--system', '--timeout=4', 'call', 'org.freedesktop.systemd1',
        '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
        'StartTransientUnit', 'ssa(sv)a(sa(sv))', SCOPE, 'fail', '5',
        'PIDs', 'au', '1', str(os.getpid()), 'Slice', 's', 'system.slice',
        'DefaultDependencies', 'b', 'true', 'IgnoreOnIsolate', 'b', 'true',
        'CollectMode', 's', 'inactive-or-failed', '0',
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=SCOPE_COMMAND_TIMEOUT)
    # StartTransientUnit queues a job. Do not acknowledge readiness until PID 1
    # actually moved us. Failure falls back to the inherited daemon cgroup.
    deadline = time.monotonic() + SCOPE_CGROUP_TIMEOUT
    while True:
        groups = Path('/proc/self/cgroup').read_text().splitlines()
        if f'0::/system.slice/{SCOPE}' in groups:
            return
        require(time.monotonic() < deadline, Code.COMMAND_FAILED, GUARD_ERROR)
        time.sleep(0.01)


class ResidentReboot:
    def __init__(self, runner, *, libc=None, shutdown=Path('/run/initramfs/shutdown'),
                 medium_size=None):
        self.runner, self.shutdown = runner, shutdown
        self.medium_size = medium_size
        libc = libc if libc is not None else ctypes.CDLL(None, use_errno=True)
        self.sync, self.lock = libc.sync, libc.mlockall
        self.signal, self.sleep, self.restart = libc.kill, libc.sleep, libc.reboot
        self.sync.argtypes, self.sync.restype = [], None
        self.lock.argtypes, self.lock.restype = [ctypes.c_int], ctypes.c_int
        self.signal.argtypes, self.signal.restype = [ctypes.c_int, ctypes.c_int], ctypes.c_int
        self.sleep.argtypes, self.sleep.restype = [ctypes.c_uint], ctypes.c_uint
        self.restart.argtypes, self.restart.restype = [ctypes.c_int], ctypes.c_int
        self.reboot_signal = signal.SIGRTMIN + 5
        self.ready = False

    def pin(self):
        # Run in the guardian at daemon startup, before accepting any installation.
        # MCL_CURRENT | MCL_FUTURE pins code/libraries and future anonymous pages.
        # Locks are not inherited across fork: the guardian must pin itself.
        require(self.lock(3) == 0, Code.COMMAND_FAILED, PREPARE_ERROR)

    def prepare(self):
        # Only the disposable child performs filesystem reads or global sync.
        self.check_medium()
        try:
            self.runner.run(['systemctl', 'start', 'mkinitcpio-generate-shutdown-ramfs.service'],
                            timeout=120, termination_timeout=2)
            require(self.shutdown.is_file() and os.access(self.shutdown, os.X_OK),
                    Code.COMMAND_FAILED, 'The shutdown ramfs is unavailable.')
        except (InstallError, OSError) as exc:
            safe_log(self.runner.log, f'Shutdown ramfs preparation failed or was skipped: {exc}')
        self.sync()
        self.check_medium()

    def check_medium(self):
        # The boot mount's directory can remain cached after unplugging. Sysfs
        # disappears with a USB block device; an ejected optical drive has size 0.
        if self.medium_size is not None:
            require(int(self.medium_size.read_text()) > 0, Code.COMMAND_FAILED, PREPARE_ERROR)

    def __call__(self, *, force=False):
        require(self.ready, Code.BAD_REQUEST, PREPARE_ERROR)
        previous = None
        try:
            if not force:
                # Our own PID 1 request stops the scope too. Keep the resident
                # fallback alive if that shutdown stalls after USB removal.
                previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                self.signal(1, self.reboot_signal)
                remaining = 8
                while remaining:
                    remaining = self.sleep(remaining)
            # RB_AUTOBOOT does no disk I/O. Only the controller's completed job may
            # arm this path: target sync, non-lazy unmounts and device close succeeded.
            self.restart(0x01234567)
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        raise InstallError(Code.COMMAND_FAILED, RESTART_ERROR)


def receive(fd, timeout=None):
    if not select.select([fd], [], [], timeout)[0]:
        return None
    return os.read(fd, 1)


def notify(fd, value):
    try:
        os.write(fd, value)
    except BrokenPipeError:
        pass  # Losing the client must not disarm an already requested restart.


def receive_packet(fd, timeout):
    if not select.select([fd], [], [], timeout)[0]:
        return None
    data = os.read(fd, PACKET.size)
    return PACKET.unpack(data) if data else (b'', 0, 0)


def reply(fd, value, request_id, deadline=None):
    notify(fd, PACKET.pack(value, request_id, deadline or 0))


def prepare_child(restart, command_fd, reply_fd, timeout):
    """Never join a child which may be in uninterruptible storage I/O."""
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    try:
        pid = os.fork()
    except OSError:
        os.close(read_fd)
        os.close(write_fd)
        return False
    if pid == 0:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        os.close(read_fd)
        os.close(command_fd)
        os.close(reply_fd)
        try:
            restart.prepare()
            notify(write_fd, b'P')
        except BaseException:
            pass
        finally:
            os._exit(0)
    os.close(write_fd)
    try:
        completed = receive(read_fd, timeout) == b'P'
        if not completed:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # The guardian ignores SIGCHLD so exited preparation children are reaped
        # by the kernel, including a killed child that leaves disk sleep later.
        return completed
    finally:
        os.close(read_fd)


def guard_loop(restart, command_fd, reply_fd, *, prepare_timeout=PREPARE_TIMEOUT,
               fallback_delay=FALLBACK_DELAY, adopted=True):
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    restart.pin()
    reply(reply_fd, b'A' if adopted else b'D', 0)
    deadline = None
    forced = False
    while True:
        packet = receive_packet(command_fd, None if deadline is None else max(0, deadline - time.monotonic()))
        command, request_id, _ = packet if packet is not None else (None, 0, 0)
        if command == b'P':
            if not restart.ready:
                try:
                    forced = not prepare_child(restart, command_fd, reply_fd, prepare_timeout)
                except OSError:
                    forced = True
                restart.ready = True
                if forced:
                    deadline = time.monotonic() + fallback_delay
            reply(reply_fd, b'F' if forced else b'P', request_id, deadline)
        elif command == b'R' or (deadline is not None and command in (None, b'')):
            # A disconnected client cannot cancel the timeout fallback. Once a
            # syscall returns, report failure and permit an explicit retry.
            if command == b'' and deadline is not None:
                remaining = max(0, deadline - time.monotonic())
                time.sleep(remaining)
            try:
                restart(force=forced)
            except InstallError:
                reply(reply_fd, b'E', request_id)
            deadline = None
        elif command == b'':
            return


class RestartGuard:
    """Pipe-only client; create before starting threads or accepting requests."""
    def __init__(self, restart, *, close_fds=(), prepare_timeout=PREPARE_TIMEOUT,
                 fallback_delay=FALLBACK_DELAY, isolate=isolate_guardian):
        command_read, self.command = os.pipe2(os.O_CLOEXEC)
        self.reply, reply_write = os.pipe2(os.O_CLOEXEC)
        self.ready = False
        self.forced = False
        self.forced_deadline = None
        self.request_id = 0
        self.timeout = prepare_timeout + 5
        try:
            self.pid = os.fork()
        except OSError:
            for fd in (command_read, self.command, self.reply, reply_write):
                os.close(fd)
            raise
        if self.pid == 0:
            # The scope's normal shutdown dependencies stop us on power-off.
            # Also honor termination if our caller had installed a handler.
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.close(self.command)
            os.close(self.reply)
            for fd in close_fds:
                os.close(fd)
            try:
                adopted = True
                try:
                    isolate()
                except (OSError, subprocess.SubprocessError, InstallError) as exc:
                    adopted = False
                    safe_log(restart.runner.log,
                             f'Restart scope adoption failed: {exc}. Continuing without verified '
                             'isolation; the guardian may remain in the installer service cgroup.')
                guard_loop(restart, command_read, reply_write,
                           prepare_timeout=prepare_timeout, fallback_delay=fallback_delay,
                           adopted=adopted)
            except BaseException:
                reply(reply_write, b'E', 0)
            finally:
                os._exit(0)
        os.close(command_read)
        os.close(reply_write)
        startup = receive_packet(self.reply, STARTUP_TIMEOUT)
        if startup not in ((b'A', 0, 0), (b'D', 0, 0)):
            self.close()
            raise InstallError(Code.COMMAND_FAILED, 'The restart service could not start.')
        self.adopted = startup[0] == b'A'

    def check_installation(self):
        """Poll a failed adoption's scope before each plan and confirmation."""
        if self.adopted:
            return
        try:
            occupants = SCOPE_PROCS.read_text().split()
        except FileNotFoundError:
            return  # The old scope has already been collected.
        except OSError:
            # Unknown membership cannot prove an old armed guardian has gone.
            raise InstallError(Code.RESTART_PENDING, PENDING_RESTART_ERROR) from None
        # A timed-out adoption may still move our new, unarmed guardian later.
        require(not any(pid != str(self.pid) for pid in occupants),
                Code.RESTART_PENDING, PENDING_RESTART_ERROR)

    def request(self, command, timeout):
        self.request_id += 1
        request_id = self.request_id
        notify(self.command, PACKET.pack(command, request_id, 0))
        deadline = time.monotonic() + timeout
        while True:
            packet = receive_packet(self.reply, max(0, deadline - time.monotonic()))
            require(packet is not None, Code.COMMAND_FAILED, UNCONFIRMED_ERROR)
            if packet[0] == b'':
                self.forced_deadline = None
            require(packet[0] != b'', Code.COMMAND_FAILED, GUARD_ERROR)
            if packet[1] == request_id:
                return packet
            # A timed-out request or an unsolicited fallback result belongs to
            # its own request, never the next one. Keep the original deadline.
            require(time.monotonic() < deadline, Code.COMMAND_FAILED, UNCONFIRMED_ERROR)

    def prepare(self):
        if not self.ready:
            result, _, deadline = self.request(b'P', self.timeout)
            require(result in (b'P', b'F'), Code.COMMAND_FAILED, GUARD_ERROR)
            self.ready, self.forced = True, result == b'F'
            self.forced_deadline = deadline or None
        return self.forced

    def __call__(self):
        require(self.ready, Code.BAD_REQUEST, PREPARE_ERROR)
        # The normal guardian path waits eight seconds after asking PID 1. The
        # caller must remain waiting beyond that interval, including stale replies.
        self.request(b'R', 12)
        self.forced_deadline = None
        raise InstallError(Code.COMMAND_FAILED, RESTART_ERROR)

    def close(self):
        os.close(self.command)
        os.close(self.reply)
        # Reap promptly on ordinary shutdown, but never wait on a stuck startup
        # memory lock or cancel a guardian already committed to forced restart.
        deadline = time.monotonic() + 0.1
        while not os.waitpid(self.pid, os.WNOHANG)[0] and time.monotonic() < deadline:
            time.sleep(0.01)
