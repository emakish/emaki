#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authenticated Savannah Git retrieval through official mirrors and archived objects."""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import quote, urlsplit

API = 'https://archive.softwareheritage.org/api/1/'
VAULT_COOK_TIMEOUT = 1800
HOSTS = {'git.savannah.gnu.org', 'git.savannah.nongnu.org', 'git.sv.gnu.org'}
MIRRORS = {
    'coreutils': ('https://github.com/coreutils/coreutils.git',
                  'https://www.gnu.org/software/coreutils/'),
    'gnulib': ('https://github.com/coreutils/gnulib.git',
               'https://github.com/coreutils/gnulib'),
}


def parse(source):
    value = source.split('::', 1)[-1]
    if not (value.startswith('git+') or value.startswith('git://')):
        return None
    origin = value.removeprefix('git+').split('#', 1)[0].split('?', 1)[0]
    parsed = urlsplit(origin)
    if parsed.hostname not in HOSTS:
        return None
    fragment = value.partition('#')[2].split('?', 1)[0]
    kind, _, reference = fragment.partition('=')
    if kind == 'commit' and re.fullmatch('[0-9a-f]{40}', reference):
        pass
    elif kind == 'tag' and reference:
        subprocess.run(['git', 'check-ref-format', 'refs/tags/' + reference],
                       check=True, capture_output=True)
    else:
        raise ValueError('Savannah source needs an exact commit or signed tag')
    if kind == 'tag' and '?signed' not in value:
        raise ValueError('unsigned Savannah tags require historical binary evidence')
    name = source.split('::', 1)[0] if '::' in source else parsed.path.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git')
    if Path(name).name != name or name in ('', '.', '..'):
        raise ValueError('unsafe Savannah source directory')
    return origin, name, kind, reference


def official_mirror(origin):
    parsed = urlsplit(origin)
    project = parsed.path.rstrip('/').removesuffix('.git').removeprefix('/git/').removeprefix('/')
    if parsed.hostname in ('git.savannah.gnu.org', 'git.sv.gnu.org') and project in MIRRORS:
        url, authority = MIRRORS[project]
        return {'mirror': url, 'authority': authority}
    return None


def request_json(url, run, *, method='GET'):
    if method not in ('GET', 'POST'):
        raise ValueError('unsupported archive request method')
    options = ('--request', 'POST') if method == 'POST' else ()
    return json.loads(run('curl', '-q', '--fail', '--location', '--silent', '--show-error',
                          '--user-agent', 'emaki-publish', '--connect-timeout', '15', '--max-time', '30', *options, url))


def ready_vault(vault, env, run):
    """Read existing bundles by default; cooking requires an explicit collection opt-in."""
    if not re.fullmatch(re.escape(API) + r'vault/git-bare/swh:1:rev:[0-9a-f]{40}/', vault):
        raise ValueError('vault cooking requires an exact revision endpoint')
    try:
        status = request_json(vault, run)
    except subprocess.CalledProcessError:
        status = {}
    if status.get('status') == 'done':
        return
    if not env or env.get('EMAKI_SWH_VAULT_COOK') != '1':
        raise ValueError('Software Heritage vault is not cooked; read-only network forbids a cooking POST: ' + vault)
    deadline = time.monotonic() + VAULT_COOK_TIMEOUT
    status = request_json(vault, run, method='POST')
    while True:
        state = status.get('status')
        if state == 'done':
            return
        if state == 'failed':
            raise ValueError('Software Heritage vault cooking failed: ' + vault)
        if state not in ('new', 'pending'):
            raise ValueError('Software Heritage vault returned an unknown cooking status: ' + vault)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError(f'Software Heritage vault cooking timed out after {VAULT_COOK_TIMEOUT} seconds: ' + vault)
        time.sleep(min(10, remaining))
        if time.monotonic() >= deadline:
            raise ValueError(f'Software Heritage vault cooking timed out after {VAULT_COOK_TIMEOUT} seconds: ' + vault)
        status = request_json(vault, run)


def object_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('invalid archived Git object identifier')
    return value


