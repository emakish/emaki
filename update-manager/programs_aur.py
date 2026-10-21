# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The AUR section: consent, trust facts, program matching and build plans.

Nothing here builds or installs. A plan names every AUR recipe by its exact
Git commit and every repository package the build pulls in, so the privileged
helper can build exactly what the person reviewed or refuse.
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

from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.parse import quote, urlencode
import zlib
from urllib.request import Request, urlopen

import programs_arch as arch
import programs_catalog as removal

# Raise when the warning changes in substance: an older consent then no longer counts.
WARNING_VERSION = '2026-10-12'
RPC = 'https://aur.archlinux.org/rpc/v5/'
ORIGIN = 'https://aur.archlinux.org/'
NAME = re.compile(r'[a-z0-9@_+][a-z0-9@._+-]{0,127}\Z')
# Package names are lower case; a dependency can also name a library a package provides (libFLAC.so).
DEPENDENCY = re.compile(r'(?P<name>[A-Za-z0-9@_+][A-Za-z0-9@._+-]{0,127})(?:(?:<=|>=|<|>|=)[^\s<>=]{1,256})?\Z')
COMMIT = re.compile(r'[0-9a-f]{40}\Z')
# Emaki installs neither by default; makepkg needs both. They are build-only.
BUILD_TOOLS = ('base-devel', 'git')
# Exact name and the upstream binary repackaging are the same program;
# development snapshots (-git and the like) are their own entries.
SAME_PROGRAM_SUFFIXES = ('', '-bin')
MAX_BUILDS = 16
MAX_REPOSITORY = 400
# The popular list: the AUR's own metadata archive (the RPC cannot sort), its first entries.
META = ORIGIN + 'packages-meta-v1.json.gz'
META_LIMIT = 64 * 1024 * 1024
META_TEXT_LIMIT = 512 * 1024 * 1024
POPULAR = 50
POPULAR_AGE = 24 * 3600


class Refused(ValueError):
    """A plan cannot be offered; the code names why for the window."""
    def __init__(self, code, **fields):
        super().__init__(code)
        self.code = code
        self.fields = fields


# Consent -------------------------------------------------------------------

def consent_path(config_home):
    return Path(config_home) / 'emaki' / 'programs-aur.json'


