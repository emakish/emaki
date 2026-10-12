#!/usr/bin/python3 -IB
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authorize one repository target and keep its full upgrade owned by systemd."""
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

SERVICE = 'emaki-programs-install'
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
        raise ValueError('[PLACEHOLDER] Select one repository program and its preview.')
    target, token = values
    arch.validate_target(target)
    if not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError('[PLACEHOLDER] Refresh the installation preview.')
    return worker, target, token


def revalidate(target, token):
    plan = arch.install_plan(target)
    if not plan.get('ok') or plan.get('token') != token:
        raise ValueError('[PLACEHOLDER] Packages changed. Refresh the installation preview.')


def worker(target, token):
    descriptor = os.open('/run/emaki-update-manager.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return finish('busy', notStarted=True, message='[PLACEHOLDER] Another package transaction is running.')
        backend = update_backend()
        try:
            backend.require_update_root()
            revalidate(target, token)
            before = backend.versions()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return finish('refused', notStarted=True, message=str(error))
        emit('progress', message='[PLACEHOLDER] Installing with a full repository update. Closing this window does not stop it.')
        try:
            result = subprocess.run(['systemd-inhibit', '--what=sleep:handle-lid-switch', '--mode=block',
                                     '--who=Emaki', '--why=[PLACEHOLDER] Installing repository programs',
                                     UPDATE, '--noninteractive', '--install', target],
                                    cwd='/', env=ENV, stdin=subprocess.DEVNULL)
            after = backend.versions()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return finish('unknown', message='[PLACEHOLDER] The transaction result could not be checked. Check repository updates before trying again.')
        restart, sign_out = backend.changed(before, after)
        code = 'success' if result.returncode == 0 else 'partial' if before != after else 'cancelled' if result.returncode == 130 else 'failed'
        return finish(code, ok=result.returncode == 0, restart=restart, signOut=sign_out,
                      message='[PLACEHOLDER] Repository installation finished.' if result.returncode == 0 else
                      '[PLACEHOLDER] Repository installation did not finish. Review the transaction details.')


def request(target, token):
    # All privileged arguments are reconstructed here, never supplied by the caller.
    update_backend().require_update_root()
    revalidate(target, token)
    # This root-generated marker survives collection of the transient service.
    # A new request cannot observe a previous or already-running transaction.
    request_field = 'EMAKI_INSTALL_REQUEST=' + uuid.uuid4().hex
    emit('progress', message='[PLACEHOLDER] Starting repository installation.')
    subprocess.run(['systemd-run', '--wait', '--collect', '--unit=' + SERVICE,
                    '--property=Type=exec', '--property=StandardOutput=journal',
                    '--property=LogExtraFields=' + request_field,
                    '--property=StandardError=journal', '--setenv=LC_ALL=C',
                    '--setenv=PATH=' + ENV['PATH'], HELPER, '--run', target, token],
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
    return finish('unknown', message='[PLACEHOLDER] The service result could not be observed. Check its journal before retrying.')


def main(argv):
    try:
        is_worker, target, token = validate(argv)
        if os.geteuid() != 0:
            raise ValueError('[PLACEHOLDER] Authorization is required to install repository programs.')
        os.umask(0o077)
        return worker(target, token) if is_worker else request(target, token)
    except (ValueError, RuntimeError) as error:
        return finish('refused', notStarted=True, message=str(error))
    except (OSError, subprocess.SubprocessError):
        return finish('unknown', message='[PLACEHOLDER] The service result could not be observed. Check its journal before retrying.')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