def resolve_origin(origin, kind, reference, run, visit=None):
    visit_url = API + 'origin/' + quote(origin, safe='/:') + '/visit/latest/?require_snapshot=true'
    if visit is None:
        visit = request_json(visit_url, run)
    else:
        visit_url = API + 'origin/' + quote(origin, safe='/:') + '/visit/' + str(visit['visit']) + '/'
    if visit.get('origin') != origin or visit.get('status') != 'full':
        raise ValueError('Software Heritage origin lacks a full matching visit')
    snapshot = object_id(visit.get('snapshot'))
    evidence = {'origin': origin, 'snapshot': snapshot, 'visit_url': visit_url,
                'visit': visit.get('visit'), 'visit_date': visit.get('date')}
    if kind == 'commit':
        revision = request_json(API + 'revision/' + reference + '/', run)
        if revision.get('id') != reference:
            raise ValueError('archived revision differs from recipe commit')
        evidence['commit'] = reference
        return evidence
    ref = 'refs/tags/' + reference
    snapshot_url = API + 'snapshot/' + snapshot + '/?branches_from=' + quote(ref, safe='') + '&branches_count=1'
    branch_data = request_json(snapshot_url, run)
    if branch_data.get('id') != snapshot:
        raise ValueError('archived snapshot identifier differs')
    branch = branch_data.get('branches', {}).get(ref)
    if not branch or branch.get('target_type') != 'release':
        raise ValueError('signed tag is absent or not an annotated release in archived snapshot')
    tag_object = object_id(branch.get('target'))
    release = request_json(API + 'release/' + tag_object + '/', run)
    if release.get('id') != tag_object or release.get('target_type') != 'revision':
        raise ValueError('archived tag must target a Git commit')
    evidence.update(tag_object=tag_object, commit=object_id(release.get('target')),
                    snapshot_url=snapshot_url, tag_manifest=release_manifest(release).decode('utf-8'))
    return evidence


def origin_aliases(origin):
    """Savannah exposes the same project at documented Git protocol/path aliases."""
    parsed = urlsplit(origin)
    project = parsed.path.removeprefix('/git/').removeprefix('/').removesuffix('.git')
    candidates = [origin]
    if '/' in project or not project:
        return candidates
    hosts = [parsed.hostname, *sorted(HOSTS - {parsed.hostname})]
    # Try each canonical HTTPS host first; archive visits differ across aliases.
    for scheme in ('https', 'git', 'http'):
        for path in ('/git/' + project + '.git', '/' + project + '.git', '/git/' + project, '/' + project):
            for host in hosts:
                candidate = scheme + '://' + host + path
                if candidate not in candidates:
                    candidates.append(candidate)
    return candidates


def resolve(origin, kind, reference, run):
    errors = []
    for candidate in origin_aliases(origin):
        try:
            result = resolve_origin(candidate, kind, reference, run)
            result.update(origin=origin, archive_origin=candidate)
            return result
        except (ValueError, subprocess.CalledProcessError) as error:
            errors.append(str(error))
        # Tags can disappear from a later snapshot; inspect up to ten prior full visits.
        if kind == 'tag':
            try:
                visits = request_json(API + 'origin/' + quote(candidate, safe='/:') + '/visits/?per_page=10', run)
                if not isinstance(visits, list):
                    continue
                for visit in visits[:10]:
                    if visit.get('status') != 'full' or not visit.get('snapshot'):
                        continue
                    try:
                        result = resolve_origin(candidate, kind, reference, run, dict(visit, origin=candidate))
                        result.update(origin=origin, archive_origin=candidate)
                        return result
                    except (ValueError, subprocess.CalledProcessError) as error:
                        errors.append(str(error))
            except (ValueError, subprocess.CalledProcessError) as error:
                errors.append(str(error))
    raise ValueError('Software Heritage has no matching exact source across Savannah origin aliases: ' + '; '.join(errors[-3:]))