def consent(path):
    """The AUR section is closed unless this person accepted the current warning."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return dict(enabled=False, showWarning=True, accepted='')
    if (not isinstance(value, dict) or value.get('version') != 1
            or value.get('warning') != WARNING_VERSION or not isinstance(value.get('accepted'), str)):
        return dict(enabled=False, showWarning=True, accepted='')
    return dict(enabled=True, showWarning=value.get('showWarning') is not False, accepted=value['accepted'])


def set_consent(path, accept, show_warning=True):
    path = Path(path)
    if accept is not True:
        path.unlink(missing_ok=True)
        return consent(path)
    if not isinstance(show_warning, bool):
        raise ValueError(_('Invalid warning choice'))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = consent(path)
    value = dict(version=1, warning=WARNING_VERSION, showWarning=show_warning,
                 accepted=previous['accepted'] or datetime.now(timezone.utc).isoformat(timespec='seconds'))
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.programs-aur-', delete=False) as output:
        json.dump(value, output)
        temporary = Path(output.name)
    os.replace(temporary, path)
    return consent(path)


# RPC records ----------------------------------------------------------------

def valid_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError(_('Invalid AUR name'))
    return value


def _stamp(value):
    if value is None:
        return ''
    if type(value) is not int or not 0 < value < 2 ** 40:
        raise ValueError(_('Invalid AUR time'))
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


def _text(value, limit=4096):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(_('Invalid AUR text'))
    return value


def _names(value, dependency=False):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 512:
        raise ValueError(_('Invalid AUR list'))
    for item in value:
        if not isinstance(item, str) or len(item) > 512:
            raise ValueError(_('Invalid AUR list entry'))
        if dependency and not DEPENDENCY.fullmatch(item):
            raise ValueError(_('Invalid AUR dependency'))
    return list(value)


def actions(installed_version, version):
    """Offer an update only when the AUR version is newer in package-manager order."""
    if installed_version is None:
        return ['install']
    if installed_version == version:
        return ['remove']
    try:
        newer = int(arch.run(['vercmp', version, installed_version]).strip()) > 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        newer = False
    return ['remove', 'install'] if newer else ['remove']


def record(value, installed):
    """One validated RPC entry with the facts the program page shows instead of screenshots."""
    if not isinstance(value, dict):
        raise ValueError(_('Invalid AUR entry'))
    name = valid_name(value.get('Name'))
    base = valid_name(value.get('PackageBase') or name)
    version = _text(value.get('Version'), 512)
    if not version:
        raise ValueError(_('Invalid AUR version'))
    votes, popularity = value.get('NumVotes'), value.get('Popularity')
    if ((votes is not None and (type(votes) is not int or votes < 0))
            or (popularity is not None and (type(popularity) not in (int, float) or popularity < 0))):
        raise ValueError(_('Invalid AUR counters'))
    url = _text(value.get('URL'), 2048)
    licence = _names(value.get('License'))
    maintainer = _text(value.get('Maintainer'), 256)
    return dict(
        key='aur:' + name, id=name, kind='aur', package=name, packageBase=base, name=name,
        summary=_text(value.get('Description')), description='', icons=[], categories=[],
        screenshots=[], licence=', '.join(licence), keywords=_names(value.get('Keywords')),
        version=version, installed=name in installed, installedVersion=installed.get(name, ''),
        downloadSize=None, installedSize=None,
        votes=votes, popularity=round(float(popularity), 2) if popularity is not None else None,
        maintainer=maintainer if 'Maintainer' in value else None,
        orphan=not maintainer if 'Maintainer' in value else None,
        outOfDate=_stamp(value['OutOfDate']) if 'OutOfDate' in value else None,
        firstSubmitted=_stamp(value['FirstSubmitted']) if 'FirstSubmitted' in value else None,
        lastModified=_stamp(value['LastModified']) if 'LastModified' in value else None,
        upstream=url if url.startswith(('https://', 'http://')) else '',
        page=ORIGIN + 'packages/' + quote(name, safe=''),
        recipe=ORIGIN + 'cgit/aur.git/tree/PKGBUILD?' + urlencode({'h': base}),
        depends=_names(value.get('Depends'), True),
        makeDepends=_names(value.get('MakeDepends'), True) + _names(value.get('CheckDepends'), True),
        actions=actions(installed.get(name), version))


def records(payload, installed):
    if (not isinstance(payload, dict) or payload.get('version') != 5
            or payload.get('type') not in ('search', 'multiinfo')
            or not isinstance(payload.get('results'), list)):
        raise ValueError(_('Invalid AUR response'))
    return removal.clean_display([record(value, installed) for value in payload['results']])


def fetch_json(url, opener=urlopen):
    """A fresh, bounded RPC read: a plan must never rest on a cached version."""
    if not url.startswith(RPC):
        raise ValueError(_('Not an AUR RPC address'))
    request = Request(url, headers={'User-Agent': 'Emaki-programs', 'Accept': 'application/json'})
    with opener(request, timeout=20) as response:
        if not response.url.startswith(ORIGIN):
            raise ValueError(_('The AUR redirected an RPC request'))
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError(_('Oversized AUR response'))
    return json.loads(raw)


def search_url(query):
    return RPC + 'search/' + quote(query, safe='') + '?by=name-desc'


def info_url(names):
    return RPC + 'info?' + urlencode([('arg[]', valid_name(name)) for name in names])


# The popular list -----------------------------------------------------------

def popular(stream, count=POPULAR):
    """The most popular AUR packages in the AUR's metadata archive, by its own Popularity value.

    Popularity is the AUR's count of votes, each weighted down as it ages. One name per
    recipe (libsoup and libsoup-docs are one entry). The archive is a gzip JSON array of
    some hundred thousand records; it is read one record at a time.
    """
    decoder = json.JSONDecoder()
    text = io.TextIOWrapper(gzip.GzipFile(fileobj=stream), encoding='utf-8')
    best, buffer, position, started, total = {}, '', 0, False, 0
    while True:
        chunk = text.read(1024 * 1024)
        total += len(chunk)
        if total > META_TEXT_LIMIT:
            raise ValueError(_('Oversized AUR metadata archive'))
        buffer = buffer[position:] + chunk
        position = 0
        while True:
            while position < len(buffer) and buffer[position] in ' \t\r\n,':
                position += 1
            if position == len(buffer):
                break
            if not started:
                if buffer[position] != '[':
                    raise ValueError(_('Invalid AUR metadata archive'))
                started, position = True, position + 1
                continue
            if buffer[position] == ']':
                ranked = sorted(best.values(), key=lambda item: (-item[0], -item[1], item[2]))
                return [dict(name=name, popularity=round(score, 2), votes=votes)
                        for score, votes, name in ranked[:count]]
            try:
                value, position = decoder.raw_decode(buffer, position)
            except ValueError:
                if not chunk:
                    raise
                break
            if not isinstance(value, dict):
                continue
            name, base, score, votes = (value.get(field) for field in ('Name', 'PackageBase', 'Popularity', 'NumVotes'))
            if (not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(base, str)
                    or type(score) not in (int, float) or not 0 <= score < float('inf')
                    or type(votes) is not int or votes < 0):
                continue
            entry = (float(score), votes, name)
            if base not in best or (-entry[0], -entry[1], entry[2]) < (-best[base][0], -best[base][1], best[base][2]):
                best[base] = entry
        if not chunk:
            raise ValueError(_('Truncated AUR metadata archive'))


def fetch_meta(opener=urlopen):
    """The AUR's metadata archive (about 10 MB), read whole and bounded."""
    request = Request(META, headers={'User-Agent': 'Emaki-programs'})
    with opener(request, timeout=60) as response:
        if not response.url.startswith(ORIGIN):
            raise ValueError(_('The AUR redirected the metadata archive'))
        raw = response.read(META_LIMIT + 1)
    if len(raw) > META_LIMIT:
        raise ValueError(_('Oversized AUR metadata archive'))
    return io.BytesIO(raw)


