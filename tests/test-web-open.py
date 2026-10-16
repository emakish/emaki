#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP routes reach xdg-open literally, through the application launch boundary."""
import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('system_tools', ROOT / 'shell/helpers/system-tools.py')
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)
launcher = system.shared

with tempfile.TemporaryDirectory(prefix='web-open-') as directory:
    environment = dict(os.environ, HOME=directory)
    for key in ('XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR'):
        environment[key] = directory
    with patch.dict(os.environ, environment, clear=True), \
            patch.object(launcher, 'gio', side_effect=AssertionError('direct GIO URL route')), \
            patch.object(launcher.app_scope, 'run', return_value=0) as launch:
        query = 'private & $(touch BAD) / café'
        assert launcher.handle(dict(op='web', query=query)) == dict(state='requested')
        launch.assert_called_once_with('xdg-open', dict(kind='exec', argv=[
            'xdg-open', 'https://duckduckgo.com/?q=private%20%26%20%24%28touch%20BAD%29%20%2F%20caf%C3%A9']))
        launch.reset_mock()
        assert system.portal_open() == dict(state='requested')
        launch.assert_called_once_with('xdg-open', dict(kind='exec', argv=['xdg-open', system.PORTAL_URL]))
        for url in ('file:///private', 'javascript:alert(1)', 'ftp://example.invalid',
                    '//example.invalid', '-x', '', None, 'https://', ' https://example.invalid',
                    'https://example.invalid/\nextra', 'https://[bad'):
            launch.reset_mock()
            try:
                launcher.open_web_url(url)
            except launcher.Refused as error:
                assert str(error) == 'invalid_url'
            else:
                raise AssertionError(('accepted invalid URL', url))
            launch.assert_not_called()
        for url in ('http://example.invalid/', 'https://example.invalid/?x=$(literal)&y=1'):
            launch.reset_mock()
            launcher.open_web_url(url)
            launch.assert_called_once_with('xdg-open', dict(kind='exec', argv=['xdg-open', url]))
        launch.return_value = 1
        try:
            system.portal_open()
        except launcher.Refused as error:
            assert str(error) == 'open_failed'
        else:
            raise AssertionError('launcher failure was accepted')
print('PASS: shared HTTP(S) route, literal xdg-open argv, forbidden schemes and launch failure')
