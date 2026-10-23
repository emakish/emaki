# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixed developer bundles using the repository transaction and receipt boundary."""
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import selectors
import time
import subprocess

import programs_arch as arch
import programs_catalog as removal

_ = arch._
DATA = Path(__file__).with_name('development-kits.json')
if not DATA.exists():
    from emaki_paths import PREFIX
    DATA = Path(PREFIX) / 'share/emaki-update-manager/development-kits.json'


def definitions():
    rows = json.loads(DATA.read_text())['kits']
    for row in rows:
        arch.validate_target('development/' + row['id'])
        if not row['packages']:
            raise ValueError(_('Invalid development kit.'))
        for package in row['packages']:
            removal.valid_name(package)
    return rows


def definition(identity):
    for row in definitions():
        if row['id'] == identity:
            return row
    raise ValueError(_('Invalid development kit.'))


def target_info(target):
    namespace, identity = arch.validate_target(target)
    if namespace not in ('development', 'development-remove'):
        return None
    access = identity == 'docker-access'
    if access:
        identity = 'docker'
    row = definition(identity)
    if access and namespace != 'development':
        raise ValueError(_('Invalid development kit.'))
    return dict(row, access=access, action='remove' if namespace.endswith('-remove') else 'install')


def command(arguments, timeout=30, env=None):
    return subprocess.run(arguments, cwd='/', env=arch.ENV if env is None else env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def stable_installed():
    try:
        result = command(['rustup', 'toolchain', 'list'], env=dict(os.environ))
        return result.returncode == 0 and any(line.split()[0].startswith('stable-')
                                              for line in result.stdout.splitlines() if line.split())
    except (OSError, subprocess.SubprocessError):
        return False


def socket_enabled():
    try:
        result = command(['systemctl', 'is-enabled', 'docker.socket'])
        return result.returncode == 0 and result.stdout.strip() == 'enabled'
    except (OSError, subprocess.SubprocessError):
        return False


def conflict_message(identity, installed):
    names = sorted(name for name in installed if
                   (identity == 'rust' and name in ('rust', 'cargo', 'rustfmt')) or
                   (identity == 'nodejs' and name.startswith('nodejs-lts-')))
    if not names:
        return ''
    return _('The installed packages %(packages)s conflict with this development kit; remove them first if you want to use this kit.') % {'packages': ', '.join(names)}


def catalog(installed):
    result = []
    for row in definitions():
        complete = all(package in installed for package in row['packages'])
        ready = complete
        if row['id'] == 'rust' and complete:
            ready = stable_installed()
        if row['id'] == 'docker' and complete:
            ready = socket_enabled()
        conflict = conflict_message(row['id'], installed)
        actions = [] if ready or conflict else ['finish' if row['id'] == 'rust' and complete else 'install']
        if any(name in installed and name not in row.get('shared', []) for name in row['packages']):
            actions.append('remove')
        if receipt_for(row['id']) is not None:
            actions.append('undo')
        result.append(dict(row, installed=ready, packagesInstalled=complete, actions=actions, conflict=conflict,
                           partial=any(package in installed for package in row['packages']) and not ready))
    return result


def install_plan(identity, docker_access=False, **kwargs):
    definition(identity)
    if docker_access and identity != 'docker':
        raise ValueError(_('Invalid development kit.'))
    return arch.install_plan('development/' + ('docker-access' if docker_access else identity), **kwargs)


def targets(row, packages):
    # Restrict resolution to official repositories even with third-party repos configured.
    by_name = {}
    for key, package in packages.items():
        if key.split('/', 1)[0] in ('core', 'extra', 'multilib'):
            by_name.setdefault(package['package'], key)
    if any(name not in by_name for name in row['packages']):
        raise ValueError(_('The development kit packages are unavailable. Refresh the catalog and try again.'))
    return [by_name[name] for name in row['packages']]


def remove_plan(identity):
    row = definition(identity)
    installed = arch.parse_versions(arch.run(['pacman', '-Q']))
    kept = [name for name in row['packages'] if name in installed and (removal.is_protected(name) or name in row.get('shared', []))]
    names = [name for name in row['packages'] if name in installed and name not in kept]
    if not names:
        return dict(ok=False, code='not-installed', message=_('No removable development kit packages are installed.'))
    preview = command(['pacman', '-Rp', '--print-format', '%n', '--', *names])
    if preview.returncode:
        return dict(ok=False, code='required', message=_('Other installed software needs this development kit, so it stays.'),
                    requiredBy=removal.required_by(preview.stdout + '\n' + preview.stderr))
    if sorted(preview.stdout.splitlines()) != sorted(names):
        raise ValueError(_('The list of packages to remove changed. Review it again.'))
    state = dict(target='development-remove/' + identity, packages=names, installed=installed)
    token = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(ok=True, code='ready', action='remove', kind='arch', target=state['target'],
                targets=names, packages=names, kept=kept, token=token,
                message=_('These development kit packages will be removed. Shared system packages stay installed.'))


def docker_state(uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError(_('Select a signed-in user for Docker access.'))
    person = pwd.getpwuid(uid)
    try:
        group = grp.getgrnam('docker')
        primary = person.pw_gid == group.gr_gid
        member = person.pw_name in group.gr_mem or primary
    except KeyError:
        member = primary = False
    return dict(uid=uid, user=person.pw_name, enabled=socket_enabled(), member=member, primary=primary)


def checked(arguments):
    result = command(arguments, timeout=120)
    if result.returncode:
        raise RuntimeError(_('Docker setup did not finish. Review the transaction details.'))


def unit_call(method, signature, *arguments):
    checked(['busctl', 'call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
             'org.freedesktop.systemd1.Manager', method, signature, *arguments])


def configure_docker(before, access):
    unit_call('EnableUnitFiles', 'asbb', '1', 'docker.socket', 'false', 'false')
    before['socketEnabledByKit'] = not before['enabled']
    unit_call('StartUnit', 'ss', 'docker.socket', 'replace')
    if access and not before['member']:
        checked(['gpasswd', '--add', before['user'], 'docker'])
        before['memberAdded'] = True


def restore_docker(before):
    # Restore only changes made by the kit, preserving pre-existing configuration.
    current = docker_state(before['uid'])
    if not before['enabled']:
        unit_call('StopUnit', 'ss', 'docker.socket', 'replace')
        unit_call('StopUnit', 'ss', 'docker.service', 'replace')
        unit_call('DisableUnitFiles', 'asb', '1', 'docker.socket', 'false')
    if not before['member'] and current['member'] and not current.get('primary', False):
        checked(['gpasswd', '--delete', before['user'], 'docker'])



def stream_user(arguments, emit, timeout=1800):
    """Bound both output and runtime while forwarding the user's toolchain progress."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(arguments, cwd=str(Path.home()), env=dict(os.environ),
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT) as process:
        try:
            pending = b''
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(arguments, timeout)
                    for key, _events in selector.select(min(1, max(0, deadline - time.monotonic()))):
                        chunk = os.read(key.fileobj.fileno(), 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        pending = (pending + chunk)[-8192:]
                        lines = pending.split(b'\n')
                        pending = lines.pop()
                        if not chunk and pending:
                            lines.append(pending)
                            pending = b''
                        for line in lines:
                            clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line.decode('utf-8', 'replace'))
                            clean = ''.join(char for char in clean if char.isprintable())[-1024:]
                            if clean:
                                emit('progress', message=clean)
            return process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except BaseException:
            process.kill()
            process.wait()
            raise

def finish_user(identity, emit):
    if identity != 'rust':
        return dict(ok=True)
    if os.geteuid() == 0:
        return dict(ok=False, code='refused', message=_('The Rust toolchain must be installed as your user, not as administrator.'))
    emit('progress', message=_('Installing the stable Rust toolchain for your user.'))
    try:
        result = stream_user(['rustup', 'toolchain', 'install', 'stable', '--profile', 'default'], emit)
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is None or result != 0 or not stable_installed():
        return dict(ok=False, code='partial', message=_('Rust packages are installed, but the stable toolchain did not finish. Choose Finish setup to retry.'))
    return dict(ok=True, message=_('The stable Rust toolchain is ready. Use cargo +stable for this toolchain.'))


def receipt_for(identity):
    definition(identity)
    import programs_receipts as receipts
    return receipts.latest(receipts.receipts(trusted=os.geteuid() == 0), key='development:' + identity)


def undo_plan(identity):
    import programs_receipts as receipts
    value = receipt_for(identity)
    if value is None:
        return dict(ok=False, code='nothing', message=_('No development kit installation receipt is available.'))
    return receipts.undo_plan(value, lambda *arguments: command(['pacman', *arguments]))
