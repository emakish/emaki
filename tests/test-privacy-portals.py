#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Permission-store safety checks using no session bus or user files."""
import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'privacy_portals', Path(__file__).resolve().parents[1] / 'scripts/emaki_privacy_portals.py')
portal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal)


class FakeStore:
    def __init__(self, tables=None, failures=None):
        self.tables = tables or {}
        self.failures = failures or {}
        self.calls = []

    def call(self, method, signature, values, timeout):
        self.calls.append((method, signature, values, timeout))
        table = values[0]
        error = self.failures.get((method, table))
        if error:
            raise error
        if method == 'List':
            if table not in self.tables:
                raise portal._Missing()
            return list(self.tables[table])
        if method == 'Lookup':
            return self.tables[table][values[1]]
        if method == 'Delete':
            del self.tables[table][values[1]]
        if method == 'SetPermission':
            self.tables[table][values[2]][values[3]] = values[4]


    def revoke_document(self, resource, app, permissions, timeout):
        self.calls.append(('RevokePermissions', '(ssas)', (resource, app, permissions), timeout))
        error = self.failures.get(('RevokePermissions', 'documents'))
        if error:
            raise error
        current = self.tables['documents'][resource][app]
        self.tables['documents'][resource][app] = [p for p in current if p not in permissions]