def release_manifest(release):
    """Recover original tag bytes only when their Git object hash matches the archive."""
    if release.get('raw_manifest'):
        data = base64.b64decode(release['raw_manifest'], validate=True)
    else:
        date = datetime.fromisoformat(release['date'])
        if date.tzinfo is None:
            raise ValueError('archived tag date lacks timezone')
        data = (f"object {release['target']}\ntype commit\ntag {release['name']}\n"
                f"tagger {release['author']['fullname']} {int(date.timestamp())} {date.strftime('%z')}\n\n"
                + release['message']).encode('utf-8')
    actual = hashlib.sha1(b'tag ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()
    if actual != release['id']:
        raise ValueError('archived tag serialization does not reproduce its exact Git object')
    return data


def extract_repository(archive, destination, prefix=None):
    """Extract only regular repository data and discard executable/configuration inputs."""
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if prefix is not None:
            members = [item for item in members if item.name.startswith(prefix.rstrip('/') + '/')]
        roots = {PurePosixPath(item.name).parts[0] for item in members if PurePosixPath(item.name).parts}
        if len(roots) != 1:
            raise ValueError('vault archive must contain one bare repository')
        seen = set()
        for item in members:
            path = PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts or not (item.isdir() or item.isfile()):
                raise ValueError('unsafe vault archive member')
            relative = PurePosixPath(*path.parts[len(PurePosixPath(prefix).parts) if prefix else 1:])
            name = str(relative)
            if name == '.':
                continue
            if name in seen:
                raise ValueError('duplicate vault archive member')
            seen.add(name)
            if (name in ('objects/info/alternates', 'objects/info/http-alternates', 'shallow', 'info/grafts')
                    or name.startswith('refs/replace/') or name.endswith('.promisor')):
                raise ValueError('vault repository is not self-contained')
            if name == 'config' or name.startswith('hooks/'):
                continue
            target = destination / relative
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(item) as incoming, target.open('wb') as outgoing:
                    import shutil
                    shutil.copyfileobj(incoming, outgoing)
    (destination / 'config').write_text('[core]\n\tbare = true\n\trepositoryformatversion = 0\n')


def signature_primary(status, allowed):
    signatures = []
    for line in status.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] != '[GNUPG:]':
            continue
        if fields[1] in ('BADSIG', 'ERRSIG', 'EXPSIG', 'REVKEYSIG', 'NO_PUBKEY'):
            raise ValueError('invalid Savannah tag signature')
        if fields[1] == 'VALIDSIG':
            if len(fields) < 12:
                raise ValueError('incomplete Savannah tag signature evidence')
            signatures.append(fields[11].upper())
    if len(signatures) != 1 or signatures[0] not in allowed:
        raise ValueError('Savannah tag signer is not a full recipe primary fingerprint')
    return signatures[0]


def verify_tag_name(repository, reference, tag, run):
    """Bind named tags to their signed header; raw object selectors bind by hash."""
    header = run('git', '--no-replace-objects', '-C', str(repository), 'cat-file', 'tag', tag).split('\n\n', 1)[0]
    names = [line.removeprefix('tag ') for line in header.splitlines() if line.startswith('tag ')]
    raw_object = re.fullmatch('[0-9a-f]{4,40}', reference) and tag.startswith(reference)
    if len(names) != 1 or (names[0] != reference and not raw_object):
        raise ValueError('Savannah tag name differs from recipe reference')


def verify(repository, kind, reference, env, allowed, run, expected=None):
    ref = 'refs/tags/' + reference if kind == 'tag' else reference
    commit = run('git', '--no-replace-objects', '-C', str(repository), 'rev-parse', '--verify', ref + '^{commit}').strip()
    object_id(commit)
    if (kind == 'commit' and commit != reference) or (expected and commit != expected['commit']):
        raise ValueError('retrieved Git commit differs from exact source evidence')
    evidence = {'commit': commit}
    if kind == 'tag':
        tag = run('git', '-C', str(repository), 'rev-parse', '--verify', ref).strip()
        if expected and tag != expected['tag_object']:
            raise ValueError('retrieved Git tag object differs from snapshot')
        verify_tag_name(repository, reference, tag, run)
        if not env or not env.get('GNUPGHOME'):
            raise ValueError('tag verification requires a disposable verification keyring')
        result = subprocess.run(['git', '-c', 'gpg.program=gpg', '-c', 'gpg.format=openpgp',
                                 '-C', str(repository), 'verify-tag', '--raw', ref],
                                env=env, text=True, capture_output=True, check=False)
        evidence.update(tag_object=object_id(tag), signer=signature_primary(result.stderr, set(allowed)))
        expired = any(line.startswith('[GNUPG:] EXPKEYSIG ') for line in result.stderr.splitlines())
        if result.returncode and not expired:
            result.check_returncode()
        if expired:
            evidence['warnings'] = ['EXPKEYSIG: valid signature from an expired signing key']
    run('git', '-C', str(repository), 'fsck', '--full', '--no-reflogs')
    run('git', '-C', str(repository), 'update-ref', 'refs/heads/emaki-source', commit)
    run('git', '-C', str(repository), 'symbolic-ref', 'HEAD', 'refs/heads/emaki-source')
    return evidence


