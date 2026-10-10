#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the authenticated password boundary with fake commands, including mutants."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'scripts/emaki-wifi-password'
UUID = '12345678-1234-1234-1234-123456789abc'


def load(text):
    module = types.ModuleType('wifi_password')
    module.__file__ = str(SOURCE)
    exec(compile(text, str(SOURCE), 'exec'), module.__dict__)
    return module


def checks(module):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['timeout'] == 8
        assert kwargs['env'] == module.ENV
        field = command[command.index('--get-values') + 1]
        value = {'connection.type': '802-11-wireless',
                 '802-11-wireless-security.key-mgmt': 'wpa-psk',
                 '802-11-wireless-security.psk': 'special: \\ value'}[field]
        return types.SimpleNamespace(stdout=value + '\n')
    assert module.read_password(UUID, run) == {'state': 'ready', 'password': 'special: \\ value'}
    assert '--show-secrets' not in calls[0] and '--show-secrets' in calls[-1]
    assert all(command[-2:] == ['uuid', UUID] for command in calls)
    calls.clear()
    assert module.read_password('--show-secrets', run)['state'] == 'invalid_connection'
    assert calls == []
    def not_wifi(command, **kwargs):
        return types.SimpleNamespace(stdout='802-3-ethernet\n')
    assert module.read_password(UUID, not_wifi)['state'] == 'invalid_connection'
    def enterprise(command, **kwargs):
        return types.SimpleNamespace(stdout='802-11-wireless\n' if 'connection.type' in command else 'wpa-eap\n')
    assert module.read_password(UUID, enterprise)['state'] == 'password_unavailable'
    def fails(*args, **kwargs):
        raise subprocess.TimeoutExpired('nmcli', 8)
    assert module.read_password(UUID, fails) == {'state': 'password_unavailable'}
    for uid, environment in [(1000, {'PKEXEC_UID': '1000'}), (0, {})]:
        output = io.StringIO()
        with patch.object(module.os, 'geteuid', return_value=uid), patch.dict(module.os.environ, environment, clear=True), patch.object(module.sys, 'argv', ['helper', 'show', UUID]), patch.object(module, 'read_password', return_value={'state': 'ready', 'password': 'never-print'}) as read, contextlib.redirect_stdout(output):
            assert module.main() == 1
            read.assert_not_called()
        assert json.loads(output.getvalue())['state'] == 'authentication_required'
        assert 'never-print' not in output.getvalue()


source = SOURCE.read_text()
checks(load(source))
mutants = [
    ("UUID.fullmatch(uuid) is None", 'False'),
    ("field('connection.type') != '802-11-wireless'", 'False'),
    ("field('802-11-wireless-security.key-mgmt') not in ('wpa-psk', 'sae')", 'False'),
    ("os.geteuid() != 0 or not os.environ.get('PKEXEC_UID', '').isdigit()", 'False'),
]
for before, after in mutants:
    assert before in source
    try:
        checks(load(source.replace(before, after, 1)))
    except AssertionError:
        continue
    raise AssertionError('Password boundary mutant survived')
print('PASS: wireless password boundary, secret transport, authentication refusal and 4 killed mutants')

# Forget always names one saved UUID, including profiles that have no current radio.
import importlib.util
spec = importlib.util.spec_from_file_location('wifi_system_tools', ROOT / 'shell/helpers/system-tools.py')
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)
commands = []
def nmcli(args, **kwargs):
    commands.append(args)
    return '802-11-wireless\n' if args[0] == '--get-values' else ''
with patch.object(system, 'nmcli', side_effect=nmcli):
    assert system.operation({'op': 'wifi-forget-saved', 'uuid': UUID}) == {'state': 'confirmed'}
assert commands[-1] == ['--wait', '5', 'connection', 'delete', 'uuid', UUID]
with patch.object(system, 'nmcli', return_value='802-3-ethernet\n') as command:
    assert system.operation({'op': 'wifi-forget-saved', 'uuid': UUID}) == {'state': 'invalid_request'}
    assert command.call_count == 1
with patch.object(system, 'nmcli') as command:
    assert system.operation({'op': 'wifi-forget-saved', 'uuid': '--all'}) == {'state': 'invalid_request'}
    command.assert_not_called()
print('PASS: saved forget uses exact wireless UUID, refuses other types and injected arguments')
