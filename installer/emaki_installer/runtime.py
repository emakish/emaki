"""Subprocess logging, secret handling, and confined target file writes."""
import codecs
from contextlib import contextmanager
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import time

from .constants import TARGET
from .errors import Code, InstallError, require


class Redactor:
    def __init__(self):
        self.secrets = set()

    def add(self, value):
        if isinstance(value, str) and value:
            self.secrets.add(value)

    def text(self, value):
        value = str(value)
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)(password|passwd|token)([\s"\x27:=]+)([^\s,}\]]+)',
                       r'\1\2[REDACTED]', value)
        # Remove terminal control sequences before displaying logs in QML.
        value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
        return ''.join(ch for ch in value if ord(ch) >= 32 or ch == '\t')


def safe_log(log, line):
    """A failing log sink (a full disk) must never stop a command or the cleanup."""
    try:
        log(line)
    except Exception:
        pass


# Seconds a timed-out command's process group gets after SIGTERM, and after SIGKILL.
KILL_GRACE = 5.0


def group_running(group, proc=Path('/proc')):
    """True while a process of the group is alive; zombies do not count."""
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            text = (entry / 'stat').read_text()
        except OSError:
            continue  # A process can exit during the scan.
        # The command name in parentheses may itself contain spaces and parentheses.
        fields = text[text.rindex(')') + 2:].split()
        if len(fields) > 2 and fields[2] == str(group) and fields[0] != 'Z':
            return True
    return False


def stop_group(process):
    """Stop a timed-out command with everything it started; True when nothing is left.

    arch-chroot runs the real command as a grandchild, so killing the direct child
    alone leaves it running. The leader is reaped only after both signals were sent:
    until then its process group ID cannot be reused by an unrelated process.
    """
    group = process.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + KILL_GRACE
        while group_running(group) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not group_running(group):
            break
    process.wait()
    deadline = time.monotonic() + KILL_GRACE
    while True:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def stop_group_bounded(process, budget):
    """Allow one total TERM/KILL budget, without waiting for an unkillable child.

    Used only after target cleanup, when a resident restart guardian supplies the
    final fallback. Disk-writing commands retain the stricter stop_group path.
    """
    deadline = time.monotonic() + budget
    for sig, until in ((signal.SIGTERM, deadline - budget / 2),
                       (signal.SIGKILL, deadline)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.poll()
            return True
        # Do not reap the leader until both signals have been sent: its group ID
        # must not become available for an unrelated process in the meantime.
        while time.monotonic() < until:
            time.sleep(min(0.05, max(0, until - time.monotonic())))
    process.poll()  # WNOHANG, including for a child stuck in uninterruptible I/O.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return True
    return False


@contextmanager
def command_process(argv, termination_timeout=None, **kwargs):
    process = subprocess.Popen(argv, **kwargs)
    if termination_timeout is None:
        with process:
            yield process
        return
    completed = False
    try:
        yield process
        completed = True
    finally:
        # Popen.__exit__ calls wait() without a deadline even after SIGKILL.
        # This optional path must return to the guardian while I/O is stuck.
        if not completed:
            stop_group_bounded(process, termination_timeout)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


class Runner:
    def __init__(self, log, redactor=None, progress=None):
        self.log = log
        self.redactor = redactor or Redactor()
        self.progress = progress

    def run(self, argv, *, check=True, input=None, secret=False, timeout=None, watch=None, cancelled=None, quiet=False,
            termination_timeout=None):
        """No shell; secret stdin AND all output of secret commands are suppressed.

        watch(line) sees each logged line of this command; a failing watch is ignored.
        termination_timeout bounds extra cleanup time for post-install restart only;
        it may leave an unkillable process behind and is unsuitable for disk writes.
        """
        if termination_timeout is not None and (timeout is None or termination_timeout <= 0 or input is not None):
            raise ValueError("Bounded termination requires a timeout, a positive budget and no stdin.")
        argv = [str(x) for x in argv]
        if not quiet:
            safe_log(self.log, self.redactor.text('$ ' + shlex.join(argv)))
        if self.progress and not secret and not quiet:
            self.progress(command=argv)
        env = dict(os.environ, LC_ALL='C', LANG='C', SYSTEMD_COLORS='0',
                   SYSTEMD_PAGER='cat', PAGER='cat')
        chunks, pending = [], ''
        retained = 0
        dropping = False
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            # Its own session: a timeout reaches every process the command started.
            with command_process(argv, termination_timeout=termination_timeout,
                                 stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                 start_new_session=True) as process:
                if input is not None:
                    data = input.encode() if isinstance(input, str) else input
                    try:
                        process.stdin.write(data)
                        process.stdin.close()
                    except BrokenPipeError:
                        pass
                start = time.monotonic()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        if cancelled is not None and cancelled.is_set():
                            if termination_timeout is None:
                                stop_group(process)
                            raise InstallError(Code.CANCELLED, 'Update download stopped.')
                        if timeout is not None and time.monotonic() - start > timeout:
                            self._timed_out(process, argv, termination_timeout)
                        for key, _ in selector.select(0.2):
                            data = os.read(key.fd, 16384)
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            if secret:
                                continue
                            # Bound retained stdout to 8 MiB; stream all log lines.
                            decoded = decoder.decode(data)
                            if retained < 8 * 1024 * 1024:
                                chunks.append(decoded)
                                retained += len(decoded)
                            if quiet:
                                continue
                            segments = re.split(r'[\r\n]', decoded)
                            for index, segment in enumerate(segments):
                                if not dropping:
                                    pending += segment
                                    if len(pending) > 65536:
                                        safe_log(self.log, 'Oversized subprocess log line suppressed.')
                                        pending, dropping = '', True
                                if index < len(segments) - 1:
                                    if not dropping:
                                        self._line(pending, watch)
                                    pending, dropping = '', False
                    if not dropping and not secret and not quiet:
                        self._line(pending + decoder.decode(b'', final=True), watch)
                if cancelled is not None:
                    while process.poll() is None:
                        if cancelled.is_set():
                            if termination_timeout is None:
                                stop_group(process)
                            raise InstallError(Code.CANCELLED, 'Update download stopped.')
                        if timeout is not None and time.monotonic() - start > timeout:
                            self._timed_out(process, argv, termination_timeout)
                        time.sleep(0.05)
                    status = process.returncode
                elif timeout is None:
                    status = process.wait()
                else:
                    # The output can close long before the command ends.
                    try:
                        status = process.wait(max(0, timeout - (time.monotonic() - start)))
                    except subprocess.TimeoutExpired:
                        self._timed_out(process, argv, termination_timeout)
        except OSError as exc:
            raise InstallError(Code.COMMAND_FAILED, f'Cannot run {argv[0]}: {exc.strerror}.') from exc
        if not quiet:
            safe_log(self.log, f'{Path(argv[0]).name}: exit {status}' + (' (private input/output)' if secret else ''))
        if self.progress and not secret and not quiet and status == 0:
            self.progress(command_done=argv)
        if check and status:
            raise InstallError(Code.COMMAND_FAILED, f'{Path(argv[0]).name} exited with status {status}.',
                               output='' if secret else self.redactor.text(''.join(chunks)), returncode=status)
        return '' if secret else ''.join(chunks)

    def _timed_out(self, process, argv, termination_timeout=None):
        if termination_timeout is None and not stop_group(process):
            safe_log(self.log, f'{Path(argv[0]).name}: processes of the stopped command are still running.')
        raise InstallError(Code.COMMAND_FAILED, f'{argv[0]} timed out.')

    def _line(self, line, watch=None):
        if line:
            safe_log(self.log, self.redactor.text(line))
            if watch:
                # Like the log: a progress display must never stop the command it follows.
                safe_log(watch, self.redactor.text(line))
            if self.progress:
                self.progress(line=self.redactor.text(line))

    def chroot(self, argv, target=TARGET, **kwargs):
        return self.run(['arch-chroot', str(target), *argv], **kwargs)


class TargetFiles:
    """Never follow an existing target symlink out into the live system."""
    def __init__(self, root):
        self.root = Path(root)

    def path(self, name):
        require(name.startswith('/') and '..' not in Path(name).parts,
                Code.UNSAFE_DISK, 'Invalid target path.')
        path = self.root / name.lstrip('/')
        current = self.root
        require(not current.is_symlink(), Code.UNSAFE_DISK, 'Target root is a symlink.')
        for part in Path(name).parts[1:]:
            current /= part
            require(not current.is_symlink(), Code.UNSAFE_DISK, f'Target symlink refused: {name}.')
        return path

    def mkdir(self, name, mode=0o755):
        p = self.path(name)
        p.mkdir(parents=True, exist_ok=True, mode=mode)
        return p

    def write(self, name, text, mode=0o644):
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        # A new inode also avoids writes through existing hard links.
        tmp = p.with_name(p.name + '.emaki-new')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(text.encode() if isinstance(text, str) else text)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)

    def read(self, name):
        p = self.path(name)
        require(p.is_file(), Code.UNSAFE_DISK, f'Required target file missing: {name}.')
        return p.read_text()


def mounts_under(root, mountinfo=None):
    text = Path('/proc/self/mountinfo').read_text() if mountinfo is None else mountinfo
    result = []
    for row in text.splitlines():
        fields = row.split()
        if len(fields) < 6:
            continue
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        p = Path(name)
        if p == root or root in p.parents:
            result.append(p)
    return sorted(result, key=lambda p: len(p.parts), reverse=True)


def processes_under(root, proc=Path('/proc')):
    """Only chrooted processes, never live processes with a cwd under target."""
    root = Path(root).resolve(strict=True)
    require(root != Path('/'), Code.UNSAFE_DISK, 'Refusing process cleanup of the live root.')
    result = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            process_root = (entry / 'root').resolve(strict=True)
        except (OSError, RuntimeError):
            continue  # A process can exit during the scan.
        if process_root == root or root in process_root.parents:
            result.append((int(entry.name), process_root))
    return result


def stop_target_processes(runner, root):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        signalled = False
        for pid, _ in processes_under(root):
            try:
                # Pin the process before rechecking its root: a recycled PID
                # must never redirect a signal to an unrelated live process.
                fd = os.pidfd_open(pid)
                try:
                    if pid not in dict(processes_under(root)):
                        continue
                    safe_log(runner.log, f'Stopping target process {pid}: {sig.name}')
                    signal.pidfd_send_signal(fd, sig)
                    signalled = True
                finally:
                    os.close(fd)
            except ProcessLookupError:
                pass
            except OSError as exc:
                safe_log(runner.log, f'Could not stop target process {pid}: {exc}')
        if sig == signal.SIGTERM and signalled:
            time.sleep(0.5)


def cleanup(runner, roots, *, lazy=False, before_unmount=None):
    """before_unmount runs once the roots' processes were stopped, while they are still mounted."""
    errors = []
    if not lazy:
        for root in roots:
            if root.exists():
                stop_target_processes(runner, root)
        for command in (['sync'], ['udevadm', 'settle', '--timeout=10']):
            try:
                runner.run(command)
            except InstallError as exc:
                errors.append(exc.message)
        if before_unmount is not None:
            try:
                before_unmount()
            except Exception as exc:
                # It must never keep the target mounted.
                safe_log(runner.log, 'WARNING: the check before unmounting failed: ' + str(exc))
    for root in roots:
        for mount in mounts_under(root):
            attempts = 1 if lazy else 10
            for attempt in range(attempts):
                try:
                    runner.run(['umount', *(['-l'] if lazy else []), '--', str(mount)])
                    break
                except InstallError as exc:
                    if attempt + 1 < attempts:
                        time.sleep(min(0.5 * 2 ** attempt, 2.0))
                        continue
                    errors.append(exc.message)
                    if not lazy:
                        safe_log(runner.log, f'Unmount failed after {attempts} attempts: {mount}')
                        try:
                            runner.run(['fuser', '-vm', '--', str(mount)], check=False)
                        except InstallError as diagnostic:
                            safe_log(runner.log, 'fuser diagnostic failed: ' + diagnostic.message)
                        remaining = processes_under(root) if root.exists() else []
                        safe_log(runner.log, 'Processes rooted inside target: ' +
                                 (', '.join(f'{pid} root={path}' for pid, path in remaining) or 'none'))
    require(not errors, Code.CLEANUP_FAILED, '; '.join(errors))


def secure_log(path):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        os.close(fd)
        raise InstallError(Code.UNSAFE_DISK, 'Unsafe log destination.')
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, 'a', buffering=1)
