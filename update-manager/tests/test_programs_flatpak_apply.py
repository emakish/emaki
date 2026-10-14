#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The authenticated Flatpak entry point accepts one fixed system app target."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_flatpak_apply as helper

REF = 'app/org.example.Editor/x86_64/stable'
TOKEN = 'a' * 64


class HelperTests(unittest.TestCase):
    def invoke(self, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = helper.main(arguments)
        return status, json.loads(output.getvalue().splitlines()[-1])

    def test_strict_argv_refuses_before_privileged_work(self):
        invalid = [[], ['install'], ['install', 'flathub', REF],
                   ['install', 'flathub', REF, TOKEN, 'extra'],
                   ['--user', 'flathub', REF, TOKEN],
                   ['install', 'flathub', REF + ' ' + TOKEN],
                   ['update', 'flathub', REF, TOKEN],
                   ['install', '--system', REF, TOKEN],
                   ['install', 'flathub', REF.replace('app/', 'runtime/'), TOKEN],
                   ['install', 'flathub', REF, TOKEN + '\n'],
                   ['install', 'flathub', 'app/' + 'x' * 513 + '/x86_64/stable', TOKEN]]
        for control in ('\n', '\r', '\t', '\0', '\u202e', '\u200d', '$', ';', '"'):
            invalid.extend([['install', 'flathub' + control, REF, TOKEN],
                            ['remove', 'flathub', REF + control, TOKEN]])
        with patch.object(helper.backend, 'execute') as execute, \
                patch.object(helper.os, 'chdir') as chdir, \
                patch.object(helper.os, 'getuid', return_value=0), \
                patch.object(helper.os, 'geteuid', return_value=0):
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    status, result = self.invoke(arguments)
                    self.assertEqual(status, 1)
                    self.assertFalse(result['ok'])
                    self.assertTrue(result['notStarted'])
            execute.assert_not_called()
            chdir.assert_not_called()

    def test_ordinary_and_mixed_uid_callers_never_execute(self):
        for real, effective in ((1000, 1000), (1000, 0), (0, 1000)):
            with patch.object(helper.os, 'getuid', return_value=real), \
                    patch.object(helper.os, 'geteuid', return_value=effective), \
                    patch.object(helper.backend, 'execute') as execute:
                status, result = self.invoke(['install', 'flathub', REF, TOKEN])
                self.assertEqual((status, result['code']), (1, 'authentication-required'))
                execute.assert_not_called()

    def test_fixed_system_target_and_clean_environment_reach_backend(self):
        def execute(action, row, token, emit):
            self.assertEqual(row, dict(installation='system', origin='flathub', ref=REF))
            self.assertEqual(token, TOKEN)
            self.assertEqual(set(os.environ), {'PATH', 'LC_ALL', 'HOME'})
            self.assertEqual(os.environ['PATH'], os.defpath)
            emit('progress', kind='flatpak', percent=50)
            return dict(ok=True, code='completed', kind='flatpak', action=action)
        for action in ('install', 'remove'):
            with patch.dict(os.environ, {'FLATPAK_SYSTEM_DIR': '/caller/path',
                                         'GI_TYPELIB_PATH': '/caller/modules', 'XDG_DATA_HOME': '/caller/data'}), \
                    patch.object(helper.os, 'getuid', return_value=0), \
                    patch.object(helper.os, 'geteuid', return_value=0), \
                    patch.object(helper.os, 'umask') as umask, patch.object(helper.os, 'chdir'), \
                    patch.object(helper.backend, 'execute', side_effect=execute) as apply:
                status, result = self.invoke([action, 'flathub', REF, TOKEN])
                self.assertEqual((status, result['code']), (0, 'completed'))
                self.assertEqual(result['action'], action)
                apply.assert_called_once()
                # Every account must read what libflatpak writes (0.8.0 used 077: root-only).
                self.assertEqual(umask.call_args_list[0].args, (0o022,))

    def test_changed_preview_refusal_is_relayed(self):
        with patch.dict(os.environ), patch.object(helper.os, 'getuid', return_value=0), \
                patch.object(helper.os, 'geteuid', return_value=0), \
                patch.object(helper.os, 'umask'), patch.object(helper.os, 'chdir'), \
                patch.object(helper.backend, 'execute', return_value=dict(ok=False, code='plan-changed')):
            status, result = self.invoke(['install', 'flathub', REF, TOKEN])
            self.assertEqual((status, result['code']), (1, 'plan-changed'))


if __name__ == '__main__':
    unittest.main()
