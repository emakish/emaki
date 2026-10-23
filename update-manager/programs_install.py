#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authorize one repository target and keep its update and install owned by systemd."""

try:
    from emaki_i18n import translation
except ModuleNotFoundError:
    import sys as _i18n_sys
    from pathlib import Path as _I18nPath
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[1] / 'scripts'))
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
    from emaki_i18n import translation

_ = translation('programs').gettext

import fcntl
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import programs_arch as arch
import programs_receipts as receipts
import programs_kits as kits

SERVICE = 'emaki-programs-install'
SNAPSHOTS = Path('/.snapshots')
ENV = arch.ENV
HELPER = str(Path(__file__).resolve())
UPDATE = str(Path(arch.BINDIR) / 'emaki-update')


def update_backend():
    directory = Path(__file__).resolve().parent
    path = directory / 'emaki-update-apply'
    if not path.exists():
        path = directory / 'apply.py'
    loader = SourceFileLoader('programs_update_apply', str(path))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def emit(event, **fields):
    print(json.dumps(dict(type=event, **fields)), flush=True)


def finish(code, ok=False, **fields):
    emit('finished', ok=ok, code=code, action='install', kind='arch', **fields)
    return 0 if ok else 1


def validate(arguments):
    worker = bool(arguments and arguments[0] == '--run')
    values = arguments[1:] if worker else arguments
    if len(values) != 2:
        raise ValueError(_('Select one repository program and its preview.'))
    target, token = values
    arch.validate_target(target)
    if not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError(_('Refresh the installation preview.'))
    return worker, target, token


def revalidate(target, token):
    kit = kits.target_info(target)
    plan = kits.remove_plan(kit['id']) if kit and kit['action'] == 'remove' else arch.install_plan(target, refresh=True)
    if plan.get('code') == 'conflict':
        raise ValueError(plan['message'])
    if not plan.get('ok') or plan.get('token') != token:
        raise ValueError(_('Packages changed. Refresh the installation preview.'))
    return plan


