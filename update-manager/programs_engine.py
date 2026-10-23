# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned program catalog and transaction protocol, without a presentation layer.

Each request is a JSON object; each response and progress event carries its id.
Catalog keys identify a source edition, never a display name. Plans are data,
not commands, and execution always resolves the key and plans again.
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

import argparse
from copy import deepcopy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import syslog
import tarfile
import zlib

import programs_appstream as appstream
import programs_arch as arch
import programs_aur as aur
import programs_catalog as removal
import programs_flatpak as flatpak
import programs_receipts as receipts
import programs_reviews as reviews
import programs_kits as kits
import programs_webapps as webapps

try:
    from emaki_redaction import Redactor
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'installer'))
    from emaki_installer.redaction import Redactor

PROTOCOL = 1
MAX_REQUEST = 65536
# Browse's picks (the featured row, the order inside category rows, short names) are data,
# not code: update-manager/storefront.json, installed next to the window's files.
STOREFRONT = 'storefront.json'
STOREFRONT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\Z')
STOREFRONT_CATEGORY = re.compile(r'[A-Za-z]{1,64}\Z')
# Browse draws the featured row as three cards.
MAX_FEATURED = 3
MAX_FIRST = 8
# Installed AUR programs looked up in one RPC request, as many as the popular list asks for.
MAX_INSTALLED_AUR = 50
# Browse's "From Emaki" shelf: Emaki's own optional packages, data in update-manager/emaki-shelf.json.
# An item with "when" shows only where its condition holds; an unknown condition hides it.
SHELF = 'emaki-shelf.json'
SHELF_WHEN = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
MAX_SHELF = 8
SYSFS = Path('/sys')
NVIDIA = 0x10de
MESSAGES = {
    'invalid-request': _('The program manager request is invalid.'),
    'not-found': _('This program is no longer in the catalog. Refresh the catalog.'),
    'changed': _('The transaction plan changed. Review it again.'),
    'aur-closed': _('The AUR section is off. Open it and read the warning first.'),
    'missing-dependency': _('This program needs a package that is neither in Arch nor in the AUR.'),
    'metadata-unavailable': str(arch.MetadataUnavailable()),
    'dependency-cycle': _('The AUR packages of this program depend on each other in a circle.'),
    'recipe-unavailable': _('The AUR did not answer for this program. Try again later.'),
    'too-large': _('This program pulls in too many packages to build here.'),
    'up-to-date': _('This program is already installed in its newest version.'),
    'nothing-to-undo': _('There is no recorded install of this program to take back.'),
    'cannot-undo': _('This install can no longer be taken back as it was.'),
    'not-authorized': _('The transaction was not authorized.'),
    'failed': _('The program manager operation could not finish.'),
    'unknown': _('The transaction connection ended. Check the installed state before trying again.'),
    'not-installed': _('This program is not installed.'),
}


def percentile_ranks(values):
    """Native scores of one source → 0–100 by rank in that source (100 = its most popular; ties share)."""
    ordered = sorted(set(values.values()), reverse=True)
    if len(ordered) < 2:
        return {key: 100.0 for key in values}
    place = {score: index for index, score in enumerate(ordered)}
    return {key: round(100.0 * (1 - place[score] / (len(ordered) - 1)), 2) for key, score in values.items()}


def arch_popularity_packages(rows, available):
    """Rank desktop programs, excluding build frontends and other programs' dependencies."""
    desktops = {row['package'] for row in rows if row['kind'] == 'arch'
                and not row.get('packageOnly') and row.get('componentType') == 'desktop-application'}
    candidates = {row['package'] for row in rows if row['kind'] == 'arch'
                  and not row.get('packageOnly') and row.get('componentType') == 'desktop-application'
                  and 'Building' not in row.get('categories', [])}
    packages = {}
    providers = {}
    for package in available.values():
        name = package['package']
        packages.setdefault(name, set()).update(package.get('requiredPackages', []))
        for provided in package.get('providedPackages', []):
            providers.setdefault(provided, set()).add(name)
    graph = {}
    for name, dependencies in packages.items():
        graph[name] = set()
        for dependency in dependencies:
            if dependency in packages:
                graph[name].add(dependency)
            elif len(providers.get(dependency, ())) == 1:
                # A virtual dependency with several providers does not identify
                # which package contributes to the install count.
                graph[name].update(providers[dependency])
    required = set()
    for program in desktops:
        seen = {program}
        pending = list(graph.get(program, ()))
        while pending:
            dependency = pending.pop()
            if dependency in seen:
                continue
            seen.add(dependency)
            required.add(dependency)
            pending.extend(graph.get(dependency, ()))
    return candidates - required