def popular_names(path, fetch=fetch_meta, now=None):
    """Names for the popular list: kept a day, since the AUR's counts move slowly."""
    path, now = Path(path), time.time() if now is None else now
    try:
        cached = json.loads(path.read_text())
        names = [valid_name(name) for name in cached['names']]
        stamp = cached['time']
        if not isinstance(stamp, (int, float)) or not names or len(names) > POPULAR:
            raise ValueError(_('Invalid popular list'))
    except (OSError, ValueError, TypeError, KeyError):
        names, stamp = [], 0
    if names and 0 <= now - stamp < POPULAR_AGE:
        return names, False
    try:
        fresh = [row['name'] for row in popular(fetch())]
        if not fresh:
            raise ValueError(_('Empty AUR metadata archive'))
    except (OSError, ValueError, EOFError, zlib.error):
        if names:
            return names, True
        raise
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.aur-popular-', delete=False) as output:
        json.dump(dict(time=now, names=fresh), output)
        temporary = Path(output.name)
    os.replace(temporary, path)
    return fresh, False


# Matching one program across sources ---------------------------------------

def aliases(row):
    """Names under which the AUR would publish this Arch or Flathub program."""
    names = set()
    if row.get('kind') == 'arch' and row.get('package'):
        names.add(row['package'].lower())
    identity = str(row.get('id') or '').removesuffix('.desktop')
    if identity:
        names.add(identity.split('.')[-1].lower())
    display = str(row.get('name') or '').strip().lower()
    if display:
        names.add(re.sub(r'\s+', '-', display))
    return {name for name in names if NAME.fullmatch(name)}


def candidates(row):
    """Exact AUR names to look up for an Arch or Flathub program page."""
    return sorted({name + suffix for name in aliases(row) for suffix in SAME_PROGRAM_SUFFIXES
                   if NAME.fullmatch(name + suffix)})


