#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Flathub discovery and transaction boundary tests."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_flatpak as backend

APP = 'app/org.example.Editor/x86_64/stable'
RUNTIME = 'runtime/org.example.Platform/x86_64/24.08'
EXTENSION = 'runtime/org.example.Editor.Locale/x86_64/stable'
ROW = dict(installation='system', origin='apps', ref=APP, remoteUrl='https://dl.flathub.org/repo/')


def reference(ref=APP, origin='apps', commit='a' * 64, delete=False):
    return SimpleNamespace(format_ref=lambda: ref, get_origin=lambda: origin,
                           get_commit=lambda: commit, get_name=lambda: ref.split('/')[1],
                           get_arch=lambda: ref.split('/')[2],
                           get_kind=lambda: ref.split('/')[0], get_appdata_version=lambda: '1.0',
                           get_download_size=lambda: 123, get_installed_size=lambda: 456,
                           should_delete=lambda: delete)


def remote(path, name='apps', url='https://dl.flathub.org/repo/', disabled=False, verified=True):
    return SimpleNamespace(get_name=lambda: name, get_url=lambda: url,
                           get_disabled=lambda: disabled, get_gpg_verify=lambda: verified,
                           get_appstream_dir=lambda arch: SimpleNamespace(get_path=lambda: str(path)))


def operation(ref=APP, kind='install', origin='apps', commit='b' * 64, related=()):
    return SimpleNamespace(get_ref=lambda: ref, get_operation_type=lambda: SimpleNamespace(value_nick=kind),
                           get_remote=lambda: origin, get_commit=lambda: commit,
                           get_related_to_ops=lambda: related, get_is_skipped=lambda: False,
                           get_subpaths=lambda: [], get_download_size=lambda: 123,
                           get_installed_size=lambda: 456)


class Installation:
    def __init__(self, path, identity='user', remotes=None, installed=None):
        self.identity = identity
        self.remotes = remotes if remotes is not None else [remote(path)]
        self.installed = installed or []
        self.refs = [reference()]
        self.related = []
        self.calls = []
        self.path = str(path / identity)
        self.no_interaction = False

    def set_no_interaction(self, value):
        self.no_interaction = value

    def get_id(self):
        return self.identity

    def get_is_user(self):
        return self.identity == 'user'

    def get_path(self):
        return SimpleNamespace(get_path=lambda: self.path)

    def list_remotes(self, unused):
        return self.remotes

    def get_remote_by_name(self, name, unused):
        return next(item for item in self.remotes if item.get_name() == name)

    def list_installed_refs(self, unused):
        return self.installed

    def list_installed_related_refs_sync(self, origin, ref, unused):
        return self.related

    def list_remote_refs_sync_full(self, origin, flags, unused):
        self.calls.append(('cached', origin, flags))
        return self.refs

    def list_remote_refs_sync(self, origin, unused):
        self.calls.append(('network', origin))
        return self.refs

    def update_appstream_sync(self, *args):
        self.calls.append(('refresh', args[0]))
        return True


class Transaction:
    def __init__(self, operations):
        self.operations = operations
        self.signals = {}
        self.options = {}
        self.committed = False
        self.request = None
        self.uninstalls = []
        self.fail_commit = False
        self.authenticated = False
        self.after_auth_operations = None

    def __getattr__(self, name):
        if name.startswith('set_'):
            return lambda value: self.options.update({name: value})
        raise AttributeError(name)

    def connect(self, signal, callback):
        self.signals[signal] = callback

    def add_install(self, remote, ref, subpaths):
        self.request = ('install', remote, ref, subpaths)

    def add_uninstall(self, ref):
        self.request = ('remove', ref)
        self.uninstalls.append(ref)

    def get_operations(self):
        return self.operations

    def run(self, unused):
        if not self.signals.get('ready-pre-auth', lambda tx: True)(self):
            raise RuntimeError('aborted before authentication')
        self.authenticated = True
        if self.after_auth_operations is not None:
            self.operations = self.after_auth_operations
        if not self.signals['ready'](self):
            raise RuntimeError('aborted before changes')
        if self.fail_commit:
            raise RuntimeError('deployment failed')
        self.committed = True
        return True


class FlatpakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='emaki-flatpak-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.installation = Installation(self.path, 'default')
        self.user = Installation(self.path)
        self.installations = [self.installation]
        self.transactions = []
        self.after_auth_operations = None
        self.operations = [operation(), operation(RUNTIME)]
        library = SimpleNamespace(QueryFlags=SimpleNamespace(ONLY_CACHED=1),
                                  get_supported_arches=lambda: ['x86_64', 'i386'],
                                  RefKind=SimpleNamespace(APP='app'),
                                  Installation=SimpleNamespace(new_user=Mock(return_value=self.user),
                                                               new_system=Mock(return_value=self.installation)),
                                  Transaction=SimpleNamespace(new_for_installation=self.new_transaction))
        self.library = library
        for patcher in [patch.object(backend.os, 'getuid', return_value=1000),
                        patch.object(backend.os, 'geteuid', return_value=1000),
                        patch.object(backend.shutil, 'which', return_value='flatpak'),
                        patch.object(backend, '_library', return_value=library),
                        patch.object(backend, '_installations', side_effect=lambda _, warnings: self.installations)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_transaction(self, installation, unused):
        tx = Transaction(self.operations)
        tx.after_auth_operations = self.after_auth_operations
        self.transactions.append(tx)
        return tx

    def execute_system(self, action, row, token, emit):
        with patch.object(backend.os, 'getuid', return_value=0), \
                patch.object(backend.os, 'geteuid', return_value=0):
            return backend.execute(action, row, token, emit)

    def test_absent_flatpak_never_loads_library(self):
        with patch.object(backend.shutil, 'which', return_value=None), patch.object(backend, '_library') as lib:
            self.assertEqual(backend.discover(), ([], []))
            self.assertEqual(backend.plan('install', ROW)['code'], 'flatpak-unavailable')
            lib.assert_not_called()

    def test_remote_name_does_not_establish_flathub(self):
        self.installation.remotes = [remote(self.path, name='flathub', url='https://example.org/repo')]
        rows, warnings = backend.discover()
        self.assertEqual(rows, [])
        self.assertTrue(warnings)

    def test_disabled_and_unsigned_remotes_excluded(self):
        self.installation.remotes = [remote(self.path, disabled=True), remote(self.path, verified=False)]
        self.assertEqual(backend.discover()[0], [])

    def test_cached_metadata_matches_exact_bundle_and_sizes(self):
        (self.path / 'appstream.xml.gz').touch()
        metadata = dict(id='org.example.Editor', name='Editor', summary='Edit files',
                        bundles=[dict(kind='flatpak', id=APP)], downloadSize=None)
        self.installation.refs += [reference(RUNTIME)]
        with patch.object(backend.programs_appstream, 'read_file', return_value=[metadata]) as read:
            rows, warnings = backend.discover()
        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['summary'], 'Edit files')
        self.assertEqual(rows[0]['downloadSize'], 123)
        self.assertFalse(rows[0]['installed'])
        self.assertEqual(read.call_count, 1)
        self.assertEqual(self.installation.calls, [('cached', 'apps', 1)])

    def test_missing_metadata_is_explicit(self):
        rows, warnings = backend.discover()
        self.assertTrue(warnings)
        self.assertFalse(rows[0]['metadataAvailable'])

    def test_metadata_different_branch_does_not_leak(self):
        (self.path / 'appstream.xml.gz').touch()
        metadata = dict(id='org.example.Editor', name='Beta',
                        bundles=[dict(kind='flatpak', id=APP.replace('/stable', '/beta'))])
        with patch.object(backend.programs_appstream, 'read_file', return_value=[metadata]):
            rows, unused = backend.discover()
        self.assertFalse(rows[0]['metadataAvailable'])
        self.assertNotEqual(rows[0]['name'], 'Beta')

    def test_refresh_is_explicit(self):
        self.installations = [self.user]
        backend.discover(refresh=True)
        self.assertEqual(self.user.calls, [('refresh', 'apps'), ('network', 'apps')])

    def test_duplicate_refs_prefer_existing_user_and_disclose_copies(self):
        self.installation.installed = [reference()]
        self.user.installed = [reference()]
        self.installations += [self.user, Installation(self.path, 'games', installed=[reference()])]
        rows, unused = backend.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['installation'], 'user')
        self.assertEqual(rows[0]['installedVersion'], '1.0')
        self.assertEqual(rows[0]['installedCopies'], [
            dict(installation='user', origin='apps'),
            dict(installation='system', origin='apps'),
            dict(installation='games', origin='apps')])
        self.assertFalse(rows[0]['requiresAuthentication'])

    def test_user_catalog_without_deployments_is_omitted(self):
        self.installations = [self.user]
        self.assertEqual(backend.discover()[0], [])
        self.installations.append(self.installation)
        rows, unused = backend.discover()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['installation'], 'system')
        self.assertEqual(rows[0]['installedCopies'], [])

    def test_named_installed_copy_is_read_only(self):
        self.installations = [Installation(self.path, 'games', installed=[reference()])]
        rows, unused = backend.discover()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['manageable'])
        self.assertEqual(rows[0]['managementNotice'], backend.READ_ONLY_NOTICE)

    def test_plan_includes_dependencies_and_never_commits(self):
        result = backend.plan('install', ROW)
        self.assertTrue(result['ok'], result)
        self.assertEqual([row['ref'] for row in result['operations']], [APP, RUNTIME])
        self.assertFalse(self.transactions[-1].committed)
        self.assertEqual(self.transactions[-1].request, ('install', 'apps', APP, None))
        self.assertEqual(len(result['token']), 64)

    def test_preview_aborts_before_authentication(self):
        result = backend.plan('install', ROW)
        self.assertTrue(result['ok'], result)
        tx = self.transactions[-1]
        self.assertIn('ready-pre-auth', tx.signals)
        self.assertFalse(tx.authenticated)
        self.assertFalse(tx.committed)
        self.assertTrue(tx.options['set_no_interaction'])
        self.assertTrue(self.installation.no_interaction)
        self.assertFalse(tx.signals['ready'](tx))

    def test_execution_compares_plan_before_deployment(self):
        planned = backend.plan('install', ROW)
        self.after_auth_operations = [operation(), operation(RUNTIME, commit='d' * 64)]
        result = self.execute_system('install', ROW, planned['token'], Mock())
        self.assertEqual(result['code'], 'plan-changed')
        tx = self.transactions[-1]
        self.assertTrue(tx.authenticated)
        self.assertTrue(tx.options['set_no_interaction'])
        self.assertTrue(self.installation.no_interaction)
        self.assertFalse(tx.committed)

    def test_cached_discovery_disables_authentication(self):
        backend.discover()
        self.assertTrue(self.installation.no_interaction)

    def test_catalog_excludes_unsupported_architecture_but_keeps_installed_apps(self):
        foreign = reference(APP.replace('x86_64', 'aarch64'))
        self.installation.refs.append(foreign)
        rows, _ = backend.discover()
        self.assertEqual([row['ref'] for row in rows], [APP])
        self.installation.installed.append(foreign)
        rows, _ = backend.discover()
        self.assertEqual({row['ref'] for row in rows}, {APP, foreign.format_ref()})
        self.assertEqual(backend.plan('install', dict(ROW, ref=foreign.format_ref()))['code'],
                         'unsupported-architecture')

    def test_system_refresh_reads_summary_without_updating_system_appstream(self):
        rows, unused = backend.discover(refresh=True)
        self.assertEqual(self.installation.calls, [('network', 'apps')])
        self.assertEqual(rows[0]['installation'], 'system')
        self.assertTrue(rows[0]['manageable'])
        self.assertTrue(rows[0]['requiresAuthentication'])
        self.assertEqual(rows[0]['managementNotice'], backend.SYSTEM_NOTICE)

    def test_installed_user_app_remains_visible_without_remote_ref_or_summary(self):
        self.installations = [self.user]
        local = reference()
        del local.get_download_size
        self.user.installed = [local]
        self.user.refs = []
        rows, _ = backend.discover()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['installed'])
        self.assertTrue(rows[0]['manageable'])
        self.assertIsNone(rows[0]['downloadSize'])
        with patch.object(self.user, 'list_remote_refs_sync_full', side_effect=RuntimeError('uncached')):
            rows, warnings = backend.discover()
        self.assertEqual([row['ref'] for row in rows], [APP])
        self.assertTrue(warnings)

    def test_named_system_targets_refused_before_library(self):
        with patch.object(backend, '_library') as library:
            for action in ('install', 'remove'):
                row = dict(ROW, installation='games')
                self.assertEqual(backend.plan(action, row)['code'], 'installation-read-only')
                self.assertEqual(backend.execute(action, row, 'a' * 64, Mock())['code'],
                                 'installation-read-only')
            library.assert_not_called()

    def test_system_execution_requires_both_root_ids_before_library(self):
        for real, effective in ((1000, 1000), (1000, 0), (0, 1000)):
            with patch.object(backend.os, 'getuid', return_value=real), \
                    patch.object(backend.os, 'geteuid', return_value=effective), \
                    patch.object(backend, '_library') as library:
                for action in ('install', 'remove'):
                    self.assertEqual(backend.execute(action, ROW, 'a' * 64, Mock())['code'],
                                     'authentication-required')
                library.assert_not_called()
        self.assertEqual(self.transactions, [])

    def test_user_install_refused_before_library(self):
        with patch.object(backend, '_library') as library:
            row = dict(ROW, installation='user')
            self.assertEqual(backend.plan('install', row)['code'], 'user-installation-refused')
            self.assertEqual(backend.execute('install', row, 'a' * 64, Mock())['code'],
                             'user-installation-refused')
            library.assert_not_called()

    def test_root_and_setuid_user_removal_refused_before_library(self):
        for real, effective in ((0, 0), (1000, 0), (0, 1000)):
            with patch.object(backend.os, 'getuid', return_value=real), \
                    patch.object(backend.os, 'geteuid', return_value=effective), \
                    patch.object(backend, '_library') as library:
                row = dict(ROW, installation='user')
                self.assertEqual(backend.plan('remove', row)['code'], 'root-not-supported')
                self.assertEqual(backend.execute('remove', row, 'a' * 64, Mock())['code'],
                                 'root-not-supported')
                library.assert_not_called()

    def test_mutations_use_fixed_system_constructor(self):
        with patch.object(backend, '_installations', side_effect=AssertionError('enumeration')):
            for action in ('install', 'remove'):
                self.installation.installed = [] if action == 'install' else [reference()]
                self.operations = [operation(kind='install' if action == 'install' else 'uninstall')]
                planned = backend.plan(action, ROW)
                self.assertTrue(planned['ok'], planned)
                self.assertEqual(planned['managementScope'], 'system')
                self.assertTrue(planned['requiresAuthentication'])
                self.assertEqual(planned['managementNotice'], backend.SYSTEM_NOTICE)
                result = self.execute_system(action, ROW, planned['token'], Mock())
                self.assertTrue(result['ok'], result)
                self.assertTrue(self.transactions[-1].committed)
                self.assertTrue(self.transactions[-1].options['set_no_interaction'])
                self.assertTrue(self.installation.no_interaction)
        self.library.Installation.new_system.assert_called_with(None)
        self.library.Installation.new_user.assert_not_called()

    def test_existing_user_removal_executes_without_root(self):
        self.user.installed = [reference()]
        self.operations = [operation(kind='uninstall')]
        row = dict(ROW, installation='user')
        planned = backend.plan('remove', row)
        self.assertTrue(planned['ok'], planned)
        self.assertFalse(planned['requiresAuthentication'])
        self.assertEqual(planned['managementScope'], 'user')
        result = backend.execute('remove', row, planned['token'], Mock())
        self.assertTrue(result['ok'], result)
        self.assertTrue(self.transactions[-1].committed)
        self.library.Installation.new_user.assert_called_with(None)
        self.library.Installation.new_system.assert_not_called()

    def test_root_discovery_does_not_refresh_user_metadata(self):
        self.installations = [self.user]
        with patch.object(backend.os, 'geteuid', return_value=0):
            backend.discover(refresh=True)
        self.assertEqual(self.user.calls, [('cached', 'apps', 1)])

    def test_execution_refuses_if_system_installation_identity_changes(self):
        planned = backend.plan('install', ROW)
        self.installation.identity = 'games'
        result = self.execute_system('install', ROW, planned['token'], Mock())
        self.assertEqual(result['code'], 'installation-changed')
        self.assertEqual(len(self.transactions), 1)
        self.assertFalse(self.transactions[0].committed)

    def test_system_constructor_must_return_default_system(self):
        for identity in ('user', 'games'):
            self.installation.identity = identity
            self.assertEqual(backend.plan('install', ROW)['code'], 'installation-changed')
        self.assertEqual(self.transactions, [])

    def test_user_constructor_must_return_user_installation(self):
        self.user.identity = 'default'
        row = dict(ROW, installation='user')
        self.assertEqual(backend.plan('remove', row)['code'], 'installation-changed')
        self.assertEqual(self.transactions, [])

    def test_exact_plan_executes(self):
        planned = backend.plan('install', ROW)
        result = self.execute_system('install', ROW, planned['token'], Mock())
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['code'], 'completed')
        self.assertTrue(self.transactions[-1].committed)

    def test_changed_dependency_commit_refuses_before_changes(self):
        planned = backend.plan('install', ROW)
        self.operations = [operation(), operation(RUNTIME, commit='c' * 64)]
        result = self.execute_system('install', ROW, planned['token'], Mock())
        self.assertEqual(result['code'], 'plan-changed')
        self.assertFalse(self.transactions[-1].committed)

    def test_changed_dependency_set_refuses(self):
        planned = backend.plan('install', ROW)
        self.operations = [operation()]
        self.assertEqual(self.execute_system('install', ROW, planned['token'], Mock())['code'], 'plan-changed')

    def test_wrong_origin_and_remote_change_refuse(self):
        self.installation.installed = [reference(origin='other')]
        self.assertEqual(backend.plan('remove', ROW)['code'], 'origin-changed')
        self.installation.installed = []
        self.installation.remotes = [remote(self.path, url='https://flathub.org/repo')]
        self.assertEqual(backend.plan('install', ROW)['code'], 'remote-changed')

    def test_arbitrary_remotes_and_runtime_targets_refused(self):
        self.assertEqual(backend.plan('install', dict(ROW, ref=RUNTIME))['code'], 'invalid-target')
        self.installation.remotes = [remote(self.path, url='https://example.org/repo')]
        self.assertEqual(backend.plan('install', ROW)['code'], 'remote-refused')

    def test_undo_selects_recorded_runtime_even_with_unrelated_unused_runtime(self):
        other = 'runtime/org.other.Platform/x86_64/1'
        self.installation.installed = [reference(), reference(RUNTIME), reference(other)]
        self.operations = [operation(kind='uninstall'), operation(RUNTIME, kind='uninstall')]
        receipt = dict(id='receipt', kind='flatpak', target=APP, refs=[APP, RUNTIME], undone='')
        variants = []
        glib = SimpleNamespace(Variant=lambda kind, refs: variants.append((kind, refs)) or refs)
        self.installation.list_unused_refs_with_options = Mock(return_value=[reference(RUNTIME), reference(other)])
        with patch.dict(sys.modules, {'gi.repository': SimpleNamespace(GLib=glib)}):
            plan = backend.undo_plan(ROW, receipt)
            self.assertTrue(plan['ok'], plan)
            self.assertEqual(plan['remove'], [APP, RUNTIME])
            self.assertEqual(plan['keep'], {})
            self.assertEqual(self.transactions[-1].uninstalls, [APP, RUNTIME])
            self.assertFalse(self.transactions[-1].options['set_include_unused_uninstall_ops'])
            with patch.object(backend.os, 'getuid', return_value=0), patch.object(backend.os, 'geteuid', return_value=0):
                result = backend.undo_execute(ROW, receipt, plan['token'], None)
            self.assertTrue(result['ok'], result)
        self.assertEqual(variants, [('as', [APP]), ('a{sv}', {'exclude-refs': [APP]})] * 4)

    def test_undo_keeps_recorded_runtime_used_by_another_application(self):
        self.installation.installed = [reference(), reference(RUNTIME)]
        self.operations = [operation(kind='uninstall')]
        receipt = dict(id='receipt', kind='flatpak', target=APP, refs=[APP, RUNTIME], undone='')
        with patch.object(backend, '_unused_recorded', return_value=set()):
            plan = backend.undo_plan(ROW, receipt)
        self.assertTrue(plan['ok'], plan)
        self.assertEqual(plan['remove'], [APP])
        self.assertEqual(plan['keep'], {RUNTIME: 'in-use'})
        self.assertEqual(self.transactions[-1].uninstalls, [APP])

    def test_undo_does_not_claim_success_when_resolver_skips_unused_recorded_runtime(self):
        self.installation.installed = [reference(), reference(RUNTIME)]
        self.operations = [operation(kind='uninstall')]
        receipt = dict(id='receipt', kind='flatpak', target=APP, refs=[APP, RUNTIME], undone='')
        with patch.object(backend, '_unused_recorded', return_value={RUNTIME}):
            plan = backend.undo_plan(ROW, receipt)
        self.assertFalse(plan['ok'])
        self.assertFalse(self.transactions[-1].committed)

    def test_undo_refuses_runtime_that_becomes_used_before_commit(self):
        self.installation.installed = [reference(), reference(RUNTIME)]
        self.operations = [operation(kind='uninstall'), operation(RUNTIME, kind='uninstall')]
        receipt = dict(id='receipt', kind='flatpak', target=APP, refs=[APP, RUNTIME], undone='')
        with patch.object(backend, '_unused_recorded', side_effect=[{RUNTIME}, set()]):
            plan = backend.undo_plan(ROW, receipt)
        self.assertFalse(plan['ok'])
        self.assertFalse(self.transactions[-1].committed)

    def test_removal_keeps_data_and_unused_runtimes(self):
        self.installation.installed = [reference(), reference(EXTENSION)]
        self.installation.related = [reference(EXTENSION, delete=True)]
        app = operation(kind='uninstall', commit=None)
        self.operations = [app, operation(EXTENSION, kind='uninstall', related=[app], commit=None)]
        result = backend.plan('remove', ROW)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['operations'][0]['commit'], 'a' * 64)
        tx = self.transactions[-1]
        self.assertFalse(tx.options['set_force_uninstall'])
        self.assertFalse(tx.options['set_include_unused_uninstall_ops'])
        self.assertEqual(tx.request, ('remove', APP))

    def test_unrelated_runtime_removal_refused_even_with_related_app(self):
        self.installation.installed = [reference(), reference(RUNTIME)]
        app = operation(kind='uninstall')
        self.operations = [app, operation(RUNTIME, kind='uninstall', related=[app])]
        self.assertEqual(backend.plan('remove', ROW)['code'], 'removal-scope-refused')
        self.assertFalse(self.transactions[-1].committed)

    def test_related_extension_without_delete_flag_refused(self):
        self.installation.installed = [reference(), reference(EXTENSION)]
        self.installation.related = [reference(EXTENSION, delete=False)]
        app = operation(kind='uninstall')
        self.operations = [app, operation(EXTENSION, kind='uninstall', related=[app])]
        self.assertEqual(backend.plan('remove', ROW)['code'], 'removal-scope-refused')

    def test_new_remote_and_rebase_never_authorized(self):
        backend.plan('install', ROW)
        signals = self.transactions[-1].signals
        self.assertFalse(signals['add-new-remote']())
        self.assertFalse(signals['end-of-lifed-with-rebase']())
        self.assertEqual(signals['choose-remote-for-ref'](None, APP, RUNTIME, ['other', 'apps']), 1)
        self.assertEqual(signals['choose-remote-for-ref'](None, APP, RUNTIME, ['other']), -1)

    def test_update_is_delegated_elsewhere_and_token_required(self):
        self.assertEqual(backend.plan('update', ROW)['code'], 'unsupported-action')
        self.assertEqual(self.execute_system('install', ROW, None, Mock())['code'], 'invalid-token')
        self.assertEqual(self.execute_system('install', ROW, 'bad', Mock())['code'], 'invalid-token')

    def test_install_cannot_smuggle_uninstall_or_another_app(self):
        for op in [operation(RUNTIME, kind='uninstall'), operation(APP.replace('Editor', 'Other'))]:
            self.operations = [operation(), op]
            self.assertEqual(backend.plan('install', ROW)['code'], 'operation-refused')

    def test_unresolved_commit_refuses(self):
        self.operations = [operation(commit='abc')]
        self.assertEqual(backend.plan('install', ROW)['code'], 'unresolved-commit')

    def test_installation_path_changes_token(self):
        planned = backend.plan('install', ROW)
        self.installation.path += '-new'
        self.assertEqual(self.execute_system('install', ROW, planned['token'], Mock())['code'], 'plan-changed')