def failure(code, **fields):
    return dict(ok=False, code=code, message=MESSAGES[code], **fields)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def report_event(action, event, fields, redactor=None):
    """Keep bounded transaction diagnostics in the local journal, never plans."""
    try:
        action = action if action in ('install', 'remove', 'update') else 'invalid'
        identifier = 'emaki-update-manager' if action == 'update' else 'emaki-programs-engine'
        # Plans contain authorization tokens. Only diagnostic fields are copied;
        # nested plans, request arguments and transaction objects stay private.
        diagnostic = {name: value for name, value in fields.items()
                      if name in ('ok', 'code', 'message', 'error', 'stage', 'percent')
                      and isinstance(value, (str, int, float, bool))}
        details = fields.get('details')
        if isinstance(details, dict):
            diagnostic['details'] = {name: value for name, value in details.items()
                                     if name in ('code', 'message', 'error')
                                     and isinstance(value, str)}
        lines = [f'action={action} event={event}']
        for name, value in diagnostic.items():
            if isinstance(value, dict):
                lines.extend(f'{name}.{key}: {item}' for key, item in value.items())
            else:
                lines.append(f'{name}: {value}')
        # Remove credentials before truncation can retain a fragment of a secret.
        # Preserve line boundaries for later report redaction of other private data.
        message = (redactor or Redactor()).text('\n'.join(lines), keep_newlines=True)
        message = message.encode('utf-8', errors='replace')[:4096].decode('utf-8', errors='ignore')
        syslog.openlog(identifier, syslog.LOG_PID, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_INFO, message)
    except Exception:
        # Missing or broken local logging must never alter a transaction result.
        pass


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
                # Both records name the edition's other IDs; ratings read every one of them.
                provided = list(previous.get('providedIds', []))
                provided += [value for value in row.get('providedIds', []) if value not in provided]
                if len(row.get('screenshots', [])) > len(previous.get('screenshots', [])):
                    rows[key] = row
                rows[key]['icons'] = icons
                if provided:
                    rows[key]['providedIds'] = provided
                release_date = max(previous.get('releaseDate', ''), row.get('releaseDate', ''))
                if release_date:
                    rows[key]['releaseDate'] = release_date
    return list(rows.values())


def storefront_paths(name=STOREFRONT):
    """The source tree's copy first (development and tests), then the installed one."""
    paths = [Path(__file__).resolve().with_name(name)]
    try:
        from emaki_paths import PREFIX
    except ModuleNotFoundError:
        return paths
    return paths + [Path(PREFIX) / 'share' / 'emaki-update-manager' / name]


def storefront(paths=None):
    """Browse's picks; a missing or invalid file gives an empty storefront, never a guess."""
    for path in storefront_paths() if paths is None else paths:
        try:
            return storefront_value(json.loads(Path(path).read_text()))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            break
    return dict(featured=dict(title='', note='', programs=[]), rows=[], names={})


def catalog_text(value):
    """Translate bundled editorial text after validating its source value."""
    messages = {
        'Picked by Emaki': _('Picked by Emaki'),
        'A good place to start': _('A good place to start'),
        'Session sudo key': _('Session sudo key'),
        'Snapshot guard': _('Snapshot guard'),
        'NVIDIA support': _('NVIDIA support'),
        'Allows password-free administrator commands for a chosen session. Read the warning before granting access.': _('Allows password-free administrator commands for a chosen session. Read the warning before granting access.'),
        'Restore-point protection for system changes. Read its setup instructions before relying on it.': _('Restore-point protection for system changes. Read its setup instructions before relying on it.'),
        "Installs the supported NVIDIA driver, graphics libraries and Emaki's session settings.": _("Installs the supported NVIDIA driver, graphics libraries and Emaki's session settings."),
    }
    return messages.get(value, value)


def storefront_value(value):
    def text(item):
        if not isinstance(item, str) or not 0 < len(item) <= 64 or item != removal.clean_text(item):
            raise ValueError(_('Invalid storefront text'))
        return item

    def identities(items, limit):
        # Lower case without .desktop, as the window compares program identities.
        if (not isinstance(items, list) or len(items) > limit
                or not all(isinstance(item, str) and STOREFRONT_ID.fullmatch(item) for item in items)):
            raise ValueError(_('Invalid storefront identities'))
        return list(dict.fromkeys(item.removesuffix('.desktop').lower() for item in items))

    if not isinstance(value, dict) or value.get('version') != 1 or set(value) != {'version', 'featured', 'rows', 'names'}:
        raise ValueError(_('Invalid storefront'))
    featured, rows, names = value['featured'], value['rows'], value['names']
    if (not isinstance(featured, dict) or set(featured) != {'title', 'note', 'programs'}
            or not isinstance(rows, list) or len(rows) > 8 or not isinstance(names, dict) or len(names) > 64):
        raise ValueError(_('Invalid storefront'))
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'category', 'first'} or not isinstance(row['category'], str)
                or not STOREFRONT_CATEGORY.fullmatch(row['category'])):
            raise ValueError(_('Invalid storefront row'))
    return dict(
        featured=dict(title=catalog_text(text(featured['title'])), note=catalog_text(text(featured['note'])),
                      programs=identities(featured['programs'], MAX_FEATURED)),
        rows=[dict(category=row['category'], first=identities(row['first'], MAX_FIRST)) for row in rows],
        names={identities([key], 1)[0]: text(name) for key, name in names.items()})


def shelf_items(paths=None):
    """The From Emaki shelf; a missing or invalid file gives an empty shelf, never a guess."""
    for path in storefront_paths(SHELF) if paths is None else paths:
        try:
            return shelf_value(json.loads(Path(path).read_text()))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, AttributeError):
            break
    return []


def shelf_value(value):
    def text(item, limit):
        if not isinstance(item, str) or not 0 < len(item) <= limit or item != removal.clean_text(item):
            raise ValueError(_('Invalid shelf text'))
        return item

    if (not isinstance(value, dict) or value.get('version') != 1 or set(value) != {'version', 'items'}
            or not isinstance(value['items'], list) or len(value['items']) > MAX_SHELF):
        raise ValueError(_('Invalid shelf'))
    items = []
    for item in value['items']:
        if (not isinstance(item, dict) or set(item) - {'when'} != {'package', 'title', 'summary'}
                or 'when' in item and not (isinstance(item['when'], str) and SHELF_WHEN.fullmatch(item['when']))):
            raise ValueError(_('Invalid shelf item'))
        found = dict(package=removal.valid_name(item['package']), title=catalog_text(text(item['title'], 64)),
                     summary=catalog_text(text(item['summary'], 160)))
        if 'when' in item:
            found['when'] = item['when']
        items.append(found)
    if len({item['package'] for item in items}) != len(items):
        raise ValueError(_('Invalid shelf: a package twice'))
    return items


