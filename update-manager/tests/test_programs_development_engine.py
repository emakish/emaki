#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kit authorization and explicit website operations at the JSON boundary."""
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_engine as engine


class DevelopmentEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = engine.Engine(Mock(), self.temporary.name)
        self.enterContext(patch.object(self.manager, 'aur_status', return_value={'snapshots': True}))
        self.native = dict(ok=True, target='development/docker', token='a' * 64, packages=[])
        self.plan = self.enterContext(patch.object(engine.kits, 'install_plan', return_value=self.native))

    def request(self, method, params=None):
        output = StringIO()
        engine.serve(self.manager, StringIO(json.dumps(dict(version=1, id=8, method=method,
                                                          params=params or {})) + '\n'), output)
        return json.loads(output.getvalue().splitlines()[-1])

    def process(self, answer, code=0):
        process = Mock(stdout=StringIO(json.dumps(dict(type='finished', **answer)) + '\n'))
        process.wait.return_value = code
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        return process

    def test_listing_reads_local_state_without_catalogue_network(self):
        with patch.object(engine.arch, 'run', return_value='python 3.14\n') as run, \
                patch.object(engine.kits, 'catalog', return_value=[{'id': 'python'}]) as catalog, \
                patch.object(self.manager, 'load') as load:
            self.assertTrue(self.request('development')['ok'])
            catalog.assert_called_once_with({'python': '3.14'})
            run.assert_called_once_with(['pacman', '-Q'])
            load.assert_not_called()

    def test_unsupported_snapshot_root_never_plans_or_authorizes(self):
        self.manager.aur_status.return_value = {'snapshots': False}
        with patch.object(engine.subprocess, 'Popen') as authorize:
            for method in ('development-plan', 'development-execute'):
                params = dict(id='docker', action='install')
                if method.endswith('execute'):
                    params['token'] = 'b' * 64
                self.assertEqual(self.request(method, params)['code'], 'snapshots-unavailable')
            self.plan.assert_not_called()
            authorize.assert_not_called()

    def test_consent_is_explicit_boolean_and_docker_only(self):
        for value in ('true', 1, None):
            self.assertFalse(self.request('development-plan', dict(id='docker', action='install',
                                                                  dockerAccess=value))['ok'])
        self.assertFalse(self.request('development-plan', dict(id='rust', action='install',
                                                              dockerAccess=True))['ok'])
        self.plan.assert_not_called()

    def test_changed_consent_or_package_plan_cannot_execute_old_token(self):
        original = self.manager.development_plan('docker', 'install')['token']
        with patch.object(engine.subprocess, 'Popen') as authorize:
            answer = self.manager.development_execute('docker', 'install', original, Mock(), True)
            self.assertEqual(answer['code'], 'changed')
            self.native['packages'] = [{'name': 'changed'}]
            answer = self.manager.development_execute('docker', 'install', original, Mock())
            self.assertEqual(answer['code'], 'changed')
            authorize.assert_not_called()

    def test_helper_uses_revalidated_target_and_native_token(self):
        token = self.manager.development_plan('docker', 'install')['token']
        with patch.object(engine.subprocess, 'Popen', return_value=self.process({'ok': True})) as authorize:
            self.assertTrue(self.manager.development_execute('docker', 'install', token, Mock())['ok'])
            args = authorize.call_args.args[0]
            self.assertEqual(args[0], 'pkexec')
            self.assertTrue(args[1].endswith('/emaki-programs-install'))
            self.assertEqual(args[2:], ['development/docker', 'a' * 64])

    def test_auth_cancel_never_runs_user_setup(self):
        token = self.manager.development_plan('rust', 'install')['token']
        with patch.object(engine.subprocess, 'Popen', return_value=self.process({'ok': False}, 126)), \
                patch.object(engine.kits, 'finish_user') as finish:
            self.assertFalse(self.manager.development_execute('rust', 'install', token, Mock())['ok'])
            finish.assert_not_called()

    def test_rust_failure_preserves_package_receipt_and_partial_state(self):
        token = self.manager.development_plan('rust', 'install')['token']
        with patch.object(engine.subprocess, 'Popen', return_value=self.process({'ok': True, 'receipt': 'saved'})), \
                patch.object(engine.kits, 'finish_user', return_value={'ok': False, 'message': 'retry'}) as finish:
            result = self.manager.development_execute('rust', 'install', token, Mock())
            self.assertFalse(result['ok'])
            self.assertTrue(result['partial'])
            self.assertEqual(result['receipt'], 'saved')
            self.assertEqual(finish.call_args.args[0], 'rust')

    def test_finish_rust_setup_needs_no_snapshots_or_privileged_transaction(self):
        self.manager.aur_status.return_value = {'snapshots': False}
        with patch.object(engine.arch, 'run', return_value='rustup 1\n'), \
                patch.object(engine.kits, 'finish_user', return_value={'ok': True}) as finish, \
                patch.object(engine.subprocess, 'Popen') as authorize:
            plan = self.manager.development_plan('rust', 'finish')
            self.assertTrue(plan['ok'])
            self.assertTrue(self.manager.development_execute('rust', 'finish', plan['token'], Mock())['ok'])
        finish.assert_called_once()
        authorize.assert_not_called()
        self.plan.assert_not_called()
        self.manager.aur_status.assert_not_called()

    def test_finish_requires_rust_packages_and_rechecks_before_execution(self):
        with patch.object(engine.arch, 'run', return_value='rustup 1\n') as run, \
                patch.object(engine.kits, 'finish_user') as finish:
            plan = self.manager.development_plan('rust', 'finish')
            run.return_value = ''
            self.assertFalse(self.manager.development_execute('rust', 'finish', plan['token'], Mock())['ok'])
        finish.assert_not_called()
        self.assertFalse(self.request('development-plan', dict(id='docker', action='finish'))['ok'])

    def test_websites_list_never_fetches_metadata(self):
        with patch.object(engine.webapps, 'list_apps', return_value=[]) as listing, \
                patch.object(engine.webapps, 'create_app') as create:
            self.assertEqual(self.request('websites')['apps'], [])
            listing.assert_called_once_with()
            create.assert_not_called()

    def test_only_add_calls_create_with_url_and_name(self):
        app = dict(id='site', name='Site', url='https://example.org', metadataWarning=False)
        with patch.object(engine.webapps, 'create_app', return_value=app) as create, \
                patch.object(engine.webapps, 'list_apps', return_value=[app]):
            answer = self.request('website-add', dict(url=app['url'], name=app['name']))
            self.assertTrue(answer['ok'])
            create.assert_called_once_with(app['url'], app['name'])

    def test_metadata_warning_describes_generated_icon(self):
        with patch.object(engine.webapps, 'create_app', return_value={'metadataWarning': True}), \
                patch.object(engine.webapps, 'list_apps', return_value=[]):
            answer = self.request('website-add', {'url': 'https://example.org'})
        self.assertIn('generated icon', answer['message'])
        self.assertNotIn('Firefox icon', answer['message'])

    def test_protocol_cannot_override_data_directory(self):
        with patch.object(engine.webapps, 'create_app') as create, \
                patch.object(engine.webapps, 'remove_app') as remove:
            for method in ('website-add', 'website-remove', 'websites'):
                self.assertFalse(self.request(method, dict(url='https://example.org', id='site',
                                                           data_home='/tmp/unowned'))['ok'])
            create.assert_not_called()
            remove.assert_not_called()

    def test_remove_passes_only_owned_id(self):
        with patch.object(engine.webapps, 'remove_app') as remove, \
                patch.object(engine.webapps, 'list_apps', return_value=[]):
            self.assertTrue(self.request('website-remove', {'id': 'site'})['ok'])
            remove.assert_called_once_with('site')

    def test_website_errors_have_translated_message_boundary(self):
        with patch.object(engine.webapps, 'create_app', side_effect=engine.webapps.WebAppError('website-invalid-url')):
            answer = self.request('website-add', {'url': 'file:///tmp/private'})
            self.assertFalse(answer['ok'])
            self.assertEqual(answer['code'], 'website-invalid-url')
            self.assertIn('https://', answer['message'])


if __name__ == '__main__':
    unittest.main()
