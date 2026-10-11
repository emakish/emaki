#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unified catalog, source identity, refusal delegation and JSON protocol tests."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_engine as engine
import programs_catalog as removal

METADATA = dict(id='org.example.Paint', name='Paint', summary='Draw colourful pictures',
                description='A drawing program.', categories=['Graphics'], screenshots=['https://example.org/paint.png'],
                keywords=['brushes'], icons=[dict(kind='stock', value='paint', width=0, height=0)],
                licence='GPL-3.0-or-later', packages=['paint'], origin='Arch', sourcePath='/metadata/catalog.xml')
PACKAGE = dict(repository='extra', package='paint', version='2', summary='Painter',
               licence='GPL', downloadSize=1234, installedSize=4567)
FLATPAK = dict(id='org.example.Paint', name='Paint', ref='app/org.example.Paint/x86_64/stable',
               installation='system', origin='flathub', version='', installed=False,
               downloadSize=123, installedSize=321, categories=['Graphics'])
ARCH_KEY = 'arch:extra/paint:org.example.Paint'
FP_KEY = 'flatpak:system:flathub:app/org.example.Paint/x86_64/stable'


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-engine-')
        self.addCleanup(self.tmp.cleanup)
        self.legacy = SimpleNamespace(pacman=Mock(return_value=SimpleNamespace(stdout='')),
                                      preview=Mock(), remove=Mock())
        self.engine = engine.Engine(self.legacy, self.tmp.name)
        self.remove_native = self.enterContext(patch.object(self.engine, 'remove_native'))
        self.inventory = self.enterContext(patch.object(engine.arch, 'inventory', return_value=(
            {'extra/paint': deepcopy(PACKAGE)}, {'paint': '1'})))
        self.metadata = self.enterContext(patch.object(engine.appstream, 'components', return_value=(
            [deepcopy(METADATA)], [])))
        self.flatpaks = self.enterContext(patch.object(engine.flatpak, 'discover', return_value=(
            [deepcopy(FLATPAK)], [])))

    def test_one_catalog_keeps_both_source_identities_and_real_sizes(self):
        result = self.engine.catalog()
        self.assertEqual(result['total'], 2)
        self.assertEqual({row['key'] for row in result['programs']}, {ARCH_KEY, FP_KEY})
        row = self.engine.details(ARCH_KEY)['program']
        self.assertEqual(row['licence'], 'GPL-3.0-or-later')
        self.assertEqual(row['downloadSize'], 1234)
        self.assertEqual(row['installedVersion'], '1')
        self.assertEqual(row['actions'], ['remove'])
        for name in ('name', 'summary', 'description', 'icons', 'categories', 'screenshots', 'licence',
                     'downloadSize', 'installedSize'):
            self.assertIn(name, self.engine.details(FP_KEY)['program'])

    def test_search_is_case_insensitive_all_terms_and_filters_combine(self):
        self.assertEqual(self.engine.catalog(query='PAINT brushes', source='arch', category='Graphics',
                                             installed=True)['total'], 1)
        self.assertEqual(self.engine.catalog(query='pictures missing')['total'], 0)
        result = self.engine.catalog(offset=1, limit=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['programs']), 1)
        self.assertEqual(result['categories'], ['Graphics'])

    def test_invalid_filters_before_backend_calls(self):
        for filters in ({'source': 'bogus'}, {'query': []}, {'limit': 0}, {'offset': True},
                        {'installed': 1}, {'refresh': 'yes'}, {'includeAur': []}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.engine.catalog(**filters)
        self.inventory.assert_not_called()

    def test_repository_failure_keeps_optional_catalog_and_marks_partial(self):
        self.inventory.side_effect = RuntimeError('missing database')
        result = self.engine.catalog()
        self.assertTrue(result['partial'])
        self.assertEqual([row['key'] for row in result['programs']], [FP_KEY])

    def test_broken_flatpak_scope_keeps_healthy_catalog_and_preview(self):
        for broken, healthy, action in (('user', 'system', 'install'),
                                        ('system', 'user', 'remove')):
            with self.subTest(broken=broken):
                warning = '[PLACEHOLDER] Flatpak installation could not be read: ' + broken
                self.flatpaks.return_value = ([dict(FLATPAK, installation=healthy,
                                                  installed=action == 'remove')], [warning])
                self.engine.rows = None
                result = self.engine.catalog(source='flatpak')
                key = FP_KEY.replace(':system:', ':' + healthy + ':')
                self.assertEqual([row['key'] for row in result['programs']], [key])
                self.assertTrue(result['partial'])
                self.assertEqual(result['warnings'], [warning])
                self.assertEqual(self.engine.details(key)['program']['actions'], [action])
                with patch.object(engine.flatpak, 'plan', return_value=dict(ok=True, token='b' * 64)) as plan:
                    self.assertTrue(self.engine.plan(action, key)['ok'])
                    self.assertEqual(plan.call_args.args[1]['installation'], healthy)

    def test_metainfo_owner_mapping_uses_existing_backend(self):
        metadata = dict(METADATA, packages=[], sourcePath='/metadata/paint.metainfo.xml')
        self.metadata.return_value = ([metadata], [])
        self.legacy.pacman.return_value.stdout = '/metadata/paint.metainfo.xml is owned by paint 1\n'
        self.assertTrue(self.engine.details(ARCH_KEY)['ok'])
        self.legacy.pacman.assert_called_once_with('-Qo', '/metadata/paint.metainfo.xml')

    def test_no_metadata_does_not_invent_programs_from_libraries(self):
        self.metadata.return_value = ([], [])
        self.assertEqual(self.engine.catalog(source='arch')['total'], 0)

    def test_removal_refusals_are_u3_results_for_every_protected_case(self):
        for code, stdout, stderr in [(0, 'paint\nemaki-config\n', ''), (0, 'paint\nlinux\n', ''),
                                    (1, ":: removing paint breaks dependency 'paint' required by viewer", 'error')]:
            expected = removal.assess('paint', code, stdout, stderr)
            self.legacy.preview.return_value = expected
            self.assertEqual(self.engine.plan('remove', ARCH_KEY), expected)
        self.legacy.remove.assert_not_called()

    def test_removal_token_binds_key_action_and_reviewed_list(self):
        self.legacy.preview.return_value = removal.assess('paint', 0, 'paint\npaint-data\n', '')
        plan = self.engine.plan('remove', ARCH_KEY)
        self.remove_native.return_value = dict(ok=True, code='removed')
        self.assertTrue(self.engine.execute('remove', ARCH_KEY, plan['token'], Mock())['ok'])
        self.remove_native.assert_called_once_with('paint', 'paint,paint-data')
        self.remove_native.reset_mock()
        self.legacy.preview.return_value = removal.assess('paint', 0, 'paint\n', '')
        self.assertEqual(self.engine.execute('remove', ARCH_KEY, plan['token'], Mock())['code'], 'changed')
        self.remove_native.assert_not_called()

    def test_removing_uninstalled_editions_refuses_before_any_backend(self):
        self.inventory.return_value = ({'extra/paint': deepcopy(PACKAGE)}, {})
        with patch.object(engine.flatpak, 'plan') as flatpak_plan, \
                patch.object(engine.subprocess, 'Popen') as authorize:
            for key in (ARCH_KEY, FP_KEY):
                with self.subTest(key=key):
                    self.assertEqual(self.engine.plan('remove', key)['code'], 'not-installed')
                    self.assertEqual(self.engine.execute('remove', key, 'a' * 64, Mock())['code'],
                                     'not-installed')
            self.legacy.preview.assert_not_called()
            self.remove_native.assert_not_called()
            flatpak_plan.assert_not_called()
            authorize.assert_not_called()

    def test_completed_transaction_invalidates_catalog_state(self):
        self.legacy.preview.return_value = removal.assess('paint', 0, 'paint\n', '')
        plan = self.engine.plan('remove', ARCH_KEY)
        def remove(_name, _token):
            self.inventory.return_value = ({'extra/paint': deepcopy(PACKAGE)}, {})
            return dict(ok=True, code='removed')
        self.remove_native.side_effect = remove
        self.engine.execute('remove', ARCH_KEY, plan['token'], Mock())
        self.assertFalse(self.engine.details(ARCH_KEY)['program']['installed'])

    def test_native_removal_runs_u3_in_fresh_interpreter_before_its_fork(self):
        self.legacy.__file__ = str(Path(engine.__file__).with_name('programs.py'))
        result = SimpleNamespace(returncode=0, stdout='{"ok":true,"code":"removed"}')
        with patch.object(engine.subprocess, 'run', return_value=result) as run:
            answer = engine.Engine.remove_native(self.engine, 'paint', 'paint,paint-data')
        self.assertTrue(answer['ok'])
        self.assertEqual(run.call_args.args[0], [sys.executable, '-IB', self.legacy.__file__,
                                               'remove', 'paint', 'paint,paint-data', '--json'])
        self.assertTrue(run.call_args.kwargs['start_new_session'])

    def test_install_delegates_source_qualified_plan(self):
        with patch.object(engine.arch, 'install_plan', return_value=dict(ok=True, token='a' * 64,
                          fullUpgrade=True, packages=[])) as install:
            plan = self.engine.plan('install', ARCH_KEY)
            install.assert_called_once_with('extra/paint')
            self.assertTrue(plan['transaction']['fullUpgrade'])

    def test_install_authorization_cancellation_is_not_success(self):
        with patch.object(engine.arch, 'install_plan', return_value=dict(ok=True, token='a' * 64,
                          fullUpgrade=True, packages=[])):
            plan = self.engine.plan('install', ARCH_KEY)
            process = Mock(stdout=StringIO(''), wait=Mock(return_value=126))
            child = Mock()
            child.__enter__ = Mock(return_value=process)
            child.__exit__ = Mock(return_value=False)
            with patch.object(engine.subprocess, 'Popen', return_value=child) as spawn:
                result = self.engine.execute('install', ARCH_KEY, plan['token'], Mock())
                self.assertEqual(result['code'], 'not-authorized')
                arguments = spawn.call_args.args[0]
                self.assertEqual(arguments[0], 'pkexec')
                self.assertEqual(Path(arguments[1]).name, 'emaki-programs-install')
                self.assertEqual(arguments[2:], ['extra/paint', 'a' * 64])

    def test_install_policy_is_bound_to_fixed_helper_and_requires_authentication(self):
        import xml.etree.ElementTree as ET
        policy = ET.parse(Path(engine.__file__).with_name('org.emaki.programs.install.policy'))
        action = policy.find('action')
        self.assertEqual(action.attrib['id'], 'org.emaki.programs.install')
        self.assertEqual(action.findtext('defaults/allow_active'), 'auth_admin')
        self.assertEqual(action.findtext('defaults/allow_any'), 'no')
        self.assertEqual(action.findtext('defaults/allow_inactive'), 'no')
        self.assertEqual(action.findtext('annotate'), '@EMAKI_LIBEXECDIR@/emaki-programs-install')

    def test_system_flatpak_execution_uses_fixed_authenticated_helper(self):
        for action in ('install', 'remove'):
            self.flatpaks.return_value = ([dict(FLATPAK, installed=action == 'remove')], [])
            self.engine.rows = None
            with patch.object(engine.flatpak, 'plan', return_value=dict(ok=True, token='b' * 64)) as plan_call, \
                    patch.object(engine.flatpak, 'execute') as direct:
                plan = self.engine.plan(action, FP_KEY)
                self.assertEqual(self.engine.details(FP_KEY)['program']['actions'], [action])
                process = Mock(stdout=StringIO('{"type":"finished","ok":true,"code":"completed"}\n'),
                               wait=Mock(return_value=0))
                child = Mock()
                child.__enter__ = Mock(return_value=process)
                child.__exit__ = Mock(return_value=False)
                with patch.object(engine.subprocess, 'Popen', return_value=child) as spawn:
                    self.assertTrue(self.engine.execute(action, FP_KEY, plan['token'], Mock())['ok'])
                    argv = spawn.call_args.args[0]
                    self.assertEqual(argv[0], 'pkexec')
                    self.assertEqual(Path(argv[1]).name, 'emaki-programs-flatpak')
                    self.assertEqual(argv[2:], [action, 'flathub', FLATPAK['ref'], 'b' * 64])
                direct.assert_not_called()
                self.assertEqual(plan_call.call_count, 2)

    def test_existing_user_flatpak_removal_does_not_authenticate(self):
        row = dict(FLATPAK, installation='user', installed=True)
        key = FP_KEY.replace(':system:', ':user:')
        self.flatpaks.return_value = ([row], [])
        with patch.object(engine.flatpak, 'plan', return_value=dict(ok=True, token='b' * 64)), \
                patch.object(engine.flatpak, 'execute', return_value=dict(ok=True)) as direct, \
                patch.object(engine.subprocess, 'Popen') as authorize:
            plan = self.engine.plan('remove', key)
            self.assertEqual(self.engine.details(key)['program']['actions'], ['remove'])
            self.assertTrue(self.engine.execute('remove', key, plan['token'], Mock())['ok'])
            self.assertEqual(direct.call_args.args[:3], ('remove', self.engine.details(key)['program'], 'b' * 64))
            authorize.assert_not_called()

    def test_user_install_is_not_offered_or_resolvable(self):
        self.flatpaks.return_value = ([dict(FLATPAK, installation='user')], [])
        key = FP_KEY.replace(':system:', ':user:')
        self.assertEqual(self.engine.details(key)['program']['actions'], [])
        for binary in ('flatpak', None):
            with self.subTest(binary=binary), \
                    patch.object(engine.flatpak.shutil, 'which', return_value=binary), \
                    patch.object(engine.flatpak, '_library', side_effect=ImportError('Flatpak unavailable')) as library, \
                    patch.object(engine.flatpak.os, 'getuid', return_value=1000), \
                    patch.object(engine.flatpak.os, 'geteuid', return_value=1000), \
                    patch.object(engine.subprocess, 'Popen') as authorize:
                self.assertEqual(self.engine.plan('install', key)['code'], 'user-installation-refused')
                self.assertEqual(self.engine.execute('install', key, 'a' * 64, Mock())['code'],
                                 'user-installation-refused')
                library.assert_not_called()
                authorize.assert_not_called()

    def test_backend_read_only_flag_cannot_be_overridden_by_installation_name(self):
        self.flatpaks.return_value = ([dict(FLATPAK, installed=True, manageable=False)], [])
        self.assertEqual(self.engine.details(FP_KEY)['program']['actions'], [])
        for binary in ('flatpak', None):
            with self.subTest(binary=binary), \
                    patch.object(engine.flatpak.shutil, 'which', return_value=binary), \
                    patch.object(engine.flatpak, '_library', side_effect=ImportError('Flatpak unavailable')) as library, \
                    patch.object(engine.subprocess, 'Popen') as authorize:
                for action in ('install', 'remove'):
                    self.assertEqual(self.engine.plan(action, FP_KEY)['code'], 'installation-read-only')
                    self.assertEqual(self.engine.execute(action, FP_KEY, 'a' * 64, Mock())['code'],
                                     'installation-read-only')
                library.assert_not_called()
                authorize.assert_not_called()

    def test_aur_search_is_validated_and_never_executable(self):
        backend = SimpleNamespace(fetch=Mock(side_effect=lambda _url, _cache, validate: (validate(json.dumps(
            dict(version=5, type='search', results=[dict(Name='paint-git', Version='3', Description='paint',
                                                       License=['GPL'])])).encode()), False)))
        with patch.object(engine, 'update_backend', return_value=backend):
            result = self.engine.catalog(query='paint', includeAur=True)
        row = next(row for row in result['programs'] if row['kind'] == 'aur')
        self.assertEqual(row['actions'], ['terminal-build'])
        self.assertIn('Build in a terminal', row['notice'])
        self.assertEqual(self.engine.plan('install', row['key'])['code'], 'terminal-only')
        self.assertEqual(self.engine.plan('remove', row['key'])['code'], 'terminal-only')

    def test_aur_foreign_name_is_not_an_executable_argument(self):
        for payload in ({'version': 5, 'type': 'search', 'results': [dict(Name='--root', Version='1')]},
                        {'version': 5, 'type': 'error', 'results': []}):
            with self.assertRaises(ValueError):
                engine.aur_records(payload, {})

    def test_updates_reuse_u1_apply_and_preserve_partial_failure(self):
        backend = SimpleNamespace(check=Mock(return_value=dict(updates=[dict(name='paint', old='1', new='2')],
                                                                 warnings=['existing warning'], news=[])), emit=Mock())
        backend.apply = Mock(side_effect=lambda _cache: backend.emit('finished', ok=False,
                                                                     message='System updated; Flatpak failed'))
        with patch.object(engine, 'update_backend', return_value=backend):
            plan = self.engine.plan('update')
            result = self.engine.execute('update', '', plan['token'], Mock())
            self.assertFalse(result['ok'])
            backend.apply.assert_called_once()
            with self.assertRaises(ValueError):
                self.engine.plan('update', ARCH_KEY)

    def test_stream_recovers_after_bad_and_oversized_requests(self):
        requests = ['{broken', json.dumps(dict(version=9, method='catalog')), 'x' * (engine.MAX_REQUEST + 10),
                    json.dumps(dict(version=1, id='good', method='search', params={'query': 'brushes'}))]
        output = StringIO()
        engine.serve(self.engine, StringIO('\n'.join(requests) + '\n'), output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertEqual([row['code'] for row in replies[:3]], ['invalid-request'] * 3)
        self.assertEqual(replies[3]['id'], 'good')
        self.assertEqual(replies[3]['total'], 1)

    def test_non_string_keys_and_extra_parameters_do_not_crash_stream(self):
        requests = [dict(version=1, method='details', params={'key': []}),
                    dict(version=1, method='execute', params={'action': 'install', 'key': ARCH_KEY, 'token': []}),
                    dict(version=1, method='catalog', params={'command': 'untrusted'})]
        output = StringIO()
        engine.serve(self.engine, StringIO('\n'.join(map(json.dumps, requests)) + '\n'), output)
        self.assertEqual([json.loads(line)['code'] for line in output.getvalue().splitlines()],
                         ['invalid-request'] * 3)

    def test_unavailable_key_never_calls_authorization(self):
        with patch.object(engine.subprocess, 'Popen') as process:
            self.assertEqual(self.engine.execute('install', 'arch:extra/missing:id', 'a' * 64, Mock())['code'], 'not-found')
            process.assert_not_called()

    def test_progress_keeps_request_id_and_backend_kind(self):
        def respond(_request, emit):
            emit('progress', kind='flatpak', percent=50)
            return dict(ok=True)
        self.engine.request = respond
        output = StringIO()
        engine.serve(self.engine, StringIO('{"version":1,"id":7,"method":"execute"}\n'), output)
        event = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(event, dict(version=1, id=7, type='progress', kind='flatpak', percent=50))

    def test_all_catalog_sources_clean_nested_display_fields(self):
        controls = '\u202e\u2067\u200f\u200b\u061c\ufeff\x1b'
        self.metadata.return_value = ([dict(METADATA, name='Paint' + controls,
                                           categories=['Graphics' + controls],
                                           icons=[dict(kind='stock', value='paint' + controls)],
                                           description='Line one\nLine two' + controls)],
                                      ['Warning' + controls])
        self.flatpaks.return_value = ([dict(FLATPAK, name='Paint' + controls,
                                            installedVersion='1' + controls)], [])
        self.inventory.return_value[0]['extra/paint']['version'] = '2' + controls
        result = self.engine.catalog()
        self.assertEqual(result['warnings'][0], 'Warning')
        self.assertEqual(result['categories'], ['Graphics'])
        for row in result['programs']:
            self.assertEqual(row['name'], 'Paint')
        self.assertEqual(self.engine.details(ARCH_KEY)['program']['description'], 'Line one\nLine two')
        aur = engine.aur_records(dict(version=5, type='search', results=[dict(
            Name='paint-git', Version='3' + controls, Description='Paint' + controls,
            License=['GPL' + controls])]), {'paint-git': '2' + controls})
        self.assertEqual(aur[0]['summary'], 'Paint')
        self.assertEqual(aur[0]['licence'], 'GPL')
        self.assertEqual(aur[0]['installedVersion'], '2')

    def test_unsafe_catalog_identity_is_not_rewritten_to_another_target(self):
        for field in ('id', 'origin'):
            self.metadata.return_value = ([dict(METADATA, **{field: METADATA[field] + '\u202e'})], [])
            self.engine.rows = None
            self.assertEqual(self.engine.catalog(source='arch')['total'], 0)
        self.assertTrue(self.engine.warnings)

    def test_protocol_cleans_results_errors_and_progress_at_output_boundary(self):
        dirty = 'Value\u202e\u2066\u200f\u200b\ufeff'
        def respond(_request, emit):
            emit('progress', message=dirty, operations=[{'label': dirty}])
            return dict(ok=False, warnings=[dirty], details={'label': dirty}, message=dirty)
        self.engine.request = respond
        output = StringIO()
        engine.serve(self.engine, StringIO('{"version":1,"id":7,"method":"execute"}\n'), output)
        progress, answer = map(json.loads, output.getvalue().splitlines())
        self.assertEqual(progress['message'], 'Value')
        self.assertEqual(progress['operations'], [{'label': 'Value'}])
        self.assertEqual(answer['warnings'], ['Value'])
        self.assertEqual(answer['details'], {'label': 'Value'})
        self.assertEqual(answer['message'], 'Value')

    def test_protocol_rejects_unsafe_request_id_instead_of_changing_correlation(self):
        request = dict(version=1, id='id\u202e', method='catalog')
        output = StringIO()
        engine.serve(self.engine, StringIO(json.dumps(request) + '\n'), output)
        answer = json.loads(output.getvalue())
        self.assertEqual(answer['code'], 'invalid-request')
        self.assertIsNone(answer['id'])
        self.inventory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
