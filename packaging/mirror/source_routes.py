#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Narrow, independently verified exceptions for exact source retrieval."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

LIBGCRYPT_RECIPE = '9d1a968ca182af4b0c4e4e1f12ed602c21d78d001a4ccf0a1314d11d516dea04'
LIBGCRYPT_SHA256 = 'd77f68f48879510e79a2f65977ccc68981781ea0923e5bdffac2a193ea3d660e'
LIBGCRYPT_SIGNERS = {
    '6DAA6E64A76D2840571B4902528897B826403ADA',
    'AC8E115BF73E2D8D47FA9908E98E9B2D19C6C8BD',
}
GNU_MIRROR = 'https://ftpmirror.gnu.org/'
GNU_SECONDARY = 'https://mirrors.kernel.org/gnu/'
SAVANNAH_MIRRORS = ('https://mirror.accum.se/mirror/gnu.org/savannah/',
                    'https://mirrors.ocf.berkeley.edu/nongnu/',
                    'https://ftp.cc.uoc.gr/mirrors/nongnu.org/',
                    'https://mirror.rabisu.com/mirrors/savannah/')
SAVANNAH_AUTHORITY = 'https://download.savannah.gnu.org/releases/00_MIRRORS.html'
FREETYPE_AUTHORITY = 'https://freetype.org/download'
# Recovery locations are scoped to unchanged recipes, not entire hosts.
RECOVERY_ROUTES = {
    ('sg3_utils', 'a5b4db13bd364535529f612b05462dc4fd658d81fc17c1e43962cc5ca0159b82'):
        ('https://sg.danny.cz/sg/p/sg3_utils-1.49.tar.xz', (), True),
    ('os-prober', 'ce3f3bbea46f2861446fd05caa1089d4b956abc034baf1984a42af0938496660'):
        ('https://deb.debian.org/debian/pool/main/o/os-prober/os-prober_1.84.tar.xz',
         ('https://snapshot.debian.org/file/5cf57d13184319c8e2548cdf599f6f9cf381eeb3',), False),
}
STRONG_CHECKSUMS = ('b2', 'sha512', 'sha256')


def fields(text):
    result = {}
    for line in text.splitlines():
        if ' = ' in line and not line.startswith('#'):
            key, value = line.split(' = ', 1)
            result.setdefault(key.strip(), []).append(value.strip())
    return result


def source_name(source):
    name = source.split('::', 1)[0] if '::' in source else urlsplit(source).path.rsplit('/', 1)[-1]
    if not name or Path(name).name != name or name in ('.', '..'):
        raise ValueError('unsafe source filename')
    return name


def download(url, target, run, *, relaxed_tls=False):
    temporary = target.with_name(target.name + '.download')
    try:
        effective = run('curl', '--fail', '--location', '--silent', '--show-error',
            '--connect-timeout', '30', '--max-time', '600', '--speed-time', '30',
            '--speed-limit', '1024', '--user-agent', 'emaki-publish',
            '--output', str(temporary), '--write-out', '%{url_effective}',
            *(['--insecure', '--max-redirs', '0'] if relaxed_tls else []), url)
        temporary.replace(target)
        return effective.strip()
    finally:
        temporary.unlink(missing_ok=True)


def verify_checksum(path, algorithm, expected):
    if expected == 'SKIP' or not re.fullmatch('[0-9a-fA-F]+', expected):
        raise ValueError('mirror source requires an independent recipe checksum')
    checksum = hashlib.new('blake2b' if algorithm == 'b2' else algorithm)
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    if checksum.hexdigest() != expected.lower():
        raise ValueError('download differs from the recipe checksum')