def same_program(aur_name, programs):
    """The single catalog program an AUR name repackages, or None."""
    found = {}
    for suffix in SAME_PROGRAM_SUFFIXES:
        if suffix and not aur_name.endswith(suffix):
            continue
        base = aur_name[:len(aur_name) - len(suffix)] if suffix else aur_name
        for row in programs:
            if base in aliases(row):
                found.setdefault(str(row['id']).removesuffix('.desktop').lower(), row)
    # An ambiguous name is not guessed into one program.
    return next(iter(found.values())) if len(found) == 1 else None


# Recipe commits -------------------------------------------------------------

def parse_refs(raw):
    """Read a Git smart-HTTP reference advertisement (pkt-lines) for HEAD."""
    position, refs = 0, {}
    while position + 4 <= len(raw):
        length = int(raw[position:position + 4], 16)
        if length == 0:
            position += 4
            continue
        if length < 4 or position + length > len(raw):
            raise ValueError(_('Invalid Git reference advertisement'))
        line = raw[position + 4:position + length].rstrip(b'\n')
        position += length
        if line.startswith(b'#'):
            continue
        line = line.split(b'\0', 1)[0].decode('ascii')
        commit, _unused, name = line.partition(' ')
        if COMMIT.fullmatch(commit) and name:
            refs[name] = commit
    commit = refs.get('HEAD') or refs.get('refs/heads/master')
    if not commit:
        raise ValueError(_('The AUR recipe has no published commit'))
    return commit


def head_commit(base, opener=urlopen):
    """The exact recipe commit, read without Git installed (smart HTTP, protocol v0)."""
    url = ORIGIN + quote(valid_name(base), safe='') + '.git/info/refs?service=git-upload-pack'
    request = Request(url, headers={'User-Agent': 'Emaki-programs'})
    with opener(request, timeout=20) as response:
        if not response.url.startswith(ORIGIN):
            raise ValueError(_('The AUR redirected the recipe address'))
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError(_('Oversized Git reference advertisement'))
    return parse_refs(raw)


def recipe_link(base, commit):
    return ORIGIN + 'cgit/aur.git/tree/PKGBUILD?' + urlencode({'h': base, 'id': commit})


# Build plans ----------------------------------------------------------------

def dependency_name(text):
    match = DEPENDENCY.fullmatch(text)
    if not match:
        raise ValueError(_('Invalid AUR dependency'))
    return match['name']


