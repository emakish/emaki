"""Subprocess logging, secret handling, and confined target file writes."""
import codecs
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


class Runner:
    def __init__(self, log, redactor=None, progress=None):
        self.log = log
        self.redactor = redactor or Redactor()
        self.progress = progress

    def run(self, argv, *, check=True, input=None, secret=False, timeout=None):
        """No shell; secret stdin AND all output of secret commands are suppressed."""
        argv = [str(x) for x in argv]
        self.log(self.redactor.text('$ ' + shlex.join(argv)))
        env = dict(os.environ, LC_ALL='C', LANG='C', SYSTEMD_COLORS='0',
                   SYSTEMD_PAGER='cat', PAGER='cat')
        chunks, pending = [], ''
        retained = 0
        dropping = False
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            with subprocess.Popen(argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env) as process:
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
                        if timeout is not None and time.monotonic() - start > timeout:
                            process.kill()
                            process.wait()
                            raise InstallError(Code.COMMAND_FAILED, f'{argv[0]} timed out.')
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
                            segments = re.split(r'[\r\n]', decoded)
                            for index, segment in enumerate(segments):
                                if not dropping:
                                    pending += segment
                                    if len(pending) > 65536:
                                        self.log('Oversized subprocess log line suppressed.')
                                        pending, dropping = '', True
                                if index < len(segments) - 1:
                                    if not dropping:
                                        self._line(pending)
                                    pending, dropping = '', False
                    if not dropping and not secret:
                        self._line(pending + decoder.decode(b'', final=True))
                status = process.wait()
        except OSError as exc:
            raise InstallError(Code.COMMAND_FAILED, f'Cannot run {argv[0]}: {exc.strerror}.') from exc
        self.log(f'{Path(argv[0]).name}: exit {status}' + (' (private input/output)' if secret else ''))
        if check and status:
            raise InstallError(Code.COMMAND_FAILED, f'{Path(argv[0]).name} exited with status {status}.')
        return '' if secret else ''.join(chunks)

    def _line(self, line):
        if line:
            self.log(self.redactor.text(line))
            if self.progress:
                match = re.search(r'\((\d+)\s*/\s*(\d+)\)', line)
                if match and int(match[2]):
                    self.progress(min(99, 100 * int(match[1]) / int(match[2])))

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
                    runner.log(f'Stopping target process {pid}: {sig.name}')
                    signal.pidfd_send_signal(fd, sig)
                    signalled = True
                finally:
                    os.close(fd)
            except ProcessLookupError:
                pass
            except OSError as exc:
                runner.log(f'Could not stop target process {pid}: {exc}')
        if sig == signal.SIGTERM and signalled:
            time.sleep(0.5)


def cleanup(runner, roots, *, lazy=False):
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
                        runner.log(f'Unmount failed after {attempts} attempts: {mount}')
                        try:
                            runner.run(['fuser', '-vm', '--', str(mount)], check=False)
                        except InstallError as diagnostic:
                            runner.log('fuser diagnostic failed: ' + diagnostic.message)
                        remaining = processes_under(root) if root.exists() else []
                        runner.log('Processes rooted inside target: ' +
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
