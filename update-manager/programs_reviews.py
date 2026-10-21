# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ratings and reviews of catalog programs from the shared open ratings service (ODRS).

GNOME Software and KDE Discover read and write the same service, so a program's stars
here are the ones people gave it there. Emaki runs no review server of its own. AUR
packages are rated by the AUR's own votes and popularity, which the AUR rows carry.

Each command prints one JSON object and never raises: a network failure answers
ok=false with a plain message, so the store goes on without ratings.

  emaki-programs ratings ID...     {ok, ratings: {ID: {average, count, stars}}, stale}
  emaki-programs reviews ID...     {ok, appId, rating, reviews: [...]} for one program
  emaki-programs review JSON       submit {ids, rating 1-5, summary, text, name, version}

A program is looked up only under IDs its own catalog row names: its AppStream ID (with or
without the old '.desktop' ending, which AppStream treats as the same ID) and the IDs its
metadata declares in <provides><id>. A name that merely looks alike is never used: the
service's code.desktop is not elementary Code's io.elementary.code. A review goes only to
the program's own ID.

Reading sends no identity: the service answers a fetch without user_hash and records
nothing for it. Only a submitted review carries this account's random user_hash.
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

import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import secrets
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import zlib

from programs_catalog import clean_text

SERVICE = 'https://odrs.gnome.org/1.0/reviews/api'
# GNOME Software and KDE Discover refresh the whole index at most once a day; so does the service.
RATINGS_TTL = 24 * 3600
REVIEWS_TTL = 3600
TIMEOUT = 15
# The index is about 2 MB (264 KB gzipped) for some 10,000 programs.
MAX_INDEX = 32 * 1024 * 1024
MAX_REVIEWS = 2 * 1024 * 1024
MAX_IDS = 5000
FETCH_LIMIT = 50
# What the service accepts for a review (odrs-web odrs/views_api.py, api_submit).
SUMMARY_MAX = 70
TEXT_MAX = 3000
NAME_MAX = 40
APP_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\Z')
HASH = re.compile(r'[0-9a-f]{40}\Z')
MESSAGES = {
    'offline': _('Ratings could not be loaded. Check your connection.'),
    'no-id': _('This program has no ratings yet.'),
    'invalid': _('This review cannot be sent. Choose the stars and write a title and a few words.'),
    'markup': _('Reviews cannot contain the “<” sign. Remove it and send again.'),
    'send-failed': _('The review could not be sent. Check your connection and try again.'),
    'already': _('You have already reviewed this program.'),
    'refused': _('The ratings service did not accept the review.'),
    'identity': _('The review could not be sent. Its key could not be saved in your home folder.'),
}


def failure(code, **fields):
    return dict(ok=False, code=code, message=MESSAGES[code], **fields)


def cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or str(Path.home() / '.cache')
    return Path(base) / 'emaki-update-manager' / 'odrs'


def state_dir():
    base = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(base) / 'emaki-update-manager'