def fetch(source, downloads, env, allowed_keys, run):
    """Seed makepkg's normal bare mirror; return evidence, or None for another host."""
    parsed = parse(source)
    if parsed is None:
        return None
    origin, name, kind, reference = parsed
    destination = Path(downloads) / name
    mirror = official_mirror(origin)
    evidence = {'source': source, 'origin': origin, 'reference': reference, 'reference_kind': kind}
    with tempfile.TemporaryDirectory(prefix='.savannah-', dir=downloads) as temporary:
        root = Path(temporary)
        repository = root / 'repository'
        run('git', 'init', '--quiet', '--bare', str(repository))
        ref = 'refs/tags/' + reference if kind == 'tag' else reference
        fetched = False
        if mirror:
            try:
                run('git', '-C', str(repository), 'fetch', '--quiet', '--no-tags', mirror['mirror'], ref,
                    timeout=600)
                if kind == 'tag':
                    tag = run('git', '-C', str(repository), 'rev-parse', 'FETCH_HEAD').strip()
                    run('git', '-C', str(repository), 'update-ref', ref, tag)
                evidence.update(verify(repository, kind, reference, env, allowed_keys, run),
                                kind='savannah-official-git', **mirror)
                fetched = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
        if not fetched:
            try:
                run('git', '-C', str(repository), 'fetch', '--quiet', '--no-tags', origin, ref,
                    timeout=60)
                if kind == 'tag':
                    tag = run('git', '-C', str(repository), 'rev-parse', 'FETCH_HEAD').strip()
                    run('git', '-C', str(repository), 'update-ref', ref, tag)
                evidence.update(verify(repository, kind, reference, env, allowed_keys, run),
                                kind='savannah-origin-git')
                fetched = True
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
        if not fetched:
            archived = resolve(origin, kind, reference, run)
            tag_manifest = archived.pop('tag_manifest', None)
            vault = API + 'vault/git-bare/swh:1:rev:' + archived['commit'] + '/'
            ready_vault(vault, env, run)
            archive = root / 'vault.tar'
            requested_url = vault + 'raw/'
            effective_url = run('curl', '-q', '--fail', '--location', '--silent', '--show-error', '--connect-timeout', '15',
                '--user-agent', 'emaki-publish', '--max-time', '600', '--output', str(archive),
                '--write-out', '%{url_effective}', requested_url).strip()
            evidence.update(requested_url=requested_url, effective_url=effective_url)
            import shutil
            shutil.rmtree(repository)
            repository.mkdir()
            extract_repository(archive, repository)
            if kind == 'tag':
                tag_path = root / 'tag-object'
                tag_path.write_bytes(tag_manifest.encode('utf-8'))
                actual = run('git', '-C', str(repository), 'hash-object', '-t', 'tag', '-w', str(tag_path)).strip()
                if actual != archived['tag_object']:
                    raise ValueError('restored tag object differs from archived snapshot')
                run('git', '-C', str(repository), 'update-ref', ref, actual)
            evidence.update(verify(repository, kind, reference, env, allowed_keys, run, archived),
                            kind='savannah-software-heritage', **archived, vault_url=vault)
        run('git', '-C', str(repository), 'config', 'remote.origin.url', origin)
        (repository / 'emaki-source-route.json').write_text(json.dumps(evidence, sort_keys=True) + '\n')
        if destination.exists():
            raise ValueError('refusing to replace an existing Savannah source directory')
        repository.rename(destination)
    return evidence


def prepare(text, downloads, env, run, record=None):
    fields = {}
    for line in text.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            fields.setdefault(key.strip(), []).append(value.strip())
    evidence = []
    for key, sources in fields.items():
        if key == 'source' or key.startswith('source_'):
            for source in sources:
                value = source.split('::', 1)[-1]
                # Reviewed secondary unpinned repositories are resolved by their gitlinks.
                if '#' not in value:
                    continue
                if record is not None:
                    import historical_sources
                    if historical_sources.allows_tag(record, source):
                        continue
                route = fetch(source, downloads, env, fields.get('validpgpkeys', []), run)
                if route:
                    evidence.append(route)
    return evidence


