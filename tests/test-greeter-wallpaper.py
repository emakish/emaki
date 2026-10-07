#!/usr/bin/env python3
"""Unprivileged publication, real inotify updates and the greeter's read boundary.

All image/config fixtures are synthetic. No root, home reads, systemd or session bus.
The wallpaper helper's existing test remains unchanged for the C8/user code path.
"""
from runtime_fixture import runtime_path
import hashlib
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import errno
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from unittest.mock import patch
from urllib.parse import unquote, urlparse
from types import SimpleNamespace

from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'shell/helpers'))
import wallpaper

spec = importlib.util.spec_from_file_location('publish_wallpaper', ROOT / 'shell/helpers/publish-wallpaper.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
provision_spec = importlib.util.spec_from_loader('greeter_provision', SourceFileLoader('greeter_provision', str(ROOT / 'scripts/emaki-greeter-provision')))
provisioner = importlib.util.module_from_spec(provision_spec)
provision_spec.loader.exec_module(provisioner)


def png(color, size=(24, 16)):
    output = io.BytesIO()
    Image.new('RGB', size, color).save(output, format='PNG')
    return output.getvalue()


def write_config(path, image, suffix=''):
    path.write_text('[default]\npath = ' + json.dumps(str(image)) + '\nmode = "center"\noffset = 0.5\n' + suffix)


def image_from(config):
    return Path(tomllib.loads(config.read_text())['default']['path'])


def until(predicate, message, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.04)
    raise AssertionError(message)


def purge_checks(base):
    for uid, mode, accepted in ((0, 0o755, True), (1000, 0o755, False), (0, 0o777, False)):
        with patch.object(provisioner.os, 'fstat', return_value=SimpleNamespace(st_uid=uid, st_mode=mode)):
            try:
                provisioner.require_root_directory(-1)
            except ValueError:
                assert not accepted
            else:
                assert accepted
    offline = base / 'purge-root'
    published_base = offline / 'var/lib/emaki-greeter'
    users = published_base / 'users'
    output = users / 'fixture'
    (output / 'wpaperd').mkdir(parents=True)
    (output / 'wpaperd/config.toml').write_text('never read this as root')
    (output / 'wallpaper-fixture.image').write_bytes(b'published copy')
    os.mkfifo(output / 'never-open-fifo')
    state = published_base / 'state/state.json'
    state.parent.mkdir()
    state.write_text('selection state is retained')
    outside = base / 'outside-purge'
    outside.mkdir()
    sentinel = outside / 'private-source'
    sentinel.write_text('source is never opened or removed')
    (output / 'file-link').symlink_to(sentinel)
    os.link(sentinel, output / 'hard-link')
    (output / 'directory-link').symlink_to(outside, target_is_directory=True)
    original_open = os.open
    def directory_open(path, flags, *args, **kwargs):
        assert flags & os.O_DIRECTORY, ('purge attempted to open file contents', path)
        return original_open(path, flags, *args, **kwargs)
    # Simulate root identity/ownership checks only; every actual operation stays
    # inside this user-owned fixture. fchown is recorded, never performed as root.
    def root_context():
        stack = ExitStack()
        stack.enter_context(patch.object(provisioner.os, 'getuid', return_value=0))
        stack.enter_context(patch.object(provisioner.os, 'geteuid', return_value=0))
        stack.enter_context(patch.object(provisioner, 'require_root_directory'))
        stack.enter_context(patch.object(provisioner.os, 'open', side_effect=directory_open))
        ownership = stack.enter_context(patch.object(provisioner.os, 'fchown'))
        return stack, ownership
    context, ownership = root_context()
    with context:
        provisioner.purge_published(offline)
        assert ownership.call_count >= 4, ownership.call_args_list
        assert all(call.args[1:] == (0, 0) for call in ownership.call_args_list)
    assert not users.exists() and not list(published_base.glob('.purge-published-*'))
    assert sentinel.read_text() == 'source is never opened or removed'
    assert state.read_text() == 'selection state is retained'

    # Root-level users link is unlinked without opening its target.
    users.symlink_to(outside, target_is_directory=True)
    context, _ = root_context()
    with context:
        provisioner.purge_published(offline)
    assert not users.is_symlink() and sentinel.exists()
    # A symlink in the fixed ancestor chain is refused before any deletion.
    saved_base = published_base.with_name('saved-greeter')
    published_base.rename(saved_base)
    published_base.symlink_to(outside, target_is_directory=True)
    context, _ = root_context()
    with context:
        try:
            provisioner.purge_published(offline)
            raise AssertionError('purge followed a system ancestor symlink')
        except OSError:
            pass
    assert sentinel.exists()
    published_base.unlink()
    saved_base.rename(published_base)

    # Failure after detach must be explicit and leave a private quarantine.
    (output / 'wpaperd').mkdir(parents=True)
    (output / 'wpaperd/config.toml').write_text('interrupted copy')
    original_rmdir = os.rmdir
    def obstructed_rmdir(name, *args, **kwargs):
        if name == 'fixture':
            raise OSError(errno.ENOTEMPTY, 'simulated concurrent writer')
        return original_rmdir(name, *args, **kwargs)
    context, _ = root_context()
    error = io.StringIO()
    with context, patch.object(provisioner.os, 'rmdir', side_effect=obstructed_rmdir), \
            patch.object(sys, 'argv', ['provision', '--root', str(offline), '--purge-published']), redirect_stderr(error):
        assert provisioner.main() == 1
    assert 'purge incomplete' in error.getvalue()
    assert 'WARNING' in error.getvalue() and str(published_base) in error.getvalue()
    quarantines = list(published_base.glob('.purge-published-*'))
    assert not users.exists() and len(quarantines) == 1
    assert stat.S_IMODE(quarantines[0].stat().st_mode) == 0o700
    context, _ = root_context()
    with context:
        provisioner.purge_published(offline)
    assert not list(published_base.glob('.purge-published-*'))
    assert state.exists() and sentinel.exists()
    # A mounted/different-device subtree is refused before ownership changes.
    mount_parent = base / 'purge-device-check'
    (mount_parent / 'mounted').mkdir(parents=True)
    mount_fd = os.open(mount_parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with patch.object(provisioner.os, 'fchown') as ownership:
            try:
                provisioner.purge_tree(mount_fd, 'mounted', os.fstat(mount_fd).st_dev + 1,
                                      {'entries': 0, 'deadline': time.monotonic() + 10})
                raise AssertionError('purge crossed a filesystem boundary')
            except ValueError:
                pass
            ownership.assert_not_called()
    finally:
        os.close(mount_fd)
    assert (mount_parent / 'mounted').is_dir()
    # The entry bound must also bound enumeration: never materialize an entire
    # user-controlled directory before checking the deletion budget.
    limited = mount_parent / 'limited'
    limited.mkdir()
    for name in ('first', 'second', 'third'):
        (limited / name).write_bytes(b'bounded fixture')
    original_scandir = os.scandir
    enumeration = {'yielded': 0, 'closed': False}
    class BoundedEntries:
        def __init__(self, descriptor):
            self.iterator = original_scandir(descriptor)
        def __enter__(self):
            return self
        def __iter__(self):
            return self
        def __next__(self):
            enumeration['yielded'] += 1
            assert enumeration['yielded'] <= 2, 'purge eagerly enumerated past its budget'
            return next(self.iterator)
        def __exit__(self, *exception):
            self.iterator.close()
            enumeration['closed'] = True
    mount_fd = os.open(mount_parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with patch.object(provisioner.os, 'fchown'), \
                patch.object(provisioner.os, 'scandir', side_effect=BoundedEntries), \
                patch.object(provisioner.os, 'listdir', side_effect=AssertionError('eager purge enumeration')):
            try:
                provisioner.purge_tree(mount_fd, 'limited', os.fstat(mount_fd).st_dev,
                                      {'entries': 4094, 'deadline': time.monotonic() + 10})
                raise AssertionError('purge exceeded its entry budget')
            except ValueError as error:
                assert str(error) == 'purge limit'
    finally:
        os.close(mount_fd)
    assert enumeration == {'yielded': 2, 'closed': True}
    assert len(list(limited.iterdir())) == 2
    # An old install without this tree is a harmless no-op, even in staging.
    provisioner.purge_published(base / 'old-install')


def uninstall_recipe_checks(base):
    """Exercise an inert target copied from the recipe, with bounded command stubs.

    This never calls the real uninstall target, provisioner, rm or rmdir. Stubs
    reject any operation outside this temporary stage before changing fixtures.
    """
    fixture = base / 'uninstall-harness'
    stage = fixture / 'stage'
    stubs = fixture / 'stubs'
    stubs.mkdir(parents=True)
    installed = (
        'usr/bin/emaki', 'usr/bin/emaki-greeter-provision',
        'usr/share/emaki/shell/greeter.qml',
        'usr/lib/systemd/system/greetd.service.d/emaki.conf',
        'usr/lib/systemd/user/emaki-shell.service',
        'usr/lib/systemd/user/emaki-greeter-wallpaper.path',
        'usr/lib/systemd/user/emaki-greeter-wallpaper.service',
        'usr/lib/systemd/user/emaki-greeter-wallpaper-watch.service',
        'usr/lib/tmpfiles.d/emaki-greeter.conf', 'usr/lib/pam.d/emaki-greetd',
        'etc/xdg/hypr/hyprlock.conf', 'etc/niri/config.kdl', 'etc/skel/.config/kitty/kitty.conf',
        # The fork session (install-niri-emaki): its wrapper reads the fork configs under share.
        'usr/bin/niri-emaki-session', 'usr/share/emaki/niri/fork.kdl',
        'usr/lib/systemd/user/niri-emaki.service', 'usr/share/wayland-sessions/niri-emaki.desktop',
    )
    for name in installed:
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('staged installation fixture')
    # The recipe removes an /etc default only when cmp says it equals the shipped source, read
    # relative to the working directory: give the fixture shipped copies equal to the staged ones.
    for source in ('hypr/hyprlock.conf', 'niri/system.kdl', 'etc-skel/.config/kitty/kitty.conf',
                   'packaging/emaki-config/kdeglobals'):
        (fixture / source).parent.mkdir(parents=True, exist_ok=True)
        (fixture / source).write_text('staged installation fixture')
    # Like the real file, the niri entry carries the marker the recipe recognises it by.
    marker = next(line for line in (ROOT / 'Makefile').read_text().splitlines()
                  if line.startswith('NIRI_ETC_MARK = ')).split(' = ', 1)[1]
    for path in (stage / 'etc/niri/config.kdl', fixture / 'niri/system.kdl'):
        path.write_text('// ' + marker + '\nstaged installation fixture\n')
    # An administrator's edit differs from the shipped copy: it is kept and named.
    edited = stage / 'etc/xdg/kdeglobals'
    edited.write_text('staged installation fixture\nedited by the administrator\n')
    wants = stage / 'usr/lib/systemd/user/graphical-session.target.wants'
    wants.mkdir()
    for name in ('emaki-greeter-wallpaper.path', 'emaki-greeter-wallpaper-watch.service'):
        (wants / name).symlink_to('../' + name)
    greeter_root = stage / 'var/lib/emaki-greeter'
    published = greeter_root / 'users/fixture/photo'
    published.parent.mkdir(parents=True)
    published.write_bytes(b'retained after simulated bounded purge failure')
    state = greeter_root / 'state/state.json'
    state.parent.mkdir()
    state.write_text('retained selection state')
    log = fixture / 'commands.jsonl'
    stub = '#!' + sys.executable + '\n' + '''import errno
import json
import os
from pathlib import Path
import shutil
import sys

stage = Path(os.environ['FIXTURE_STAGE']).resolve()
name = Path(sys.argv[0]).name
arguments = sys.argv[1:]
def bounded(value):
    path = Path(value)
    assert path.is_absolute() and '..' not in path.parts
    assert path != stage and path.is_relative_to(stage)
    assert path.parent.resolve().is_relative_to(stage)
    return path
with open(os.environ['FIXTURE_LOG'], 'a') as stream:
    stream.write(json.dumps([name, arguments]) + '\\n')
if name == 'python3':
    assert arguments == ['-I', 'scripts/emaki-greeter-provision', '--root', str(stage), '--purge-published']
    base = bounded(stage / 'var/lib/emaki-greeter')
    quarantine = base / ('.purge-published-' + 'a' * 32)
    quarantine.mkdir(mode=0o700)
    (base / 'users').rename(quarantine / 'users')
    print('fixture: simulated bounded purge failure', file=sys.stderr)
    sys.exit(1)
elif name == 'bash':
    # make uninstall points the staged /etc/os-release back at Arch's file first.
    assert arguments == ['os-release/emaki-os-release', '--restore', str(stage)], arguments
elif name == 'rm':
    for value in arguments:
        if value.startswith('-'):
            continue
        path = bounded(value)
        if path.is_dir() and not path.is_symlink():
            assert '-rf' in arguments
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
elif name == 'rmdir':
    for value in arguments:
        if value.startswith('-'):
            continue
        path = bounded(value)
        try:
            path.rmdir()
        except OSError as error:
            assert '--ignore-fail-on-non-empty' in arguments and error.errno == errno.ENOTEMPTY
elif name == 'cmp':
    # Read-only, with cmp's exit codes: 0 equal, 1 different, 2 a file is missing.
    assert len(arguments) == 3 and arguments[0] == '-s', arguments
    shipped = Path(arguments[1])
    assert not shipped.is_absolute() and '..' not in shipped.parts, arguments
    assert (Path.cwd() / shipped).resolve().is_relative_to(Path.cwd().resolve()), arguments
    files = (Path.cwd() / shipped, bounded(arguments[2]))
    if not all(path.is_file() for path in files):
        sys.exit(2)
    sys.exit(0 if files[0].read_bytes() == files[1].read_bytes() else 1)
elif name == 'grep':
    assert len(arguments) == 3 and arguments[0] == '-qF', arguments
    path = bounded(arguments[2])
    if not path.is_file():
        sys.exit(2)
    sys.exit(0 if arguments[1] in path.read_text() else 1)
else:
    raise AssertionError('unexpected fixture command: ' + name)
'''
    for name in ('python3', 'bash', 'rm', 'rmdir', 'cmp', 'grep'):
        command = stubs / name
        command.write_text(stub)
        command.chmod(0o700)
    makefile = (ROOT / 'Makefile').read_text()
    recipe = makefile.split('\nuninstall:\n', 1)[1].split('\n# The emaki CLI binary', 1)[0]
    harness = fixture / 'fixture.mk'
    # Every variable the recipe uses must be defined here: an undefined one expands to ''
    # and turns a staged path into a root path, which the stubs reject.
    mark = next(line for line in makefile.splitlines() if line.startswith('NIRI_ETC_MARK = '))
    harness.write_text('''PREFIX = /usr
BIN = $(DESTDIR)$(PREFIX)/bin
SHARE = $(DESTDIR)$(PREFIX)/share/emaki
ICONS = $(DESTDIR)$(PREFIX)/share/icons
XDG = $(DESTDIR)/etc/xdg
SYSTEMD = $(DESTDIR)$(PREFIX)/lib/systemd
PAMDIR = $(DESTDIR)/usr/lib/pam.d
NIRI_ETC = $(DESTDIR)/etc/niri/config.kdl
BOOTLIB = $(DESTDIR)$(PREFIX)/lib/emaki/boot/emaki_boot
BOOT_SHARED = boot errors grub_screen
BOOT_ARTWORK =
''' + mark + '''
.PHONY: fixture-cleanup
fixture-cleanup:
''' + recipe)
    environment = dict(os.environ, PATH=str(stubs), FIXTURE_STAGE=str(stage), FIXTURE_LOG=str(log))
    for key in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'PYTHONPATH', 'PYTHONHOME'):
        environment.pop(key, None)
    command = [shutil.which('make'), '-rR', '--no-print-directory', '-f', str(harness), 'fixture-cleanup']
    for invalid in ('stage', str(fixture / 'ignored/../stage')):
        result = subprocess.run(command + ['DESTDIR=' + invalid], cwd=fixture, env=environment,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and 'nothing changed' in result.stderr, result
        assert not log.exists(), 'invalid DESTDIR reached a side-effect command'
        assert published.exists() and all((stage / name).exists() for name in installed)
    result = subprocess.run(command + ['DESTDIR=' + str(stage)], cwd=fixture, env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result
    assert 'WARNING' in result.stderr and str(greeter_root) in result.stderr, result.stderr
    assert 'Continuing removal' in result.stderr
    remaining = [name for name in installed if (stage / name).exists()]
    assert not remaining, (remaining, result)
    assert edited.read_text() == 'staged installation fixture\nedited by the administrator\n'
    # The command echo shows `$dst`; only the executed line carries the expanded path.
    assert f'uninstall: kept {edited} (it differs from the shipped file)\n' in result.stdout, result
    assert not wants.exists()
    remainder = greeter_root / ('.purge-published-' + 'a' * 32) / 'users/fixture/photo'
    assert remainder.read_bytes() == b'retained after simulated bounded purge failure'
    assert state.read_text() == 'retained selection state'
    names = [json.loads(line)[0] for line in log.read_text().splitlines()]
    # The purge runs (removing the GRUB title hook may come first), and removal continues after it fails.
    assert 'python3' in names and 'rm' in names[names.index('python3') + 1:], names


def package_removal_checks(base):
    """emaki-config's scriptlet purges published copies on removal only, and never fails it.

    Runs a copy of the scriptlet whose provisioner path points at a fixture command; the real
    provisioner then purges a fixture root as a simulated root. Nothing here runs as root or
    touches anything outside this temporary directory.
    """
    package = ROOT / 'packaging/emaki-config'
    assert 'install=emaki-config.install' in (package / 'PKGBUILD').read_text().splitlines(), \
        'emaki-config has no removal scriptlet'
    scriptlet = package / 'emaki-config.install'
    provisioner_path = '/usr/bin/emaki-greeter-provision'
    # The packaged path: pacman's environment fixes no PATH for scriptlets.
    assert provisioner_path in (package / 'expected-files.list').read_text().splitlines()
    assert scriptlet.read_text().count(provisioner_path) == 1
    assert subprocess.run(['bash', '-n', str(scriptlet)]).returncode == 0
    if shutil.which('shellcheck'):
        lint = subprocess.run(['shellcheck', '-s', 'bash', str(scriptlet)], capture_output=True, text=True)
        assert lint.returncode == 0, lint.stdout
    fixture = base / 'package-removal'
    empty_path = fixture / 'no-commands'
    empty_path.mkdir(parents=True)
    log = fixture / 'calls.jsonl'
    command = fixture / 'provisioner'
    copy = fixture / 'emaki-config.install'
    copy.write_text(scriptlet.read_text().replace(provisioner_path, str(command)))
    environment = {'PATH': str(empty_path), 'FIXTURE_LOG': str(log),
                   'FIXTURE_PROVISIONER': str(ROOT / 'scripts/emaki-greeter-provision')}

    def calls():
        lines = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return [json.loads(line) for line in lines]

    def scriptlet_run(script, **extra):
        # As libalpm runs it: source the file, call one function with the old version.
        return subprocess.run([shutil.which('bash'), '-c', '. "$1"; ' + script, 'scriptlet', str(copy), '0.1.2-1'],
                              env=environment | extra, cwd=fixture, capture_output=True, text=True, timeout=30)

    # Sourcing runs nothing; only pre_remove purges, while post_upgrade migrates settings.
    listed = scriptlet_run('declare -F')
    assert listed.returncode == 0 and not listed.stderr, listed
    assert listed.stdout.splitlines() == ['declare -f post_install', 'declare -f post_upgrade',
                                      'declare -f pre_remove', 'declare -f pre_upgrade'], listed.stdout
    assert calls() == []
    warning = ('emaki-config: published wallpaper copies remain in /var/lib/emaki-greeter; '
               'an administrator can remove them\n')
    # A failed or missing purge is named but never fails the removal. No other command runs:
    # PATH is empty, so any would fail with "command not found" on stderr.
    command.write_text('#!' + sys.executable + '''
import json, os, sys
with open(os.environ['FIXTURE_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
sys.exit(int(os.environ['FIXTURE_EXIT']))
''')
    command.chmod(0o700)
    for status, expected in (('0', ''), ('1', warning)):
        result = scriptlet_run('pre_remove "$2"', FIXTURE_EXIT=status)
        assert (result.returncode, result.stdout, result.stderr) == (0, '', expected), result
        assert calls() == [['--root', '/', '--purge-published']]
    command.unlink()
    result = scriptlet_run('pre_remove "$2"')
    assert result.returncode == 0 and result.stderr.endswith(warning), result
    assert calls() == []

    # The real purge, given the root the scriptlet names (pacman chroots into its --root first).
    # Simulated root, as in purge_checks: it may open directories only, never file contents.
    command.write_text('#!' + sys.executable + '''
import importlib.util, json, os, sys
from importlib.machinery import SourceFileLoader
from unittest.mock import patch
sys.dont_write_bytecode = True
with open(os.environ['FIXTURE_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
assert sys.argv[1:] == ['--root', '/', '--purge-published'], sys.argv
spec = importlib.util.spec_from_loader('provisioner', SourceFileLoader('provisioner', os.environ['FIXTURE_PROVISIONER']))
provisioner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provisioner)
original_open = os.open
def directory_open(path, flags, *args, **kwargs):
    assert flags & os.O_DIRECTORY, ('purge opened file contents', path)
    return original_open(path, flags, *args, **kwargs)
sys.argv[1:] = ['--root', os.environ['FIXTURE_ROOT'], '--purge-published']
with patch.object(provisioner.os, 'getuid', return_value=0), patch.object(provisioner.os, 'geteuid', return_value=0), \\
        patch.object(provisioner, 'require_root_directory'), patch.object(provisioner.os, 'fchown'), \\
        patch.object(provisioner.os, 'open', side_effect=directory_open):
    sys.exit(provisioner.main())
''')
    command.chmod(0o700)

    def tree(root):
        # Every entry with its type, mode and contents or link target; link counts are left out.
        entries = {}
        for directory, names, files in os.walk(root):
            for name in names + files:
                path = Path(directory, name)
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    value = os.readlink(path)
                elif stat.S_ISREG(info.st_mode):
                    value = path.read_bytes()
                else:
                    value = None
                entries[str(path.relative_to(root))] = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), value)
        return entries

    root = fixture / 'root'
    greeter = root / 'var/lib/emaki-greeter'
    person = root / 'home/fixture'
    (person / 'Pictures').mkdir(parents=True, mode=0o700)
    (person / 'Pictures/PRIVATE_WALLPAPER.png').write_bytes(png('red'))
    (person / 'Pictures/linked.png').write_bytes(png('blue'))
    (person / '.config/wpaperd').mkdir(parents=True)
    write_config(person / '.config/wpaperd/config.toml', person / 'Pictures/PRIVATE_WALLPAPER.png')
    (root / 'var/lib/other').mkdir(parents=True)
    (root / 'var/lib/other/data').write_text('another package')
    greeter.mkdir(mode=0o755)
    published = greeter / 'users/fixture'
    (published / 'wpaperd').mkdir(parents=True)
    (published / 'wpaperd/config.toml').write_text('path = "published copy"\n')
    (published / 'wallpaper-fixture.png').write_bytes(png('red'))
    # Links a person could leave in their publishing directory: removed as entries only.
    (published / 'file-link').symlink_to(person / 'Pictures/PRIVATE_WALLPAPER.png')
    (published / 'directory-link').symlink_to(person / 'Pictures', target_is_directory=True)
    os.link(person / 'Pictures/linked.png', published / 'hard-link')
    for name in ('state', 'handoff', 'home', 'cache', 'config', 'data'):
        (greeter / name).mkdir(mode=0o700)
        (greeter / name / 'kept').write_text('greeter ' + name)
    before = tree(root)
    first = scriptlet_run('pre_remove "$2"', FIXTURE_ROOT=str(root))
    assert first.returncode == 0 and first.stderr == '', first
    assert 'Published wallpaper copies removed' in first.stdout, first
    after = tree(root)
    # Exactly the published tree went; the person's files and greeter state are unchanged.
    expected = {path: entry for path, entry in before.items()
                if path != 'var/lib/emaki-greeter/users' and not path.startswith('var/lib/emaki-greeter/users/')}
    assert after == expected, (set(expected) ^ set(after), [path for path in after if after[path] != expected.get(path)])
    # Idempotent: a repeated removal finds nothing and changes nothing.
    second = scriptlet_run('pre_remove "$2"', FIXTURE_ROOT=str(root))
    assert second.returncode == 0 and second.stderr == '', second
    assert tree(root) == after
    assert calls() == [['--root', '/', '--purge-published']] * 2
    # A root that never had a greeter, and one whose users entry is unsafe, stay untouched.
    for name in ('never-provisioned', 'unsafe-users'):
        other = fixture / name
        (other / 'var/lib').mkdir(parents=True)
        if name == 'unsafe-users':
            (other / 'var/lib/emaki-greeter').mkdir()
            (other / 'var/lib/emaki-greeter/users').write_text('not a directory')
        before = tree(other)
        result = scriptlet_run('pre_remove "$2"', FIXTURE_ROOT=str(other))
        assert result.returncode == 0 and tree(other) == before, result
        assert result.stderr.endswith(warning) == (name == 'unsafe-users'), result
        assert calls() == [['--root', '/', '--purge-published']]


def default_config_checks(base):
    """The installer's publication before the first login (worker.settings): the new account
    runs this publisher with the default wpaperd config. Its picture passes both sides."""
    assert 'install -Dm644 -t $(SHARE)/wallpaper art/wallpaper/ring.png' in (ROOT / 'Makefile').read_text()
    skel = (ROOT / 'etc-skel/.config/wpaperd/config.toml').read_text()
    installed = '"/usr/share/emaki/wallpaper/'
    assert installed in skel
    config_home = base / 'default-config'
    config = config_home / 'wpaperd/config.toml'
    config.parent.mkdir(parents=True)
    config.write_text(skel.replace(installed, json.dumps(str(ROOT / 'art/wallpaper'))[:-1] + '/'))
    destination = base / 'default-published'
    destination.mkdir(mode=0o750)
    destination.chmod(0o2750)
    with patch.dict(os.environ, XDG_CONFIG_HOME=str(config_home)):
        assert publisher.paths()[0] == config
    assert publisher.publish(config, destination) == {'state': 'published', 'images': 1}
    published = tomllib.loads((destination / 'wpaperd/config.toml').read_text())
    image = Path(next(iter(published.values()))['path'])
    assert image.parent == destination
    assert image.read_bytes() == (ROOT / 'art/wallpaper/fallback.png').read_bytes()
    with patch.dict(os.environ, EMAKI_GREETER_WALLPAPER_ROOT=str(destination), XDG_CACHE_HOME=str(base / 'default-cache'),
                    XDG_CONFIG_HOME='/home/DO_NOT_READ/config', HOME='/home/DO_NOT_READ'):
        assert wallpaper.texture(80, 50, 1, 'Fixture', 'sharp')['state'] == 'ready'


def legacy_toggle_migration_checks():
    root = ROOT
    path = root / 'scripts/emaki-migrate-installer-config'
    spec = importlib.util.spec_from_loader('legacy_migration', SourceFileLoader('legacy_migration', str(path)))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    original = b'include "/usr/share/emaki/niri/default.kdl"\n\ninput {\n    keyboard {\n        xkb {\n            layout "us,ru"\n            options "grp:win_space_toggle"\n        }\n    }\n}\n\noutput "eDP-1" {\n    scale 1.25\n}\n'
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / '.config/niri/config.kdl'
        config.parent.mkdir(parents=True)
        for contents in (original, original + b'// My settings\n', original.replace(b'grp:win_space_toggle', b'grp:alt_shift_toggle'), original.replace(b'            options', b'        options')):
            config.write_bytes(contents)
            config.chmod(0o640)
            tool.migrate(temp)
            expected = contents.replace(tool.LINE, b'', 1) if contents == original else contents
            assert config.read_bytes() == expected
            assert config.stat().st_mode & 0o777 == 0o640
            tool.migrate(temp)
            assert config.read_bytes() == expected
        config.unlink()
        outside = Path(temp) / 'outside'
        outside.write_bytes(original)
        config.symlink_to(outside)
        tool.migrate(temp)
        assert outside.read_bytes() == original
        config.unlink()
        os.link(outside, config)
        tool.migrate(temp)
        assert outside.read_bytes() == original

def pam_account_provision_checks():
    root = ROOT
    assert 'session    optional    pam_exec.so quiet /usr/bin/python3 -I /usr/bin/emaki-greeter-provision --pam-session' in (root / 'greetd/pam').read_text()
    path = root / 'scripts/emaki-greeter-provision'
    spec = importlib.util.spec_from_loader('login_provision', SourceFileLoader('login_provision', str(path)))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with patch.object(tool, 'provision') as provision, patch.object(tool.sys, 'argv', ['provision', '--pam-session']):
        for user in ('first', 'second'):
            with patch.dict(os.environ, {'PAM_TYPE': 'open_session', 'PAM_USER': user}):
                assert tool.main() == 0
            provision.assert_called_with(Path('/'), user)
        assert provision.call_count == 2
        for event, user in (('close_session', 'second'), ('open_session', 'greeter'), ('open_session', 'root'), ('open_session', '')):
            with patch.dict(os.environ, {'PAM_TYPE': event, 'PAM_USER': user}):
                assert tool.main() == 0
        assert provision.call_count == 2


def main():
    legacy_toggle_migration_checks()
    pam_account_provision_checks()
    assert os.getuid() != 0, 'run publishing tests as an ordinary user'
    (ROOT / '.cache').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cw-', dir=ROOT / '.cache') as temporary:
        base = Path(temporary)
        source = base / 'private-home'
        source.mkdir(mode=0o700)
        config = source / 'config.toml'
        image = source / 'PRIVATE_WALLPAPER.png'
        image.write_bytes(png('red'))
        destination = base / 'published'
        destination.mkdir(mode=0o750)
        destination.chmod(0o2750)
        published_config = destination / 'wpaperd/config.toml'
        cache = base / 'cache'
        environment = dict(os.environ, EMAKI_GREETER_WALLPAPER_ROOT=str(destination),
                           XDG_CONFIG_HOME='/home/DO_NOT_READ/config', HOME='/home/DO_NOT_READ',
                           XDG_CACHE_HOME=str(cache))

        def render():
            with patch.dict(os.environ, environment):
                return wallpaper.texture(80, 50, 1, 'Fixture', 'sharp')

        assert publisher.attempt(config, destination)['state'] == 'unpublished'
        assert render()['state'] == 'config_missing', 'no copy selects the existing flat fallback'
        write_config(config, image)
        assert publisher.publish(config, destination) == {'state': 'published', 'images': 1}
        published = image_from(published_config)
        assert published.read_bytes() == image.read_bytes()
        assert str(source) not in published_config.read_text()
        assert stat.S_IMODE(published.stat().st_mode) == 0o640
        assert stat.S_IMODE(published_config.stat().st_mode) == 0o640
        assert stat.S_IMODE(published_config.parent.stat().st_mode) == 0o2750
        # A login/start with the same data keeps both inodes, allowing warm crops.
        before_image = published.stat()
        before_config = published_config.stat()
        publisher.publish(config, destination)
        assert (published.stat().st_ino, published.stat().st_mtime_ns) == (before_image.st_ino, before_image.st_mtime_ns)
        assert (published_config.stat().st_ino, published_config.stat().st_mtime_ns) == (before_config.st_ino, before_config.st_mtime_ns)
        with patch.object(wallpaper, 'open_no_symlinks', wraps=wallpaper.open_no_symlinks) as opens:
            assert render()['state'] == 'ready'
            assert all(Path(call.args[0]).is_relative_to(destination) for call in opens.call_args_list)
        assert not list(base.glob('**/.publish-*')), 'no incomplete atomic writes'

        # Missing selected source or config revokes the public-facing copy. An
        # editor's brief delete/replace gap is rechecked before removing anything.
        private_bytes = image.read_bytes()
        image.unlink()
        assert publisher.attempt(config, destination)['state'] == 'unpublished'
        assert not published_config.exists() and not list(destination.glob('wallpaper-*.image'))
        assert render()['state'] == 'config_missing'
        image.write_bytes(private_bytes)
        publisher.publish(config, destination)
        source_config = config.read_bytes()
        config.unlink()
        assert publisher.attempt(config, destination)['state'] == 'unpublished'
        assert render()['state'] == 'config_missing'
        config.write_bytes(source_config)
        publisher.publish(config, destination)
        original_bytes = published_config.read_bytes()
        config.unlink()
        replacement = threading.Timer(.04, lambda: config.write_bytes(source_config))
        replacement.start()
        try:
            assert publisher.attempt(config, destination)['state'] == 'published'
        finally:
            replacement.join()
        assert published_config.read_bytes() == original_bytes

        # Both sides restrict plugin selection before load. Even a syntactically
        # recognizable EPS with malformed PostScript must never launch Ghostscript.
        eps = source / 'private-malformed.eps'
        eps.write_bytes(b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 24 16\nmalformed-postscript\nshowpage\n')
        write_config(config, eps)
        with patch('subprocess.Popen') as spawned:
            assert publisher.attempt(config, destination)['state'] == 'refused'
            unsafe_copy = destination / 'direct-upload.image'
            unsafe_copy.write_bytes(eps.read_bytes())
            write_config(published_config, unsafe_copy)
            assert render()['state'] == 'image_invalid'
            spawned.assert_not_called()
        unsafe_copy.unlink()
        write_config(config, image)
        publisher.publish(config, destination)
        # PNG, JPEG and WebP remain supported and revalidated in restricted mode.
        for format_name in ('JPEG', 'WEBP'):
            candidate = source / ('accepted-' + format_name)
            Image.new('RGB', (24, 16), 'red').save(candidate, format=format_name)
            write_config(config, candidate)
            assert publisher.publish(config, destination)['state'] == 'published'
            assert render()['state'] == 'ready'
        write_config(config, image)
        publisher.publish(config, destination)

        original_config = published_config.read_bytes()
        # Input failures preserve a coherent last good generation.
        def refused():
            assert publisher.attempt(config, destination)['state'] == 'refused'
            assert published_config.read_bytes() == original_config

        config_link = source / 'config-link.toml'
        config_link.symlink_to(config)
        assert publisher.attempt(config_link, destination)['state'] == 'refused'
        source_link = base / 'linked-home'
        source_link.symlink_to(source, target_is_directory=True)
        assert publisher.attempt(source_link / 'config.toml', destination)['state'] == 'refused'
        linked_image = source / 'image-link.png'
        linked_image.symlink_to(image)
        write_config(config, linked_image)
        refused()
        write_config(config, source_link / image.name)
        refused()
        write_config(config, image)
        with patch.object(publisher.os, 'geteuid', return_value=0):
            refused()
            assert publisher.attempt(config, destination)['reason'] == 'root_refused'
        with patch.object(publisher.os, 'getuid', return_value=0):
            refused()
        destination_link = base / 'published-link'
        destination_link.symlink_to(destination, target_is_directory=True)
        assert publisher.attempt(config, destination_link)['state'] == 'refused'
        destination.chmod(0o2755)
        refused()
        destination.chmod(0o2750)

        oversize = source / 'oversize.image'
        with oversize.open('wb') as stream:
            stream.truncate(wallpaper.MAX_IMAGE + 1)
        write_config(config, oversize)
        refused()
        write_config(config, image)
        with patch.object(publisher, 'MAX_PIXELS', 100):
            refused()
            assert publisher.attempt(config, destination)['reason'] == 'image_limit'
        assert publisher.refusal(OSError(errno.ELOOP, str(source)))['reason'] == 'symlink'
        assert str(source) not in json.dumps(publisher.refusal(ValueError(str(source))))
        config.write_bytes(b'x' * (wallpaper.MAX_CONFIG + 1))
        refused()
        write_config(config, image)
        fifo = source / 'fifo'
        os.mkfifo(fifo)
        write_config(config, fifo)
        refused()
        write_config(config, image)

        # A file swapped for a link exactly at open cannot escape the fd walk.
        original_open = os.open
        swapped = False
        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == image.name and not swapped:
                swapped = True
                image.rename(source / 'saved.png')
                image.symlink_to(source / 'saved.png')
            return original_open(path, flags, *args, **kwargs)
        with patch.object(wallpaper.os, 'open', side_effect=racing_open):
            refused()
        assert swapped
        image.unlink()
        (source / 'saved.png').rename(image)

        # A published image/config link is refused, not followed by the greeter.
        saved = published.read_bytes()
        published.unlink()
        published.symlink_to(image)
        assert render()['state'] == 'image_invalid'
        refused()
        published.unlink()
        published.write_bytes(saved)
        published.chmod(0o640)
        published_config.unlink()
        published_config.symlink_to(config)
        assert render()['state'] == 'config_invalid'
        assert publisher.attempt(config, destination)['state'] == 'refused'
        published_config.unlink()
        published_config.write_bytes(original_config)

        # Parent-directory symlinks at the destination are also rejected; neither
        # publisher nor greeter follows the target even when it holds valid data.
        original_directory = destination / 'wpaperd-original'
        published_config.parent.rename(original_directory)
        (destination / 'wpaperd').symlink_to(original_directory, target_is_directory=True)
        assert publisher.attempt(config, destination)['state'] == 'refused'
        assert render()['state'] == 'config_invalid'
        (destination / 'wpaperd').unlink()
        original_directory.rename(destination / 'wpaperd')
        published_config.chmod(0o640)
        with patch.dict(os.environ, environment | {'EMAKI_GREETER_WALLPAPER_ROOT': str(destination_link)}):
            assert wallpaper.texture(80, 50, 1, 'Fixture', 'sharp')['state'] == 'config_invalid'

        # Absolute paths and .. cannot cause even an attempted read outside root.
        write_config(published_config, image)
        with patch.object(wallpaper.os, 'open', wraps=os.open) as opens:
            assert render()['state'] == 'image_invalid'
            assert image.name not in [call.args[0] for call in opens.call_args_list]
        write_config(published_config, destination / '..' / 'private-home' / image.name)
        assert render()['state'] == 'image_invalid'
        published_config.write_bytes(original_config)

        # No new config becomes visible if publication fails before its commit.
        image.write_bytes(png('blue'))
        old_atomic = publisher.atomic_write
        def fail_commit(parent, name, data):
            if name == 'config.toml':
                raise OSError('simulated disk failure')
            return old_atomic(parent, name, data)
        with patch.object(publisher, 'atomic_write', side_effect=fail_commit):
            refused()
        assert published.is_file() and render()['state'] == 'ready'
        assert publisher.publish(config, destination)['state'] == 'published'
        assert image_from(published_config).read_bytes() == image.read_bytes()

        # Restrictive interactive umask still creates exactly the greeter-readable
        # directory; file modes stay private to the publishing user and greeter.
        another = base / 'published-private'
        another.mkdir()
        another.chmod(0o2750)
        old_umask = os.umask(0o077)
        try:
            publisher.publish(config, another)
        finally:
            os.umask(old_umask)
        assert stat.S_IMODE((another / 'wpaperd').stat().st_mode) == 0o2750

        # The real root-owned users/0711 grants an ordinary user and greeter only
        # search permission. Owner0111 reproduces those effective permissions
        # without root; intermediate descriptor opens must use O_PATH, not read.
        users_gate = base / 'users-search-only'
        users_gate.mkdir()
        gated_destination = users_gate / 'fixture'
        gated_destination.mkdir()
        gated_destination.chmod(0o2750)
        users_gate.chmod(0o111)
        try:
            assert publisher.publish(config, gated_destination)['state'] == 'published'
            with patch.dict(os.environ, environment | {'EMAKI_GREETER_WALLPAPER_ROOT': str(gated_destination)}):
                assert wallpaper.texture(80, 50, 1, 'Fixture', 'sharp')['state'] == 'ready'
        finally:
            users_gate.chmod(0o700)

        # Two generation retention avoids growing /var on repeated source edits.
        for color in ('green', 'orange', 'purple', 'white'):
            image.write_bytes(png(color))
            publisher.publish(config, destination)
        assert len(list(destination.glob('wallpaper-*.image'))) == 2

        # Real kernel watches: same-file write, atomic image replacement, config
        # selecting a new file and then an edit to that newly selected image.
        watcher_code = ('import importlib.util,sys; sys.path.insert(0,sys.argv[1]); '
                        's=importlib.util.spec_from_file_location("publisher",sys.argv[2]); '
                        'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                        'm.watch(m.Path(sys.argv[3]),m.Path(sys.argv[4]))')
        watcher = subprocess.Popen([sys.executable, '-B', '-c', watcher_code,
                                    str(ROOT / 'shell/helpers'), str(ROOT / 'shell/helpers/publish-wallpaper.py'),
                                    str(config), str(destination)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(.2)
            for index, color in enumerate(('black', 'yellow', 'pink')):
                content = png(color)
                if index == 0:
                    image.write_bytes(content)
                elif index == 1:
                    replacement = source / 'replace.png'
                    replacement.write_bytes(content)
                    replacement.replace(image)
                else:
                    image = source / 'NEW_PRIVATE_WALLPAPER.png'
                    image.write_bytes(content)
                    write_config(config, image)
                expected = 'wallpaper-' + hashlib.sha256(content).hexdigest() + '.image'
                until(lambda: image_from(published_config).name == expected, 'real image/config update was not published')
            image.write_bytes(png('cyan'))
            expected = 'wallpaper-' + hashlib.sha256(image.read_bytes()).hexdigest() + '.image'
            until(lambda: image_from(published_config).name == expected, 'newly selected image was not watched')
            image.unlink()
            until(lambda: not published_config.exists() and not list(destination.glob('wallpaper-*.image')),
                  'watcher did not revoke a removed private image')
            assert render()['state'] == 'config_missing'
            image.write_bytes(png('cyan'))
            until(published_config.exists, 'recreated image was not republished')
            config.unlink()
            until(lambda: not published_config.exists(), 'watcher did not revoke a removed config')
            write_config(config, image)
            until(published_config.exists, 'recreated config was not republished')
            assert watcher.poll() is None
        finally:
            watcher.terminate()
            stdout, stderr = watcher.communicate(timeout=5)
        assert not stdout and not stderr, 'watcher must not log source paths or decoder messages'

        # Restricted mode understands each published exact output without changing
        # the older user's conservative config-selection contract.
        write_config(config, image, '\n["Other-1"]\nmode = "fit"\n')
        publisher.publish(config, destination)
        assert render()['state'] == 'ready'
        second_image = source / 'SECOND_PRIVATE_WALLPAPER.png'
        second_image.write_bytes(png('magenta'))
        config.write_text('[default]\nmode = "center"\n'
                          '["DP-1"]\npath = ' + json.dumps(str(image)) + '\n'
                          '["HDMI-A-1"]\npath = ' + json.dumps(str(second_image)) + '\n')
        assert publisher.publish(config, destination)['images'] == 2
        colors = []
        with patch.dict(os.environ, environment):
            for output in ('DP-1', 'HDMI-A-1'):
                with patch.object(wallpaper, 'open_no_symlinks', wraps=wallpaper.open_no_symlinks) as opens:
                    result = wallpaper.texture(80, 50, 1, output, 'sharp')
                assert result['state'] == 'ready', (output, result)
                assert all(Path(call.args[0]).is_relative_to(destination) for call in opens.call_args_list)
                with Image.open(Path(unquote(urlparse(result['texture']).path))) as rendered:
                    colors.append(rendered.getpixel((40, 25)))
        assert colors == [(0, 255, 255), (255, 0, 255)], colors
        safe_config = config.read_text()
        safe_published = published_config.read_text()
        for selector in ('DP-*', 'Dell Monitor'):
            invalid_section = '\n[' + json.dumps(selector) + ']\nmode = "center"\n'
            config.write_text(safe_config + invalid_section)
            assert publisher.attempt(config, destination)['state'] == 'refused'
            published_config.write_text(safe_published + invalid_section)
            with patch.dict(os.environ, environment):
                assert wallpaper.texture(80, 50, 1, 'DP-1', 'sharp')['state'] == 'output_selection_unsupported'
            published_config.write_text(safe_published)
        # Restore a default picture for the real-QS fixture's synthetic output.
        write_config(config, image)
        publisher.publish(config, destination)
        # Exercise the installed QS Process.environment type and inheritance,
        # including the helper's publishedRoot override and separate cache dir.
        qml_home = base / 'qml'
        qml_home.mkdir()
        for name in ('r', 'cache', 'config', 'data', 'state', 'helpers'):
            (qml_home / name).mkdir(mode=0o700)
        injection = qml_home / 'untrusted-python-path'
        injection.mkdir()
        injection_marker = qml_home / 'python-site-was-executed'
        (injection / 'sitecustomize.py').write_text('from pathlib import Path\nPath(' + repr(str(injection_marker)) + ').touch()\n')
        shutil.copy(ROOT / 'shell/WallpaperSource.qml', qml_home)
        shutil.copy(ROOT / 'shell/helpers/wallpaper.py', qml_home / 'helpers')
        (qml_home / 'test.qml').write_text('''import QtQuick
import Quickshell
Scope {
    WallpaperSource {
        outputWidth: 80
        outputHeight: 50
        outputName: "Fixture"
        variant: "sharp"
        publishedRoot: ''' + json.dumps(str(destination)) + '''
        onStateChanged: {
            if (state === "ready") {
                console.log("PUBLISHED_WALLPAPER_READY");
                Qt.quit();
            }
        }
    }
    Timer { interval: 4000; running: true; onTriggered: Qt.exit(2) }
}
''')
        qml_environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                               QML_DISABLE_DISK_CACHE='1', HOME=str(qml_home),
                               XDG_RUNTIME_DIR=str(runtime_path(qml_home)), XDG_CACHE_HOME=str(qml_home / 'cache'),
                               XDG_CONFIG_HOME=str(qml_home / 'config'), XDG_DATA_HOME=str(qml_home / 'data'),
                               XDG_STATE_HOME=str(qml_home / 'state'), EMAKI_PYTHON=sys.executable,
                               PYTHONPATH=str(injection))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS',
                    'EMAKI_GREETER_WALLPAPER_ROOT'):
            qml_environment.pop(key, None)
        qml = subprocess.run([shutil.which('qs'), '-p', str(qml_home / 'test.qml'), '--no-color'],
                             env=qml_environment, capture_output=True, text=True, timeout=8)
        assert qml.returncode == 0, qml.stdout + qml.stderr
        assert 'PUBLISHED_WALLPAPER_READY' in qml.stdout + qml.stderr, qml.stdout + qml.stderr
        assert list((qml_home / 'cache/emaki/wallpaper-sharp').glob('*.png'))
        assert str(source) not in qml.stdout + qml.stderr
        assert not injection_marker.exists(), 'greeter helper must ignore Python user/import environment'
        disabled_marker = qml_home / 'disabled-helper-was-run'
        unused_python = qml_home / 'unused-python'
        unused_python.write_text('#!/bin/sh\ntouch ' + str(disabled_marker) + '\nexit 1\n')
        unused_python.chmod(0o700)
        (qml_home / 'disabled.qml').write_text('''import QtQuick
import Quickshell
Scope {
    WallpaperSource {
        id: disabled
        enabled: false
        outputWidth: 80
        outputHeight: 50
        isolateHelper: true
    }
    WallpaperSource { id: ordinary; enabled: false }
    Timer {
        interval: 250
        running: true
        onTriggered: {
            if (disabled.state !== "disabled" || disabled.texture !== "" || !disabled.helperCommand().includes("-I") || ordinary.helperCommand().includes("-I"))
                Qt.exit(2);
            else
                Qt.quit();
        }
    }
}
''')
        disabled_result = subprocess.run([shutil.which('qs'), '-p', str(qml_home / 'disabled.qml'), '--no-color'],
                                        env=qml_environment | {'EMAKI_PYTHON': str(unused_python)}, capture_output=True,
                                        text=True, timeout=8)
        assert disabled_result.returncode == 0, disabled_result.stdout + disabled_result.stderr
        assert not disabled_marker.exists(), 'disabled greeter source must never launch a helper'
        assert (ROOT / 'systemd/emaki-greeter-wallpaper.path').read_text().find('PathChanged=%h/.config/wpaperd/config.toml') >= 0
        assert '--watch' in (ROOT / 'systemd/emaki-greeter-wallpaper-watch.service').read_text()
        # Provisioning refuses nonroot before reading any account/user paths.
        with patch.object(provisioner, 'account_ids') as account_reads:
            try:
                provisioner.provision(base, 'fixture')
                raise AssertionError('nonroot provisioner accepted')
            except ValueError:
                pass
            account_reads.assert_not_called()
        offline = base / 'offline'
        (offline / 'etc').mkdir(parents=True)
        (offline / 'etc/passwd').write_text('fixture:x:1001:1001::/home/NEVER_READ:/bin/sh\ngreeter:x:947:947::/:/bin/sh\n')
        (offline / 'etc/group').write_text('greeter:x:947:\n')
        assert provisioner.account_ids(offline, 'fixture') == (1001, 947, 947)
        assert provisioner.account_ids(offline, None) == (None, 947, 947)
        (offline / 'var/lib').mkdir(parents=True)
        for no_user in ('', 'root', None):
            created = []
            def record_directory(parent, name, mode, uid, gid):
                created.append((name, mode, uid, gid))
                return os.dup(parent)
            with patch.object(provisioner.os, 'getuid', return_value=0), patch.object(provisioner.os, 'geteuid', return_value=0), \
                    patch.object(provisioner, 'ensure_directory', side_effect=record_directory):
                assert provisioner.provision(offline, no_user) is None
            assert [entry[0] for entry in created] == ['emaki-greeter', 'handoff', 'users', 'home', 'state', 'cache', 'config', 'data']
            assert created[:3] == [('emaki-greeter', 0o755, 0, 0), ('handoff', 0o711, 947, 947), ('users', 0o711, 0, 0)]
            assert all(entry[1:] == (0o700, 947, 947) for entry in created[3:])
        (offline / 'etc/passwd').unlink()
        (offline / 'etc/passwd').symlink_to(config)
        try:
            provisioner.account_ids(offline, 'fixture')
            raise AssertionError('offline account symlink accepted')
        except OSError:
            pass
        makefile = (ROOT / 'Makefile').read_text()
        assert "echo '  systemctl --user daemon-reload'" not in makefile
        assert 'rmdir --ignore-fail-on-non-empty $(SYSTEMD)/user/graphical-session.target.wants' in makefile
        uninstall = makefile.split('uninstall:\n', 1)[1]
        assert uninstall.index('--purge-published') < uninstall.index('rm -f $(BIN)')
        assert 'python3 -I scripts/emaki-greeter-provision' in uninstall
        for user, expects_hint in ((None, False), ('fixture', True)):
            hint = io.StringIO()
            with patch.object(provisioner, 'provision', return_value=user), \
                    patch.object(sys, 'argv', ['provision', '--user', user or '']), redirect_stdout(hint):
                assert provisioner.main() == 0
            assert ('systemctl --user daemon-reload' in hint.getvalue()) is expects_hint
        purge_checks(base)
        uninstall_recipe_checks(base)
        package_removal_checks(base)
        default_config_checks(base)
        watcher_unit =(ROOT / 'systemd/emaki-greeter-wallpaper-watch.service').read_text()
        assert 'StartLimitIntervalSec=60' in watcher_unit and 'StartLimitBurst=5' in watcher_unit
    print('Greeter wallpaper: safe publication/removal/purge, PNG/JPEG/WebP-only/no Ghostscript, bounds/symlinks/races, private roots, inotify, isolated QS helpers, optional provisioning/hints, private refusal codes: PASS')


if __name__ == '__main__':
    main()
