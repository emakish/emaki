#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unified catalog, source identity, refusal delegation and JSON protocol tests."""
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_engine as engine
import programs_catalog as removal

METADATA = dict(id='org.example.Paint', componentType='desktop-application', name='Paint', summary='Draw colourful pictures',
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
        self.enterContext(patch.object(engine, 'syslog'))
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
        self.receipts = Path(self.tmp.name) / "installs"
        self.enterContext(patch.object(engine.receipts, "DIRECTORY", self.receipts))

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

    def test_essentials_follow_rich_package_metadata_and_installed_marker(self):
        root = Path(__file__).resolve().parents[2]
        rich = runpy.run_path(str(root / 'installer/emaki_installer/constants.py'))['RICH_APPS']
        description = '%NAME%\nemaki-apps\n\n%VERSION%\n0.8.0-3\n\n%OPTDEPENDS%\n' + '\n'.join(rich)
        self.inventory.return_value[0]['emaki/emaki-apps'] = engine.arch.parse_desc(description, 'emaki')
        result = self.engine.catalog()
        self.assertEqual(result['essentials'], dict(minimal=True, programs=rich))
        # Installed Rich programs can be individually removed without making
        # the machine a Minimal installation.
        self.inventory.return_value[1]['emaki-apps'] = '0.8.0-3'
        result = self.engine.catalog(refresh=True)
        self.assertEqual(result['essentials'], dict(minimal=False, programs=rich))
        self.assertNotIn('firefox', result['essentials']['programs'])
        self.assertNotIn('dolphin', result['essentials']['programs'])

    def test_essentials_fail_closed_when_package_state_is_unknown(self):
        self.inventory.side_effect = RuntimeError('unavailable')
        for query in (SimpleNamespace(returncode=1, stdout=''), OSError('unavailable')):
            with self.subTest(query=query):
                self.legacy.pacman.side_effect = query if isinstance(query, Exception) else None
                self.legacy.pacman.return_value = query
                self.assertEqual(self.engine.catalog(refresh=True)['essentials'],
                                 dict(minimal=False, programs=[]))

    def test_missing_rich_metadata_has_no_invented_essentials(self):
        self.assertEqual(self.engine.catalog()['essentials'], dict(minimal=True, programs=[]))

    def test_popularity_is_ranked_within_each_source(self):
        ranks = engine.percentile_ranks({'a': 198059, 'b': 1000, 'c': 1000, 'd': 5})
        self.assertEqual(ranks, {'a': 100.0, 'b': 50.0, 'c': 50.0, 'd': 0.0})
        self.assertEqual(engine.percentile_ranks({'only': 3}), {'only': 100.0})
        self.assertEqual(engine.percentile_ranks({}), {})

    def test_live_popularity_uses_package_and_bundle_ids_and_expires(self):
        self.flatpaks.return_value[0][0]['id'] = 'org.example.Paint.desktop'
        self.engine.popularity = Mock()
        self.engine.popularity.snapshot.return_value = {
            'arch': {'paint': 50, 'other': 10.5}, 'flathub': {'org.example.Paint': 1234, 'org.example.Big': 99999}}
        result = self.engine.catalog()
        # Each source is ranked by itself (100 = its most popular): installs and shares never compete raw.
        self.assertEqual({row['key']: row['popularity'] for row in result['programs']},
                         {ARCH_KEY: 100.0, FP_KEY: 0.0})
        self.assertEqual(self.engine.catalog(scope='packages')['programs'][0]['popularity'], 100.0)
        # Later requests observe the cache without reloading repository data.
        self.engine.popularity.snapshot.return_value = {'arch': {'paint': 0}, 'flathub': {}}
        result = self.engine.catalog()
        rows = {row['key']: row for row in result['programs']}
        self.assertEqual(rows[ARCH_KEY]['popularity'], 100.0)
        self.assertNotIn('popularity', rows[FP_KEY])
        self.engine.popularity.snapshot.return_value = {'arch': {}, 'flathub': {}}
        self.assertTrue(all('popularity' not in row for row in self.engine.catalog()['programs']))
        self.inventory.assert_called_once()

    def test_arch_ranks_ignore_nonprogram_samples_and_catalog_filters(self):
        for name, category in (('viewer', 'Graphics'), ('player', 'AudioVideo')):
            self.inventory.return_value[0]['extra/' + name] = dict(PACKAGE, package=name)
            self.metadata.return_value[0].append(dict(METADATA, id='org.example.' + name,
                                                       name=name, packages=[name], categories=[category]))
        # These available packages are absent from AppStream; their high scores
        # must not dilute the percentile of real catalog programs.
        self.inventory.return_value[0]['extra/library'] = dict(PACKAGE, package='library')
        self.engine.popularity = Mock()
        scores = {'paint': 30, 'viewer': 20, 'player': 10}
        self.engine.popularity.snapshot.return_value = {'arch': scores, 'flathub': {}}
        def ranks(**options):
            return {row['package']: row['popularity']
                    for row in self.engine.catalog(source='arch', **options)['programs']}
        expected = {'paint': 100.0, 'viewer': 50.0, 'player': 0.0}
        self.assertEqual(ranks(), expected)
        scores.update(library=99, absent=98, unsampled=0)
        self.assertEqual(ranks(), expected)
        self.assertEqual(ranks(query='viewer'), {'viewer': 50.0})
        self.assertEqual(ranks(category='Graphics'), {'paint': 100.0, 'viewer': 50.0})
        self.assertEqual(ranks(offset=1, limit=1), {'player': 0.0})
        package_rows = self.engine.catalog(scope='packages')['programs']
        self.assertNotIn('popularity', next(row for row in package_rows if row['package'] == 'library'))

    def test_arch_popularity_excludes_dependency_tools_and_build_frontends(self):
        available = self.inventory.return_value[0]
        metadata = self.metadata.return_value[0]
        names = ('avahi', 'v4l-utils', 'appstream', 'cmake', 'qt6-tools', 'firefox', 'editor')
        for name in names:
            available['extra/' + name] = dict(PACKAGE, package=name)
            metadata.append(dict(METADATA, id='org.example.' + name, name=name, packages=[name]))
        available['extra/paint']['requiredPackages'] = ['bridge', 'v4l-utils', 'appstream', 'qt6-tools']
        available['extra/bridge'] = dict(PACKAGE, package='bridge', requiredPackages=['avahi'])
        # Language packs and optional integrations are not evidence that a
        # desktop application's install count came from other programs.
        available['extra/firefox-lang'] = dict(PACKAGE, package='firefox-lang', requiredPackages=['firefox'])
        available['extra/paint']['optionalPackages'] = ['editor']
        next(row for row in metadata if row['packages'] == ['cmake'])['categories'] = ['Development', 'Building']
        next(row for row in metadata if row['packages'] == ['editor'])['categories'] = ['Development', 'IDE']
        self.engine.popularity = Mock()
        self.engine.popularity.snapshot.return_value = {
            'arch': dict(zip(names, (99, 98, 97, 96, 95, 80, 40)), paint=20), 'flathub': {}}
        rows = {row['package']: row for row in self.engine.catalog(source='arch')['programs']}
        self.assertEqual({name: row['popularity'] for name, row in rows.items() if 'popularity' in row},
                         {'firefox': 100.0, 'editor': 50.0, 'paint': 0.0})
        self.assertTrue(all('popularity' not in rows[name] for name in names[:5]))

    def test_arch_popularity_requires_desktop_type_and_preserves_aur_scores(self):
        self.engine.popularity = Mock()
        self.engine.popularity.snapshot.return_value = {'arch': {'paint': 70}, 'flathub': {}}
        self.engine.aur = {'aur:foreign': dict(key='aur:foreign', kind='aur', package='foreign',
                                             id='foreign', name='Foreign', recorded=True, popularity=12.5)}
        self.inventory.return_value[1]['foreign'] = '1'
        for component_type in ('console-application', ''):
            with self.subTest(component_type=component_type):
                self.metadata.return_value[0][0]['componentType'] = component_type
                rows = {row['key']: row for row in self.engine.catalog(refresh=True)['programs']}
                self.assertNotIn('popularity', rows[ARCH_KEY])
                self.assertEqual(rows['aur:foreign']['popularity'], 12.5)

    def test_arch_dependency_cycles_and_unique_virtual_providers(self):
        rows = [dict(kind='arch', package=name, componentType='desktop-application')
                for name in ('paint', 'viewer', 'shared', 'choice-a', 'choice-b')]
        available = {
            'paint': dict(package='paint', requiredPackages=['bridge', 'unique-api', 'ambiguous-api']),
            'bridge': dict(package='bridge', requiredPackages=['paint', 'viewer']),
            'viewer': dict(package='viewer', requiredPackages=['bridge']),
            'shared': dict(package='shared', providedPackages=['unique-api']),
            'choice-a': dict(package='choice-a', providedPackages=['ambiguous-api']),
            'choice-b': dict(package='choice-b', providedPackages=['ambiguous-api']),
        }
        self.assertEqual(engine.arch_popularity_packages(rows, available), {'choice-a', 'choice-b'})
        # A cycle back to the same desktop is not a different program's dependency.
        self.assertEqual(engine.arch_popularity_packages(rows[:1], available), {'paint'})

    def test_dependency_package_names_strip_constraints_and_reject_invalid_names(self):
        package = engine.arch.parse_desc('%NAME%\npaint\n\n%VERSION%\n1\n\n'
                                         '%DEPENDS%\nviewer>=2\nlibview.so=1-64\ninvalid name\n../escape\n\n'
                                         '%PROVIDES%\ncanvas=1\ninvalid name\n', 'extra')
        self.assertEqual(package['requiredPackages'], ['viewer', 'libview.so'])
        self.assertEqual(package['providedPackages'], ['canvas'])
        package = engine.arch.parse_desc('%NAME%\npaint\n\n%VERSION%\n1\n', 'extra')
        self.assertEqual(package['requiredPackages'], [])
        self.assertEqual(package['providedPackages'], [])

    def test_newest_release_survives_native_merge_and_flatpak_catalog(self):
        first = dict(METADATA, releaseDate='2026-09-30', screenshots=[])
        second = dict(METADATA, releaseDate='2026-10-12')
        third = dict(METADATA, releaseDate='2026-10-13', screenshots=[])
        self.flatpaks.return_value[0][0]['releaseDate'] = '2026-10-11'
        for records in ([first, second, third], [third, second, first]):
            with self.subTest(first=records[0]['releaseDate']):
                self.metadata.return_value = deepcopy(records), []
                rows = {row['key']: row for row in self.engine.catalog(refresh=True)['programs']}
                self.assertEqual(rows[ARCH_KEY]['releaseDate'], '2026-10-13')
                self.assertEqual(rows[ARCH_KEY]['screenshots'], second['screenshots'])
                self.assertEqual(rows[FP_KEY]['releaseDate'], '2026-10-11')
                self.assertNotIn('releaseDate', self.engine.catalog(scope='packages')['programs'][0])

    def test_installed_metainfo_keeps_catalog_icons_in_every_catalog_view(self):
        installed = dict(METADATA, icons=[], origin='', screenshots=[])
        catalog = dict(METADATA, screenshots=[], icons=[dict(
            kind='cached', value='/metadata/icons/64x64/paint.jxl', width=64, height=64)])
        for records in ([installed, catalog], [catalog, installed]):
            with self.subTest(installed_first=records[0] is installed):
                self.metadata.return_value = (deepcopy(records), [])
                self.engine.rows = None
                for filters in ({}, {'installed': True}, {'query': 'Paint'}):
                    result = self.engine.catalog(source='arch', **filters)
                    self.assertEqual(result['total'], 1)
                    self.assertTrue(result['programs'][0]['installed'])
                    self.assertEqual(result['programs'][0]['icons'], catalog['icons'])
                self.assertEqual(self.engine.details(ARCH_KEY)['program']['icons'], catalog['icons'])

    def test_duplicate_icons_survive_screenshot_preference_without_mutating_metadata(self):
        first = dict(METADATA, screenshots=[])
        second = dict(METADATA, icons=[dict(kind='cached', value='/metadata/paint.jxl',
                                          width=64, height=64)])
        third = dict(METADATA, icons=first['icons'] + second['icons'], screenshots=[])
        for records in ([first, second, third], [second, first, third]):
            with self.subTest(first=records[0]['icons']):
                original = deepcopy(records)
                rows = engine.arch_rows(records, {'extra/paint': PACKAGE}, {'paint': '1'})
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['screenshots'], second['screenshots'])
                self.assertCountEqual(rows[0]['icons'], first['icons'] + second['icons'])
                self.assertEqual(records, original)

    def test_duplicate_records_keep_every_provided_id(self):
        # The record with fewer screenshots may be the one naming an older ratings key.
        first = dict(METADATA, screenshots=[], providedIds=['paint.desktop'])
        second = dict(METADATA, providedIds=['org.example.OldPaint'])
        for records in ([first, second], [second, first]):
            with self.subTest(first=records[0]['providedIds']):
                original = deepcopy(records)
                rows = engine.arch_rows(records, {'extra/paint': PACKAGE}, {'paint': '1'})
                self.assertCountEqual(rows[0]['providedIds'], ['paint.desktop', 'org.example.OldPaint'])
                self.assertEqual(records, original)

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
                warning = 'Flatpak installation could not be read: ' + broken
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

    def test_package_scope_has_inventory_rows_and_exact_program_links(self):
        available, installed = self.inventory.return_value
        available['core/library'] = dict(PACKAGE, repository='core', package='library', summary='Audio codec')
        available['testing/paint'] = dict(PACKAGE, repository='testing')
        installed['library'] = '3-1'
        result = self.engine.catalog(scope='packages')
        rows = {row['key']: row for row in result['programs']}
        self.assertEqual(set(rows), {'package:core/library', 'package:extra/paint', 'package:testing/paint'})
        library = rows['package:core/library']
        self.assertEqual((library['name'], library['installed'], library['installedVersion'], library['actions']),
                         ('library', True, '3-1', ['remove']))
        self.assertTrue(library['packageOnly'])
        self.assertEqual(library['programKey'], '')
        self.assertEqual(rows['package:extra/paint']['programKey'], ARCH_KEY)
        self.assertEqual(rows['package:testing/paint']['programKey'], 'arch:testing/paint:org.example.Paint')
        self.assertEqual(rows['package:extra/paint']['summary'], 'Painter')
        self.assertEqual(result['categories'], [])
        self.assertFalse(any(row.get('packageOnly') for row in self.engine.catalog()['programs']))

    def test_package_scope_search_pagination_and_uninstalled_inventory(self):
        available, _ = self.inventory.return_value
        available['core/codec'] = dict(PACKAGE, repository='core', package='codec', summary='Audio codec')
        available['extra/sound'] = dict(PACKAGE, package='sound', summary='Audio tools')
        self.metadata.return_value = ([], [])
        result = self.engine.catalog(scope='packages', query='AUDIO', limit=1, offset=1)
        self.assertEqual((result['total'], result['offset'], result['limit']), (2, 1, 1))
        self.assertEqual(result['programs'][0]['package'], 'sound')
        self.assertEqual(result['programs'][0]['actions'], ['install'])
        self.assertEqual(self.engine.catalog(scope='packages', query='CODEC audio')['total'], 1)
        self.assertEqual(self.engine.catalog(scope='packages', installed=True)['total'], 1)
        self.assertEqual(self.engine.catalog()['total'], 1)

    def test_package_scope_rejects_invalid_options_before_inventory(self):
        for options in ({'scope': 'everything'}, {'scope': []}, {'scope': 'packages', 'includeAur': True},
                        {'scope': 'packages', 'source': 'flatpak'}, {'scope': 'packages', 'source': 'aur'},
                        {'scope': 'packages', 'category': 'Graphics'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.engine.catalog(**options)
        self.inventory.assert_not_called()

    def test_invalid_program_identity_does_not_hide_repository_package(self):
        self.metadata.return_value[0][0]['id'] = 'unsafe\u202eidentifier'
        result = self.engine.catalog(scope='packages')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['programs'][0]['programKey'], '')
        self.assertTrue(self.engine.details('package:extra/paint')['ok'])

    def test_generic_package_uses_existing_install_and_remove_transactions(self):
        self.metadata.return_value = ([], [])
        key = 'package:extra/paint'
        with patch.object(engine.arch, 'install_plan', return_value=dict(ok=True, token='a' * 64,
                          fullUpgrade=True, packages=[])) as install:
            plan = self.engine.plan('install', key)
            install.assert_called_once_with('extra/paint', cache=self.engine.cache, refresh=True)
            process = Mock(stdout=StringIO(''), wait=Mock(return_value=126))
            child = Mock(__enter__=Mock(return_value=process), __exit__=Mock(return_value=False))
            with patch.object(engine.subprocess, 'Popen', return_value=child) as spawn:
                self.assertEqual(self.engine.execute('install', key, plan['token'], Mock())['code'],
                                 'not-authorized')
            arguments = spawn.call_args.args[0]
            self.assertEqual((arguments[0], Path(arguments[1]).name, arguments[2:]),
                             ('pkexec', 'emaki-programs-install', ['extra/paint', 'a' * 64]))
        self.legacy.preview.return_value = removal.assess('paint', 0, 'paint\npaint-data\n', '')
        plan = self.engine.plan('remove', key)
        self.remove_native.return_value = dict(ok=True, code='removed')
        self.assertTrue(self.engine.execute('remove', key, plan['token'], Mock())['ok'])
        self.remove_native.assert_called_once_with('paint', 'paint,paint-data')

    def test_generic_package_removal_keeps_protections_and_rechecks_state(self):
        key = 'package:extra/paint'
        for code, stdout, stderr in [(0, 'paint\nemaki-config\n', ''), (0, 'paint\nlinux\n', ''),
                                    (1, ":: removing paint breaks dependency 'paint' required by viewer", 'error')]:
            expected = removal.assess('paint', code, stdout, stderr)
            self.legacy.preview.return_value = expected
            self.assertEqual(self.engine.plan('remove', key), expected)
        self.legacy.preview.return_value = removal.assess('paint', 0, 'paint\n', '')
        plan = self.engine.plan('remove', key)
        self.inventory.return_value[1].clear()
        self.assertEqual(self.engine.execute('remove', key, plan['token'], Mock())['code'], 'not-installed')
        self.remove_native.assert_not_called()

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
            install.assert_called_once_with('extra/paint', cache=self.engine.cache, refresh=True)
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

    def test_browse_picks_come_from_the_storefront_file_and_the_window_names_none(self):
        path = Path(engine.__file__).with_name('storefront.json')
        written = json.loads(path.read_text())
        served = self.engine.catalog()['storefront']
        self.assertEqual(served, engine.storefront_value(written))
        self.assertEqual(served['featured']['programs'], [name.lower() for name in written['featured']['programs']])
        self.assertEqual(len(served['featured']['programs']), 3)
        # Browse has no hand-made category rows (the 13.10 layout): picks, then live shelves.
        self.assertEqual(served['rows'], [])
        identities = (written['featured']['programs'] + [name for row in written['rows'] for name in row['first']]
                      + list(written['names']) + list(written['names'].values()))
        ui = Path(engine.__file__).with_name('ui')
        for source in sorted(ui.glob('*.qml')) + sorted(ui.glob('*.js')):
            text = source.read_text().lower()
            for identity in identities:
                self.assertNotIn('"' + identity.lower() + '"', text, f'{source.name} names {identity}')

    def test_a_broken_storefront_file_shows_no_picks_rather_than_guessing(self):
        good = json.loads(Path(engine.__file__).with_name('storefront.json').read_text())
        empty = dict(featured=dict(title='', note='', programs=[]), rows=[], names={})
        broken = [
            '{', '[]', json.dumps(dict(good, version=2)), json.dumps(dict(good, extra=1)),
            json.dumps(dict(good, featured=dict(good['featured'], programs=['a.b', 'c.d', 'e.f', 'g.h']))),
            json.dumps(dict(good, featured=dict(good['featured'], programs=['--root']))),
            json.dumps(dict(good, featured=dict(good['featured'], title='x' * 65))),
            json.dumps(dict(good, featured=dict(good['featured'], note='Picked‮by'))),
            json.dumps(dict(good, rows=[dict(category='Graphics', first=['krita'], extra=1)])),
            json.dumps(dict(good, rows=[dict(category='../x', first=[])])),
            json.dumps(dict(good, names={'org.gimp.GIMP': 7})),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'storefront.json'
            for text in broken:
                with self.subTest(text=text[:60]):
                    path.write_text(text)
                    self.assertEqual(engine.storefront([path, Path(engine.__file__).with_name('storefront.json')]), empty)
            # A missing file falls through to the installed copy.
            self.assertEqual(engine.storefront([Path(directory) / 'missing.json', path.with_name('none.json')]), empty)
            path.write_text(json.dumps(good))
            self.assertEqual(engine.storefront([Path(directory) / 'missing.json', path])['names'], {'org.gimp.gimp': 'GIMP'})

    def test_popular_aur_list_needs_consent_and_keeps_the_aurs_order(self):
        names = Mock(return_value=(['zeta', 'yay', 'paint-bin'], False))
        results = [dict(Name='zeta', Version='1-1', NumVotes=900, Popularity=0.5),
                   dict(Name='yay', Version='12-1', NumVotes=2649, Popularity=20.7),
                   dict(Name='paint-bin', Version='3-1', NumVotes=10, Popularity=2.0)]
        backend = SimpleNamespace(fetch=Mock(side_effect=lambda _url, _cache, validate: (validate(json.dumps(
            dict(version=5, type='multiinfo', results=results)).encode()), False)))
        with patch.object(engine.aur, 'popular_names', names), patch.object(engine, 'update_backend', return_value=backend):
            self.assertEqual(self.engine.request(dict(version=1, method='popular', params={}), Mock())['code'], 'aur-closed')
            names.assert_not_called()
            backend.fetch.assert_not_called()
            self.engine.aur_status(accept=True)
            answer = self.engine.request(dict(version=1, method='popular', params={}), Mock())
        self.assertEqual(set(answer), {'ok', 'programs', 'warnings'})
        self.assertEqual([row['package'] for row in answer['programs']], ['yay', 'paint-bin', 'zeta'])
        self.assertEqual([row['popularity'] for row in answer['programs']], [20.7, 2.0, 0.5])
        # A repackaging of a catalog program joins that program's page, with its source choice.
        joined = answer['programs'][1]
        self.assertEqual((joined['id'], joined['sameAs']), ('org.example.Paint', ARCH_KEY))
        self.assertIn('arg%5B%5D=yay', backend.fetch.call_args.args[0])
        self.assertEqual(self.engine.details('aur:yay')['program']['votes'], 2649)
        with patch.object(engine.aur, 'popular_names', side_effect=OSError('offline')):
            answer = self.engine.aur_popular()
        self.assertEqual((answer['ok'], answer['programs']), (True, []))
        self.assertTrue(answer['warnings'])

    def test_popular_aur_texts_endorse_nothing(self):
        endorsement = re.compile(r'trust|tested|recommend|verified|vetted|curated|endorse|\bsafe|picked|\bbest\b'
                                 r'|featured|approved|reliable|worth trying', re.I)
        source = Path(engine.__file__).read_text()
        body = source[source.index('    def aur_popular(self):'):source.index('    def details(self, key):')]
        texts = re.findall(r"'([^']*)'", body) + list(engine.MESSAGES.values())
        self.assertTrue(texts)
        self.assertFalse([text for text in texts if endorsement.search(text)])
        view = (Path(engine.__file__).with_name('ui') / 'ManagerView.qml').read_text()
        page = view[view.index('objectName: "aur-page"'):view.index('objectName: "installed-page"')]
        self.assertIn('Popular on the AUR', page)
        self.assertFalse(endorsement.findall(' '.join(re.findall(r'"([^"]*)"', page))))

    def test_aur_results_rank_the_exact_name_first_then_by_use(self):
        rows = [dict(key='aur:' + name, kind='aur', package=name, name=name, popularity=popularity)
                for name, popularity in (('cppfetch', 0.0), ('pfetch-git', 0.5), ('pfetch', 0.1), ('dripfetch', 2.0),
                                         ('pfetch-bin', 0.9))]
        ranked = [row['package'] for row in engine.select(rows, query='pfetch', source='aur')['programs']]
        self.assertEqual(ranked, ['pfetch', 'pfetch-bin', 'pfetch-git', 'dripfetch', 'cppfetch'])
        self.assertEqual([row['package'] for row in engine.select(rows, query='fetch')['programs']],
                         ['cppfetch', 'dripfetch', 'pfetch', 'pfetch-bin', 'pfetch-git'])

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

    def aur_backend(self, *names):
        results = [dict(Name=name, Version='3-1', Description='paint', License=['GPL']) for name in names]
        return SimpleNamespace(fetch=Mock(side_effect=lambda _url, _cache, validate: (validate(json.dumps(
            dict(version=5, type='search', results=results)).encode()), False)))

    def test_direct_aur_install_plan_refuses_an_older_available_version(self):
        self.installed_foreign('paint-bin')
        self.inventory.return_value[1]['paint-bin'] = '4-1'
        self.engine.aur_status(accept=True)
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-bin')), \
                patch.object(self.engine, 'aur_plan', return_value={'ok': True, 'kind': 'aur', 'token': 'b' * 64}) as build:
            self.engine.catalog(query='paint', source='aur')
            result = self.engine.plan('install', 'aur:paint-bin')
        self.assertEqual(result['code'], 'up-to-date')
        build.assert_not_called()

    def test_aur_section_stays_closed_without_consent(self):
        backend = self.aur_backend('paint-git')
        with patch.object(engine, 'update_backend', return_value=backend), \
                patch.object(engine.aur, 'build_plan') as build, patch.object(engine.subprocess, 'Popen') as process:
            self.assertEqual(self.engine.catalog(query='paint', source='aur')['code'], 'aur-closed')
            result = self.engine.catalog(query='paint', includeAur=True)
            self.assertFalse([row for row in result['programs'] if row['kind'] == 'aur'])
            self.assertEqual(self.engine.details('aur:paint-git')['code'], 'aur-closed')
            self.assertEqual(self.engine.plan('install', 'aur:paint-git')['code'], 'aur-closed')
            self.assertEqual(self.engine.execute('install', 'aur:paint-git', 'a' * 64, Mock())['code'], 'aur-closed')
            self.assertEqual(self.engine.related(ARCH_KEY), dict(ok=True, programs=[]))
            backend.fetch.assert_not_called()
            build.assert_not_called()
            process.assert_not_called()

    def test_consent_opens_search_and_installs_only_through_the_reviewed_build_plan(self):
        status = self.engine.request(dict(version=1, method='aur', params=dict(accept=True)), Mock())
        self.assertTrue(status['enabled'])
        plan = dict(kind='aur', target='paint-git', builds=[], packages=[], fullUpgrade=True, token='b' * 64)
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-git')), \
                patch.object(engine.aur, 'build_plan', return_value=plan) as build, \
                patch.object(self.engine, 'aur_status', return_value=dict(snapshots=True)):
            row = next(row for row in self.engine.catalog(query='paint', source='aur')['programs'])
            self.assertEqual((row['key'], row['actions']), ('aur:paint-git', ['install']))
            reviewed = self.engine.plan('install', row['key'])
            self.assertEqual((reviewed['kind'], reviewed['transaction']['token']), ('aur', 'b' * 64))
            build.assert_called_once()
            self.assertEqual(build.call_args.args[0], 'paint-git')
            process = Mock()
            process.__enter__ = Mock(return_value=SimpleNamespace(stdout=[json.dumps(dict(
                type='finished', ok=True, code='success'))], wait=Mock(return_value=0)))
            process.__exit__ = Mock(return_value=False)
            with patch.object(engine.subprocess, 'Popen', return_value=process) as popen:
                self.assertTrue(self.engine.execute('install', row['key'], reviewed['token'], Mock())['ok'])
            command = popen.call_args.args[0]
            self.assertEqual(command[0], 'pkexec')
            self.assertTrue(command[1].endswith('/emaki-programs-aur'))
            self.assertEqual(command[2:], ['paint-git', 'b' * 64])
            self.assertEqual(self.engine.execute('install', row['key'], 'c' * 64, Mock())['code'], 'changed')
        self.engine.request(dict(version=1, method='aur', params=dict(accept=False)), Mock())
        self.assertEqual(self.engine.plan('install', 'aur:paint-git')['code'], 'aur-closed')

    def test_build_plan_refusals_keep_their_reason(self):
        self.engine.aur_status(accept=True)
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-git')), \
                patch.object(engine.aur, 'build_plan', side_effect=engine.aur.Refused(
                    'missing-dependency', dependency='ghost')), \
                patch.object(self.engine, 'aur_status', return_value=dict(snapshots=True)):
            self.engine.catalog(query='paint', source='aur')
            answer = self.engine.plan('install', 'aur:paint-git')
        self.assertEqual((answer['code'], answer['dependency']), ('missing-dependency', 'ghost'))

    def test_an_aur_repackaging_joins_the_catalog_program_page(self):
        self.engine.aur_status(accept=True)
        backend = self.aur_backend('paint-bin', 'paint-git')
        with patch.object(engine, 'update_backend', return_value=backend):
            rows = self.engine.catalog(query='paint', source='aur')['programs']
            joined = next(row for row in rows if row['key'] == 'aur:paint-bin')
            alone = next(row for row in rows if row['key'] == 'aur:paint-git')
            self.assertEqual((joined['id'], joined['sameAs']), ('org.example.Paint', ARCH_KEY))
            self.assertEqual(joined['icons'], METADATA['icons'])
            self.assertEqual(alone['id'], 'paint-git')
            related = self.engine.related(ARCH_KEY)['programs']
            self.assertEqual([row['key'] for row in related], ['aur:paint-bin'])
        self.assertIn('arg%5B%5D=paint-bin', backend.fetch.call_args.args[0])

    AUR_FACTS = ('votes', 'popularity', 'maintainer', 'orphan', 'outOfDate', 'firstSubmitted', 'lastModified',
                 'page', 'recipe')

    def installed_foreign(self, *names):
        # paint comes from extra; every other installed name no configured repository has.
        self.inventory.return_value = ({'extra/paint': deepcopy(PACKAGE)},
                                       dict({'paint': '1'}, **{name: '3-1' for name in names}))

    def aur_record(self, name, undone=False):
        value = engine.receipts.record('aur', 'aur:' + name, name.title(), name, {}, {name: '3-1'},
                                       directory=self.receipts)
        if undone:
            engine.receipts.mark_undone(self.receipts / (value['id'] + '.json'), value)
        return value

    def test_installed_aur_program_gets_its_rpc_facts_and_undo_in_a_fresh_window(self):
        self.installed_foreign('paint-bin', 'zz-handmade')
        receipt = self.aur_record('paint-bin')
        self.engine.aur_status(accept=True)
        results = [dict(Name='paint-bin', Version='3-1', NumVotes=7, Popularity=0.25, Maintainer='someone',
                        FirstSubmitted=1600000000, LastModified=1700000000)]
        backend = SimpleNamespace(fetch=Mock(side_effect=lambda _url, _cache, validate: (validate(json.dumps(
            dict(version=5, type='multiinfo', results=results)).encode()), False)))
        with patch.object(engine, 'update_backend', return_value=backend):
            rows = self.engine.catalog(limit=500)['programs']
            # One request for every installed foreign package, once per catalog load.
            self.engine.catalog(limit=500)
            self.engine.catalog(limit=500, offset=1)
        backend.fetch.assert_called_once()
        self.assertTrue(backend.fetch.call_args.args[0].endswith('?arg%5B%5D=paint-bin&arg%5B%5D=zz-handmade'))
        self.assertEqual(self.engine.foreign, {'paint-bin', 'zz-handmade'})
        row = next(row for row in rows if row['key'] == 'aur:paint-bin')
        self.assertEqual((row['kind'], row['installed'], row['installedVersion']), ('aur', True, '3-1'))
        self.assertEqual((row['votes'], row['popularity'], row['maintainer']), (7, 0.25, 'someone'))
        self.assertEqual(row['actions'], ['remove', 'undo'])
        self.assertEqual(row['undo']['id'], receipt['id'])
        self.assertEqual(row['sameAs'], ARCH_KEY)
        self.assertFalse([row for row in rows if row['key'] == 'aur:zz-handmade'])

    def test_the_lookup_cap_keeps_recorded_installs_and_skips_debug_symbols(self):
        names = ['lib-%02d' % index for index in range(engine.MAX_INSTALLED_AUR + 10)]
        self.installed_foreign('zz-recorded', 'aa-debug', *names)
        self.aur_record('zz-recorded')
        self.engine.aur_status(accept=True)
        backend = SimpleNamespace(fetch=Mock(return_value=([], False)))
        with patch.object(engine, 'update_backend', return_value=backend):
            self.engine.catalog(limit=500)
        asked = backend.fetch.call_args.args[0]
        self.assertEqual(asked.count('arg%5B%5D='), engine.MAX_INSTALLED_AUR)
        self.assertIn('=zz-recorded', asked)
        self.assertNotIn('aa-debug', asked)

    def test_offline_only_a_recorded_aur_install_gets_a_row_and_its_facts_stay_unknown(self):
        self.installed_foreign('paint-bin', 'brush-git', 'zz-handmade')
        self.aur_record('paint-bin')
        self.aur_record('brush-git', undone=True)
        # Only an AUR receipt says the AUR: a repository install record of the same name does not.
        engine.receipts.record('arch', '', 'Handmade', 'zz-handmade', {}, {'zz-handmade': '3-1'},
                               directory=self.receipts)
        self.engine.aur_status(accept=True)
        backend = SimpleNamespace(fetch=Mock(side_effect=RuntimeError('offline')))
        with patch.object(engine, 'update_backend', return_value=backend):
            answer = self.engine.catalog(limit=500)
        backend.fetch.assert_called_once()
        rows = [row for row in answer['programs'] if row['kind'] == 'aur']
        self.assertEqual([row['key'] for row in rows], ['aur:paint-bin'])
        row = rows[0]
        self.assertEqual((row['name'], row['package'], row['version'], row['installed'], row['installedVersion']),
                         ('Paint-Bin', 'paint-bin', '3-1', True, '3-1'))
        self.assertEqual(row['actions'], ['remove', 'undo'])
        for fact in self.AUR_FACTS:
            self.assertNotIn(fact, row)
        self.assertFalse([warning for warning in answer['warnings'] if 'AUR' in warning])
        # A confirmed Undo reloads before it plans again: the stand-in survives that reload offline.
        with patch.object(engine, 'update_backend', return_value=backend):
            self.engine.rows = None
            answer = self.engine.details('aur:paint-bin')
        self.assertTrue(answer['ok'])
        self.assertEqual(answer['program']['actions'], ['remove', 'undo'])
        backend.fetch.assert_called_once()
        # Its version follows the installed one: a stand-in never offers an install.
        self.inventory.return_value = (self.inventory.return_value[0], dict(self.inventory.return_value[1], **{'paint-bin': '4-1'}))
        self.engine.load(refresh=True)
        self.assertEqual((self.engine.aur['aur:paint-bin']['version'], self.engine.aur['aur:paint-bin']['actions']),
                         ('4-1', ['remove']))
        # Once the package is gone, so is its stand-in.
        self.installed_foreign('brush-git')
        self.engine.load(refresh=True)
        self.assertNotIn('aur:paint-bin', self.engine.aur)

    def test_successful_rpc_omission_keeps_only_live_recorded_aur_installs(self):
        self.installed_foreign('paint-bin', 'brush-git', 'private-app')
        receipt = self.aur_record('paint-bin')
        self.aur_record('brush-git', undone=True)
        self.engine.aur_status(accept=True)
        backend = SimpleNamespace(fetch=Mock(return_value=([], False)))
        with patch.object(engine, 'update_backend', return_value=backend):
            rows = self.engine.catalog(limit=500)['programs']
        rows = [row for row in rows if row['kind'] == 'aur']
        self.assertEqual([row['key'] for row in rows], ['aur:paint-bin'])
        self.assertEqual(rows[0]['undo']['id'], receipt['id'])
        self.assertEqual(rows[0]['actions'], ['remove', 'undo'])

    def test_recorded_aur_identity_survives_closed_section_and_missing_repository_data(self):
        self.installed_foreign('paint-bin')
        self.aur_record('paint-bin')
        backend = SimpleNamespace(fetch=Mock(side_effect=RuntimeError('must not fetch')))
        with patch.object(engine, 'update_backend', return_value=backend):
            rows = self.engine.catalog(limit=500)['programs']
            recorded = [row for row in rows if row['kind'] == 'aur']
            self.assertEqual([row['key'] for row in recorded], ['aur:paint-bin'])
            self.assertEqual(recorded[0]['actions'], ['remove', 'undo'])
            self.assertTrue(self.engine.details('aur:paint-bin')['ok'])
            self.assertEqual(self.engine.plan('install', 'aur:paint-bin')['code'], 'aur-closed')
            self.assertEqual(self.engine.details('aur:other')['code'], 'aur-closed')
            self.engine.aur_status(accept=True)
            self.inventory.return_value = ({}, {'paint': '1', 'paint-bin': '3-1'})
            self.engine.catalog(limit=500, refresh=True)
            self.assertEqual(self.engine.foreign, set())
            self.assertIn('aur:paint-bin', self.engine.aur)
        backend.fetch.assert_not_called()

    def test_closing_aur_keeps_recorded_installed_identity_without_network(self):
        self.installed_foreign('paint-bin')
        self.aur_record('paint-bin')
        self.engine.aur_status(accept=True)
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-bin')):
            self.engine.catalog(limit=500)
        self.engine.aur_status(accept=False)
        backend = SimpleNamespace(fetch=Mock(side_effect=RuntimeError('must not fetch')))
        with patch.object(engine, 'update_backend', return_value=backend):
            rows = self.engine.catalog(limit=500, refresh=True)['programs']
            recorded = [row for row in rows if row['kind'] == 'aur']
            self.assertEqual([row['key'] for row in recorded], ['aur:paint-bin'])
            self.assertTrue(self.engine.details('aur:paint-bin')['ok'])
            self.assertEqual(recorded[0]['actions'], ['remove', 'undo'])
        backend.fetch.assert_not_called()

    def test_manual_version_change_withdraws_repository_undo(self):
        engine.receipts.record('arch', '', 'paint', 'paint', {}, {'paint': '1'},
                               '17', directory=self.receipts)
        self.assertIn('undo', self.engine.details(ARCH_KEY)['program']['actions'])
        self.inventory.return_value = ({'extra/paint': deepcopy(PACKAGE)}, {'paint': '2'})
        self.engine.rows = None
        row = self.engine.details(ARCH_KEY)['program']
        self.assertNotIn('undo', row['actions'])
        self.assertNotIn('undo', row)

    def test_manual_change_to_install_dependencies_withdraws_undo(self):
        engine.receipts.record('arch', '', 'paint', 'paint', {'libbrush': '2'},
                               {'paint': '1', 'libbrush': '3'}, '17', directory=self.receipts)
        self.inventory.return_value[1]['libbrush'] = '3'
        self.assertIn('undo', self.engine.details(ARCH_KEY)['program']['actions'])
        self.inventory.return_value[1]['libbrush'] = '4'
        self.engine.rows = None
        self.assertNotIn('undo', self.engine.details(ARCH_KEY)['program']['actions'])
        self.assertEqual(self.engine.plan('undo', ARCH_KEY)['code'], 'nothing-to-undo')

    def test_undo_is_offered_from_the_receipt_and_maps_to_that_exact_install(self):
        self.inventory.return_value[1]['libbrush'] = '3'
        self.assertNotIn('undo', self.engine.details(ARCH_KEY)['program']['actions'])
        receipt = engine.receipts.record('arch', '', 'paint', 'paint', {'base': '1'},
                                         {'base': '1', 'paint': '1', 'libbrush': '3'}, '17',
                                         directory=self.receipts)
        self.engine.rows = None
        program = self.engine.details(ARCH_KEY)['program']
        self.assertIn('undo', program['actions'])
        self.assertEqual(program['undo']['id'], receipt['id'])
        self.assertEqual(program['undo']['added'], ['libbrush', 'paint'])
        native = dict(ok=True, code='ready', receipt=receipt['id'], target='paint', remove=['libbrush', 'paint'],
                      keep={}, gone=[], upgraded=[], snapshot='17', token='d' * 64)
        with patch.object(engine.receipts, 'undo_plan', return_value=native) as undo_plan:
            plan = self.engine.plan('undo', ARCH_KEY)
            self.assertEqual(undo_plan.call_args.args[0]['id'], receipt['id'])
            self.assertIs(undo_plan.call_args.args[1], self.legacy.pacman)
            self.assertEqual(plan['transaction']['packages'], ['libbrush', 'paint'])
            process = Mock()
            process.__enter__ = Mock(return_value=SimpleNamespace(stdout=[json.dumps(dict(
                type='finished', ok=True, code='undone'))], wait=Mock(return_value=0)))
            process.__exit__ = Mock(return_value=False)
            with patch.object(engine.subprocess, 'Popen', return_value=process) as popen:
                self.assertTrue(self.engine.execute('undo', ARCH_KEY, plan['token'], Mock())['ok'])
            command = popen.call_args.args[0]
            self.assertTrue(command[1].endswith('/emaki-programs-undo'))
            self.assertEqual(command[2:], [receipt['id'], 'paint', 'd' * 64])

    def test_aur_lookups_keep_the_recorded_undo_offer(self):
        self.inventory.return_value[1]['paint-bin'] = '3-1'
        receipt = engine.receipts.record('aur', 'aur:paint-bin', 'Paint', 'paint-bin',
                                         {'paint': '1'}, {'paint': '1', 'paint-bin': '3-1'},
                                         '17', directory=self.receipts)
        engine.aur.set_consent(self.engine.consent_file, True)
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-bin')):
            for lookup in ('details', 'search', 'search', 'related', 'popular'):
                with self.subTest(lookup=lookup):
                    if lookup == 'details':
                        rows = [self.engine.details('aur:paint-bin')['program']]
                    elif lookup == 'search':
                        rows = self.engine.catalog(query='paint', source='aur')['programs']
                    elif lookup == 'related':
                        rows = self.engine.related(ARCH_KEY)['programs']
                    else:
                        with patch.object(engine.aur, 'popular_names', return_value=(['paint-bin'], False)):
                            rows = self.engine.aur_popular()['programs']
                    self.assertEqual(len(rows), 1)
                    self.assertIn('undo', rows[0]['actions'])
                    self.assertEqual(rows[0]['undo']['id'], receipt['id'])
                    self.assertEqual(self.engine.aur['aur:paint-bin']['undo']['id'], receipt['id'])

    def test_aur_undo_survives_engine_restart_from_its_receipt(self):
        self.inventory.return_value[1]['paint-bin'] = '3-1'
        receipt = engine.receipts.record('aur', 'aur:paint-bin', 'Paint', 'paint-bin', {},
                                         {'paint-bin': '3-1'}, '17', directory=self.receipts)
        engine.aur.set_consent(self.engine.consent_file, True)
        restarted = engine.Engine(self.legacy, self.tmp.name)
        self.assertEqual(restarted.aur, {})
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-bin')):
            row = restarted.details('aur:paint-bin')['program']
        self.assertIn('undo', row['actions'])
        self.assertEqual(row['undo']['id'], receipt['id'])

    def test_snapshot_undo_is_offered_without_package_additions(self):
        receipt = engine.receipts.record('arch', ARCH_KEY, 'Paint', 'paint',
                                         {'paint': '0', 'old-library': '1'}, {'paint': '1'},
                                         '17', directory=self.receipts)
        row = self.engine.details(ARCH_KEY)['program']
        self.assertIn('undo', row['actions'])
        self.assertEqual(row['undo']['id'], receipt['id'])
        self.assertEqual(row['undo']['mode'], 'snapshot')

    def test_cli_and_privileged_helper_use_account_consent_location(self):
        with patch.object(engine.os, 'geteuid', return_value=1000), \
                patch.object(engine.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=self.tmp.name)), \
                patch.dict(engine.os.environ, {'XDG_CONFIG_HOME': '/untrusted-config', 'HOME': '/untrusted-home'}), \
                patch.object(engine, 'Engine') as constructor, patch.object(engine, 'serve'):
            self.assertEqual(engine.main(['serve'], self.legacy), 0)
        self.assertEqual(constructor.call_args.args[2], Path(self.tmp.name) / '.config')

    def test_refreshed_aur_lookup_drops_an_already_consumed_receipt(self):
        self.inventory.return_value[1]['paint-bin'] = '3-1'
        receipt = engine.receipts.record('aur', 'aur:paint-bin', 'Paint', 'paint-bin', {},
                                         {'paint-bin': '3-1'}, '17', directory=self.receipts)
        engine.aur.set_consent(self.engine.consent_file, True)
        with patch.object(engine, 'update_backend', return_value=self.aur_backend('paint-bin')):
            self.assertIn('undo', self.engine.details('aur:paint-bin')['program']['actions'])
            receipt['undone'] = '2026-10-13T14:00:00+00:00'
            engine.receipts.write(self.receipts / (receipt['id'] + '.json'), receipt)
            row = self.engine.catalog(query='paint', source='aur', refresh=True)['programs'][0]
        self.assertNotIn('undo', row['actions'])
        self.assertNotIn('undo', row)

    def test_refused_undo_keeps_its_reason_and_never_authorizes(self):
        engine.receipts.record('arch', '', 'paint', 'paint', {}, {'paint': '1'}, directory=self.receipts)
        refused = dict(ok=False, code='required', requiredBy=['sketch'])
        with patch.object(engine.receipts, 'undo_plan', return_value=refused), \
                patch.object(engine.subprocess, 'Popen') as popen:
            answer = self.engine.plan('undo', ARCH_KEY)
            self.assertEqual((answer['code'], answer['reason'], answer['requiredBy']),
                             ('cannot-undo', 'required', ['sketch']))
            self.assertEqual(self.engine.execute('undo', ARCH_KEY, 'a' * 64, Mock())['code'], 'cannot-undo')
            popen.assert_not_called()
        self.assertEqual(self.engine.plan('undo', FP_KEY)['code'], 'not-installed')

    def test_aur_status_reports_snapshot_support(self):
        for mode, expected in (('normal', True), ('unsupported', False)):
            with self.subTest(mode=mode), patch.object(engine.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(dict(mode=mode)))):
                self.assertEqual(self.engine.aur_status()['snapshots'], expected)

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
