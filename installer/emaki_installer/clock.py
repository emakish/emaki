# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the live clock before asking the package manager to verify signatures."""
from email.utils import parsedate_to_datetime
from functools import partial
from http.client import HTTPException
import math
from pathlib import Path
from ssl import SSLCertVerificationError
import tarfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .errors import Code, InstallError

CLOCK_TOLERANCE = 300
HTTP_TIMEOUT = 5
TIME_URLS = ('https://archlinux.org/', 'https://www.kernel.org/')


def time_urls(root='/'):
    """Prefer the configured HTTPS mirror, then independent HTTPS hosts."""
    urls = []
    try:
        lines = (Path(root) / 'etc/pacman.d/mirrorlist').read_text().splitlines()
        for line in lines:
            key, separator, value = line.partition('#')[0].partition('=')
            if key.strip() != 'Server' or not separator:
                continue
            mirror = urlsplit(value.strip())
            if mirror.scheme == 'https' and mirror.hostname:
                # Request the origin; mirror paths contain pacman substitutions.
                urls.append(f'https://{mirror.netloc}/')
                break
    except (OSError, UnicodeError, ValueError):
        pass
    for url in TIME_URLS:
        if urlsplit(url).hostname not in {urlsplit(item).hostname for item in urls}:
            urls.append(url)
    return urls


def http_date(url):
    """A certificate-checked HTTPS response; never relax TLS for a broken clock."""
    request = Request(url, method='HEAD', headers={'Cache-Control': 'no-cache'})
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Time source did not use HTTPS.')
        value = parsedate_to_datetime(response.headers['Date'])
        if value.tzinfo is None:
            raise ValueError('Time source omitted the time zone.')
        return value.timestamp()


def newest_build_date(repo, *, root='/'):
    """Read only bounded package descriptions; do not extract repository archives."""
    if repo is None:
        return None
    path = Path(repo)
    if path.is_absolute():
        path = Path(root) / path.relative_to('/')
    else:
        path = Path(root) / path
    newest = None
    for database in sorted(path.glob('*.db')):
        try:
            with tarfile.open(database, 'r:*') as archive:
                for member in archive:
                    if not member.isfile() or Path(member.name).name != 'desc' or member.size > 131072:
                        continue
                    stream = archive.extractfile(member)
                    if stream is None:
                        continue
                    with stream:
                        lines = stream.read(131073).decode('utf-8', errors='replace').splitlines()
                    for index, line in enumerate(lines[:-1]):
                        if line == '%BUILDDATE%':
                            try:
                                stamp = int(lines[index + 1])
                                # Reject nonsensical metadata rather than overflowing date formatting.
                                if 0 < stamp < 253402300799:
                                    newest = max(newest or 0, stamp)
                            except ValueError:
                                pass
        except (OSError, tarfile.TarError, EOFError):
            continue
    return newest


def _reference(sources):
    date_error = False
    for source in sources:
        try:
            stamp = float(source())
            if math.isfinite(stamp) and 0 < stamp < 253402300799:
                return stamp, False
        except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            # OpenSSL: certificate not yet valid (9), certificate expired (10).
            if isinstance(reason, SSLCertVerificationError) and getattr(reason, 'verify_code', None) in (9, 10):
                date_error = True
    return None, date_error


def _set_clock(runner, stamp, now):
    """Change the live session only and preserve its automatic time setting."""
    changed = False
    try:
        automatic = runner.run(['timedatectl', 'show', '--property=NTP', '--value'],
                               timeout=10).strip() == 'yes'
        try:
            if automatic:
                runner.run(['timedatectl', 'set-ntp', 'false'], timeout=10)
            runner.run(['date', '-u', '-s', f'@{stamp}'], timeout=10)
            changed = abs(now() - stamp) <= CLOCK_TOLERANCE
        finally:
            if automatic:
                runner.run(['timedatectl', 'set-ntp', 'true'], timeout=10)
    except (InstallError, OSError, ValueError, OverflowError) as exc:
        raise InstallError(Code.CLOCK_SKEW,
                           'The computer clock could not be corrected. Set the date and time in '
                           'the live session, then try again. Your disk has not been changed.') from exc
    if not changed:
        raise InstallError(Code.CLOCK_SKEW,
                           'The computer clock is still incorrect. Set the date and time in '
                           'the live session, then try again. Your disk has not been changed.')


def _sync_clock(runner, now, monotonic, sleep):
    """Give the live time service a bounded chance to repair a TLS-blocking clock."""
    start = monotonic()
    before = now()
    automatic = None
    try:
        setting = runner.run(['timedatectl', 'show', '--property=NTP', '--value'],
                             timeout=5).strip()
        if setting not in ('yes', 'no'):
            return False
        automatic = setting == 'yes'
        runner.run(['timedatectl', 'set-ntp', 'true'], timeout=5)
        while monotonic() - start < 10:
            remaining = 10 - (monotonic() - start)
            if remaining <= 0:
                break
            synchronized = runner.run(
                ['timedatectl', 'show', '--property=NTPSynchronized', '--value'],
                timeout=min(5, remaining)).strip()
            if synchronized == 'yes':
                break
            remaining = 10 - (monotonic() - start)
            if remaining > 0:
                sleep(min(1, remaining))
    except (InstallError, OSError):
        # A missing or unreachable time service is not proof that the clock is wrong.
        pass
    finally:
        if automatic is False:
            try:
                runner.run(['timedatectl', 'set-ntp', 'false'], timeout=5)
            except (InstallError, OSError) as exc:
                raise InstallError(Code.CLOCK_SKEW,
                                   'The automatic clock setting could not be restored '
                                   '(network time). Check the date and time in the live '
                                   'session, then try again. Your disk has not been changed.') from exc
    elapsed = monotonic() - start
    return abs(now() - before - elapsed) > CLOCK_TOLERANCE


def ensure_clock(runner, *, online, repo=None, root='/', now=time.time,
                 source=None, notice=lambda message: None,
                 monotonic=time.monotonic, sleep=time.sleep):
    """Correct known skew; return whether corrected. Offline dates are a lower bound.

    Call before repository synchronization as that step also verifies signatures.
    Inject the time reader, HTTPS source, root, and runner for fixture-only tests.
    Only certificate-date failures justify network-time recovery when all HTTPS
    sources fail. An unreachable host is not evidence that the clock is wrong.
    """
    sources = ()
    if online:
        sources = (source,) if source is not None else tuple(partial(http_date, url) for url in time_urls(root))
    reference, date_error = _reference(sources) if online else (None, False)
    lower_bound = newest_build_date(repo, root=root) if reference is None else None
    corrected = False
    if reference is None and lower_bound is not None and now() < lower_bound:
        _set_clock(runner, lower_bound + CLOCK_TOLERANCE, now)
        corrected = True
        if online:
            reference, date_error = _reference(sources)
    if online and reference is None and date_error:
        corrected = _sync_clock(runner, now, monotonic, sleep) or corrected
        reference, _ = _reference(sources)
    if reference is not None and abs(now() - reference) > CLOCK_TOLERANCE:
        _set_clock(runner, reference, now)
        corrected = True
    if corrected:
        notice('The computer clock was corrected for this live session so package signatures '
               'can be checked.')
    elif online and reference is None and date_error:
        notice('The current time could not be checked online. If package signatures fail, '
               'check the date and time in the live session.')
    return corrected