def signature_evidence(status, allowed):
    """Require two valid reviewed primaries, independent of signature order."""
    signers = []
    for line in status.splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] == '[GNUPG:]':
            if words[1] in ('BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'NO_PUBKEY'):
                raise ValueError('libgcrypt signature file contains an invalid signature')
            if words[1] == 'VALIDSIG':
                if len(words) != 12:
                    raise ValueError('incomplete GnuPG signature evidence')
                signers.append({'fingerprint': words[2], 'primary': words[11],
                                'timestamp': words[4], 'allowed': words[11] in allowed})
    if len(signers) != 2 or {s['primary'] for s in signers} != LIBGCRYPT_SIGNERS:
        raise ValueError('libgcrypt signature file differs from the reviewed two-signer case')
    if not any(s['allowed'] for s in signers):
        raise ValueError('no valid signature from a recipe validpgpkeys primary')
    return signers


def prepare_libgcrypt(info, record, downloads, env, run):
    if record.get('base') != 'libgcrypt' or record.get('recipe_sha256') != LIBGCRYPT_RECIPE:
        return None
    stem = 'https://gnupg.org/ftp/gcrypt/libgcrypt/libgcrypt-1.12.4.tar.bz2'
    if (info.get('pkgver') != ['1.12.4'] or info.get('pkgrel') != ['1'] or
            info.get('source') != [stem, stem + '.sig'] or
            info.get('sha256sums') != [LIBGCRYPT_SHA256, 'SKIP']):
        raise ValueError('libgcrypt exception metadata differs from reviewed recipe')
    effective = {url: download(url, downloads / source_name(url), run) for url in info['source']}
    payload = downloads / source_name(stem)
    signature = downloads / source_name(stem + '.sig')
    verify_checksum(payload, 'sha256', LIBGCRYPT_SHA256)
    status = run('gpg', '--batch', '--no-auto-key-retrieve', '--status-fd', '1',
                 '--verify', str(signature), str(payload), env=env)
    signers = signature_evidence(status, set(info.get('validpgpkeys', [])))
    return {'kind': 'reviewed-multiple-signature', 'source': stem,
            'sha256': LIBGCRYPT_SHA256, 'signature': stem + '.sig',
            'effective_url': effective[stem], 'signature_effective_url': effective[stem + '.sig'],
            'signature_sha256': hashlib.sha256(signature.read_bytes()).hexdigest(),
            'signers': signers,
            'reason': 'The unchanged signature file has two valid signatures; at least one '
                      'primary is listed in the exact recipe. The later appended signer '
                      'does not invalidate the listed signature over checksum-pinned bytes.'}


def gnu_mirror_url(source):
    url = source.split('::', 1)[-1]
    parsed = urlsplit(url)
    if (parsed.scheme in ('http', 'https', 'ftp') and parsed.hostname == 'ftp.gnu.org'
            and not parsed.query and not parsed.fragment):
        for prefix in ('/gnu/', '/pub/gnu/'):
            if parsed.path.startswith(prefix):
                return GNU_MIRROR + parsed.path[len(prefix):]
    return None


def checksum_mirrors(source):
    """Known official distribution routes; identity still comes from the recipe."""
    mirror = gnu_mirror_url(source)
    if mirror:
        return ([mirror, mirror.replace(GNU_MIRROR, GNU_SECONDARY)],
                'https://www.gnu.org/prep/ftp.html')
    parsed = urlsplit(source.split('::', 1)[-1])
    if (parsed.scheme in ('http', 'https', 'ftp') and
            parsed.hostname in ('download.savannah.gnu.org', 'download.savannah.nongnu.org',
                                'download-mirror.savannah.gnu.org',
                                'download-mirror.savannah.nongnu.org') and
            parsed.path.startswith('/releases/') and not parsed.query and not parsed.fragment):
        relative = parsed.path[len('/releases/'):]
        mirrors = [root + relative for root in SAVANNAH_MIRRORS]
        release = re.fullmatch(r'freetype/(freetype|freetype-doc|ft2demos)-'
                              r'([0-9]+(?:\.[0-9]+)+)\.tar\.(?:xz|gz|bz2)(?:\.sig)?', relative)
        if release:
            section = {'freetype': 'freetype2', 'freetype-doc': 'freetype-docs',
                       'ft2demos': 'freetype-demos'}[release[1]]
            mirrors.append('https://downloads.sourceforge.net/project/freetype/' +
                           section + '/' + release[2] + '/' + relative.split('/')[-1])
        return (mirrors, SAVANNAH_AUTHORITY)
    return ([], None)


def prepare_routes(text, record, downloads, env, run, *, rejected_dir=None, refresh_keys=None):
    """Prefetch original source filenames; preserve recipes and signature bytes."""
    refreshed = False

    def refresh_once():
        nonlocal refreshed
        if refreshed or refresh_keys is None:
            return False
        refreshed = True
        return refresh_keys()

    info = fields(text)
    result = {'makepkg_flags': [], 'evidence': []}
    special = prepare_libgcrypt(info, record, downloads, env, run)
    if special:
        result['makepkg_flags'] = ['--skippgpcheck']
        result['evidence'].append(special)
    result['evidence'].extend(prepare_recovery(info, record, downloads, run, rejected_dir))
    # Independently checksum-pinned payloads qualify. Their detached signatures
    # still pass the unmodified recipe's normal signature verification.
    for suffix in ('', '_x86_64'):
        sources = info.get('source' + suffix, [])
        for index, source in enumerate(sources):
            mirrors, authority = checksum_mirrors(source)
            if not mirrors:
                continue
            pins = [(algorithm, info.get(algorithm + 'sums' + suffix, [])[index])
                    for algorithm in ('b2', 'sha512', 'sha384', 'sha256', 'sha224', 'sha1', 'md5')
                    if len(info.get(algorithm + 'sums' + suffix, [])) == len(sources)
                    and info[algorithm + 'sums' + suffix][index] != 'SKIP']
            # A mirror copy needs at least one strong pin; md5 or sha1 alone never qualifies.
            if not any(algorithm in STRONG_CHECKSUMS for algorithm, _ in pins):
                continue
            target = downloads / source_name(source)
            original = source.split('::', 1)[-1]
            def verify_payload(path):
                for algorithm, expected in pins:
                    verify_checksum(path, algorithm, expected)
            transfer = {}
            used = download_routes([original, *mirrors], target, run, verify=verify_payload,
                                   evidence=transfer, rejected_dir=rejected_dir)
            result['evidence'].append({'kind': 'official-checksum-mirror', 'source': source,
                                       'mirror': used, **transfer, 'checksums': dict(pins),
                                       'authority': (FREETYPE_AUTHORITY if
                                           used.startswith('https://downloads.sourceforge.net/')
                                           else authority)})
            for companion in sources:
                signature_url = companion.split('::', 1)[-1]
                if signature_url in (original + '.sig', original + '.asc', original + '.sign'):
                    sidecar = downloads / source_name(companion)
                    signature_mirror = used + signature_url[len(original):]
                    signature_mirrors, _ = checksum_mirrors(companion)
                    signature_transfer = {}
                    signature_mirror = download_routes(list(dict.fromkeys([
                        signature_mirror, signature_url, *signature_mirrors])), sidecar, run,
                        verify=lambda path: verify_detached(path, target,
                            info.get('validpgpkeys', []), env, run, refresh_keys=refresh_once,
                            evidence=signature_transfer),
                        evidence=signature_transfer, rejected_dir=rejected_dir)
                    result['evidence'].append({
                        'kind': 'detached-signature-mirror', 'source': companion,
                        'mirror': signature_mirror, **signature_transfer, 'payload': source,
                        'verification': 'normal recipe signature verification over checksum-pinned payload'})
    return result


def prepare_recovery(info, record, downloads, run, rejected_dir=None):
    reviewed = RECOVERY_ROUTES.get((record.get('base'), record.get('recipe_sha256')))
    if reviewed is None:
        return []
    original, alternatives, relaxed = reviewed
    sources = info.get('source', [])
    if sources.count(original) != 1:
        raise ValueError('recovery URL differs from the reviewed recipe')
    index = sources.index(original)
    pins = {algorithm: info[algorithm + 'sums'][index]
            for algorithm in STRONG_CHECKSUMS
            if len(info.get(algorithm + 'sums', [])) == len(sources)
            and info[algorithm + 'sums'][index] != 'SKIP'}
    if not pins:
        raise ValueError('source recovery requires a strong recipe checksum')
    def verify(path):
        for algorithm, expected in pins.items():
            verify_checksum(path, algorithm, expected)
    target = downloads / source_name(original)
    transfer = {}
    urls = [original, *alternatives]
    if relaxed:
        urls.append(original)
    used = download_routes(urls, target, run, verify=verify, evidence=transfer,
                           rejected_dir=rejected_dir, relaxed_last=relaxed)
    return [{'kind': 'reviewed-checksum-recovery', 'source': original, 'mirror': used,
             'checksums': pins, **transfer}]


class SignatureKeyUnavailable(ValueError):
    """Missing verification material is not evidence of corrupt mirror bytes."""


def verify_detached(signature, payload, allowed, env, run, *, refresh_keys=None, evidence=None):
    """Check each candidate before accepting it; makepkg still verifies the recipe."""
    for attempt in range(2):
        failure = None
        try:
            status = run('gpg', '--batch', '--no-auto-key-retrieve', '--status-fd', '1',
                         '--verify', str(signature), str(payload), env=env)
        except subprocess.CalledProcessError as error:
            failure = error
            status = (error.stdout or '') + '\n' + (error.stderr or '')
        statuses = [line.split()[1:] for line in status.splitlines()
                    if line.startswith('[GNUPG:] ')]
        codes = {words[0] for words in statuses if words}
        if 'NO_PUBKEY' not in codes:
            break
        if attempt == 0 and refresh_keys is not None:
            try:
                refreshed = refresh_keys()
            except (ValueError, subprocess.CalledProcessError) as error:
                raise SignatureKeyUnavailable('detached signature public key refresh failed') from error
            if refreshed:
                continue
        raise SignatureKeyUnavailable('detached signature public key is unavailable')
    valid = [words[10].upper() for words in statuses if len(words) == 11 and words[0] == 'VALIDSIG']
    if codes & {'BADSIG', 'ERRSIG', 'EXPSIG', 'REVKEYSIG', 'NO_PUBKEY'}:
        raise ValueError('detached signature is invalid')
    if not valid or (allowed and not any(key in allowed for key in valid)):
        raise ValueError('detached signature lacks a valid recipe primary')
    if failure is not None and 'EXPKEYSIG' not in codes:
        raise failure
    if 'EXPKEYSIG' in codes and evidence is not None:
        evidence['warnings'] = ['The signing key has expired.']


def download_routes(urls, target, run, *, verify=None, evidence=None, rejected_dir=None,
                    relaxed_last=False):
    last_error = None
    for index, url in enumerate(urls):
        relaxed = relaxed_last and index == len(urls) - 1
        if relaxed and verify is None:
            raise ValueError('relaxed TLS requires checksum verification')
        try:
            effective = download(url, target, run, relaxed_tls=relaxed)
        except subprocess.CalledProcessError as error:
            last_error = error
            continue
        try:
            if verify:
                verify(target)
        except SignatureKeyUnavailable:
            raise
        except (ValueError, subprocess.CalledProcessError) as error:
            # Failed candidates never remain under the name consumed by makepkg.
            rejected = Path(rejected_dir) if rejected_dir is not None else target.parent / '.rejected'
            rejected.mkdir(parents=True, exist_ok=True)
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            identity = hashlib.sha256(url.encode()).hexdigest()[:16]
            name = target.name + '.' + identity + '.' + digest
            retained = rejected / (name + '.bytes')
            target.replace(retained)
            (rejected / (name + '.json')).write_text(json.dumps({
                'requested_url': url, 'effective_url': effective, 'sha256': digest,
                'file': retained.name, 'reason': str(error)}, sort_keys=True) + '\n')
            last_error = error
            continue
        if evidence is not None:
            evidence['effective_url'] = effective
            if relaxed:
                evidence['tls_verification'] = 'relaxed for exact recipe URL; redirects forbidden'
        return url
    if last_error is not None:
        raise last_error
    raise ValueError('no source download routes')


def git_mirror(source, commit):
    """Return reviewed mirrors of independently pinned commits."""
    if not re.fullmatch('[0-9a-f]{40}', commit or ''):
        return None
    url = source.split('::', 1)[-1].removeprefix('git+').split('#', 1)[0].split('?', 1)[0]
    parsed = urlsplit(url)
    recovered = {
        ('https://git.0pointer.net/clone/libasyncns.git',
         '68cd5aff1467638c086f1bedcc750e34917168e4'):
            'https://github.com/Cor0n4V1rus/libasyncns.git',
        ('https://git.0pointer.net/clone/libcanberra.git',
         'c0620e432650e81062c1967cc669829dbd29b310'):
            'https://github.com/Distrotech/libcanberra.git',
    }.get((url, commit))
    if recovered:
        return {'source': url, 'mirror': recovered, 'kind': 'reviewed-commit-mirror',
                'commit': commit, 'authority': 'Exact commit object pinned by the recipe.'}
    if (parsed.hostname == 'git.savannah.gnu.org' and
            parsed.path.rstrip('/').removesuffix('.git') in ('/git/coreutils', '/coreutils')):
        return {'source': url, 'mirror': 'https://github.com/coreutils/coreutils.git',
                'kind': 'official-commit-mirror', 'commit': commit,
                'authority': 'https://github.com/coreutils/coreutils/blob/master/README'}
    return None


def validate_routes(archive, record):
    """Bind recorded exceptions to the actual archived payload and signature bytes."""
    import tarfile
    import historical_sources
    import savannah_git
    historical_sources.validate(archive, record)
    savannah_git.validate_routes(archive, record)
    evidence = record.get('source_routes', [])
    special = record.get('base') == 'libgcrypt' and record.get('recipe_sha256') == LIBGCRYPT_RECIPE
    if special and len([item for item in evidence if item.get('kind') == 'reviewed-multiple-signature']) != 1:
        raise ValueError('libgcrypt archive lacks reviewed signature evidence')
    if not evidence:
        return
    with tarfile.open(archive, 'r:*') as bundle:
        def member_checksum(source, algorithm):
            name = record['base'] + '/' + source_name(source)
            members = [member for member in bundle.getmembers() if member.name == name]
            if len(members) != 1 or not members[0].isfile():
                raise ValueError('source route evidence lacks one regular archived source')
            checksum = hashlib.new('blake2b' if algorithm == 'b2' else algorithm)
            with bundle.extractfile(members[0]) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    checksum.update(chunk)
            return checksum.hexdigest()
        for item in evidence:
            if item.get('kind') == 'reviewed-multiple-signature':
                if not special or item.get('sha256') != LIBGCRYPT_SHA256:
                    raise ValueError('unreviewed multiple-signature exception')
                signers = item.get('signers', [])
                if (len(signers) != 2 or {s.get('primary') for s in signers} != LIBGCRYPT_SIGNERS
                        or not any(s.get('allowed') and s.get('primary') ==
                                   '6DAA6E64A76D2840571B4902528897B826403ADA' for s in signers)):
                    raise ValueError('missing listed-primary signature evidence')
                if (member_checksum(item['source'], 'sha256') != LIBGCRYPT_SHA256 or
                        member_checksum(item['signature'], 'sha256') != item.get('signature_sha256')):
                    raise ValueError('archived signature exception bytes differ')
            elif item.get('kind') == 'reviewed-checksum-recovery':
                reviewed = RECOVERY_ROUTES.get((record.get('base'), record.get('recipe_sha256')))
                if (reviewed is None or item.get('source') != reviewed[0] or
                        item.get('mirror') not in [reviewed[0], *reviewed[1]] or
                        not item.get('checksums') or
                        not set(item['checksums']).issubset(STRONG_CHECKSUMS) or
                        (item.get('tls_verification') and not reviewed[2])):
                    raise ValueError('unreviewed checksum recovery evidence')
                for algorithm, checksum in item['checksums'].items():
                    if member_checksum(item['source'], algorithm) != checksum:
                        raise ValueError('archived recovery source differs from recipe checksum')
            elif item.get('kind') == 'official-checksum-mirror':
                mirrors, authority = checksum_mirrors(item.get('source', ''))
                original = item['source'].split('::', 1)[-1]
                if (not mirrors or item.get('mirror') not in [original, *mirrors] or
                        not item.get('checksums')):
                    raise ValueError('unreviewed source mirror evidence')
                for algorithm, checksum in item['checksums'].items():
                    if member_checksum(item['source'], algorithm) != checksum:
                        raise ValueError('archived mirror source differs from its checksum')
            elif item.get('kind') == 'detached-signature-mirror':
                if not any(p.get('kind') == 'official-checksum-mirror' and
                           p.get('source') == item.get('payload') for p in evidence):
                    raise ValueError('detached signature mirror lacks pinned payload')
            elif item.get('kind') in ('official-commit-mirror', 'reviewed-commit-mirror'):
                expected = git_mirror(item.get('source', ''), item.get('commit', ''))
                if expected != item:
                    raise ValueError('unreviewed Git mirror evidence')
            elif item.get('kind') == 'arch-source-package':
                import arch_source_fallback
                arch_source_fallback.validate(archive, record)
            elif item.get('kind') in ('software-heritage-tag', 'savannah-official-git',
                                      'savannah-software-heritage', 'savannah-origin-git'):
                pass  # Checked before the optional route list above.
            else:
                raise ValueError('unknown source route evidence')
