#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build and install one reviewed AUR plan.

  emaki-programs-aur PACKAGE TOKEN       (through pkexec) plan again, then hand over
  emaki-programs-aur --run PACKAGE TOKEN (system-owned service) the transaction
  emaki-programs-aur --build BASE COMMIT (throwaway unprivileged user) one build

The plan is computed again as root and must equal the one the person reviewed.
The transaction belongs to a transient system service, so closing the window
does not stop it. Every recipe runs as a throwaway user with no home folder
and a read-only system; only its finished packages are installed. A receipt
records what the install added so that it can be undone exactly.
"""
import codecs
from collections import deque
import fcntl
import io
import json
import os
import pwd
import stat
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))

SERVICE = 'emaki-programs-aur'
HELPER = str(Path(__file__).resolve())
NAME = re.compile(r'[a-z0-9@_+][a-z0-9@._+-]{0,127}\Z')
TOKEN = re.compile(r'[0-9a-f]{64}\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
KEY = re.compile(r'[0-9A-F]{40}\Z')
LOCK = '/run/emaki-update-manager.lock'
SNAPSHOTS = Path('/.snapshots')
# Finished packages are copied out of the build user's reach before they are checked.
PACKAGES = Path('/var/cache/emaki/programs-aur')
BUILD_CACHE = 'emaki-programs-aur'
# The build user can write only its own cache directory; it never sees a home folder.
SANDBOX = ('DynamicUser=yes', 'CacheDirectory=' + BUILD_CACHE, 'ProtectHome=yes', 'PrivateTmp=yes',
           'NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectKernelTunables=yes',
           'ProtectKernelModules=yes', 'ProtectControlGroups=yes', 'RestrictSUIDSGID=yes',
           'LockPersonality=yes', 'RuntimeMaxSec=6h')
KEYSERVER = 'hkps://keyserver.ubuntu.com'


def emit(event, **fields):
    print(json.dumps(dict(type=event, **fields)), flush=True)


def finish(code, ok=False, **fields):
    emit('finished', ok=ok, code=code, action='install', kind='aur', **fields)
    return 0 if ok else 1


def progress(message):
    emit('progress', message=message)


# The build, as the throwaway user ---------------------------------------------

def srcinfo_keys(text):
    keys = []
    for line in text.splitlines():
        name, separator, value = line.strip().partition(' = ')
        if separator and name == 'validpgpkeys' and KEY.fullmatch(value.strip().upper()):
            keys.append(value.strip().upper())
    return sorted(set(keys))


def build(base, commit):
    """Fetch the reviewed recipe commit and build it; print the finished files."""
    if os.geteuid() == 0:
        raise RuntimeError('A recipe never runs as root.')
    root = Path(os.environ['CACHE_DIRECTORY'].split(':')[0])
    work = root / 'build'
    shutil.rmtree(work, ignore_errors=True)
    (work / 'out').mkdir(parents=True)
    environment = dict(PATH=os.environ.get('PATH', os.defpath), LC_ALL='C', HOME=str(root),
                       GNUPGHOME=str(root / 'gnupg'), PKGDEST=str(work / 'out'),
                       SRCDEST=str(work / 'sources'), BUILDDIR=str(work / 'tmp'))
    Path(environment['GNUPGHOME']).mkdir(mode=0o700, exist_ok=True)
    source = work / 'recipe'

    def run(arguments, cwd=None):
        result = subprocess.run(arguments, cwd=cwd or root, env=environment, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError(' '.join(arguments[:2]) + ' failed with status ' + str(result.returncode))

    run(['git', 'clone', '--quiet', '--no-checkout', 'https://aur.archlinux.org/' + base + '.git', str(source)])
    run(['git', '-c', 'advice.detachedHead=false', 'checkout', '--quiet', commit], cwd=source)
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=source, env=environment,
                          capture_output=True, text=True).stdout.strip()
    if head != commit:
        raise RuntimeError('The recipe is not the reviewed commit.')
    try:
        keys = srcinfo_keys((source / '.SRCINFO').read_text(errors='replace'))
    except OSError:
        keys = []
    if keys:
        # The reviewed recipe names the keys its sources are signed with.
        run(['gpg', '--batch', '--keyserver', KEYSERVER, '--recv-keys', *keys])
    run(['makepkg', '--noconfirm', '--noprogressbar', '--cleanbuild', '--clean'], cwd=source)
    files = sorted(path.name for path in (work / 'out').iterdir() if path.is_file())
    print(json.dumps(dict(type='built', files=files)), flush=True)


# The transaction, as root ------------------------------------------------------

def modules():
    import programs_arch as arch
    import programs_aur as aur
    import programs_receipts as receipts
    import emaki_update_root
    return arch, aur, receipts, emaki_update_root


def environment():
    arch = modules()[0]
    return dict(arch.ENV)


def run(arguments, timeout=None, **options):
    return subprocess.run(arguments, cwd='/', env=environment(), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout, **options)


def versions():
    result = run(['pacman', '-Q'], timeout=60)
    if result.returncode:
        raise RuntimeError('The installed package versions could not be read.')
    return dict(line.split(maxsplit=1) for line in result.stdout.splitlines())


def reviewed_plan(target, token):
    arch, aur, _, _ = modules()
    require_consent()
    plan = aur.build_plan(target, aur.System(aur.fetch_json, dict(arch.ENV), refresh=True))
    if plan['token'] != token:
        raise ValueError('The AUR program or its packages changed. Review the install again.')
    return plan


def require_consent():
    """Read the authenticated caller's acceptance again at the privileged boundary."""
    _, aur, _, _ = modules()
    caller = os.environ.get('PKEXEC_UID') or os.environ.get('EMAKI_AUR_UID', '')
    message = 'Open the AUR section and accept its warning before installing.'
    if not caller.isascii() or not caller.isdecimal() or int(caller) == 0:
        raise ValueError(message)
    try:
        person = pwd.getpwuid(int(caller))
        path = aur.consent_path(Path(person.pw_dir) / '.config')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != person.pw_uid
                    or metadata.st_mode & 0o022 or metadata.st_size > 65536):
                raise ValueError(message)
            value = json.loads(source.read(65537))
        if (not isinstance(value, dict) or value.get('version') != 1
                or value.get('warning') != aur.WARNING_VERSION
                or not isinstance(value.get('accepted'), str) or not value['accepted']):
            raise ValueError(message)
    except (OSError, KeyError, ValueError) as error:
        raise ValueError(message) from error
    return str(person.pw_uid)


