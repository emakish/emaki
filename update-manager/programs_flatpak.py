# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional Flathub catalog and preview-bound libflatpak transactions.

No remotes are added and no application data is deleted. Unused-runtime
cleanup happens only in an undo, and only for runtimes that install recorded.
New programs use the default system installation through a fixed authenticated
helper. Existing user programs can be removed without root.
"""

try:
    from emaki_i18n import translation
except ModuleNotFoundError:
    import sys as _i18n_sys
    from pathlib import Path as _I18nPath
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parents[1] / 'scripts'))
    _i18n_sys.path.insert(0, str(_I18nPath(__file__).resolve().parent))
    from emaki_i18n import translation

_ = translation('programs').gettext

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil

import programs_appstream

FLATHUB_URLS = frozenset(('https://dl.flathub.org/repo', 'https://flathub.org/repo'))
REF = re.compile(r'app/[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\Z')
LABEL = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z')
TOKEN = re.compile(r'[0-9a-f]{64}\Z')
USER_NOTICE = _('This existing Flatpak program can be removed from your account without administrator authentication.')
SYSTEM_NOTICE = _('Flatpak changes apply to all users and require administrator authentication.')
READ_ONLY_NOTICE = _('This additional Flatpak installation is read-only in this program manager.')


def _library():
    import gi
    gi.require_version('Flatpak', '1.0')
    from gi.repository import Flatpak
    return Flatpak


def _installations(library, warnings):
    installations = []
    # Constructors open the repo and can fail before catalog reads begin. Keep
    # the two managed scopes independent, including when enumeration omits a
    # broken default system repo but returns healthy additional installations.
    for scope, constructor in (('user', library.Installation.new_user),
                               ('system', library.Installation.new_system)):
        try:
            installations.append(constructor(None))
        except Exception:
            warnings.append(_('Flatpak installation could not be read: {scope}').format(scope=scope))
    try:
        installations.extend(installation for installation in library.get_system_installations(None)
                             if _scope(installation) != 'system')
    except Exception:
        warnings.append(_('Additional Flatpak installations could not be read.'))
    return installations


def _scope(installation):
    if installation.get_is_user():
        return 'user'
    identity = installation.get_id()
    return 'system' if identity in (None, 'default') else identity


def _flathub(remote):
    return (not remote.get_disabled() and remote.get_gpg_verify()
            and (remote.get_url() or '').rstrip('/') in FLATHUB_URLS)


def _size(value):
    # A zero-valued property is also libflatpak's unknown-size default.
    return int(value) if value and value > 0 else None


def _metadata(remote, arch, cache, warnings):
    directory = remote.get_appstream_dir(arch)
    base = Path(directory.get_path()) if directory and directory.get_path() else None
    path = next((base / name for name in ('appstream.xml.gz', 'appstream.xml')
                 if base and (base / name).is_file()), None)
    key = (str(base), remote.get_name())
    if key not in cache:
        records = []
        if path is None:
            warnings.append(_('Flathub AppStream metadata is not cached: {remote}').format(remote=remote.get_name()))
        else:
            try:
                records = programs_appstream.read_file(path, origin=remote.get_name())
            except Exception:
                warnings.append(_('Flathub AppStream metadata could not be read: {remote}').format(remote=remote.get_name()))
        by_ref = {}
        by_id = {}
        for record in records:
            by_id.setdefault(record['id'], record)
            for bundle in record.get('bundles', []):
                if bundle.get('kind') == 'flatpak':
                    by_ref.setdefault(bundle['id'], record)
        cache[key] = (by_ref, by_id, str(path) if path else '')
    return cache[key]


def discover(refresh=False):
    """Return catalog rows and incompleteness warnings, without enabling Flatpak.

    Ordinary reads use cached remote summaries and AppStream. Explicit refresh
    updates remote summaries and user AppStream caches. System AppStream remains
    cached; a summary refresh writes only the library's unprivileged cache.
    Transactions are separate authenticated requests.
    """
    if not shutil.which('flatpak'):
        return [], []
    rows, warnings, cache = [], [], {}
    try:
        library = _library()
    except Exception:
        return [], [_('Flatpak installations could not be read.')]
    installations = _installations(library, warnings)
    found = False
    for installation in installations:
        scope = _scope(installation)
        try:
            installation.set_no_interaction(True)
            installed = {ref.format_ref(): ref for ref in installation.list_installed_refs(None)}
            remotes = [remote for remote in installation.list_remotes(None) if _flathub(remote)]
        except Exception:
            warnings.append(_('Flatpak installation could not be read: {scope}').format(scope=scope))
            continue
        for remote in remotes:
            found = True
            origin = remote.get_name()
            try:
                try:
                    if refresh and os.getuid() != 0 and os.geteuid() != 0:
                        if installation.get_is_user():
                            installation.update_appstream_sync(origin, None, None, None)
                        refs = installation.list_remote_refs_sync(origin, None)
                    else:
                        refs = installation.list_remote_refs_sync_full(origin, library.QueryFlags.ONLY_CACHED, None)
                except Exception:
                    warnings.append(_('Flathub catalog could not be read: {source}').format(source=scope + '/' + origin))
                    refs = []
                # A missing summary or an app withdrawn upstream must not hide
                # an existing deployment that the person can still remove.
                advertised = {ref.format_ref() for ref in refs}
                refs = list(refs) + [ref for full, ref in installed.items()
                                     if full not in advertised and ref.get_origin() == origin]
                supported = set(library.get_supported_arches())
                refs = [ref for ref in refs if ref.get_kind() == library.RefKind.APP
                        and (ref.get_arch() in supported or ref.format_ref() in installed)]
                if not refs:
                    warnings.append(_('No Flathub programs are available in the cached catalog: {scope}').format(scope=scope))
                for ref in refs:
                    full = ref.format_ref()
                    local = installed.get(full)
                    ours = local is not None and local.get_origin() == origin
                    # A user remote must not offer a second installation of every
                    # program. Keep only its actual deployments for removal.
                    if scope != 'system' and not ours:
                        continue
                    by_ref, by_id, path = _metadata(remote, ref.get_arch(), cache, warnings)
                    metadata = by_ref.get(full)
                    if metadata is None:
                        candidate = by_id.get(ref.get_name()) or by_id.get(ref.get_name() + '.desktop')
                        # A bundle for another branch is not metadata for this ref.
                        if candidate and not any(b.get('kind') == 'flatpak' for b in candidate.get('bundles', [])):
                            metadata = candidate
                    row = dict(metadata or {})
                    row.update(kind='flatpak', installation=scope, origin=origin,
                               manageable=scope in ('user', 'system'), managementScope=scope,
                               requiresAuthentication=scope == 'system',
                               managementNotice=(USER_NOTICE if scope == 'user' else
                                                 SYSTEM_NOTICE if scope == 'system' else READ_ONLY_NOTICE),
                               remoteUrl=remote.get_url(), ref=full, version='',
                               downloadSize=_size(getattr(ref, 'get_download_size', lambda: 0)()),
                               installedSize=_size(ref.get_installed_size()), installed=ours,
                               installedVersion=(local.get_appdata_version() or local.get_commit()) if ours else '',
                               installedCommit=local.get_commit() if ours else '',
                               commit=ref.get_commit(), appstreamPath=path,
                               metadataAvailable=metadata is not None, aur=False)
                    row.setdefault('id', ref.get_name())
                    row.setdefault('name', '')
                    rows.append(row)
            except Exception:
                warnings.append(_('Flathub catalog could not be read: {source}').format(source=scope + '/' + origin))
    if not found and not warnings:
        warnings.append(_('No enabled, verified Flathub remote is configured.'))
    # One row per ref even when old user and system remotes both exist. Prefer
    # an existing user deployment so it can be removed before a system install;
    # disclose all installed copies on that row instead of duplicating it.
    groups = {}
    for row in rows:
        groups.setdefault(row['ref'], []).append(row)
    unique = []
    for editions in groups.values():
        editions.sort(key=lambda row: (not row['installed'],
                                      row['installation'] != 'user' if row['installed'] else False,
                                      row['installation'] != 'system', row['origin']))
        selected = editions[0]
        selected['installedCopies'] = [dict(installation=row['installation'], origin=row['origin'])
                                       for row in editions if row['installed']]
        unique.append(selected)
    return unique, list(dict.fromkeys(warnings))


def _failure(action, code):
    return dict(ok=False, code=code, action=action, kind='flatpak', operations=[])


def _target(row):
    target = {key: row.get(key) for key in ('installation', 'origin', 'ref')}
    if (not isinstance(target['installation'], str) or not LABEL.fullmatch(target['installation'])
            or not isinstance(target['origin'], str) or not LABEL.fullmatch(target['origin'])
            or not isinstance(target['ref'], str) or len(target['ref']) > 512
            or not REF.fullmatch(target['ref'])):
        raise ValueError('invalid-target')
    return target


def _digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _resolve(library, row, action):
    target = _target(row)
    if action == 'install' and target['ref'].split('/')[2] not in library.get_supported_arches():
        raise ValueError('unsupported-architecture')
    installation = (library.Installation.new_system(None) if target['installation'] == 'system'
                    else library.Installation.new_user(None))
    if _scope(installation) != target['installation']:
        raise ValueError('installation-changed')
    installation.set_no_interaction(True)
    remote = installation.get_remote_by_name(target['origin'], None)
    if not _flathub(remote):
        raise ValueError('remote-refused')
    if row.get('remoteUrl') and row['remoteUrl'].rstrip('/') != remote.get_url().rstrip('/'):
        raise ValueError('remote-changed')
    # Include deployment and remote provenance in the token, independently of
    # display metadata. A different installation at the same ID also changes it.
    target['remoteUrl'] = remote.get_url()
    target['installationPath'] = installation.get_path().get_path()
    installed = {item.format_ref(): item for item in installation.list_installed_refs(None)}
    current = installed.get(target['ref'])
    if current is not None and current.get_origin() != target['origin']:
        raise ValueError('origin-changed')
    if action in ('remove', 'undo') and current is None:
        raise ValueError('not-installed')
    if action == 'install' and current is not None:
        raise ValueError('already-installed')
    removable = {target['ref']}
    if action in ('remove', 'undo'):
        removable.update(item.format_ref() for item in installation.list_installed_related_refs_sync(
            target['origin'], target['ref'], None) if item.should_delete())
    return target, installation, installed, removable


def _operations(transaction, target, installation, installed, removable, action, recorded=frozenset()):
    operations = []
    for operation in transaction.get_operations():
        if operation.get_is_skipped():
            continue
        ref = operation.get_ref()
        kind = operation.get_operation_type().value_nick
        current = installed.get(ref)
        remote = operation.get_remote() or (current.get_origin() if current else '')
        commit = operation.get_commit() or (current.get_commit() if current else '')
        if action in ('remove', 'undo'):
            related = operation.get_related_to_ops() or []
            related_refs = {item.get_ref() for item in related}
            own = ref in removable and (ref == target['ref'] or target['ref'] in related_refs)
            if kind != 'uninstall' or current is None:
                raise ValueError('removal-scope-refused')
            # An undo may also take a runtime this install brought, once nothing uses it.
            if not own and not (action == 'undo' and ref in recorded):
                raise ValueError('removal-scope-refused' if action == 'remove' else 'undo-scope-refused')
        elif kind not in ('install', 'update'):
            raise ValueError('operation-refused')
        if ref == target['ref'] and remote != target['origin']:
            raise ValueError('origin-changed')
        if action == 'install' and ref.startswith('app/') and ref != target['ref']:
            raise ValueError('operation-refused')
        source = installation.get_remote_by_name(remote, None)
        if source.get_disabled() or not source.get_gpg_verify():
            raise ValueError('remote-refused')
        if not commit or not TOKEN.fullmatch(commit):
            raise ValueError('unresolved-commit')
        operations.append(dict(action=kind, ref=ref, origin=remote, remoteUrl=source.get_url(), commit=commit,
                               oldCommit=current.get_commit() if current else '',
                               downloadSize=_size(operation.get_download_size()),
                               installedSize=_size(operation.get_installed_size()),
                               subpaths=sorted(operation.get_subpaths() or [])))
    if not any(item['ref'] == target['ref'] for item in operations):
        raise ValueError('target-missing')
    return operations


def _unused_recorded(installation, removable, recorded):
    """Ask libflatpak what becomes unused after this app and its extensions go."""
    from gi.repository import GLib
    options = GLib.Variant('a{sv}', {'exclude-refs': GLib.Variant('as', sorted(removable))})
    unused = installation.list_unused_refs_with_options(None, None, options, None)
    return {item.format_ref() for item in unused
            if item.format_ref().startswith('runtime/') and item.format_ref() in recorded}


def _transaction(action, row, token=None, emit=None, recorded=()):
    if action not in ('install', 'remove', 'undo'):
        return _failure(action, 'unsupported-action')
    if token is not None and (not isinstance(token, str) or not TOKEN.fullmatch(token)):
        return _failure(action, 'invalid-token')
    result = _failure(action, 'resolution-failed')
    accepted = False
    try:
        scope = _target(row)['installation']
        if scope not in ('system', 'user') or row.get('manageable') is False:
            return _failure(action, 'installation-read-only')
        if scope == 'user':
            if os.getuid() == 0 or os.geteuid() == 0:
                return _failure(action, 'root-not-supported')
            if action != 'remove':
                return _failure(action, 'user-installation-refused')
        elif token is not None and (os.getuid() != 0 or os.geteuid() != 0):
            return _failure(action, 'authentication-required')
        # These refusals apply even when the optional backend is absent.
        if not shutil.which('flatpak'):
            return _failure(action, 'flatpak-unavailable')
        library = _library()
        target, installation, installed, removable = _resolve(library, row, action)
        transaction = library.Transaction.new_for_installation(installation, None)
        transaction.set_no_interaction(True)
        transaction.set_force_uninstall(False)
        transaction.set_include_unused_uninstall_ops(False)
        transaction.set_auto_install_debug(False)
        transaction.set_auto_install_sdk(False)
        transaction.connect('add-new-remote', lambda *args: False)
        transaction.connect('end-of-lifed-with-rebase', lambda *args: False)
        transaction.connect('operation-error', lambda *args: False)
        # Ambiguous dependencies require a separate choice; do not silently pick
        # the first unrelated origin. Prefer the selected app's existing remote.
        transaction.connect('choose-remote-for-ref', lambda tx, app, ref, remotes:
                            remotes.index(target['origin']) if target['origin'] in remotes else -1)
        if action == 'install':
            transaction.add_install(target['origin'], target['ref'], None)
        else:
            transaction.add_uninstall(target['ref'])
            if action == 'undo':
                for ref in sorted(_unused_recorded(installation, removable, recorded)):
                    transaction.add_uninstall(ref)

        def ready(tx):
            nonlocal result, accepted
            try:
                fresh_target, fresh_installation, fresh_installed, fresh_removable = _resolve(library, row, action)
                eligible = (_unused_recorded(fresh_installation, fresh_removable, recorded)
                            if action == 'undo' else frozenset())
                operations = _operations(tx, fresh_target, fresh_installation, fresh_installed, fresh_removable,
                                         action, eligible)
                if action == 'undo' and not eligible.issubset({item['ref'] for item in operations}):
                    raise ValueError('undo-scope-refused')
                body = dict(action=action, kind='flatpak', target=fresh_target, operations=operations,
                            managementScope=scope, requiresAuthentication=scope == 'system',
                            managementNotice=SYSTEM_NOTICE if scope == 'system' else USER_NOTICE)
                if action == 'undo':
                    body.update(recorded=sorted(recorded))
                digest = _digest(body)
                result = dict(body, ok=True, code='ready', token=digest)
                if token is None:
                    return False
                if not hmac.compare_digest(token, digest):
                    result = _failure(action, 'plan-changed')
                    return False
                accepted = True
                return True
            except ValueError as error:
                result = _failure(action, str(error))
                return False
            except Exception:
                result = _failure(action, 'resolution-failed')
                return False

        if token is None:
            # Complete operation resolution is available before authentication.
            # A preview must not ask for credentials or continue to deployment.
            transaction.connect('ready-pre-auth', ready)
            transaction.connect('ready', lambda tx: False)
        else:
            # Revalidate the complete operation list immediately before deployment.
            transaction.connect('ready', ready)
        if emit is not None:
            def new_operation(tx, operation, progress):
                def changed(value):
                    emit('progress', kind='flatpak', ref=operation.get_ref(),
                         percent=value.get_progress(), message=_('Applying Flatpak transaction.'))
                progress.connect('changed', changed)
                changed(progress)
            transaction.connect('new-operation', new_operation)
        try:
            completed = transaction.run(None)
        except Exception:
            if accepted:
                return dict(result, ok=False, code='transaction-failed')
            return result
        if token is None or not accepted:
            return result
        return dict(result, ok=bool(completed), code='completed' if completed else 'transaction-failed')
    except ValueError as error:
        return _failure(action, str(error))
    except Exception:
        return _failure(action, 'flatpak-failed')


def plan(action, row):
    """Resolve every operation and abort before authentication or deployment."""
    return _transaction(action, row)


def execute(action, row, token, emit):
    """Resolve again; only commit the exact operations approved in the preview."""
    if token is None:
        return _failure(action, 'invalid-token')
    return _transaction(action, row, token, emit)


def _undo(row, receipt, token=None, emit=None):
    """Remove recorded runtimes unused after this app, without global cleanup."""
    if receipt.get('kind') != 'flatpak' or receipt.get('target') != row.get('ref') or receipt.get('undone'):
        return _failure('undo', 'receipt-mismatch')
    recorded = [ref for ref in receipt.get('refs', []) if isinstance(ref, str) and ref != row.get('ref')]
    result = _transaction('undo', row, token, emit, recorded)
    if result.get('ok'):
        removed = [operation['ref'] for operation in result['operations']]
        result.update(receipt=receipt['id'], target=row['ref'], remove=removed,
                      keep={ref: 'in-use' for ref in recorded if ref not in removed})
    return result


def undo_plan(row, receipt):
    return _undo(row, receipt)


def undo_execute(row, receipt, token, emit):
    if token is None:
        return _failure('undo', 'invalid-token')
    return _undo(row, receipt, token, emit)