def build_plan(target, system):
    """Resolve what an AUR install builds and pulls, without changing anything.

    system provides: info(names) -> {name: record}, unsatisfied(deps) -> [deps],
    repository(dep) -> [package rows] or None, commit(base) -> sha1, installed() -> {name: version}.
    """
    valid_name(target)
    if isinstance(system, System) and system.database is None:
        with arch.private_database(system.cache_directory, refresh=system.refresh) as database:
            system.database = database
            try:
                return build_plan(target, system)
            finally:
                system.database = None
    installed = system.installed()
    known = dict(system.info([target]))
    if target not in known:
        raise Refused('not-found')
    builds, order, repository, edges, purposes = {}, [], {}, {}, {}

    def add_repository(rows, purpose):
        for row in rows:
            current = repository.get(row['package'])
            if current is None:
                repository[row['package']] = dict(row, purpose=purpose)
            elif purpose == 'run':
                current['purpose'] = 'run'
        if len(repository) > MAX_REPOSITORY:
            raise Refused('too-large')

    def require(deps, purpose, parent=None, build_time=False):
        for dep in system.unsatisfied(deps):
            rows = system.repository(dep)
            if rows is not None:
                add_repository(rows, purpose)
                continue
            name = dependency_name(dep)
            # A library name (libFLAC.so) no repository provides is no AUR package name either.
            if name not in known and NAME.fullmatch(name):
                known.update(system.info([name]))
            if name not in known:
                raise Refused('missing-dependency', dependency=dep)
            base = known[name]['packageBase']
            if parent is not None:
                # Runtime outputs of one recipe are installed together. A build
                # prerequisite, however, must exist before that recipe can run.
                if base == parent and build_time:
                    raise Refused('dependency-cycle', dependency=name)
                if base != parent:
                    edges[parent].add(base)
            visit(name, purpose)

    def visit(name, purpose):
        row = known[name]
        base = row['packageBase']
        previous = purposes.get(name)
        if previous == 'run' or previous == purpose:
            return
        purposes[name] = purpose
        if base not in builds:
            if len(builds) >= MAX_BUILDS:
                raise Refused('too-large')
            commit = system.commit(base)
            if not COMMIT.fullmatch(commit):
                raise Refused('recipe-unavailable', packageBase=base)
            builds[base] = dict(packageBase=base, packages={}, commit=commit,
                                version=row['version'], purpose=purpose, maintainer=row['maintainer'],
                                orphan=row['orphan'], outOfDate=row['outOfDate'], recipe=recipe_link(base, commit))
            edges[base] = set()
        builds[base]['packages'][name] = row['version']
        if purpose == 'run':
            builds[base]['purpose'] = 'run'
        require(row['depends'], purpose, base)
        require(row['makeDepends'], 'build', base, build_time=True)

    require(list(BUILD_TOOLS), 'build')
    visit(target, 'run')
    visiting, ordered = set(), set()

    def schedule(base):
        if base in visiting:
            raise Refused('dependency-cycle', dependency=base)
        if base in ordered:
            return
        visiting.add(base)
        for dependency in sorted(edges[base]):
            schedule(dependency)
        visiting.remove(base)
        ordered.add(base)
        order.append(base)

    for base in builds:
        schedule(base)
    plan = dict(
        kind='aur', target=target, fullUpgrade=True,
        builds=[dict(builds[base], packages=[dict(package=name, version=version)
                                             for name, version in sorted(builds[base]['packages'].items())])
                for base in order],
        packages=sorted(repository.values(), key=lambda row: (row['purpose'], row['repository'], row['package'])))
    state = dict(plan, installed=installed)
    plan['token'] = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'),
                                              ensure_ascii=True).encode()).hexdigest()
    return plan


class System:
    """Real package-manager and AUR reads for a build plan; no transaction."""
    def __init__(self, fetch_json, environment, opener=urlopen, cache=None, refresh=False):
        self.fetch_json = fetch_json
        self.environment = environment
        self.opener = opener
        self.cache = {}
        self.cache_directory = cache
        self.refresh = refresh
        self.database = None

    def pacman(self, *arguments):
        if self.database is None:
            raise arch.MetadataUnavailable()
        return subprocess.run(['pacman', *arch.database_arguments(self.database), *arguments], capture_output=True, text=True, cwd='/',
                              env=self.environment, stdin=subprocess.DEVNULL, timeout=120)

    def installed(self):
        result = self.pacman('-Q')
        if result.returncode:
            raise RuntimeError(_('Installed packages could not be read'))
        return dict(line.split(maxsplit=1) for line in result.stdout.splitlines())

    def info(self, names):
        names = [valid_name(name) for name in names if name not in self.cache]
        if names:
            payload = self.fetch_json(info_url(names))
            for row in records(payload, {}):
                self.cache[row['package']] = row
        return {name: self.cache[name] for name in self.cache}

    def unsatisfied(self, deps):
        if not deps:
            return []
        for dep in deps:
            dependency_name(dep)
        result = self.pacman('-T', '--', *deps)
        if result.returncode not in (0, 127):
            raise RuntimeError(_('Installed dependencies could not be checked'))
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def repository(self, dep):
        result = self.pacman('-Sp', '--noconfirm', '--print-format', '%r|%n|%v|%s', '--', dep)
        if result.returncode:
            if 'target not found' in result.stderr:
                return None
            raise RuntimeError(_('Repository dependencies could not be resolved'))
        rows = []
        for line in result.stdout.splitlines():
            if '|' not in line:
                continue
            repository, name, version, size = line.split('|')
            valid_name(name)
            if not NAME.fullmatch(repository.lower()) or not version:
                raise ValueError(_('Invalid repository dependency'))
            rows.append(dict(repository=repository, package=name, version=version,
                             downloadSize=int(size) if size.isascii() and size.isdigit() else None))
        return rows

    def commit(self, base):
        return head_commit(base, self.opener)