def nvidia_gpu(sysfs_root=Path('/sys')):
    """An NVIDIA display controller (PCI class 0x03), read from cached sysfs attributes only."""
    for device in sorted((Path(sysfs_root) / 'bus' / 'pci' / 'devices').glob('*')):
        try:
            vendor = int((device / 'vendor').read_text().strip(), 16)
            pci_class = int((device / 'class').read_text().strip(), 16)
        except (OSError, ValueError):
            continue
        if vendor == NVIDIA and pci_class >> 16 == 0x03:
            return True
    return False


def shelf(rows, items=None, sysfs_root=None):
    """Shelf rows: the catalog's own package row under the shelf's title; missing packages are left out."""
    conditions = {'nvidia-gpu': lambda: nvidia_gpu(SYSFS if sysfs_root is None else sysfs_root)}
    held = {}
    rows = [row for row in rows if row.get('packageOnly')]
    found = []
    for item in shelf_items() if items is None else items:
        when = item.get('when')
        if when is not None:
            if when not in conditions:
                continue
            if when not in held:
                held[when] = conditions[when]()
            if not held[when]:
                continue
        # Rows keep pacman's repository order, so the first match is the one pacman would install.
        match = next((row for row in rows if row.get('package') == item['package']), None)
        if match is not None:
            entry = dict(deepcopy(match), name=item['title'], summary=item['summary'], shelf=True)
            if item['package'] == 'emaki-nvidia':
                available = {row['target']: row for row in rows}
                try:
                    devices = arch.graphics_policy().detect(SYSFS if sysfs_root is None else sysfs_root)
                    targets = arch.install_targets(match['target'], available, devices)
                except (OSError, ValueError):
                    continue
                entry['installed'] = all(available[target].get('installed') for target in targets)
                entry['actions'] = [] if entry['installed'] else ['install']
            found.append(entry)
    return found


def aur_records(payload, installed):
    """AUR RPC data, validated; installing it always goes through a reviewed build plan."""
    return aur.records(payload, installed)