class PermissionsTest(unittest.TestCase):
    def status(self, store):
        with patch.object(portal, '_connect', return_value=store):
            return portal.status()

    def revoke(self, store, row):
        with patch.object(portal, '_connect', return_value=store):
            return portal.revoke(row['table'], row['id'], row['app'])

    def test_empty_missing_is_normal_but_failed_is_unavailable(self):
        self.assertEqual(self.status(FakeStore()), dict(state='ready', rows=[], unavailable=[]))
        failures = {('List', table): portal._Unavailable() for table in portal.TABLES}
        self.assertEqual(self.status(FakeStore(failures=failures))['state'], 'unavailable')
        result = self.status(FakeStore(failures={('List', 'devices'): portal._Unavailable()}))
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['unavailable'], ['devices'])

    def test_screencast_token_is_private_and_delete_removes_restore_data(self):
        store = FakeStore({'screencast': {'private-token': {'org.example.App': ['yes']}}})
        row = self.status(store)['rows'][0]
        self.assertNotIn('private-token', repr(row))
        self.assertEqual(row['permissions'], ['remembered'])
        self.assertTrue(self.revoke(store, row))
        self.assertEqual(store.calls[-1][0:3], ('Delete', '(ss)', ('screencast', 'private-token')))
        self.assertEqual(store.tables['screencast'], {})

    def test_shared_or_malformed_restore_entries_are_read_only(self):
        for grants in ({'a': ['yes'], 'b': ['yes']}, {'a': ['no']}, {'a': ['opaque']}):
            store = FakeStore({'screencast': {'token': grants}})
            for row in self.status(store)['rows']:
                self.assertFalse(row['can_revoke'])
                self.assertFalse(self.revoke(store, row))

    def test_revoke_resets_tri_state_and_clears_inhibition(self):
        for table, resource, permission, revoked in (
                ('inhibit', 'inhibit', ['idle', 'suspend'], []),
                ('devices', 'camera', ['yes'], ['ask']),
                ('devices', 'microphone', ['yes'], ['ask']),
                ('devices', 'speakers', ['yes'], ['ask']),
                ('screenshot', 'screenshot', ['yes'], ['ask']),
                ('background', 'background', ['yes'], ['ask'])):
            with self.subTest(table=table):
                store = FakeStore({table: {resource: {'app': permission}}})
                row = self.status(store)['rows'][0]
                self.assertTrue(self.revoke(store, row))
                self.assertEqual(store.calls[-1][0:3],
                                 ('SetPermission', '(sbssas)', (table, False, resource, 'app', revoked)))
                self.assertEqual(store.tables[table][resource]['app'], revoked)
                self.assertFalse(self.status(store)['rows'][0]['can_revoke'])

    def test_notification_grants_are_read_only(self):
        # xdg-desktop-portal allows notifications on ask without a prompt.
        for permission in (['yes'], ['no'], ['ask']):
            with self.subTest(permission=permission):
                store = FakeStore({'notifications': {'notification': {'app': permission}}})
                row = self.status(store)['rows'][0]
                self.assertEqual(row['permissions'], permission)
                self.assertFalse(row['can_revoke'])
                self.assertFalse(self.revoke(store, row))
                self.assertFalse(portal.revoke('notifications', 'notification', 'app'))
                self.assertEqual(store.tables['notifications']['notification']['app'], permission)
                self.assertTrue(all(call[0] in ('List', 'Lookup') for call in store.calls))

    def test_unidentified_applications_are_read_only_in_every_table(self):
        for table, resource, permission in (
                ('screencast', 'private-token', ['yes']),
                ('screenshot', 'screenshot', ['yes']),
                ('devices', 'camera', ['yes']),
                ('notifications', 'notification', ['yes']),
                ('background', 'background', ['yes']),
                ('inhibit', 'inhibit', ['idle']),
                ('documents', 'private-document', ['read']),
                ('location', 'location', ['CITY', '123'])):
            with self.subTest(table=table):
                store = FakeStore({table: {resource: {'': permission}}})
                row = self.status(store)['rows'][0]
                self.assertFalse(row['can_revoke'])
                self.assertFalse(self.revoke(store, row))
                self.assertEqual(store.tables[table][resource][''], permission)
                self.assertTrue(all(call[0] in ('List', 'Lookup') for call in store.calls))

    def test_unidentified_application_is_refused_before_connecting(self):
        with patch.object(portal, '_connect') as connect:
            self.assertFalse(portal.revoke('devices', 'camera', ''))
        connect.assert_not_called()

    def test_denied_unknown_and_ask_never_become_allowed(self):
        for permission in (['no'], ['ask'], ['secret-value'], [], ['yes', 'no']):
            store = FakeStore({'devices': {'camera': {'app': permission}}})
            row = self.status(store)['rows'][0]
            self.assertFalse(self.revoke(store, row))
            self.assertTrue(all(call[0] in ('List', 'Lookup') for call in store.calls))
            self.assertNotIn('secret-value', repr(row))

    def test_stale_and_arbitrary_targets_cannot_mutate(self):
        store = FakeStore({'devices': {'camera': {'app': ['yes']}}})
        row = self.status(store)['rows'][0]
        store.tables['devices']['camera']['app'] = ['no']
        self.assertFalse(self.revoke(store, row))
        for table, resource, app in (('secrets', 'secret', 'app'), ('devices', 'arbitrary', 'app'),
                                     ('devices', 'camera', 'other')):
            self.assertFalse(self.revoke(store, dict(table=table, id=resource, app=app)))
        self.assertTrue(all(call[0] in ('List', 'Lookup') for call in store.calls))

    def test_documents_and_location_summaries_hide_private_data(self):
        store = FakeStore({'documents': {'document-id': {'app': ['read', 'write']}},
                           'location': {'location': {'app': ['EXACT', 'private-timestamp']}}})
        result = self.status(store)
        self.assertNotIn('document-id', repr(result))
        self.assertNotIn('private-timestamp', repr(result))
        self.assertTrue(next(row for row in result['rows'] if row['table'] == 'documents')['can_revoke'])
        self.assertFalse(next(row for row in result['rows'] if row['table'] == 'location')['can_revoke'])

    def test_document_revoke_uses_live_permissions_and_document_portal(self):
        store = FakeStore({'documents': {'private-id': {'app': ['read', 'write']}}})
        row = self.status(store)['rows'][0]
        store.tables['documents']['private-id']['app'] = ['read']
        self.assertTrue(self.revoke(store, row))
        self.assertEqual(store.calls[-1][0:3],
                         ('RevokePermissions', '(ssas)', ('private-id', 'app', ['read'])))
        self.assertFalse(self.status(store)['rows'][0]['can_revoke'])
        self.assertTrue(all(call[0] not in ('Delete', 'SetPermission') for call in store.calls))

    def test_document_failure_and_unknown_schema_do_not_fallback(self):
        for permissions, failures in ((['read'], {('RevokePermissions', 'documents'): portal._Unavailable()}),
                                     (['unknown'], {}), ([], {})):
            store = FakeStore({'documents': {'id': {'app': permissions}}}, failures)
            self.assertFalse(self.revoke(store, self.status(store)['rows'][0]))
            self.assertTrue(all(call[0] not in ('Delete', 'SetPermission') for call in store.calls))

    def test_location_deny_preserves_timestamp(self):
        store = FakeStore({'location': {'location': {'app': ['CITY', '000123']}}})
        row = self.status(store)['rows'][0]
        self.assertNotIn('000123', repr(row))
        self.assertTrue(self.revoke(store, row))
        self.assertEqual(store.calls[-1][0:3], ('SetPermission', '(sbssas)',
                         ('location', False, 'location', 'app', ['NONE', '000123'])))
        self.assertFalse(self.revoke(store, row))

    def test_location_denied_malformed_or_overflow_are_read_only(self):
        for permissions in (['NONE', '0'], ['CITY'], ['CITY', 'bad'], ['CITY', '-1'],
                            ['CITY', '9223372036854775808'], ['CITY', '1', 'extra'],
                            ['CITY', '１'], ['CITY', ''], ['other', '0']):
            store = FakeStore({'location': {'location': {'app': permissions}}})
            row = self.status(store)['rows'][0]
            self.assertFalse(row['can_revoke'])
            self.assertFalse(self.revoke(store, row))
            self.assertTrue(all(call[0] in ('List', 'Lookup') for call in store.calls))

    def test_immediate_reread_prevents_revoke_after_grant_becomes_denied(self):
        store = FakeStore({'location': {'location': {'app': ['CITY', '123']}}})
        row = self.status(store)['rows'][0]
        original = store.call
        lookups = 0

        def changed(method, signature, values, timeout):
            nonlocal lookups
            if method == 'Lookup':
                lookups += 1
                if lookups == 2:
                    store.tables['location']['location']['app'] = ['NONE', '124']
            return original(method, signature, values, timeout)

        store.call = changed
        self.assertFalse(self.revoke(store, row))
        self.assertEqual(store.tables['location']['location']['app'], ['NONE', '124'])

    def test_entry_disappearing_marks_table_partial(self):
        store = FakeStore({'devices': {'camera': {'app': ['yes']}}},
                          {('Lookup', 'devices'): portal._Missing()})
        self.assertEqual(self.status(store)['unavailable'], ['devices'])

    def test_application_disappearing_at_immediate_reread_cannot_mutate(self):
        store = FakeStore({'devices': {'camera': {'app': ['yes']}}})
        row = self.status(store)['rows'][0]
        original = store.call
        lookups = 0

        def disappeared(method, signature, values, timeout):
            nonlocal lookups
            if method == 'Lookup':
                lookups += 1
                if lookups == 2:
                    del store.tables['devices']['camera']['app']
            return original(method, signature, values, timeout)

        store.call = disappeared
        self.assertFalse(self.revoke(store, row))
        self.assertEqual(lookups, 2)
        self.assertEqual(store.tables['devices']['camera'], {})
        self.assertTrue(all(call[0] in ('List', 'Lookup') for call in store.calls))

    def test_bounds_and_timeout(self):
        store = FakeStore({'screencast': {str(n): {'app': ['yes']}
                                        for n in range(portal.MAX_IDS + 1)}})
        result = self.status(store)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['unavailable'], ['screencast'])
        self.assertTrue(all(0 < call[3] <= portal.TIMEOUT_MS for call in store.calls))
        with patch.object(portal.time, 'monotonic', return_value=10):
            result, _ = portal._scan(store, 9)
        self.assertEqual(result['state'], 'unavailable')

    def test_gio_boundary_never_unpacks_attached_data(self):
        class Reply:
            def get_child_value(inner, index):
                self.assertEqual(index, 0)
                return SimpleNamespace(unpack=lambda: {'app': ['yes']})

        calls = []
        store = portal._Store.__new__(portal._Store)
        store.GLib = SimpleNamespace(Variant=lambda signature, values: (signature, values),
                                     Error=RuntimeError)
        store.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
        store.bus = SimpleNamespace(call_sync=lambda *args: calls.append(args) or Reply())
        self.assertEqual(store.call('Lookup', '(ss)', ('screencast', 'private'), 321),
                         {'app': ['yes']})
        self.assertEqual(calls[0][-2], 321)
        self.assertEqual(calls[0][0], 'org.freedesktop.impl.portal.PermissionStore')
        store.revoke_document('private-id', 'app', ['read'], 222)
        self.assertEqual(calls[-1][0:5], ('org.freedesktop.portal.Documents',
                         '/org/freedesktop/portal/documents', 'org.freedesktop.portal.Documents',
                         'RevokePermissions', ('(ssas)', ('private-id', 'app', ['read']))))
        self.assertEqual(calls[-1][-2], 222)

    def test_no_bus_and_mutation_failure_are_safe(self):
        with patch.object(portal, '_connect', side_effect=RuntimeError('private diagnostic')):
            self.assertEqual(portal.status()['state'], 'unavailable')
            self.assertFalse(portal.revoke('devices', 'camera', 'app'))
        store = FakeStore({'devices': {'camera': {'app': ['yes']}}},
                          {('SetPermission', 'devices'): portal._Unavailable()})
        self.assertFalse(self.revoke(store, self.status(store)['rows'][0]))


if __name__ == '__main__':
    unittest.main()