class InstallationIsolationTests(unittest.TestCase):
    """Exercise enumeration with an actual incomplete OSTree repository."""

    def setUp(self):
        try:
            self.flatpak = backend._library()
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as error:
            self.skipTest('Flatpak introspection is unavailable: ' + str(error))
        self.gio, self.glib = Gio, GLib
        self.tmp = tempfile.TemporaryDirectory(prefix='emaki-flatpak-broken-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.user = Installation(self.path, installed=[reference()])
        self.system = Installation(self.path, 'default')
        self.named = Installation(self.path, 'games', installed=[reference()])
        self.transactions = []
        self.library = SimpleNamespace(
            QueryFlags=SimpleNamespace(ONLY_CACHED=1),
            RefKind=SimpleNamespace(APP='app'),
            get_supported_arches=lambda: ['x86_64'],
            Installation=SimpleNamespace(new_user=Mock(return_value=self.user),
                                         new_system=Mock(return_value=self.system)),
            get_system_installations=Mock(return_value=[self.system]),
            Transaction=SimpleNamespace(new_for_installation=self.new_transaction))
        for patcher in [patch.object(backend.os, 'getuid', return_value=1000),
                        patch.object(backend.os, 'geteuid', return_value=1000),
                        patch.object(backend.shutil, 'which', return_value='flatpak'),
                        patch.object(backend, '_library', return_value=self.library)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_transaction(self, installation, unused):
        kind = 'uninstall' if installation is self.user else 'install'
        tx = Transaction([operation(kind=kind)])
        self.transactions.append(tx)
        return tx

    def break_installation(self, scope):
        path = self.path / ('broken-' + scope)
        repo = path / 'repo'
        repo.mkdir(parents=True)
        (repo / 'config').write_text('[core]\nrepo_version=1\nmode=bare-user-only\n')
        (repo / 'flathub.trustedkeys.gpg').touch()

        def open_broken(unused):
            return self.flatpak.Installation.new_for_path(
                self.gio.File.new_for_path(str(path)), scope == 'user', None)

        with self.assertRaises(self.glib.Error) as error:
            open_broken(None)
        self.assertIn('objects', str(error.exception))
        self.assertFalse((repo / 'objects').exists())
        constructor = getattr(self.library.Installation, 'new_' + scope)
        constructor.side_effect = open_broken
        return constructor

    def test_broken_user_keeps_system_discovery_and_install_preview(self):
        broken = self.break_installation('user')
        rows, warnings = backend.discover()
        self.assertEqual([row['installation'] for row in rows], ['system'])
        self.assertTrue(rows[0]['manageable'])
        self.assertTrue(rows[0]['requiresAuthentication'])
        self.assertIn('Flatpak installation could not be read: user', warnings)
        result = backend.plan('install', rows[0])
        self.assertTrue(result['ok'], result)
        self.assertEqual(broken.call_count, 1)
        self.assertFalse(self.transactions[-1].authenticated)
        self.assertFalse(self.transactions[-1].committed)

    def test_broken_system_keeps_user_discovery_and_removal_preview(self):
        broken = self.break_installation('system')
        self.library.get_system_installations.return_value = []
        rows, warnings = backend.discover()
        self.assertEqual([row['installation'] for row in rows], ['user'])
        self.assertTrue(rows[0]['installed'])
        self.assertTrue(rows[0]['manageable'])
        self.assertFalse(rows[0]['requiresAuthentication'])
        self.assertIn('Flatpak installation could not be read: system', warnings)
        result = backend.plan('remove', rows[0])
        self.assertTrue(result['ok'], result)
        self.assertEqual(broken.call_count, 1)
        self.assertFalse(self.transactions[-1].committed)
        self.assertEqual(backend.plan('install', rows[0])['code'], 'user-installation-refused')

    def test_both_broken_report_each_scope(self):
        self.break_installation('user')
        self.break_installation('system')
        self.library.get_system_installations.return_value = []
        rows, warnings = backend.discover()
        self.assertEqual(rows, [])
        self.assertEqual(warnings, [
            'Flatpak installation could not be read: user',
            'Flatpak installation could not be read: system'])

    def test_named_installation_survives_broken_default_without_becoming_writable(self):
        self.break_installation('system')
        self.user.installed = []
        self.library.get_system_installations.return_value = [self.named]
        rows, warnings = backend.discover()
        self.assertEqual([row['installation'] for row in rows], ['games'])
        self.assertFalse(rows[0]['manageable'])
        self.assertEqual(rows[0]['managementNotice'], backend.READ_ONLY_NOTICE)
        self.assertIn('Flatpak installation could not be read: system', warnings)
        self.assertEqual(backend.plan('remove', rows[0])['code'], 'installation-read-only')

    def test_enumerated_default_is_not_added_twice(self):
        warnings = []
        self.library.get_system_installations.return_value = [self.system, self.named]
        self.assertEqual(backend._installations(self.library, warnings),
                         [self.user, self.system, self.named])
        self.assertEqual(warnings, [])
        self.library.Installation.new_user.assert_called_once_with(None)
        self.library.Installation.new_system.assert_called_once_with(None)

    def test_named_enumeration_failure_keeps_both_managed_scopes(self):
        self.library.get_system_installations.side_effect = RuntimeError('unreadable configuration')
        warnings = []
        self.assertEqual(backend._installations(self.library, warnings), [self.user, self.system])
        self.assertEqual(warnings, ['Additional Flatpak installations could not be read.'])


if __name__ == '__main__':
    unittest.main()