def require_update_root():
    _, _, _, guard = modules()
    result = run([guard.ROLLBACK, 'status', '--json'], timeout=20)
    try:
        mode = json.loads(result.stdout)['mode'] if result.returncode == 0 else ''
    except (ValueError, KeyError, TypeError):
        mode = ''
    if mode == 'unsupported':
        raise RuntimeError('AUR installs need system snapshots. This system does not support them, so nothing was installed.')
    if mode in ('pending', 'snapshot'):
        raise RuntimeError('Restart into the normal system before installing programs.')
    if mode != 'normal':
        raise RuntimeError('The system restore status could not be checked. Nothing was installed.')


def discard_snapshot(number):
    """Remove a point that cannot be needed by an install transaction."""
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(number)):
        return False
    try:
        result = run(['snapper', '--no-dbus', '-c', 'root', 'delete', str(number)], timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def preinstall_snapshot():
    """Create our own pre point and verify its receipt and read-only btrfs subvolume."""
    import restore
    message = 'A verified system snapshot could not be created. Nothing was installed.'
    result = run(['snapper', '--no-dbus', '-c', 'root', 'create', '--type', 'pre',
                  '--print-number', '--cleanup-algorithm', 'number', '--description', 'AUR install'], timeout=120)
    number = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[1-9][0-9]{0,18}', number):
        raise RuntimeError(message)
    directory = SNAPSHOTS / number
    try:
        row = restore.receipt(directory)
        if row['kind'] != 'pre':
            raise ValueError('not a pre snapshot')
        snapshot = str(directory / 'snapshot')
        subvolume = run(['btrfs', 'subvolume', 'show', snapshot], timeout=20)
        readonly = run(['btrfs', 'property', 'get', '-ts', snapshot, 'ro'], timeout=20)
        if subvolume.returncode or readonly.returncode or readonly.stdout.strip() != 'ro=true':
            raise ValueError('unverified subvolume')
    except (OSError, ValueError, subprocess.SubprocessError, restore.ET.ParseError) as error:
        discard_snapshot(number)
        raise RuntimeError(message) from error
    return number, number + ':' + row['digest']


def stream(arguments, prefix=''):
    """Run one step and relay its output lines as progress; return its status."""
    with subprocess.Popen(arguments, cwd='/', env=environment(), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
        lines = deque(maxlen=200)
        hook_failed = False
        line = ''
        pending = False
        decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder('utf-8')('replace'), True)

        def relay():
            nonlocal hook_failed
            lines.append(line)
            hook_failed |= line.lower() == 'error: command failed to execute correctly'
            if line:
                progress(prefix + line)

        # read1 relays available output without waiting for a full chunk. Neither
        # decoding nor sanitizing ever sees an entire untrusted, unbounded line.
        while True:
            chunk = process.stdout.read1(4096)
            parts = decoder.decode(chunk, final=not chunk).split('\n')
            for index, part in enumerate(parts):
                if index:
                    relay()
                    line, pending = '', False
                pending |= bool(part)
                if len(line) < 2048:
                    line += ''.join(char for char in part if char.isprintable() or char == '\t')[:2048 - len(line)]
            if not chunk:
                break
        if pending:
            relay()
        status = process.wait()
        if hook_failed:
            status = status or 1
        return status, list(lines)


def sandbox_build(base, commit):
    unit = 'emaki-programs-aur-build-' + uuid.uuid4().hex[:12]
    properties = ['--property=' + value for value in SANDBOX]
    status, lines = stream(['systemd-run', '--wait', '--pipe', '--collect', '--quiet', '--unit=' + unit,
                            *properties, '--setenv=LC_ALL=C', '--setenv=PATH=' + environment()['PATH'],
                            HELPER, '--build', base, commit], prefix=base + ': ')
    files = []
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') == 'built' and isinstance(event.get('files'), list):
            files = event['files']
            break
    if status or not files:
        raise RuntimeError('Building ' + base + ' did not finish. The details are above.')
    return unit, files


def collect(base, files, wanted):
    """Copy the finished packages out of the build user's reach and check their names."""
    output = Path('/var/cache/private') / BUILD_CACHE / 'build' / 'out'
    target = PACKAGES / uuid.uuid4().hex
    target.mkdir(mode=0o700, parents=True)
    chosen = {}
    for name in files:
        if '/' in name or name.startswith('.') or not name.endswith(('.pkg.tar.zst', '.pkg.tar.xz', '.pkg.tar.gz')):
            continue
        source = output / name
        if source.is_symlink() or not source.is_file():
            continue
        copy = target / name
        shutil.copyfile(source, copy, follow_symlinks=False)
        os.chmod(copy, 0o600)
        query = run(['pacman', '-Qp', str(copy)], timeout=60)
        if query.returncode:
            continue
        package, _, version = query.stdout.strip().partition(' ')
        if package in wanted and NAME.fullmatch(package):
            chosen[package] = (str(copy), version)
    missing = sorted(set(wanted) - set(chosen))
    if missing:
        raise RuntimeError('The build of ' + base + ' did not produce: ' + ', '.join(missing) + '.')
    return target, chosen


def remove_leftovers(before, additions):
    """Remove new build-only orphans, retry failures, and verify no eligible tools remain."""
    removed = []
    failures = 0
    for _ in range(32):
        try:
            orphans = run(['pacman', '-Qdttq'], timeout=60)
            if (orphans.returncode not in (0, 1) or orphans.stderr.strip()
                    or (orphans.returncode and orphans.stdout.strip())):
                failures += 1
            else:
                names = sorted(name for name in orphans.stdout.split() if name not in before and name in additions)
                if not names:
                    return removed
                status, _ = stream(['pacman', '-Rn', '--noconfirm', '--', *names])
                if not status:
                    removed += names
                    continue
                failures += 1
        except (OSError, subprocess.SubprocessError):
            failures += 1
        if failures >= 2:
            break
        progress('Removing build tools did not finish. Trying again.')
    raise RuntimeError('Build tools could not be removed. The install was not completed.')


def build_only_packages(plan):
    """The reviewed tools that were not also selected as runtime dependencies."""
    build, runtime = set(), set()
    for row in plan['packages']:
        (build if row.get('purpose') == 'build' else runtime).add(row['package'])
    for step in plan['builds']:
        names = {row['package'] for row in step['packages']}
        (build if step.get('purpose') == 'build' else runtime).update(names)
    return build - runtime


def install_state(baseline, state, changes):
    """Receipt state contains only changes made by this install."""
    result = dict(baseline)
    for name in changes:
        if name in state:
            result[name] = state[name]
        else:
            result.pop(name, None)
    return result


def add_only(arguments, baseline, additions):
    """Install previewed additions and upgrades; default No refuses conflict removals."""
    state = versions()
    preview = run([*arguments[:2], '--needed', '--print', '--print-format', '%n %v',
                   '--noconfirm', *arguments[2:]], timeout=120)
    if preview.returncode:
        raise RuntimeError('The packages to install could not be checked.')
    selected = set()
    for line in preview.stdout.splitlines():
        name, separator, version = line.partition(' ')
        if not separator or not NAME.fullmatch(name) or not version:
            raise RuntimeError('The packages to install could not be checked.')
        selected.add(name)
    # Keep the selected scope even if an interrupted command cannot report its result.
    previous_changes = set(additions)
    additions.update(selected)
    # --print skips conflict checks. The real transaction still checks them, with
    # --noconfirm selecting No for ALPM_QUESTION_CONFLICT_PKG; never add --ask.
    install_arguments = [argument for argument in arguments[2:] if argument != '--asdeps']
    status, _ = stream([*arguments[:2], '--needed', '--noconfirm', *install_arguments])
    after = versions()
    # A transaction-wide --asdeps also demotes existing explicit packages.
    # Keep their reasons and mark only packages this transaction added.
    new_dependencies = sorted((selected - set(state)) & set(after))
    if new_dependencies:
        reason_status, _ = stream(['pacman', '-D', '--asdeps', '--', *new_dependencies])
        status = status or reason_status
    # External transactions during a build are not part of this install's receipt.
    additions.difference_update(name for name in selected - previous_changes
                                if state.get(name) == after.get(name))
    return status


def rollback_additions(baseline, additions):
    """Undo install additions, then check only packages this install changed."""
    current = versions()
    added = sorted((set(current) - set(baseline)) & additions)
    if added:
        progress('Removing packages added by the failed install. The completed system update will stay.')
        for _ in range(2):
            status, _ = stream(['pacman', '-Rn', '--noconfirm', '--', *added])
            current = versions()
            added = sorted((set(current) - set(baseline)) & additions)
            if not added:
                break
        if added:
            raise RuntimeError('Some packages from the failed install remain. Restore the pre-install snapshot before trying again.')
    if install_state(baseline, versions(), additions) != baseline:
        raise RuntimeError('The package state after the failed install could not be restored. Use the pre-install snapshot.')


def worker(target, token):
    arch, aur, receipts, _ = modules()
    descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return finish('busy', notStarted=True, message='Another package transaction is running.')
        try:
            require_update_root()
            plan = reviewed_plan(target, token)
            before = versions()
            preflight, _ = preinstall_snapshot()
            if not discard_snapshot(preflight):
                raise RuntimeError('The snapshot check could not be cleaned up. Nothing was installed.')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, aur.Refused) as error:
            return finish('refused', notStarted=True, message=str(error))
        snapshot, snapshot_token = '', ''
        baseline = None
        additions = set()
        built = []
        cleaned = []
        receipt = None
        try:
            progress('Bringing the system up to date first.')
            status, _ = stream(['systemd-inhibit', '--what=sleep:handle-lid-switch', '--mode=block',
                                '--who=Emaki', '--why=Installing a program from the AUR',
                                str(Path(arch.BINDIR) / 'emaki-update'), '--noninteractive'])
            if status:
                raise RuntimeError('The system update did not finish, so nothing was built.')
            baseline = versions()
            snapshot, snapshot_token = preinstall_snapshot()
            needed = [row['package'] for row in plan['packages'] if row['package'] not in baseline]
            if needed:
                progress('Installing what the build needs from Arch.')
                status = add_only(['pacman', '-S', '--asdeps', '--', *needed], baseline, additions)
                if status:
                    raise RuntimeError('Packages from Arch the build needs could not be installed.')
            for step in plan['builds']:
                base = step['packageBase']
                progress('Building ' + base + ' from the AUR as a separate user without access to your files.')
                _, files = sandbox_build(base, step['commit'])
                wanted = [row['package'] for row in step['packages']]
                directory, chosen = collect(base, files, wanted)
                try:
                    status = add_only(['pacman', '-U', '--asdeps',
                                       '--', *[path for path, _ in chosen.values()]], baseline, additions)
                    if not status and target in chosen:
                        status, _ = stream(['pacman', '-D', '--asexplicit', '--', target])
                finally:
                    shutil.rmtree(directory, ignore_errors=True)
                if status:
                    raise RuntimeError(base + ' was built but could not be installed.')
                built += [dict(package=name, version=version) for name, (_, version) in sorted(chosen.items())]
            expected_tools = (build_only_packages(plan) - set(baseline)) & additions
            for attempt in range(2):
                cleaned += remove_leftovers(baseline, additions)
                after = versions()
                if not expected_tools.intersection(after):
                    break
                if attempt == 0:
                    progress('Removing build tools did not finish. Trying again.')
            else:
                raise RuntimeError('Build tools could not be removed. The install was not completed.')
            try:
                receipt = receipts.record('aur', 'aur:' + target, target, target, baseline,
                                          install_state(baseline, after, additions), snapshot,
                                          snapshot_token=snapshot_token)
            except (OSError, ValueError, RuntimeError) as error:
                raise RuntimeError('The install could not be saved for Undo. The added packages will be removed.') from error
            code, ok, message = 'success', True, 'The program from the AUR is installed.'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            code, ok, message = 'failed', False, str(error)
        if not ok and baseline is not None:
            try:
                rollback_additions(baseline, additions)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                code, message = 'partial', message + ' ' + str(error)
        try:
            after = versions()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return finish('unknown', message='The result could not be checked. Look at Installed before trying again.')
        if not ok and snapshot and not additions:
            if not discard_snapshot(snapshot):
                message += ' The unused snapshot will be removed by scheduled cleanup.'
        restart, sign_out = update_backend().changed(before, after)
        if not ok and baseline is not None and install_state(baseline, after, additions) != baseline:
            code = 'partial'
        return finish(code, ok=ok, message=message, built=built, removedAfterBuild=cleaned,
                      receipt=receipt['id'] if receipt else '', restart=restart, signOut=sign_out,
                      snapshot=snapshot if code == 'partial' else '',
                      restoreToken=snapshot_token if code == 'partial' else '')


