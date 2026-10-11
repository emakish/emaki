# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded permission-store inspection and explicit privilege-reducing actions.

Only stored decisions are visible, not running sessions or every app that could
ask a portal. Opaque restore data and document paths never leave this module.
"""
import hashlib
import time

TABLES = ('screencast', 'screenshot', 'devices', 'notifications', 'location',
          'inhibit', 'background', 'documents')
MAX_IDS = 64
MAX_ROWS = 256
TIME_BUDGET = 5.0
TIMEOUT_MS = 600


class _Missing(Exception):
    pass


class _Unavailable(Exception):
    pass


class _Store:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(self, method, signature, values, timeout):
        try:
            reply = self.bus.call_sync(
                'org.freedesktop.impl.portal.PermissionStore',
                '/org/freedesktop/impl/portal/PermissionStore',
                'org.freedesktop.impl.portal.PermissionStore', method,
                self.GLib.Variant(signature, values), None,
                self.Gio.DBusCallFlags.NONE, timeout, None)
            # Lookup's second child is private backend data. Never unpack it.
            return reply.get_child_value(0).unpack() if method in ('List', 'Lookup') else None
        except self.GLib.Error as error:
            name = self.Gio.DBusError.get_remote_error(error)
            if name == 'org.freedesktop.portal.Error.NotFound':
                raise _Missing() from None
            raise _Unavailable() from None


    def revoke_document(self, resource, app, permissions, timeout):
        """Let the document portal update its own permission and FUSE state."""
        try:
            self.bus.call_sync(
                'org.freedesktop.portal.Documents', '/org/freedesktop/portal/documents',
                'org.freedesktop.portal.Documents', 'RevokePermissions',
                self.GLib.Variant('(ssas)', (resource, app, permissions)), None,
                self.Gio.DBusCallFlags.NONE, timeout, None)
        except self.GLib.Error:
            raise _Unavailable() from None


def _connect():
    return _Store()


def _text(value):
    return isinstance(value, str) and len(value) <= 512 and all(
        char.isprintable() for char in value)


def _handle(table, resource):
    if table in ('screencast', 'documents'):
        return hashlib.sha256((table + '\0' + resource).encode()).hexdigest()
    return resource


def _describe(table, resource, permissions, single_owner):
    if not isinstance(permissions, (list, tuple)) or len(permissions) > 16:
        return ['unknown'], False
    if table == 'screencast':
        return ['remembered'], single_owner and permissions == ['yes']
    tri_ids = {'screenshot': ('screenshot',), 'devices': ('camera', 'microphone', 'speakers'),
               'notifications': ('notification',), 'background': ('background',)}
    if table in tri_ids:
        known = len(permissions) == 1 and permissions[0] in ('yes', 'no', 'ask')
        # The notification portal allows ask without a prompt, and Emaki cannot grant again
        # after a denial, so its rows stay read-only.
        return (list(permissions) if known else ['unknown'],
                known and permissions == ['yes'] and resource in tri_ids[table]
                and table != 'notifications')
    if table == 'inhibit':
        known = all(value in ('logout', 'switch', 'suspend', 'idle') for value in permissions)
        return (list(permissions) if known and permissions else ['none'] if known else ['unknown'],
                known and bool(permissions) and resource == 'inhibit')
    if table == 'documents':
        known = all(value in ('read', 'write', 'delete', 'grant-permissions') for value in permissions)
        return (list(permissions) if known and permissions else ['none'] if known else ['unknown'],
                known and bool(permissions))
    if table == 'location' and permissions and permissions[0] in (
            'NONE', 'COUNTRY', 'CITY', 'NEIGHBORHOOD', 'STREET', 'EXACT'):
        timestamp = permissions[1] if len(permissions) == 2 else None
        valid_timestamp = (isinstance(timestamp, str) and 1 <= len(timestamp) <= 19
                           and timestamp.isascii() and timestamp.isdecimal()
                           and int(timestamp) <= 9223372036854775807)
        return [permissions[0]], (resource == 'location' and valid_timestamp
                                  and permissions[0] != 'NONE')
    return ['unknown'], False


def _scan(store, deadline):
    rows, unavailable, targets = [], [], {}
    ids_seen = 0

    def call(method, signature, values):
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise _Unavailable()
        return store.call(method, signature, values, min(TIMEOUT_MS, remaining))

    for table in TABLES:
        table_rows, table_targets = [], {}
        listed = False
        try:
            resources = call('List', '(s)', (table,))
            listed = True
            if not isinstance(resources, (list, tuple)) or len(resources) > MAX_IDS - ids_seen:
                raise _Unavailable()
            ids_seen += len(resources)
            for resource in resources:
                if not _text(resource) or not resource:
                    raise _Unavailable()
                grants = call('Lookup', '(ss)', (table, resource))
                if not isinstance(grants, dict) or len(grants) > MAX_ROWS - len(rows) - len(table_rows):
                    raise _Unavailable()
                for app, permissions in grants.items():
                    if not _text(app):
                        raise _Unavailable()
                    summary, actionable = _describe(table, resource, permissions, len(grants) == 1)
                    # An empty ID is shared by unidentified applications.
                    actionable = actionable and bool(app)
                    handle = _handle(table, resource)
                    row = dict(table=table, id=handle, app=app,
                               permissions=summary, can_revoke=actionable)
                    table_rows.append(row)
                    if actionable:
                        table_targets[(table, handle, app)] = resource
        except _Missing:
            # A missing table is normal. An entry disappearing mid-scan is not
            # sufficient evidence that the rest of the table is empty.
            if listed:
                unavailable.append(table)
            continue
        except Exception:
            unavailable.append(table)
            continue
        rows.extend(table_rows)
        targets.update(table_targets)
    state = 'ready' if not unavailable else 'unavailable' if len(unavailable) == len(TABLES) else 'partial'
    return dict(state=state, rows=rows, unavailable=unavailable), targets


def status():
    """Read stored permission summaries without modifying the session."""
    try:
        return _scan(_connect(), time.monotonic() + TIME_BUDGET)[0]
    except Exception:
        return dict(state='unavailable', rows=[], unavailable=list(TABLES))


def revoke(table, resource, app):
    """Remove a remembered grant or reduce an existing permission.

    Documents use the document portal to invalidate its own access state.
    Screenshot, device and background decisions return to ask; notification
    decisions are read-only; location writes NONE while preserving its
    validated timestamp. Denied, unknown and unidentified application entries
    cannot be changed. No operation stops active captures or alters transient
    sessions.
    """
    if table not in TABLES or not _text(resource) or not _text(app) or not app:
        return False
    try:
        store = _connect()
        deadline = time.monotonic() + TIME_BUDGET
        _, targets = _scan(store, deadline)
        actual = targets.get((table, resource, app))
        remaining = int((deadline - time.monotonic()) * 1000)
        if actual is None or remaining <= 0:
            return False
        # Recheck immediately before changing a grant; the page may be stale.
        grants = store.call('Lookup', '(ss)', (table, actual), min(TIMEOUT_MS, remaining))
        if not isinstance(grants, dict) or app not in grants:
            return False
        current = grants[app]
        if not _describe(table, actual, current, len(grants) == 1)[1]:
            return False
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            return False
        if table == 'documents':
            store.revoke_document(actual, app, list(current), min(TIMEOUT_MS, remaining))
        elif table == 'screencast':
            store.call('Delete', '(ss)', (table, actual), min(TIMEOUT_MS, remaining))
        else:
            permissions = (['NONE', current[1]] if table == 'location' else
                           [] if table == 'inhibit' else ['ask'])
            store.call('SetPermission', '(sbssas)',
                       (table, False, actual, app, permissions), min(TIMEOUT_MS, remaining))
        return True
    except Exception:
        return False