def validate_routes(archive, record, run=None):
    """Bind retained route identities to the self-contained archived Git objects."""
    if run is None:
        def run(*args):
            return subprocess.run(args, check=True, capture_output=True, text=True).stdout
    relevant = [route for route in record.get('source_routes', [])
                if route.get('kind') in ('savannah-official-git', 'savannah-origin-git', 'savannah-software-heritage')]
    if not relevant:
        return
    with tarfile.open(archive) as bundle:
        members = [member for member in bundle.getmembers()
                   if member.name == record['base'] + '/.SRCINFO']
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('Savannah archive lacks one regular recipe .SRCINFO')
        metadata = bundle.extractfile(members[0]).read().decode('utf-8')
    recipe_sources, recipe_keys = set(), set()
    for line in metadata.splitlines():
        key, separator, value = line.strip().partition(' = ')
        if separator:
            if key == 'source' or key.startswith('source_'):
                recipe_sources.add(value)
            elif key == 'validpgpkeys':
                recipe_keys.add(value.upper())
    for route in record.get('source_routes', []):
        if route.get('kind') not in ('savannah-official-git', 'savannah-origin-git', 'savannah-software-heritage'):
            continue
        parsed = parse(route.get('source', ''))
        if parsed is None:
            raise ValueError('Savannah route has no matching recipe source')
        origin, name, kind, reference = parsed
        if (route.get('origin') != origin or route.get('reference') != reference or
                route.get('reference_kind') != kind):
            raise ValueError('Savannah route identity differs from recipe source')
        if (route['source'] not in recipe_sources and
                not (kind == 'commit' and route['source'].removesuffix('#commit=' + reference) in recipe_sources)):
            raise ValueError('Savannah route source differs from archived recipe')
        commit = object_id(route.get('commit'))
        if kind == 'commit' and commit != reference:
            raise ValueError('Savannah route differs from pinned commit')
        pins = record.get('git_sources', [])
        if not any(pin.get('commit') == commit and
                   (pin.get('source') == route['source'] or
                    pin.get('source', '') + '#commit=' + commit == route['source']) for pin in pins):
            raise ValueError('Savannah route lacks a matching retained Git pin')
        if route['kind'] == 'savannah-official-git':
            expected = official_mirror(origin)
            if expected is None or any(route.get(key) != value for key, value in expected.items()):
                raise ValueError('unreviewed Savannah Git mirror')
        elif route['kind'] == 'savannah-software-heritage':
            object_id(route.get('snapshot'))
            if route.get('archive_origin', origin) not in origin_aliases(origin):
                raise ValueError('Savannah archive origin is not a permitted alias')
            if route.get('vault_url') != API + 'vault/git-bare/swh:1:rev:' + commit + '/':
                raise ValueError('Savannah vault differs from exact revision')
        if kind == 'tag':
            object_id(route.get('tag_object'))
            if not re.fullmatch('[A-F0-9]{40,64}', route.get('signer', '')):
                raise ValueError('Savannah tag lacks primary signer evidence')
            if route['signer'] not in recipe_keys:
                raise ValueError('Savannah tag signer differs from archived recipe validpgpkeys')
        with tempfile.TemporaryDirectory(prefix='.verify-savannah-', dir=Path(archive).parent) as temporary:
            repository = Path(temporary)
            extract_repository(archive, repository, record['base'] + '/' + name)
            ref = 'refs/tags/' + reference if kind == 'tag' else reference
            actual = run('git', '--no-replace-objects', '-C', str(repository), 'rev-parse', '--verify', ref + '^{commit}').strip()
            if actual != commit:
                raise ValueError('archived Savannah commit differs from route')
            if kind == 'tag':
                actual = run('git', '-C', str(repository), 'rev-parse', '--verify', ref).strip()
                if actual != route['tag_object']:
                    raise ValueError('archived Savannah tag differs from verified object')
                verify_tag_name(repository, reference, actual, run)
            run('git', '-C', str(repository), 'fsck', '--full', '--no-reflogs')