def update_backend():
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    directory = Path(__file__).resolve().parent
    path = directory / 'emaki-update-apply'
    if not path.exists():
        path = directory / 'apply.py'
    loader = SourceFileLoader('programs_aur_apply', str(path))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def request(target, token):
    """Check the request, then let a system service own the transaction and relay it."""
    require_update_root()
    caller = require_consent()
    reviewed_plan(target, token)
    field = 'EMAKI_AUR_REQUEST=' + uuid.uuid4().hex
    progress('Starting the install from the AUR.')
    finished, cursor = None, ''
    with subprocess.Popen(['systemd-run', '--wait', '--collect', '--unit=' + SERVICE,
                           '--property=Type=exec', '--property=StandardOutput=journal',
                           '--property=StandardError=journal', '--property=LogExtraFields=' + field,
                           '--setenv=LC_ALL=C', '--setenv=PATH=' + environment()['PATH'],
                           '--setenv=EMAKI_AUR_UID=' + caller,
                           HELPER, '--run', target, token],
                          cwd='/', env=environment(), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as starter:
        stopped = False
        while True:
            log = run(['journalctl', '--quiet', '--no-pager', '-o', 'json', '_SYSTEMD_UNIT=' + SERVICE + '.service',
                       '_UID=0', field, *(['--after-cursor', cursor] if cursor else [])], timeout=20)
            for line in log.stdout.splitlines():
                try:
                    entry = json.loads(line)
                    cursor = entry['__CURSOR']
                    message = entry.get('MESSAGE', '')
                    if not isinstance(message, str):
                        continue
                    try:
                        event = json.loads(message)
                    except ValueError:
                        progress(message[:2048])
                        continue
                    if isinstance(event, dict) and event.get('type') == 'finished':
                        finished = event
                    elif isinstance(event, dict) and event.get('type') == 'progress':
                        progress(str(event.get('message', ''))[:2048])
                except (ValueError, KeyError, TypeError):
                    continue
            if stopped:
                break
            stopped = starter.poll() is not None
            if not stopped:
                time.sleep(1)
    if finished:
        emit('finished', **{key: value for key, value in finished.items() if key != 'type'})
        return 0 if finished.get('ok') else 1
    return finish('unknown', message='The service result could not be observed. Check its journal before retrying.')


def validate(arguments):
    if len(arguments) == 3 and arguments[0] == '--build':
        if not NAME.fullmatch(arguments[1]) or not COMMIT.fullmatch(arguments[2]):
            raise ValueError('Invalid build request.')
        return 'build', arguments[1], arguments[2]
    mode = 'run' if arguments[:1] == ['--run'] else 'request'
    values = arguments[1:] if mode == 'run' else arguments
    if len(values) != 2 or not NAME.fullmatch(values[0]) or not TOKEN.fullmatch(values[1]):
        raise ValueError('Select one AUR program and its reviewed install.')
    return mode, values[0], values[1]


def main(argv):
    try:
        mode, first, second = validate(argv)
    except ValueError as error:
        return finish('refused', notStarted=True, message=str(error))
    if mode == 'build':
        try:
            build(first, second)
            return 0
        except (OSError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            print(str(error), file=sys.stderr, flush=True)
            return 1
    try:
        if os.geteuid() != 0:
            raise ValueError('Authorization is required to install programs from the AUR.')
        os.umask(0o077)
        return worker(first, second) if mode == 'run' else request(first, second)
    except (ValueError, RuntimeError) as error:
        return finish('refused', notStarted=True, message=str(error))
    except (OSError, subprocess.SubprocessError):
        return finish('unknown', message='The service result could not be observed. Check its journal before retrying.')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
