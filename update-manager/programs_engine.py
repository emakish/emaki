# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned program catalog and transaction protocol, without a presentation layer.

Each request is a JSON object; each response and progress event carries its id.
Catalog keys identify a source edition, never a display name. Plans are data,
not commands, and execution always resolves the key and plans again.
"""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from urllib.parse import quote, urlencode

import programs_appstream as appstream
import programs_arch as arch
import programs_catalog as removal
import programs_flatpak as flatpak

PROTOCOL = 1
MAX_REQUEST = 65536
BUILD_NOTICE = '[PLACEHOLDER] Build in a terminal with your preferred AUR helper.'
MESSAGES = {
    'invalid-request': '[PLACEHOLDER] The program manager request is invalid.',
    'not-found': '[PLACEHOLDER] This program is no longer in the catalog. Refresh the catalog.',
    'changed': '[PLACEHOLDER] The transaction plan changed. Review it again.',
    'terminal-only': BUILD_NOTICE,
    'not-authorized': '[PLACEHOLDER] The transaction was not authorized.',
    'failed': '[PLACEHOLDER] The program manager operation could not finish.',
    'unknown': '[PLACEHOLDER] The transaction connection ended. Check the installed state before trying again.',
    'not-installed': '[PLACEHOLDER] This program is not installed.',
}


def failure(code, **fields):
    return dict(ok=False, code=code, message=MESSAGES[code], **fields)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def update_backend():
    """Load the existing backend under both its source and installed filenames."""
    directory = Path(__file__).resolve().parent
    path = directory / 'update-manager-backend'
    if not path.exists():
        path = directory / 'backend.py'
    loader = importlib.machinery.SourceFileLoader('programs_updates', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def arch_rows(metadata, available, installed):
    """Join only real AppStream programs to enabled repository packages."""
    by_package = {}
    for target, package in available.items():
        by_package.setdefault(package['package'], []).append((target, package))
    rows = {}
    for component in metadata:
        for name in component.get('packages', []):
            for target, package in by_package.get(name, []):
                key = 'arch:' + target + ':' + component['id']
                row = dict(component, key=key, kind='arch', target=target,
                           repository=package['repository'], package=name,
                           version=package['version'], installed=name in installed,
                           installedVersion=installed.get(name, ''),
                           downloadSize=package.get('downloadSize'),
                           installedSize=package.get('installedSize'))
                row['name'] = row.get('name') or name
                row['summary'] = row.get('summary') or package.get('summary', '')
                row['licence'] = row.get('licence') or package.get('licence', '')
                if key not in rows:
                    rows[key] = row
                    continue
                previous = rows[key]
                # Installed metainfo and repository catalogs can supply different
                # assets for one edition. Keep its icons even when the record
                # with more screenshots is the other one.
                icons = list(previous.get('icons', []))
                for icon in row.get('icons', []):
                    if icon not in icons:
                        icons.append(icon)
                if len(row.get('screenshots', [])) > len(previous.get('screenshots', [])):
                    rows[key] = row
                rows[key]['icons'] = icons
    return list(rows.values())


def aur_records(payload, installed):
    """AUR RPC data is information only; recipes and helpers are never executed."""
    if (not isinstance(payload, dict) or payload.get('version') != 5
            or payload.get('type') not in ('search', 'multiinfo')
            or not isinstance(payload.get('results'), list)):
        raise ValueError('Invalid AUR response')
    rows = []
    for value in payload['results']:
        if not isinstance(value, dict):
            raise ValueError('Invalid AUR entry')
        name = removal.valid_name(value.get('Name'))
        version = value.get('Version')
        if not isinstance(version, str) or len(version) > 512:
            raise ValueError('Invalid AUR version')
        summary = value.get('Description') or ''
        licence = value.get('License') or []
        if not isinstance(summary, str) or not isinstance(licence, list) or not all(isinstance(x, str) for x in licence):
            raise ValueError('Invalid AUR metadata')
        rows.append(dict(key='aur:' + name, id=name, kind='aur', package=name,
                         name=name, summary=summary, description='', icons=[], categories=[],
                         screenshots=[], licence=', '.join(licence), keywords=[],
                         version=version, installed=name in installed,
                         installedVersion=installed.get(name, ''), downloadSize=None,
                         installedSize=None, notice=BUILD_NOTICE, actions=['terminal-build']))
    return removal.clean_display(rows)


def select(rows, query='', source='', category='', installed=None, offset=0, limit=100):
    if (not isinstance(query, str) or len(query) > 256 or source not in ('', 'arch', 'flatpak', 'aur')
            or not isinstance(category, str) or len(category) > 128
            or (installed is not None and not isinstance(installed, bool))
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError('Invalid filters')
    terms = query.casefold().split()
    found = []
    for row in rows:
        if source and row['kind'] != source:
            continue
        if category and category not in row.get('categories', []):
            continue
        if installed is not None and row.get('installed', False) != installed:
            continue
        text = ' '.join([row.get(field, '') for field in ('name', 'id', 'summary', 'description', 'package')]
                        + row.get('keywords', []) + row.get('categories', [])).casefold()
        if all(term in text for term in terms):
            found.append(row)
    found.sort(key=lambda row: (row.get('name', '').casefold(), row['key']))
    return dict(programs=found[offset:offset + limit], total=len(found), offset=offset, limit=limit,
                categories=sorted({category for row in rows for category in row.get('categories', [])}))


class Engine:
    def __init__(self, legacy, cache):
        self.legacy = legacy
        self.cache = Path(cache)
        self.rows = None
        self.installed = {}
        self.warnings = []
        self.aur = {}

    def load(self, refresh=False):
        if self.rows is not None and not refresh:
            return
        self.warnings = []
        try:
            available, self.installed = arch.inventory()
        except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.SubprocessError):
            available, self.installed = {}, {}
            self.warnings.append('[PLACEHOLDER] Repository package metadata could not be read. The catalog is incomplete.')
        metadata, warnings = appstream.components()
        self.warnings.extend(warnings)
        paths = sorted({row['sourcePath'] for row in metadata
                        if not row.get('packages') and row.get('sourcePath')})
        for offset in range(0, len(paths), 100):
            owners = removal.owners(self.legacy.pacman('-Qo', *paths[offset:offset + 100]).stdout)
            for row in metadata:
                if not row.get('packages') and row.get('sourcePath') in owners:
                    row['packages'] = [owners[row['sourcePath']][0]]
        rows = arch_rows(metadata, available, self.installed)
        if not any(row.get('origin') for row in metadata if row.get('packages')):
            self.warnings.append('[PLACEHOLDER] Repository AppStream data is missing. Install the platform application metadata package and refresh.')
        optional, warnings = flatpak.discover(refresh=refresh)
        self.warnings.extend(warnings)
        for row in optional:
            row.update(key='flatpak:' + ':'.join(row[field] for field in ('installation', 'origin', 'ref')),
                       kind='flatpak')
            for field in ('summary', 'description', 'licence'):
                row.setdefault(field, '')
            for field in ('icons', 'categories', 'screenshots', 'keywords'):
                row.setdefault(field, [])
            row['name'] = row.get('name') or row['id']
        rows += optional
        for target, package in available.items():
            name = package['package']
            rows.append(dict(package, key='package:' + target, id=name, kind='arch',
                             target=target, name=name, packageOnly=True,
                             programKey='',
                             installed=name in self.installed,
                             installedVersion=self.installed.get(name, ''),
                             description='', icons=[], categories=[], screenshots=[], keywords=[]))
        # Do not rewrite executable identities into a different program. Reject
        # unsafe keys before cleaning display fields and indexing the catalog.
        safe_rows = []
        for row in rows:
            identities = [row.get(field, '') for field in
                          ('key', 'id', 'target', 'package', 'installation', 'origin', 'ref', 'programKey')]
            if any(value != removal.clean_text(value) for value in identities):
                self.warnings.append('[PLACEHOLDER] Program metadata contains an invalid identifier.')
                continue
            row = removal.clean_display(row)
            if row['kind'] == 'flatpak' and (row.get('manageable') is False
                    or row['installation'] not in ('system', 'user')
                    or (row['installation'] == 'user' and not row.get('installed'))):
                row['actions'] = []
            else:
                row['actions'] = ['install'] if not row.get('installed') else ['remove']
            safe_rows.append(row)
        program_keys = {}
        for row in sorted(safe_rows, key=lambda item: item['key']):
            if row['kind'] == 'arch' and not row.get('packageOnly'):
                program_keys.setdefault(row['target'], row['key'])
        for row in safe_rows:
            if row.get('packageOnly'):
                row['programKey'] = program_keys.get(row['target'], '')
        self.rows = {row['key']: row for row in safe_rows}
        self.warnings = removal.clean_display(self.warnings)
        for row in self.aur.values():
            row.update(installed=row['package'] in self.installed,
                       installedVersion=removal.clean_text(self.installed.get(row['package'], '')))

    def search_aur(self, query):
        if len(query.strip()) < 2:
            return
        backend = update_backend()
        url = 'https://aur.archlinux.org/rpc/v5/search/' + quote(query, safe='') + '?by=name-desc'
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            def validate(raw):
                return aur_records(json.loads(raw), self.installed)
            rows, stale = backend.fetch(url, self.cache, validate)
            self.aur.update({row['key']: row for row in rows})
            if stale:
                self.warnings.append('[PLACEHOLDER] AUR search results come from an older check.')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.warnings.append('[PLACEHOLDER] AUR search could not finish. Narrow the search or try again later.')

    def catalog(self, **options):
        refresh = options.pop('refresh', False)
        include_aur = options.pop('includeAur', False)
        scope = options.pop('scope', 'programs')
        if not isinstance(refresh, bool) or not isinstance(include_aur, bool):
            raise ValueError('Invalid discovery option')
        if scope not in ('programs', 'packages'):
            raise ValueError('Invalid catalog scope')
        if scope == 'packages' and (include_aur or options.get('source', '') not in ('', 'arch')
                                    or options.get('category', '')):
            raise ValueError('Invalid package filters')
        select([], **options)
        self.load(refresh)
        if scope == 'programs' and (include_aur or options.get('source') == 'aur'):
            self.search_aur(options.get('query', ''))
        rows = [row for row in self.rows.values() if bool(row.get('packageOnly')) == (scope == 'packages')]
        if scope == 'programs':
            rows += list(self.aur.values())
        result = select(rows, **options)
        return dict(ok=True, **result, warnings=list(dict.fromkeys(self.warnings)), partial=bool(self.warnings))

    def details(self, key):
        if not isinstance(key, str) or not 0 < len(key) <= 2048:
            raise ValueError('Invalid catalog key')
        self.load()
        if key.startswith('aur:') and key not in self.aur:
            # Exact lookup also permits a search result to survive a CLI restart.
            name = removal.valid_name(key[4:])
            backend = update_backend()
            self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            rows, _ = backend.fetch('https://aur.archlinux.org/rpc/v5/info?' + urlencode({'arg[]': name}),
                                    self.cache, lambda raw: aur_records(json.loads(raw), self.installed))
            self.aur.update({row['key']: row for row in rows if row['package'] == name})
        row = self.rows.get(key) or self.aur.get(key)
        return dict(ok=True, program=row, warnings=self.warnings) if row else failure('not-found')

    def plan(self, action, key=''):
        if action == 'update':
            if key:
                raise ValueError('Updates always include the full system')
            backend = update_backend()
            backend.emit = lambda *_args, **_fields: None
            self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = backend.check(self.cache, False)
            if data.get('error'):
                return failure('failed', details=data)
            plan = dict(ok=True, action='update', kind='updates', fullUpgrade=True,
                        updates=data.get('updates', []), warnings=data.get('warnings', []), news=data.get('news', []))
            plan['token'] = digest(plan)
            return plan
        if action not in ('install', 'remove'):
            raise ValueError('Invalid transaction')
        answer = self.details(key)
        if not answer['ok']:
            return answer
        row = answer['program']
        if row['kind'] == 'aur':
            return failure('terminal-only')
        if action == 'remove' and not row.get('installed'):
            return failure('not-installed')
        if row['kind'] == 'arch':
            native = (arch.install_plan(row['target']) if action == 'install'
                      else self.legacy.preview(row['package']))
        else:
            native = flatpak.plan(action, row)
        if not native.get('ok'):
            return native
        plan = dict(ok=True, action=action, key=key, kind=row['kind'], transaction=native)
        plan['token'] = digest(plan)
        return plan

    def execute(self, action, key, token, emit):
        try:
            return self._execute(action, key, token, emit)
        finally:
            # A failed transaction can still have changed packages. Subsequent
            # catalog/details requests must observe the actual installed state.
            self.rows = None

    def remove_native(self, package, token):
        # AppStream and Flatpak can create GLib threads. U3 deliberately forks
        # to detach authentication; give it a fresh interpreter before that fork.
        result = subprocess.run([sys.executable, '-IB', str(Path(self.legacy.__file__).resolve()),
                                 'remove', package, token, '--json'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                start_new_session=True)
        try:
            answer = json.loads(result.stdout)
            if not isinstance(answer, dict) or type(answer.get('ok')) is not bool:
                raise ValueError('Invalid removal result')
            if result.returncode:
                answer['ok'] = False
            return answer
        except ValueError:
            return failure('unknown')

    def _execute(self, action, key, token, emit):
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
            raise ValueError('Invalid plan token')
        # State is reloaded locally, without refreshing remotes or system databases.
        self.rows = None
        plan = self.plan(action, key)
        if not plan.get('ok'):
            return plan
        if token != plan['token']:
            return failure('changed', plan=plan)
        if action == 'update':
            backend = update_backend()
            finished = []
            def relay(kind, **fields):
                if kind == 'finished':
                    finished.append(fields)
                else:
                    emit(kind, **fields)
            backend.emit = relay
            backend.apply(self.cache)
            return finished[-1] if finished else failure('unknown')
        row = self.rows[key]
        native_token = plan['transaction']['token']
        if row['kind'] == 'flatpak' and row['installation'] == 'user':
            return flatpak.execute(action, row, native_token, emit)
        if row['kind'] == 'arch' and action == 'remove':
            return self.remove_native(row['package'], native_token)
        if row['kind'] == 'flatpak':
            helper = Path(__file__).resolve().with_name('emaki-programs-flatpak')
            arguments = [action, row['origin'], row['ref'], native_token]
        else:
            helper = Path(__file__).resolve().with_name('emaki-programs-install')
            arguments = [row['target'], native_token]
        finished = None
        with subprocess.Popen(['pkexec', str(helper), *arguments],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, start_new_session=True) as process:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                    if event.get('type') == 'finished':
                        finished = event
                    elif event.get('type') == 'progress':
                        emit('progress', **{k: v for k, v in event.items() if k != 'type'})
                except (ValueError, AttributeError):
                    continue
            code = process.wait()
        if finished is None:
            return failure('not-authorized' if code in (126, 127) else 'unknown')
        finished.pop('type', None)
        if code:
            finished['ok'] = False
        return finished

    def request(self, request, emit):
        if not isinstance(request, dict) or request.get('version') != PROTOCOL:
            raise ValueError('Invalid protocol version')
        operation = request.get('method')
        params = request.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('Invalid parameters')
        if operation in ('catalog', 'search'):
            return self.catalog(**params)
        if operation == 'details':
            return self.details(**params)
        if operation == 'plan':
            return self.plan(**params)
        if operation == 'execute':
            return self.execute(emit=emit, **params)
        if operation == 'updates':
            backend = update_backend()
            self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            if params:
                raise ValueError('Invalid update status parameters')
            return dict(ok=True, **backend.status(self.cache))
        raise ValueError('Unknown method')


def serve(engine, source, output):
    def write(value):
        output.write(json.dumps(removal.clean_display(value), ensure_ascii=True) + '\n')
        output.flush()
    status = 0
    while raw := source.readline(MAX_REQUEST + 1):
        identity = None
        try:
            if len(raw.encode('utf-8')) > MAX_REQUEST:
                # Drain the entire oversized request, never execute its suffix.
                while raw and not raw.endswith('\n'):
                    raw = source.readline(MAX_REQUEST + 1)
                raise ValueError('Request too large')
            request = json.loads(raw)
            if isinstance(request, dict):
                identity = request.get('id')
                if not (identity is None or type(identity) is int or isinstance(identity, str) and len(identity) <= 128):
                    identity = None
                    raise ValueError('Invalid request id')
                if isinstance(identity, str) and identity != removal.clean_text(identity):
                    identity = None
                    raise ValueError('Invalid request id')
            def emit(event_type, **fields):
                write(dict(version=PROTOCOL, id=identity, type=event_type, **fields))
            answer = engine.request(request, emit)
        except (ValueError, TypeError, KeyError):
            answer = failure('invalid-request')
        except (OSError, RuntimeError, ImportError, tarfile.TarError, subprocess.SubprocessError):
            answer = failure('failed')
        write(dict(version=PROTOCOL, id=identity, type='result', **answer))
        status = 0 if answer.get('ok') else 1
    return status


def main(argv, legacy):
    if os.geteuid() == 0:
        print(json.dumps(dict(version=PROTOCOL, id=None, type='result', **failure('not-authorized'))))
        return 2
    parser = argparse.ArgumentParser(description='[PLACEHOLDER] Program manager JSON protocol')
    parser.add_argument('method', choices=('catalog', 'search', 'details', 'plan', 'execute', 'updates', 'serve'))
    parser.add_argument('arguments', nargs='*')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--source', choices=('arch', 'flatpak', 'aur'), default='')
    parser.add_argument('--scope', choices=('programs', 'packages'), default='programs')
    parser.add_argument('--category', default='')
    parser.add_argument('--installed', action='store_true', default=None)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--include-aur', action='store_true')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--offset', type=int, default=0)
    args = parser.parse_args(argv)
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'emaki-update-manager'
    engine = Engine(legacy, cache)
    if args.method == 'serve':
        if args.arguments:
            parser.error('[PLACEHOLDER] The JSON service takes requests on standard input.')
        serve(engine, sys.stdin, sys.stdout)
        return 0
    params = {}
    expected = {'catalog': 0, 'search': 1, 'details': 1, 'plan': (1, 2), 'execute': (2, 3), 'updates': 0}
    counts = expected[args.method]
    if len(args.arguments) not in (counts if isinstance(counts, tuple) else (counts,)):
        parser.error('[PLACEHOLDER] Invalid program manager arguments.')
    if args.method in ('catalog', 'search'):
        params = dict(query=args.arguments[0] if args.arguments else '', source=args.source,
                      category=args.category, installed=args.installed, refresh=args.refresh,
                      includeAur=args.include_aur, scope=args.scope, limit=args.limit, offset=args.offset)
    elif args.method == 'details':
        params = dict(key=args.arguments[0])
    elif args.method in ('plan', 'execute'):
        action = args.arguments[0]
        rest = args.arguments[1:]
        token = rest.pop() if args.method == 'execute' else None
        params = dict(action=action, key=rest[0] if rest else '')
        if token is not None:
            params['token'] = token
    request = dict(version=PROTOCOL, id=None, method=args.method, params=params)
    from io import StringIO
    # One-shot mode and the stream use exactly the same request validation.
    return serve(engine, StringIO(json.dumps(request) + '\n'), sys.stdout)