def write_private(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.' + path.name, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def read_hash(path):
    """The saved identity, or '' when there is none or it is unreadable; reading never fails."""
    try:
        value = path.read_bytes()[:256].decode('ascii').strip()
    except (OSError, ValueError):
        return ''
    return value if HASH.match(value) else ''


def user_hash(state=None, create=True):
    """A random identity made once for this account on this computer.

    ODRS keys one review per program and person by a 40-character user_hash and publishes
    it with every review. GNOME Software and KDE Discover hash the machine ID with the
    user name; a random value is as stable for one installation and leads back to nothing.
    Creating it holds a lock, so two first reviews at once both get the one that is saved.
    Raises OSError when it cannot be saved.
    """
    path = (state or state_dir()) / 'odrs-user'
    value = read_hash(path)
    if value or not create:
        return value
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path.parent / '.odrs-user.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = read_hash(path)
        if not value:
            write_private(path, secrets.token_hex(20).encode())
            value = read_hash(path)
    if not value:
        raise OSError(_('The review identity could not be saved.'))
    return value


def distro():
    try:
        return platform.freedesktop_os_release().get('NAME') or 'Emaki'
    except OSError:
        return 'Emaki'


def user_locale():
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        value = os.environ.get(name, '')
        if value:
            value = value.split('.')[0].split('@')[0]
            return 'en_US' if value in ('C', 'POSIX') else value
    return 'en_US'


def clean_version(value):
    """The upstream version, as GNOME Software sends it: no epoch, no package release."""
    value = ' '.join(str(value or '').split())
    value = value.split(':', 1)[1] if re.match(r'\d+:', value) else value
    value = value.rsplit('-', 1)[0] if re.search(r'-\d+(\.\d+)?\Z', value) else value
    return value[:64] or 'unknown'


def request(path, body=None, limit=MAX_REVIEWS, opener=urlopen):
    data = None if body is None else json.dumps(body).encode()
    headers = {'User-Agent': 'Emaki-programs', 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
    if data is not None:
        headers['Content-Type'] = 'application/json; charset=utf-8'
    with opener(Request(SERVICE + path, data=data, headers=headers), timeout=TIMEOUT) as response:
        if not str(getattr(response, 'url', SERVICE)).startswith('https://'):
            raise ValueError(_('The ratings service redirected to an insecure address.'))
        raw = response.read(limit + 1)
        encoding = (response.headers.get('Content-Encoding') or '').lower() if response.headers else ''
    if len(raw) > limit:
        raise ValueError(_('The ratings service returned too much data.'))
    if encoding == 'gzip':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as unpacked:
            raw = unpacked.read(limit + 1)
        if len(raw) > limit:
            raise ValueError(_('The ratings service returned too much data.'))
    return json.loads(raw)


def summarize(entry):
    """ODRS counts reviews per star (star1..star5 for 20..100; star0 is unrated)."""
    if not isinstance(entry, dict):
        return None
    stars = []
    for number in range(1, 6):
        value = entry.get('star%d' % number, 0)
        stars.append(value if type(value) is int and value >= 0 else 0)
    count = sum(stars)
    if not count:
        return None
    average = sum(number * value for number, value in zip(range(1, 6), stars)) / count
    return dict(average=round(average, 2), count=count, stars=stars)


def valid_id(value):
    return isinstance(value, str) and bool(APP_ID.match(value))


def candidates(app_id, aliases=()):
    """The ODRS keys of one program: its AppStream ID, the same ID with or without the old
    '.desktop' ending, and the IDs its own metadata provides, exactly as written (GNOME
    Software sends those as compat_ids). Nothing is guessed from the name."""
    base = app_id[:-8] if app_id.endswith('.desktop') else app_id
    names = []
    for name in [app_id, base, base + '.desktop'] + [alias for alias in aliases if valid_id(alias)][:16]:
        if name not in names:
            names.append(name)
    return names


def best(index, app_id, aliases=()):
    """(key, rating) of this program's best-counted key; separate keys are never added, they can overlap."""
    found = [(name, summarize(index.get(name))) for name in candidates(app_id, aliases)]
    found = [item for item in found if item[1]]
    return max(found, key=lambda item: item[1]['count']) if found else (app_id, None)


def rating_for(index, app_id, aliases=()):
    return best(index, app_id, aliases)[1]


def ratings_index(cache=None, now=None, opener=urlopen):
    """The whole index from the user's cache, refreshed when older than a day.

    Returns (index, stale). A failed refresh keeps the older copy; with no copy it raises OSError.
    """
    path = (cache or cache_dir()) / 'ratings.json'
    now = time.time() if now is None else now
    cached = None
    stamp = 0
    try:
        stamp = path.stat().st_mtime
        cached = json.loads(path.read_bytes())
        if not isinstance(cached, dict):
            cached = None
    except (OSError, ValueError):
        cached = None
    if cached is not None and 0 <= now - stamp < RATINGS_TTL:
        return cached, False
    try:
        index = request('/ratings', limit=MAX_INDEX, opener=opener)
        if not isinstance(index, dict):
            raise ValueError(_('The ratings index is not an object.'))
        write_private(path, json.dumps(index, separators=(',', ':')).encode())
        return index, False
    except (OSError, ValueError, EOFError, zlib.error) as error:
        if cached is not None:
            return cached, True
        raise OSError(_('The ratings index could not be read.')) from error


def ratings(ids, cache=None, opener=urlopen):
    ids = [value for value in ids if valid_id(value)][:MAX_IDS]
    try:
        index, stale = ratings_index(cache, opener=opener)
    except OSError:
        return failure('offline')
    found = {}
    for app_id in ids:
        value = rating_for(index, app_id)
        if value:
            found[app_id] = value
    return dict(ok=True, ratings=found, stale=stale)


def clean_review(raw, me=''):
    """A review as the window shows it; placeholders and unrated items are dropped."""
    if not isinstance(raw, dict):
        return None
    rating = raw.get('rating')
    if type(rating) is not int or not 0 < rating <= 100:
        return None
    # KDE Discover's rule (OdrsReviewsJob.cpp): a review readers voted down twice as often as up is hidden.
    up, down = raw.get('karma_up'), raw.get('karma_down')
    if type(up) is int and type(down) is int and up < 2 * down:
        return None

    # Third-party text goes through the catalog's display sanitizer: no direction overrides or
    # other invisible controls that could reorder the name, date and version shown beside it.
    def line(name, limit):
        value = raw.get(name)
        return ' '.join(clean_text(value).split())[:limit] if isinstance(value, str) else ''

    summary = line('summary', SUMMARY_MAX * 2)
    text = raw.get('description')
    text = clean_text(text).strip()[:TEXT_MAX * 2] if isinstance(text, str) else ''
    if not summary and not text:
        return None
    created = raw.get('date_created')
    author = raw.get('user_hash') if isinstance(raw.get('user_hash'), str) else ''
    return dict(id=raw['review_id'] if type(raw.get('review_id')) is int else 0,
                stars=max(1, min(5, round(rating / 20))),
                summary=summary, text=text, name=line('user_display', NAME_MAX * 2),
                version=line('version', 64),
                date=int(created) if isinstance(created, (int, float)) and created > 0 else 0,
                mine=bool(me) and author == me, author=author)


def reviews(ids, cache=None, state=None, now=None, opener=urlopen):
    """Reviews for one program; ids are the AppStream IDs its catalog rows name (each edition's
    own ID and the IDs it provides), the program's own ID first."""
    ids = [value for value in ids if valid_id(value)][:32]
    if not ids:
        return failure('no-id')
    names = candidates(ids[0], ids[1:])
    cache = cache or cache_dir()
    now = time.time() if now is None else now
    path = cache / ('reviews-' + hashlib.sha256('\n'.join(names).encode()).hexdigest()[:32] + '.json')
    try:
        if 0 <= now - path.stat().st_mtime < REVIEWS_TTL:
            cached = json.loads(path.read_bytes())
            if isinstance(cached, dict) and cached.get('ok') is True:
                return cached
    except (OSError, ValueError):
        pass
    try:
        index, _unused = ratings_index(cache, now=now, opener=opener)
    except OSError:
        index = {}
    main, rating = best(index, ids[0], ids[1:])
    # No user_hash: an anonymous fetch creates no user record on the service.
    body = dict(app_id=main, locale=user_locale(), distro=distro(), version='unknown',
                limit=FETCH_LIMIT, compat_ids=[name for name in names if name != main])
    try:
        answer = request('/fetch', body, opener=opener)
        if not isinstance(answer, list):
            raise ValueError(_('The reviews answer is not a list.'))
    except (OSError, ValueError, EOFError, zlib.error):
        return failure('offline', appId=main, rating=rating, reviews=[])
    me = user_hash(state, create=False)
    found, authors = [], set()
    for raw in answer:
        review = clean_review(raw, me)
        # One review per person, as GNOME Software shows them: the service can return the
        # same person's review under two IDs of one program.
        if review and (not review['author'] or review['author'] not in authors):
            authors.add(review['author'])
            found.append(review)
    for review in found:
        del review['author']
    result = dict(ok=True, appId=main, rating=rating, reviews=found,
                  reviewed=any(review['mine'] for review in found))
    try:
        write_private(path, json.dumps(result).encode())
    except OSError:
        pass
    return result


def submission(form, state=None):
    """The exact body ODRS takes, or a failure code when the form is not a valid review.

    The review goes to ids[0], the program's own AppStream ID, and to no other key."""
    if not isinstance(form, dict):
        return 'invalid'
    ids = form.get('ids')
    stars = form.get('rating')
    if (not isinstance(ids, list) or not ids or not all(valid_id(value) for value in ids)
            or type(stars) is not int or not 1 <= stars <= 5):
        return 'invalid'
    summary = ' '.join(str(form.get('summary') or '').split())
    text = str(form.get('text') or '').strip()
    name = ' '.join(str(form.get('name') or '').split())[:NAME_MAX]
    # The service drops a trailing full stop and needs two characters left.
    if not 2 <= len(summary.rstrip('.')) or len(summary) > SUMMARY_MAX or not 2 <= len(text) <= TEXT_MAX:
        return 'invalid'
    if '<' in summary or '<' in text:
        return 'markup'
    try:
        identity = user_hash(state)
    except OSError:
        return 'identity'
    return dict(user_hash=identity, app_id=ids[0], locale=user_locale(), distro=distro(),
                version=clean_version(form.get('version')), user_display=name or 'Anonymous',
                summary=summary, description=text, rating=stars * 20)


def submit(form, cache=None, state=None, opener=urlopen):
    body = submission(form, state)
    if isinstance(body, str):
        return failure(body)
    try:
        answer = request('/submit', body, opener=opener)
    except OSError as error:
        # The service answers a refused review with HTTP 400 and {"success": false, "msg"}.
        answer = None
        reply = getattr(error, 'fp', None)
        if getattr(error, 'code', None) == 400 and reply is not None:
            try:
                answer = json.loads(reply.read(65536))
            except (OSError, ValueError):
                answer = None
        if not isinstance(answer, dict):
            return failure('send-failed')
    except (ValueError, EOFError, zlib.error):
        return failure('send-failed')
    if not isinstance(answer, dict) or answer.get('success') is not True:
        message = answer.get('msg') if isinstance(answer, dict) else ''
        return failure('already' if isinstance(message, str) and 'already reviewed' in message else 'refused')
    # The next read shows the new review instead of the hour-old copy.
    for path in (cache or cache_dir()).glob('reviews-*.json'):
        try:
            path.unlink()
        except OSError:
            pass
    return dict(ok=True, reviewId=answer.get('review_id') if type(answer.get('review_id')) is int else 0)


def main(argv, opener=urlopen):
    command, arguments = (argv[0], argv[1:]) if argv else ('', [])
    arguments = [value for value in arguments if value != '--json']
    if command == 'ratings':
        answer = ratings(arguments, opener=opener)
    elif command == 'reviews':
        answer = reviews(arguments, opener=opener)
    elif command == 'review' and len(arguments) == 1:
        try:
            form = json.loads(arguments[0])
        except ValueError:
            form = None
        answer = submit(form, opener=opener)
    else:
        print(_('Usage: emaki-programs ratings ID... | reviews ID... | review JSON'), file=sys.stderr)
        return 2
    print(json.dumps(answer, ensure_ascii=True))
    return 0 if answer.get('ok') else 1


class Annotator:
    """Puts {average, count} on catalog rows from the cached index; never waits on the network."""
    def __init__(self, cache=None):
        self.cache = cache
        self.stamp = None
        self.index = {}

    def current(self):
        path = (self.cache or cache_dir()) / 'ratings.json'
        try:
            stamp = path.stat().st_mtime_ns
            if stamp != self.stamp:
                index = json.loads(path.read_bytes())
                self.index, self.stamp = (index if isinstance(index, dict) else {}), stamp
        except (OSError, ValueError):
            self.index, self.stamp = {}, None
        return self.index

    def annotate(self, rows):
        index = self.current()
        for row in rows:
            if row.get('kind') in ('arch', 'flatpak') and not row.get('packageOnly') and valid_id(row.get('id')):
                aliases = row.get('providedIds')
                value = rating_for(index, row['id'], aliases if isinstance(aliases, list) else ())
                row['rating'] = dict(average=value['average'], count=value['count']) if value else None
        return rows


def refresh_in_background(cache=None, opener=urlopen):
    """Bring the index up to date beside the engine's service; rows read it on their next request."""
    def work():
        try:
            ratings_index(cache, opener=opener)
        except Exception:
            # Ratings are an extra: a failure here never reaches the store.
            pass
    thread = threading.Thread(target=work, name='odrs-ratings', daemon=True)
    thread.start()
    return thread