def select(rows, query='', source='', category='', installed=None, offset=0, limit=100):
    if (not isinstance(query, str) or len(query) > 256 or source not in ('', 'arch', 'flatpak', 'aur')
            or not isinstance(category, str) or len(category) > 128
            or (installed is not None and not isinstance(installed, bool))
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError(_('Invalid filters'))
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
    if source == 'aur':
        # The AUR has thousands of near names: the exact name first, then what people use.
        wanted = query.strip().casefold()
        found.sort(key=lambda row: (row.get('package', '') != wanted, not row.get('package', '').startswith(wanted),
                                    -float(row.get('popularity') or 0), row.get('name', '').casefold(), row['key']))
    else:
        found.sort(key=lambda row: (row.get('name', '').casefold(), row['key']))
    return dict(programs=found[offset:offset + limit], total=len(found), offset=offset, limit=limit,
                categories=sorted({category for row in rows for category in row.get('categories', [])}))


class Engine:
    def __init__(self, legacy, cache, config=None, popularity=None):
        self.legacy = legacy
        self.cache = Path(cache)
        self.consent_file = aur.consent_path(self.cache / 'config' if config is None else config)
        self.rows = None
        self.installed = {}
        self.installed_known = False
        self.rich_programs = []
        self.popularity = popularity
        self.arch_popularity = set()
        self.warnings = []
        self.aur = {}
        self.receipts = []
        self.foreign = set()
        self.foreign_read = False
        # Stars on catalog rows come from the cached ODRS index (programs_reviews.py).
        self.ratings = reviews.Annotator(self.cache / 'odrs')

    def aur_enabled(self):
        return aur.consent(self.consent_file)['enabled']

    def aur_status(self, accept=None, showWarning=True):
        if accept is not None:
            if not isinstance(accept, bool):
                raise ValueError(_('Invalid consent'))
            aur.set_consent(self.consent_file, accept, showWarning)
            self.foreign_read = False
        state = aur.consent(self.consent_file)
        try:
            result = subprocess.run(['emaki-rollback', 'status', '--json'], capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=20)
            mode = json.loads(result.stdout).get('mode') if result.returncode == 0 else ''
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            mode = ''
        # Restore points exist only on the snapshotted btrfs root.
        return dict(ok=True, **state, snapshots=mode in ('normal', 'pending', 'snapshot'))

    def match_aur(self, rows):
        """Give an AUR row that repackages a catalog program that program's identity and icons."""
        programs = [row for row in (self.rows or {}).values()
                    if row['kind'] in ('arch', 'flatpak') and not row.get('packageOnly')]
        for row in rows:
            match = aur.same_program(row['package'], programs)
            if match is not None:
                # The AUR has no icons or categories; borrow display data, never identity for actions.
                row.update(id=match['id'], sameAs=match['key'], icons=list(match.get('icons', [])),
                           categories=list(match.get('categories', [])))
        return rows

    def load(self, refresh=False):
        if self.rows is not None and not refresh:
            return
        self.warnings = []
        try:
            available, self.installed = arch.inventory(cache=self.cache, refresh=refresh, warnings=self.warnings)
            self.installed_known = True
        except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.SubprocessError):
            available = {}
            try:
                installed = self.legacy.pacman('-Q')
                if getattr(installed, 'returncode', 0) != 0:
                    raise RuntimeError(_('Installed package query failed'))
                self.installed = arch.parse_versions(installed.stdout)
                self.installed_known = True
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                self.installed = {}
                self.installed_known = False
            self.warnings.append(_('Repository package metadata could not be read. The catalog is incomplete.'))
        # The package recipe owns the Rich set. No installer module or second
        # package list is needed on the installed desktop.
        self.rich_programs = next((list(dict.fromkeys(package.get('optionalPackages', [])))
                                   for package in available.values()
                                   if package['package'] == 'emaki-apps'), [])
        # Installed packages no configured repository has (pacman's foreign ones). Without
        # repository data nothing is known to be foreign, rather than everything.
        sync = {package['package'] for package in available.values()}
        self.foreign = {name for name in self.installed if name not in sync} if sync and not self.warnings else set()
        self.foreign_read = False
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
            self.warnings.append(_('Repository AppStream data is missing. Install the platform application metadata package and refresh.'))
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
                self.warnings.append(_('Program metadata contains an invalid identifier.'))
                continue
            row = removal.clean_display(row)
            if row['kind'] == 'flatpak' and (row.get('manageable') is False
                    or row['installation'] not in ('system', 'user')
                    or (row['installation'] == 'user' and not row.get('installed'))):
                row['actions'] = []
            else:
                row['actions'] = ['install'] if not row.get('installed') else ['remove']
            if row['kind'] == 'arch' and row.get('installed') and removal.is_protected(row['package']):
                row.update(protected=True, removable=False, actions=[])
            safe_rows.append(row)
        program_keys = {}
        for row in sorted(safe_rows, key=lambda item: item['key']):
            if row['kind'] == 'arch' and not row.get('packageOnly'):
                program_keys.setdefault(row['target'], row['key'])
        for row in safe_rows:
            if row.get('packageOnly'):
                row['programKey'] = program_keys.get(row['target'], '')
        self.rows = {row['key']: row for row in safe_rows}
        self.arch_popularity = arch_popularity_packages(safe_rows, available)
        self.warnings = removal.clean_display(self.warnings)
        # Offline stand-ins for installed AUR programs last while their package is installed, so a
        # confirmed Undo (which reloads) still finds them; their version follows the installed one,
        # so they never offer an install. Without a live receipt offer_undo below takes Undo away.
        self.aur = {key: row for key, row in self.aur.items()
                    if not row.get('recorded') or row['package'] in self.installed}
        for row in self.aur.values():
            if row.get('recorded'):
                row['version'] = removal.clean_text(self.installed[row['package']])
            row.update(installed=row['package'] in self.installed,
                       installedVersion=removal.clean_text(self.installed.get(row['package'], '')),
                       actions=aur.actions(self.installed.get(row['package']), row['version']))
        self.match_aur(list(self.aur.values()))
        self.receipts = receipts.receipts()
        for row in list(self.rows.values()) + list(self.aur.values()):
            self.offer_undo(row)

    def receipt_for(self, row):
        if not row.get('installed'):
            return None
        if row['kind'] != 'flatpak' and row.get('package') == 'emaki-nvidia':
            return None
        if row['kind'] == 'flatpak':
            return receipts.latest(self.receipts, key=row['key'])
        found = receipts.latest(self.receipts, package=row.get('package', ''))
        if found is not None:
            expected = dict(found['added'])
            expected.update({name: versions[1] for name, versions in found['upgraded'].items()})
            if (any(self.installed.get(name) != version for name, version in expected.items())
                    or any(name in self.installed for name in found.get('removed', {}))):
                return None
        return found

    def offer_undo(self, row):
        """An installed program whose install was recorded can be taken back exactly."""
        if row['kind'] != 'flatpak' and row.get('package') == 'emaki-nvidia':
            row['description'] = (_('NVIDIA support has no Undo button because removing graphics drivers can leave a black screen. '
                                  'A restore point is made before installation. To go back, open Updates, choose the '
                                  'restore point dated just before this installation, select Restore, then restart.'))
        found = self.receipt_for(row)
        row.pop('undo', None)
        if found is not None and (found['added'] or found['kind'] == 'flatpak' or receipts.needs_restore(found)):
            row['undo'] = removal.clean_display(receipts.summary(found))
            if 'undo' not in row.get('actions', []):
                row['actions'] = list(row.get('actions', [])) + ['undo']
        elif 'undo' in row.get('actions', []):
            row['actions'] = [action for action in row['actions'] if action != 'undo']

    def search_aur(self, query):
        if len(query.strip()) < 2 or not self.aur_enabled():
            return
        backend = update_backend()
        url = aur.search_url(query)
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            def validate(raw):
                return aur_records(json.loads(raw), self.installed)
            rows, stale = backend.fetch(url, self.cache, validate)
            for row in rows:
                self.offer_undo(row)
            self.aur.update({row['key']: row for row in self.match_aur(rows)})
            if stale:
                self.warnings.append(_('AUR search results come from an older check.'))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            self.warnings.append(_('AUR search could not finish. Narrow the search or try again later.'))

    def installed_aur(self):
        """Installed AUR programs keep their AUR identity, facts and Undo in a freshly started window.

        At most one RPC request per catalog load, with consent. A recorded install stays known
        locally if the section is closed, offline, or the package disappeared from the AUR; its
        stand-in facts remain unknown.
        """
        if self.foreign_read:
            return
        self.foreign_read = True
        # Installs Emaki recorded come first, so the cap never hides an Undo; debug symbols are no program.
        recorded = {value['target'] for value in self.receipts if value['kind'] == 'aur' and not value['undone']}
        recorded &= self.installed.keys()
        candidates = (self.foreign if self.aur_enabled() else set()) | recorded
        names = sorted((name for name in candidates
                        if aur.NAME.fullmatch(name) and not name.endswith('-debug')
                        and ('aur:' + name not in self.aur or not self.aur_enabled())),
                       key=lambda name: (name not in recorded, name))[:MAX_INSTALLED_AUR]
        names.sort()
        if not names:
            return
        rows = []
        lookup = [name for name in names if name in self.foreign] if self.aur_enabled() else []
        if lookup:
            try:
                backend = update_backend()
                self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
                rows, _unused = backend.fetch(aur.info_url(lookup), self.cache,
                                        lambda raw: aur_records(json.loads(raw), self.installed))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                pass
        returned = {row['package'] for row in rows}
        for name in names:
            if name in returned:
                continue
            found = receipts.latest(self.receipts, package=name)
            if found is None or found['kind'] != 'aur':
                continue
            version = removal.clean_text(self.installed.get(name, ''))
            rows.append(removal.clean_display(dict(
                key='aur:' + name, id=name, kind='aur', package=name, packageBase=name,
                name=found['name'] or name, summary='', description='', licence='', icons=[],
                categories=[], screenshots=[], keywords=[], version=version, installed=True,
                installedVersion=version, recorded=True, actions=aur.actions(version, version))))
        for row in self.match_aur(rows):
            if row['package'] in candidates and (row['key'] not in self.aur or not self.aur_enabled()):
                self.offer_undo(row)
                self.aur[row['key']] = row

    def catalog(self, **options):
        refresh = options.pop('refresh', False)
        include_aur = options.pop('includeAur', False)
        scope = options.pop('scope', 'programs')
        if not isinstance(refresh, bool) or not isinstance(include_aur, bool):
            raise ValueError(_('Invalid discovery option'))
        if scope not in ('programs', 'packages'):
            raise ValueError(_('Invalid catalog scope'))
        if scope == 'packages' and (include_aur or options.get('source', '') not in ('', 'arch')
                                    or options.get('category', '')):
            raise ValueError(_('Invalid package filters'))
        select([], **options)
        enabled = self.aur_enabled()
        if options.get('source') == 'aur' and not enabled:
            return failure('aur-closed')
        self.load(refresh)
        if scope == 'programs' and enabled and (include_aur or options.get('source') == 'aur'):
            self.search_aur(options.get('query', ''))
        if scope == 'programs' and not options.get('offset'):
            self.installed_aur()
        if self.popularity is not None:
            # Flathub counts monthly installs, pkgstats gives the share of machines: different units.
            # Rank each source by itself (100 = its most popular) so Popular can mix both fairly.
            values = self.popularity.snapshot()
            scores = {'arch': percentile_ranks({name: score for name, score in values.get('arch', {}).items()
                                                if name in self.arch_popularity}),
                      'flathub': percentile_ranks(values.get('flathub', {}))}
            for row in self.rows.values():
                # AUR keeps its separate popularity semantics. Flatpak discovery
                # only supplies apps from remotes with a verified Flathub URL.
                source = 'flathub' if row['kind'] == 'flatpak' else 'arch'
                identity = row['ref'].split('/')[1] if source == 'flathub' else row['package']
                row.pop('popularity', None)
                if source == 'arch' and not row.get('packageOnly') and (
                        row.get('componentType') != 'desktop-application'
                        or 'Building' in row.get('categories', [])):
                    continue
                if identity in scores.get(source, {}):
                    row['popularity'] = scores[source][identity]
        rows = [row for row in self.rows.values() if bool(row.get('packageOnly')) == (scope == 'packages')]
        if scope == 'programs':
            rows += [row for row in self.aur.values() if enabled or row.get('recorded')]
        result = select(rows, **options)
        self.ratings.annotate(result['programs'])
        return dict(ok=True, **result, storefront=storefront(), shelf=shelf(self.rows.values()),
                    essentials=dict(minimal=self.installed_known and 'emaki-apps' not in self.installed,
                                    programs=list(self.rich_programs)),
                    warnings=list(dict.fromkeys(self.warnings)),
                    partial=bool(self.warnings))

    def aur_popular(self):
        """What the AUR counts as most popular, in its order: no picks of Emaki's own."""
        if not self.aur_enabled():
            return failure('aur-closed')
        self.load()
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            names, stale = aur.popular_names(self.cache / 'aur-popular.json')
            backend = update_backend()
            rows, _unused = backend.fetch(aur.info_url(names), self.cache,
                                    lambda raw: aur_records(json.loads(raw), self.installed))
        except (OSError, ValueError, RuntimeError, EOFError, zlib.error):
            return dict(ok=True, programs=[], warnings=[_('The popular AUR list could not be read. Search still works.')])
        rows = sorted(self.match_aur(rows), key=lambda row: (
            -(row['popularity'] if row['popularity'] is not None else -1),
            -(row['votes'] if row['votes'] is not None else -1), row['package']))
        for row in rows:
            self.offer_undo(row)
        self.aur.update({row['key']: row for row in rows})
        return dict(ok=True, programs=rows,
                    warnings=[_('The popular AUR list comes from an older check.')] if stale else [])

    def details(self, key):
        if not isinstance(key, str) or not 0 < len(key) <= 2048:
            raise ValueError(_('Invalid catalog key'))
        self.load()
        if key.startswith('aur:') and not self.aur_enabled():
            self.installed_aur()
            row = self.aur.get(key)
            if row is None or not row.get('recorded'):
                return failure('aur-closed')
        if key.startswith('aur:') and key not in self.aur:
            # Exact lookup also permits a search result to survive a CLI restart.
            name = removal.valid_name(key[4:])
            backend = update_backend()
            self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            rows, _unused = backend.fetch(aur.info_url([name]), self.cache,
                                    lambda raw: aur_records(json.loads(raw), self.installed))
            for row in rows:
                self.offer_undo(row)
            self.aur.update({row['key']: row for row in self.match_aur(rows) if row['package'] == name})
        row = self.rows.get(key) or self.aur.get(key)
        if row:
            self.ratings.annotate([row])
        return dict(ok=True, program=row, warnings=self.warnings) if row else failure('not-found')

    def related(self, key):
        """AUR editions of an Arch or Flathub program, once the AUR section is open."""
        if not isinstance(key, str) or not 0 < len(key) <= 2048:
            raise ValueError(_('Invalid catalog key'))
        if not self.aur_enabled():
            return dict(ok=True, programs=[])
        self.load()
        row = self.rows.get(key)
        if row is None or row['kind'] not in ('arch', 'flatpak') or row.get('packageOnly'):
            return failure('not-found')
        names = aur.candidates(row)
        if not names:
            return dict(ok=True, programs=[])
        backend = update_backend()
        self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            rows, _unused = backend.fetch(aur.info_url(names), self.cache,
                                    lambda raw: aur_records(json.loads(raw), self.installed))
        except (OSError, ValueError, RuntimeError):
            return dict(ok=True, programs=[], warnings=[_('The AUR could not be checked for this program.')])
        found = [aur_row for aur_row in self.match_aur(rows) if aur_row.get('sameAs') == key]
        for aur_row in found:
            self.offer_undo(aur_row)
        self.aur.update({aur_row['key']: aur_row for aur_row in found})
        return dict(ok=True, programs=found)

    def aur_plan(self, name):
        if not self.aur_enabled():
            return failure('aur-closed')
        if not self.aur_status().get('snapshots'):
            return dict(ok=False, code='snapshots-unavailable', message=
                        _('AUR installs need system snapshots. This system does not support them, so nothing was installed.'))
        system = aur.System(aur.fetch_json, dict(arch.ENV), cache=self.cache, refresh=True)
        try:
            native = aur.build_plan(name, system)
        except arch.MetadataUnavailable as error:
            return dict(failure('metadata-unavailable'), message=str(error))
        except aur.Refused as refusal:
            return failure(refusal.code if refusal.code in MESSAGES else 'failed', **refusal.fields)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            return dict(failure('failed'), message=str(error) or MESSAGES['failed'])
        return dict(native, ok=True, code='ready', action='install')

    def plan(self, action, key=''):
        try:
            answer = self._plan(action, key)
        except arch.MetadataUnavailable as error:
            answer = dict(failure('metadata-unavailable'), message=str(error))
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            answer = dict(failure('failed'), message=str(error) or MESSAGES['failed'])
        except Exception as error:
            self.report_plan_failure('invalid-request', type(error).__name__)
            raise
        if not answer.get('ok'):
            self.report_plan_failure(answer.get('code', 'failed'), answer.get('message', ''))
        return answer

    @staticmethod
    def report_plan_failure(code, message):
        # One bounded line, without targets, tokens or nested transaction data.
        try:
            clean = lambda value: ' '.join(str(value).split())[:1024]
            syslog.openlog('emaki-programs-engine', syslog.LOG_PID, syslog.LOG_USER)
            syslog.syslog(syslog.LOG_INFO, 'action=plan event=failed code=' + clean(code) + ' message=' + clean(message))
        except Exception:
            pass

    def _plan(self, action, key=''):
        if action == 'update':
            if key:
                raise ValueError(_('Updates always include the full system'))
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
        if action not in ('install', 'remove', 'undo'):
            raise ValueError(_('Invalid transaction'))
        answer = self.details(key)
        if not answer['ok']:
            return answer
        row = answer['program']
        if action == 'install' and row['kind'] == 'aur' and not self.aur_enabled():
            return failure('aur-closed')
        if action in ('remove', 'undo') and not row.get('installed'):
            return failure('not-installed')
        if action == 'undo':
            found = self.receipt_for(row)
            if found is None:
                return failure('nothing-to-undo')
            try:
                native = (flatpak.undo_plan(row, found) if found['kind'] == 'flatpak'
                          else receipts.undo_plan(found, self.legacy.pacman))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return failure('failed')
            if not native.get('ok'):
                return failure('cannot-undo', reason=native.get('code', ''), requiredBy=native.get('requiredBy', []),
                               keep=native.get('keep', {}))
            native = dict(native, packages=native.get('remove', []))
        elif row['kind'] == 'aur':
            if (action == 'install' and row.get('installed')
                    and 'install' not in aur.actions(row.get('installedVersion'), row.get('version'))):
                return failure('up-to-date')
            native = self.aur_plan(row['package']) if action == 'install' else self.legacy.preview(row['package'])
        elif row['kind'] == 'arch':
            native = (arch.install_plan(row['target'], cache=self.cache, refresh=True) if action == 'install'
                      else self.legacy.preview(row['package']))
        else:
            native = flatpak.plan(action, row)
        if not native.get('ok'):
            return native
        plan = dict(ok=True, action=action, key=key, kind=row['kind'], transaction=native)
        plan['token'] = digest(plan)
        return plan

    def execute(self, action, key, token, emit):
        redactor = Redactor()
        redactor.add(token)
        self._diagnostic_redactor = redactor
        failure_diagnostic = None
        def relay(event_type, **fields):
            nonlocal failure_diagnostic
            # Buffer explicit failures until the result establishes failure.
            # Progress can carry error lines, but routine messages are not causes.
            diagnostic = {name: redactor.text(value, keep_newlines=True)[:4096]
                          for name in ('message', 'error')
                          if isinstance(value := fields.get(name), str) and value}
            if diagnostic:
                if (event_type == 'error' or fields.get('ok') is False
                        or fields.get('error') or fields.get('stage') in ('error', 'failed', 'failure')):
                    failure_diagnostic = diagnostic
                elif event_type == 'progress':
                    for line in diagnostic.get('message', '').splitlines():
                        if (re.search(r'\berror:', line, re.IGNORECASE)
                                or re.match(r'^\s*(?:[\w.+-]+:\s*)?(?:fatal:|\[Errno \d+\])',
                                            line, re.IGNORECASE)):
                            failure_diagnostic = {'message': line}
            emit(event_type, **fields)

        try:
            report_event(action, 'started', {}, redactor)
            answer = self._execute(action, key, token, relay)
            failed = (not answer.get('ok') or answer.get('partial')
                      or answer.get('status') in ('failed', 'partial')
                      or answer.get('code') in ('failed', 'partial'))
            if failed and failure_diagnostic:
                report_event(action, 'error', failure_diagnostic, redactor)
            report_event(action, 'failed' if failed else 'finished', answer, redactor)
            return answer
        except Exception as error:
            report_event(action, 'exception', dict(error=type(error).__name__), redactor)
            raise
        finally:
            redactor.secrets.clear()
            self._diagnostic_redactor = None
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
                raise ValueError(_('Invalid removal result'))
            if result.returncode:
                answer['ok'] = False
            return answer
        except ValueError:
            return failure('unknown')

    def _execute(self, action, key, token, emit):
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
            raise ValueError(_('Invalid plan token'))
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
        row = self.rows.get(key) or self.aur[key]
        native_token = plan['transaction']['token']
        redactor = getattr(self, '_diagnostic_redactor', None)
        if redactor is not None and re.fullmatch('[0-9a-f]{64}', native_token):
            redactor.add(native_token)
        if row['kind'] == 'flatpak' and row['installation'] == 'user':
            return flatpak.execute(action, row, native_token, emit)
        if row['kind'] in ('arch', 'aur') and action == 'remove':
            return self.remove_native(row['package'], native_token)
        if action == 'undo' and row['kind'] == 'flatpak':
            helper = Path(__file__).resolve().with_name('emaki-programs-flatpak')
            arguments = ['undo', row['origin'], row['ref'], plan['transaction']['receipt'], native_token]
        elif action == 'undo':
            helper = Path(__file__).resolve().with_name('emaki-programs-undo')
            arguments = [plan['transaction']['receipt'], plan['transaction']['target'], native_token]
        elif row['kind'] == 'flatpak':
            helper = Path(__file__).resolve().with_name('emaki-programs-flatpak')
            arguments = [action, row['origin'], row['ref'], native_token]
        elif row['kind'] == 'aur':
            helper = Path(__file__).resolve().with_name('emaki-programs-aur')
            arguments = [row['package'], native_token]
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

    def development(self):
        # Read local package state without loading remote application catalogues.
        installed = arch.parse_versions(arch.run(['pacman', '-Q']))
        return dict(ok=True, kits=kits.catalog(installed))

    def development_plan(self, id, action, dockerAccess=False):
        if action not in ('install', 'remove', 'undo', 'finish') or type(dockerAccess) is not bool:
            raise ValueError(_('Invalid transaction'))
        if dockerAccess and (id != 'docker' or action != 'install'):
            raise ValueError(_('Invalid consent'))
        if action == 'finish':
            if id != 'rust':
                raise ValueError(_('Invalid transaction'))
            installed = arch.parse_versions(arch.run(['pacman', '-Q']))
            if not all(name in installed for name in kits.definition(id)['packages']):
                return dict(ok=False, code='not-installed', message=_('Install the Rust packages before finishing setup.'))
            native = dict(ok=True, packages=[], message=_('Installing the stable Rust toolchain for your user.'))
        elif not self.aur_status().get('snapshots'):
            return dict(ok=False, code='snapshots-unavailable', message=
                        _('Development kits need system snapshots. This system does not support them.'))
        elif action == 'install':
            native = kits.install_plan(id, docker_access=dockerAccess, cache=self.cache, refresh=True)
        elif action == 'remove':
            native = kits.remove_plan(id)
        else:
            native = kits.undo_plan(id)
            if native.get('ok'):
                native = dict(native, packages=native.get('remove', []))
        if not native.get('ok'):
            return native
        plan = dict(ok=True, action=action, key='development:' + id, kind='development',
                    dockerAccess=dockerAccess, transaction=native)
        plan['token'] = digest(plan)
        return plan

    def development_execute(self, id, action, token, emit, dockerAccess=False):
        if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
            raise ValueError(_('Invalid plan token'))
        plan = self.development_plan(id, action, dockerAccess)
        if not plan.get('ok'):
            return plan
        if token != plan['token']:
            return failure('changed', plan=plan)
        native = plan['transaction']
        if action == 'finish':
            return kits.finish_user(id, emit)
        if action == 'undo':
            helper = Path(__file__).resolve().with_name('emaki-programs-undo')
            arguments = [native['receipt'], native['target'], native['token']]
        else:
            helper = Path(__file__).resolve().with_name('emaki-programs-install')
            arguments = [native['target'], native['token']]
        finished = None
        try:
            with subprocess.Popen(['pkexec', str(helper), *arguments],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, start_new_session=True) as process:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                        if event.get('type') == 'finished':
                            finished = event
                        elif event.get('type') == 'progress':
                            emit('progress', **{key: value for key, value in event.items() if key != 'type'})
                    except (ValueError, AttributeError):
                        continue
                code = process.wait()
            if finished is None:
                return failure('not-authorized' if code in (126, 127) else 'unknown')
            finished.pop('type', None)
            if code:
                finished['ok'] = False
            if finished.get('ok') and action == 'install' and id == 'rust':
                user_result = kits.finish_user(id, emit)
                if not user_result.get('ok'):
                    finished.update(user_result, partial=True)
                elif user_result.get('message'):
                    finished['message'] = user_result['message']
            return finished
        finally:
            # Failures can still leave completed package work behind.
            self.rows = None

    def websites(self, operation, **params):
        messages = {
            'website-invalid-url': _('Enter a complete http:// or https:// website address.'),
            'website-invalid-name': _('Choose a shorter name without control characters.'),
            'website-invalid-id': _('This website app is invalid.'),
            'website-unsafe-path': _('The website app folder is not safe to change.'),
            'website-not-found': _('This website app no longer exists.'),
            'website-modified': _('The website app files have changed. Nothing was removed.'),
            'website-running': _('Close this website app before removing it.'),
        }
        try:
            if operation == 'website-add':
                if not set(params) <= {'url', 'name'} or 'url' not in params:
                    raise TypeError
                app = webapps.create_app(params['url'], params.get('name', ''))
                message = (_('Website app added. Its name or icon could not be fetched; a generated icon is used when needed.')
                           if app.get('metadataWarning') else _('Website app added. Find it in the launcher.'))
                return dict(ok=True, app=app, apps=webapps.list_apps(), message=message)
            if operation == 'website-remove':
                if set(params) != {'id'}:
                    raise TypeError
                webapps.remove_app(params['id'])
                return dict(ok=True, apps=webapps.list_apps(), message=_('Website app removed.'))
            if params:
                raise TypeError
            return dict(ok=True, apps=webapps.list_apps())
        except webapps.WebAppError as error:
            code = str(error)
            return dict(ok=False, code=code, message=messages.get(code, MESSAGES['failed']))

    def request(self, request, emit):
        if not isinstance(request, dict) or request.get('version') != PROTOCOL:
            raise ValueError(_('Invalid protocol version'))
        operation = request.get('method')
        params = request.get('params', {})
        if not isinstance(params, dict):
            raise ValueError(_('Invalid parameters'))
        if operation in ('catalog', 'search'):
            return self.catalog(**params)
        if operation == 'details':
            return self.details(**params)
        if operation == 'related':
            return self.related(**params)
        if operation == 'aur':
            return self.aur_status(**params)
        if operation == 'popular':
            return self.aur_popular(**params)
        if operation == 'plan':
            return self.plan(**params)
        if operation == 'execute':
            return self.execute(emit=emit, **params)
        if operation == 'development':
            return self.development(**params)
        if operation == 'development-plan':
            return self.development_plan(**params)
        if operation == 'development-execute':
            return self.development_execute(emit=emit, **params)
        if operation in ('websites', 'website-add', 'website-remove'):
            return self.websites(operation, **params)
        if operation == 'updates':
            backend = update_backend()
            self.cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            if params:
                raise ValueError(_('Invalid update status parameters'))
            return dict(ok=True, **backend.status(self.cache))
        raise ValueError(_('Unknown method'))


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
                raise ValueError(_('Request too large'))
            request = json.loads(raw)
            if isinstance(request, dict):
                identity = request.get('id')
                if not (identity is None or type(identity) is int or isinstance(identity, str) and len(identity) <= 128):
                    identity = None
                    raise ValueError(_('Invalid request id'))
                if isinstance(identity, str) and identity != removal.clean_text(identity):
                    identity = None
                    raise ValueError(_('Invalid request id'))
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
    parser = argparse.ArgumentParser(description=_('Program manager JSON protocol'))
    parser.add_argument('method', choices=('catalog', 'search', 'details', 'related', 'plan', 'execute',
                                           'updates', 'aur', 'popular', 'serve', 'development',
                                           'development-plan', 'development-execute', 'websites',
                                           'website-add', 'website-remove'))
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
    parser.add_argument('--docker-access', action='store_true')
    args = parser.parse_args(argv)
    cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'emaki-update-manager'
    # The privileged helper reads consent for this account from the same fixed
    # location; a caller-supplied environment must not select another consent.
    config = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.config'
    engine = Engine(legacy, cache, config, popularity=removal.PopularityCache(cache))
    if args.method == 'serve':
        if args.arguments:
            parser.error(_('The JSON service takes requests on standard input.'))
        # The window's service: refresh the ratings index beside it, never in a request's way.
        reviews.refresh_in_background(engine.cache / 'odrs')
        serve(engine, sys.stdin, sys.stdout)
        return 0
    params = {}
    expected = {'catalog': 0, 'search': 1, 'details': 1, 'related': 1, 'plan': (1, 2), 'execute': (2, 3),
                'updates': 0, 'aur': 0, 'popular': 0, 'development': 0, 'development-plan': 2,
                'development-execute': 3, 'websites': 0, 'website-add': (1, 2), 'website-remove': 1}
    counts = expected[args.method]
    if len(args.arguments) not in (counts if isinstance(counts, tuple) else (counts,)):
        parser.error(_('Invalid program manager arguments.'))
    if args.method in ('development-plan', 'development-execute'):
        params = dict(action=args.arguments[0], id=args.arguments[1], dockerAccess=args.docker_access)
        if args.method == 'development-execute':
            params['token'] = args.arguments[2]
    elif args.method == 'website-add':
        params = dict(url=args.arguments[0], name=args.arguments[1] if len(args.arguments) == 2 else '')
    elif args.method == 'website-remove':
        params = dict(id=args.arguments[0])
    elif args.method in ('catalog', 'search'):
        params = dict(query=args.arguments[0] if args.arguments else '', source=args.source,
                      category=args.category, installed=args.installed, refresh=args.refresh,
                      includeAur=args.include_aur, scope=args.scope, limit=args.limit, offset=args.offset)
    elif args.method in ('details', 'related'):
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