def preinstall_snapshot(required=False):
    """Keep a verified store-owned point that the existing restore list accepts."""
    if not SNAPSHOTS.is_dir() and not required:
        return '', ''
    message = _('A verified system snapshot could not be created. Nothing was installed.')
    result = subprocess.run(['snapper', '--no-dbus', '-c', 'root', 'create', '--type', 'pre',
                             '--print-number', '--cleanup-algorithm', 'number',
                             '--description', 'Program install'],
                            cwd='/', env=ENV, capture_output=True, text=True, timeout=120)
    number = result.stdout.strip()
    if result.returncode or not re.fullmatch(r'[1-9][0-9]{0,18}', number):
        raise RuntimeError(message)
    directory = SNAPSHOTS / number
    try:
        point = receipts.restore.receipt(directory)
        if point['kind'] != 'pre' or point['description'] != 'Program install':
            raise ValueError('invalid install snapshot')
        path = str(directory / 'snapshot')
        volume = subprocess.run(['btrfs', 'subvolume', 'show', path], cwd='/', env=ENV,
                                capture_output=True, text=True, timeout=20)
        readonly = subprocess.run(['btrfs', 'property', 'get', '-ts', path, 'ro'], cwd='/', env=ENV,
                                  capture_output=True, text=True, timeout=20)
        if volume.returncode or readonly.returncode or readonly.stdout.strip() != 'ro=true':
            raise ValueError('unverified snapshot')
    except (OSError, ValueError, subprocess.SubprocessError, receipts.restore.ET.ParseError) as error:
        try:
            subprocess.run(['snapper', '--no-dbus', '-c', 'root', 'delete', number],
                           cwd='/', env=ENV, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            pass
        raise RuntimeError(message) from error
    return number, number + ':' + point['digest']


def worker(target, token, uid=None):
    descriptor = os.open('/run/emaki-update-manager.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return finish('busy', notStarted=True, message=_('Another package transaction is running.'))
        backend = update_backend()
        try:
            backend.require_update_root()
            plan = revalidate(target, token)
            targets = plan['targets']
            kit = kits.target_info(target)
            docker = kits.docker_state(uid) if kit and kit['id'] == 'docker' else None
            before = backend.versions()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return finish('refused', notStarted=True, message=str(error))
        if kit and kit['action'] == 'remove':
            return remove_kit(kit, plan, backend, docker)
        emit('progress', message=_('Installing with a full repository update. Closing this window does not stop it.'))
        baseline = None
        snapshot, snapshot_token = '', ''
        try:
            result = subprocess.run(['systemd-inhibit', '--what=sleep:handle-lid-switch', '--mode=block',
                                     '--who=Emaki', '--why=Installing repository programs',
                                     UPDATE, '--noninteractive'],
                                    cwd='/', env=ENV, stdin=subprocess.DEVNULL)
            after = backend.versions()
            if result.returncode == 0:
                # The completed update belongs to the system, not to this install.
                baseline = after
                try:
                    snapshot, snapshot_token = preinstall_snapshot(required=bool(kit) or target.endswith('/emaki-nvidia'))
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    restart, sign_out = backend.changed(before, after)
                    return finish('refused', restart=restart, signOut=sign_out,
                                  message=_('A verified system snapshot could not be created. Nothing was installed.'))
                result = subprocess.run(['systemd-inhibit', '--what=sleep:handle-lid-switch', '--mode=block',
                                         '--who=Emaki', '--why=Installing repository programs',
                                         UPDATE, '--noninteractive', '--install-only', *targets],
                                        cwd='/', env=ENV, stdin=subprocess.DEVNULL)
                after = backend.versions()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return finish('unknown', snapshot=snapshot, restoreToken=snapshot_token,
                          message=_('The transaction result could not be checked. Check repository updates before trying again.'))
        setup_error = ''
        if kit and result.returncode == 0 and docker is not None:
            try:
                kits.configure_docker(docker, kit['access'])
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                setup_error = str(error)
        restart, sign_out = backend.changed(before, after)
        if docker and kit['access'] and not docker['member']:
            sign_out = True
        code = 'success' if result.returncode == 0 else 'partial' if before != after else 'cancelled' if result.returncode == 130 else 'failed'
        receipt = ''
        # A failing post-transaction hook does not undo committed packages.
        if baseline is not None and (result.returncode == 0 or baseline != after):
            name = target.split('/', 1)[1]
            try:
                # What this install added, read from the package database it changed.
                if kit:
                    metadata = dict(id=kit['id'])
                    previous = kits.receipt_for(kit['id'])
                    if previous:
                        # A setup retry still takes back the original kit install.
                        baseline = dict(baseline)
                        for package in previous['added']:
                            baseline.pop(package, None)
                        baseline.update(previous.get('removed', {}))
                        baseline.update({package: versions[0] for package, versions in previous['upgraded'].items()})
                        snapshot = previous['snapshot']
                        snapshot_token = previous.get('snapshotToken', '')
                    if docker is not None:
                        metadata['docker'] = dict(previous.get('kit', {}).get('docker', docker)) if previous else dict(docker)
                        for field in ('memberAdded', 'socketEnabledByKit'):
                            metadata['docker'][field] = (metadata['docker'].get(field, False)
                                                        or docker.get(field, False))
                    receipt = receipts.record('arch', 'development:' + kit['id'], kit['name'], kit['packages'][0],
                                              baseline, after, snapshot, snapshot_token=snapshot_token, kit=metadata)['id']
                else:
                    receipt = receipts.record('arch', '', name, name, baseline, after, snapshot,
                                              snapshot_token=snapshot_token)['id']
            except (OSError, ValueError):
                receipt = ''
        if kit and not receipt and result.returncode == 0:
            setup_error = _('The development kit receipt could not be saved. Review the system restore point before retrying.')
        return finish('partial' if setup_error else code, ok=result.returncode == 0 and not setup_error, restart=restart, signOut=sign_out, receipt=receipt,
                      snapshot=snapshot if result.returncode != 0 or setup_error else '',
                      restoreToken=snapshot_token if result.returncode != 0 or setup_error else '',
                      message=setup_error or (_('Repository installation finished.') if result.returncode == 0 else
                      _('Repository installation did not finish. Review the transaction details.')))



def remove_kit(kit, plan, backend, docker):
    snapshot, restore_token = preinstall_snapshot(required=True)
    sign_out = False
    try:
        if docker is not None:
            receipt = kits.receipt_for(kit['id'])
            before = receipt.get('kit', {}).get('docker') if receipt else None
            if before is not None:
                # Never change a different person's membership on this request.
                before = dict(before, uid=docker['uid'], user=docker['user'],
                              enabled=before['enabled'] if before.get('socketEnabledByKit', True) else True,
                              member=before['member'] if before['uid'] == docker['uid'] and before.get('memberAdded', True) else True)
                sign_out = docker['member'] and not before['member'] and not docker.get('primary', False)
                kits.restore_docker(before)
        # Commit only the exact reviewed set. No recursive or dependency override flags.
        result = subprocess.run(['pacman', '-Rn', '--noconfirm', '--', *plan['targets']],
                                cwd='/', env=ENV, stdin=subprocess.DEVNULL)
        installed = backend.versions()
        remaining = [name for name in plan['targets'] if name in installed]
        if result.returncode or remaining:
            raise RuntimeError('incomplete removal')
        for receipt in receipts.receipts(trusted=True):
            if receipt['key'] == 'development:' + kit['id'] and not receipt['undone']:
                receipts.mark_undone(receipts.DIRECTORY / (receipt['id'] + '.json'), receipt)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return finish('partial', snapshot=snapshot, restoreToken=restore_token, signOut=sign_out,
                      message=_('The development kit removal did not finish. Review the transaction details.'))
    return finish('removed', ok=True, signOut=sign_out,
                  message=_('The development kit was removed. Docker images and containers, personal projects and Rust toolchains are unchanged.'))

def request(target, token, uid=None):
    # All privileged arguments are reconstructed here, never supplied by the caller.
    update_backend().require_update_root()
    revalidate(target, token)
    # This root-generated marker survives collection of the transient service.
    # A new request cannot observe a previous or already-running transaction.
    request_field = 'EMAKI_INSTALL_REQUEST=' + uuid.uuid4().hex
    emit('progress', message=_('Starting repository installation.'))
    user_arguments = ['--kit-uid', str(uid)] if uid is not None else []
    subprocess.run(['systemd-run', '--wait', '--collect', '--unit=' + SERVICE,
                    '--property=Type=exec', '--property=StandardOutput=journal',
                    '--property=LogExtraFields=' + request_field,
                    '--property=StandardError=journal', '--setenv=LC_ALL=C',
                    '--setenv=PATH=' + ENV['PATH'], HELPER, '--run', target, token, *user_arguments],
                   cwd='/', env=ENV, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    # systemd-run's optional invocation status line is not a result protocol.
    # Match trusted service credentials as well as this request's journal field.
    log = subprocess.run(['journalctl', '--quiet', '--no-pager', '-o', 'json',
                          '_SYSTEMD_UNIT=' + SERVICE + '.service', '_UID=0', request_field],
                         cwd='/', env=ENV, capture_output=True, text=True, timeout=20)
    finished = None
    details = ""
    if log.returncode == 0:
        for line in log.stdout.splitlines():
            try:
                message = json.loads(line).get('MESSAGE', '')
                try:
                    record = json.loads(message)
                except (ValueError, TypeError):
                    if isinstance(message, str):
                        # Retain only a bounded tail from this exact invocation.
                        clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', message)
                        clean = ''.join(c for c in clean if c.isprintable() or c in '\n\t')
                        details = (details + clean + '\n')[-4096:]
                    continue
                if record.get('type') == 'finished' and record.get('action') == 'install':
                    finished = record
            except (ValueError, TypeError, AttributeError):
                continue
    if finished:
        if not finished.get('ok') and details.strip():
            finished['message'] = (finished.get('message', '') + '\n' + details.strip()).strip()
        emit('finished', **{key: value for key, value in finished.items() if key != 'type'})
        return 0 if finished.get('ok') else 1
    return finish('unknown', message=_('The service result could not be observed. Check its journal before retrying.'))


def main(argv):
    try:
        uid = None
        if len(argv) == 5 and argv[0] == '--run' and argv[3] == '--kit-uid':
            if os.environ.get('PKEXEC_UID') is not None:
                raise ValueError(_('Invalid development kit.'))
            uid = int(argv[4])
            argv = argv[:3]
        is_worker, target, token = validate(argv)
        kit = kits.target_info(target)
        if kit and kit['id'] == 'docker':
            if not is_worker:
                uid = int(os.environ.get('PKEXEC_UID', '0'))
            kits.docker_state(uid)
        if os.geteuid() != 0:
            raise ValueError(_('Authorization is required to install repository programs.'))
        os.umask(0o077)
        operation = worker if is_worker else request
        return operation(target, token, uid) if uid is not None else operation(target, token)
    except (KeyError, ValueError, RuntimeError) as error:
        return finish('refused', notStarted=True, message=str(error))
    except (OSError, subprocess.SubprocessError):
        return finish('unknown', message=_('The service result could not be observed. Check its journal before retrying.'))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
