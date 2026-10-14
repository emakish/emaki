#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ratings and reviews from ODRS: recorded answers, request shapes, cache, failures. No network.

tests/odrs/ratings.json is a slice of the live /ratings index and fetch-krita.json a live
/fetch answer (2026-10-13), with reviewer names, hashes and texts replaced.
"""
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import programs_reviews as reviews  # noqa: E402

RATINGS = (HERE / 'odrs/ratings.json').read_bytes()
FETCH = (HERE / 'odrs/fetch-krita.json').read_bytes()


class Response:
    def __init__(self, body, url, encoding=''):
        self.body = io.BytesIO(body)
        self.url = url
        self.headers = {'Content-Encoding': encoding} if encoding else {}

    def read(self, limit=-1):
        return self.body.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Service:
    """Answers like odrs.gnome.org from recordings and keeps every request it got."""
    def __init__(self, answers=None, gzip_index=False):
        self.answers = {'/ratings': RATINGS, '/fetch': FETCH,
                        '/submit': json.dumps(dict(success=True, review_id=684001, msg='ok')).encode()}
        self.answers.update(answers or {})
        self.gzip_index = gzip_index
        self.requests = []

    def __call__(self, request, timeout=None):
        path = request.full_url[len(reviews.SERVICE):]
        assert request.full_url.startswith('https://odrs.gnome.org/1.0/reviews/api/'), request.full_url
        assert timeout == reviews.TIMEOUT
        body = json.loads(request.data) if request.data else None
        self.requests.append((path, body, dict(request.header_items())))
        answer = self.answers[path]
        if isinstance(answer, BaseException):
            raise answer
        if path == '/ratings' and self.gzip_index:
            return Response(gzip.compress(answer), request.full_url, 'gzip')
        return Response(answer, request.full_url)

    def paths(self):
        return [path for path, _, _ in self.requests]


class ReviewTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='programs-reviews-')
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'cache'
        self.state = Path(self.tmp.name) / 'state'
        environment = patch.dict(os.environ, {'LANG': 'de_DE.UTF-8', 'LC_ALL': '', 'LC_MESSAGES': ''})
        environment.start()
        self.addCleanup(environment.stop)
        release = patch.object(reviews.platform, 'freedesktop_os_release', return_value={'NAME': 'Emaki'})
        release.start()
        self.addCleanup(release.stop)


class RatingsTests(ReviewTestCase):
    def test_index_maps_appstream_and_desktop_ids(self):
        service = Service()
        answer = reviews.ratings(['org.kde.krita', 'org.mozilla.firefox', 'org.gimp.GIMP', 'no.such.App',
                                  'bad id', '../escape'], cache=self.cache, opener=service)
        self.assertTrue(answer['ok'])
        # The larger of org.kde.krita (495) and org.kde.krita.desktop (12), never their sum.
        self.assertEqual(answer['ratings']['org.kde.krita'],
                         dict(average=3.78, count=495, stars=[92, 32, 35, 70, 266]))
        # Firefox's reviews sit under firefox.desktop only, a name the bare ID does not reach:
        # only the catalog row's own <provides><id> can name it.
        self.assertNotIn('org.mozilla.firefox', answer['ratings'])
        index = json.loads(RATINGS)
        self.assertEqual(reviews.rating_for(index, 'org.mozilla.firefox', ['firefox.desktop'])['count'], 628)
        self.assertEqual(answer['ratings']['org.gimp.GIMP']['count'], 774)
        self.assertNotIn('no.such.App', answer['ratings'])
        self.assertEqual(set(answer['ratings']), {'org.kde.krita', 'org.gimp.GIMP'})
        self.assertEqual(service.paths(), ['/ratings'])
        path, body, headers = service.requests[0]
        self.assertIsNone(body)
        self.assertEqual(headers.get('Accept-encoding'), 'gzip')

    def test_gzip_index_is_unpacked(self):
        answer = reviews.ratings(['org.videolan.VLC'], cache=self.cache, opener=Service(gzip_index=True))
        self.assertEqual(answer['ratings']['org.videolan.VLC']['count'], 1426)

    def test_index_is_cached_for_a_day(self):
        service = Service()
        reviews.ratings(['org.kde.krita'], cache=self.cache, opener=service)
        reviews.ratings(['org.kde.krita'], cache=self.cache, opener=service)
        self.assertEqual(service.paths(), ['/ratings'])
        path = self.cache / 'ratings.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        old = path.stat().st_mtime - reviews.RATINGS_TTL - 5
        os.utime(path, (old, old))
        reviews.ratings(['org.kde.krita'], cache=self.cache, opener=service)
        self.assertEqual(service.paths(), ['/ratings', '/ratings'])

    def test_stale_index_survives_a_failed_refresh(self):
        reviews.ratings(['org.kde.krita'], cache=self.cache, opener=Service())
        path = self.cache / 'ratings.json'
        old = path.stat().st_mtime - reviews.RATINGS_TTL - 5
        os.utime(path, (old, old))
        answer = reviews.ratings(['org.kde.krita'], cache=self.cache,
                                 opener=Service({'/ratings': URLError('offline')}))
        self.assertTrue(answer['ok'])
        self.assertTrue(answer['stale'])
        self.assertEqual(answer['ratings']['org.kde.krita']['count'], 495)

    def test_failures_without_a_copy_answer_plainly(self):
        for broken in (URLError('offline'), TimeoutError('slow'), b'<html>maintenance</html>', b'[]',
                       gzip.compress(b'{"truncated":')[:-6]):
            with self.subTest(broken=broken):
                answer = reviews.ratings(['org.kde.krita'], cache=self.cache / str(id(broken)),
                                         opener=Service({'/ratings': broken}))
                self.assertEqual(answer, dict(ok=False, code='offline', message=reviews.MESSAGES['offline']))

    def test_oversized_index_is_refused(self):
        with patch.object(reviews, 'MAX_INDEX', 100):
            answer = reviews.ratings(['org.kde.krita'], cache=self.cache, opener=Service())
        self.assertFalse(answer['ok'])

    def test_insecure_redirect_is_refused(self):
        def redirected(request, timeout=None):
            return Response(RATINGS, 'http://odrs.gnome.org/1.0/reviews/api/ratings')
        self.assertFalse(reviews.ratings(['org.kde.krita'], cache=self.cache, opener=redirected)['ok'])


class FetchTests(ReviewTestCase):
    def test_fetch_is_anonymous_and_names_every_id(self):
        service = Service()
        answer = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state, opener=service)
        self.assertTrue(answer['ok'])
        self.assertEqual(service.paths(), ['/ratings', '/fetch'])
        body = service.requests[1][1]
        self.assertEqual(body, dict(app_id='org.kde.krita', locale='de_DE', distro='Emaki', version='unknown',
                                    limit=50, compat_ids=['org.kde.krita.desktop']))
        self.assertNotIn('user_hash', body)
        # Reading creates no identity.
        self.assertFalse((self.state / 'odrs-user').exists())
        self.assertEqual(answer['appId'], 'org.kde.krita')
        self.assertEqual(answer['rating']['count'], 495)
        self.assertEqual(len(answer['reviews']), 4)
        first = answer['reviews'][0]
        self.assertEqual(sorted(first), ['date', 'id', 'mine', 'name', 'stars', 'summary', 'text', 'version'])
        self.assertEqual((first['stars'], first['name'], first['summary'], first['mine']),
                         (2, 'Reviewer 1', 'Slow to start', False))
        self.assertGreater(first['date'], 1700000000)

    def test_the_best_counted_edition_is_the_main_id(self):
        service = Service()
        answer = reviews.reviews(['org.kde.krita.desktop', 'org.kde.krita'], cache=self.cache,
                                 state=self.state, opener=service)
        self.assertEqual(answer['appId'], 'org.kde.krita')
        self.assertEqual(service.requests[1][1]['app_id'], 'org.kde.krita')
        self.assertIn('org.kde.krita.desktop', service.requests[1][1]['compat_ids'])

    def test_a_look_alike_name_is_never_mixed_in(self):
        # Live index entries, 2026-10-13: elementary Code has 13 ratings; code.desktop (1,277) is another program.
        code = dict(star0=0, star1=4, star2=1, star3=0, star4=4, star5=4, total=13)
        other = dict(star0=0, star1=295, star2=118, star3=140, star4=99, star5=625, total=1277)
        index = json.dumps({'io.elementary.code': code, 'code.desktop': other}).encode()
        service = Service({'/ratings': index})
        self.assertEqual(reviews.ratings(['io.elementary.code'], cache=self.cache, opener=service)['ratings'],
                         {'io.elementary.code': dict(average=3.23, count=13, stars=[4, 1, 0, 4, 4])})
        answer = reviews.reviews(['io.elementary.code'], cache=self.cache, state=self.state, opener=service)
        self.assertEqual((answer['appId'], answer['rating']['count']), ('io.elementary.code', 13))
        self.assertEqual(service.requests[-1][1]['compat_ids'], ['io.elementary.code.desktop'])
        # With only the look-alike key in the index the program has no rating and keeps its own ID.
        cache = Path(self.tmp.name) / 'other-cache'
        service = Service({'/ratings': json.dumps({'code.desktop': other}).encode()})
        self.assertEqual(reviews.ratings(['io.elementary.code'], cache=cache, opener=service)['ratings'], {})
        answer = reviews.reviews(['io.elementary.code'], cache=cache, state=self.state, opener=service)
        self.assertEqual((answer['appId'], answer['rating']), ('io.elementary.code', None))
        self.assertEqual(service.requests[-1][1]['app_id'], 'io.elementary.code')
        self.assertNotIn('code.desktop', service.requests[-1][1]['compat_ids'])

    def test_ids_the_row_provides_are_used(self):
        # The page passes each edition's ID and the IDs its metadata provides.
        service = Service()
        answer = reviews.reviews(['org.mozilla.firefox', 'firefox.desktop'], cache=self.cache,
                                 state=self.state, opener=service)
        self.assertEqual((answer['appId'], answer['rating']['count']), ('firefox.desktop', 628))
        self.assertEqual(service.requests[-1][1]['compat_ids'],
                         ['org.mozilla.firefox', 'org.mozilla.firefox.desktop'])

    def test_direction_and_control_characters_are_removed(self):
        items = json.loads(FETCH)[:1]
        items[0].update(user_display='‮Sam⁦', summary='Good‪ one\x1b[31m',
                        description='Line‏ one\nLine two⁩ \U0001F469‍\U0001F3A8', version='5.2‮')
        answer = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state,
                                 opener=Service({'/fetch': json.dumps(items).encode()}))
        review = answer['reviews'][0]
        self.assertEqual((review['name'], review['summary'], review['version']), ('Sam', 'Good one[31m', '5.2'))
        # Line breaks and emoji joiners stay, as in catalog text.
        self.assertEqual(review['text'], 'Line one\nLine two \U0001F469‍\U0001F3A8')

    def test_placeholders_unrated_downvoted_and_repeated_authors_are_dropped(self):
        items = json.loads(FETCH)
        mine = reviews.user_hash(self.state)
        items[2]['user_hash'] = mine
        items += [
            # The service's placeholder for an identified reader with no reviews.
            dict(score=0, app_id='org.kde.krita', user_hash='a' * 40, user_skey='b' * 40),
            dict(items[0], review_id=1, rating=0),
            dict(items[0], review_id=2, user_hash='c' * 40, karma_up=1, karma_down=1),
            dict(items[0], review_id=3, summary='Same person, other ID'),
            dict(items[0], review_id=4, user_hash='d' * 40, summary='', description=' '),
            'not an object',
        ]
        answer = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state,
                                 opener=Service({'/fetch': json.dumps(items).encode()}))
        self.assertEqual([review['id'] for review in answer['reviews']],
                         [review['review_id'] for review in json.loads(FETCH)])
        self.assertEqual([review['mine'] for review in answer['reviews']], [False, False, True, False])
        self.assertTrue(answer['reviewed'])

    def test_reviews_are_cached_for_an_hour(self):
        service = Service()
        first = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state, opener=service)
        second = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state, opener=service)
        self.assertEqual(first, second)
        self.assertEqual(service.paths(), ['/ratings', '/fetch'])
        later = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state, opener=service,
                                now=__import__('time').time() + reviews.REVIEWS_TTL + 5)
        self.assertEqual(later['reviews'], first['reviews'])
        self.assertEqual(service.paths(), ['/ratings', '/fetch', '/fetch'])

    def test_offline_fetch_keeps_the_stars_it_has(self):
        reviews.ratings(['org.kde.krita'], cache=self.cache, opener=Service())
        answer = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state,
                                 opener=Service({'/fetch': URLError('offline')}))
        self.assertFalse(answer['ok'])
        self.assertEqual(answer['message'], reviews.MESSAGES['offline'])
        self.assertEqual(answer['rating']['count'], 495)
        self.assertEqual(answer['reviews'], [])
        # A failure is never cached.
        self.assertEqual(list(self.cache.glob('reviews-*.json')), [])

    def test_offline_everything(self):
        answer = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state,
                                 opener=Service({'/ratings': URLError('x'), '/fetch': URLError('x')}))
        self.assertEqual((answer['ok'], answer['rating']), (False, None))

    def test_no_usable_id(self):
        self.assertEqual(reviews.reviews(['', 'two words'], cache=self.cache, opener=Service())['code'], 'no-id')


class SubmitTests(ReviewTestCase):
    FORM = dict(ids=['org.kde.krita', 'org.kde.krita.desktop'], rating=4, summary='  Good  for drawing ',
                text='Works with my tablet.\n\nStarts slowly.', name=' Sam ', version='1:5.2.13-1')

    def test_payload_shape(self):
        service = Service()
        answer = reviews.submit(dict(self.FORM), cache=self.cache, state=self.state, opener=service)
        self.assertEqual(answer, dict(ok=True, reviewId=684001))
        path, body, headers = service.requests[0]
        self.assertEqual(path, '/submit')
        identity = (self.state / 'odrs-user').read_text()
        self.assertRegex(identity, r'\A[0-9a-f]{40}\Z')
        self.assertEqual((self.state / 'odrs-user').stat().st_mode & 0o777, 0o600)
        self.assertEqual(body, dict(user_hash=identity, app_id='org.kde.krita', locale='de_DE', distro='Emaki',
                                    version='5.2.13', user_display='Sam', summary='Good for drawing',
                                    description='Works with my tablet.\n\nStarts slowly.', rating=80))
        self.assertEqual(headers['Content-type'], 'application/json; charset=utf-8')

    def test_identity_is_stable_and_random(self):
        first = reviews.user_hash(self.state)
        self.assertEqual(reviews.user_hash(self.state), first)
        other = reviews.user_hash(Path(self.tmp.name) / 'other')
        self.assertNotEqual(first, other)
        # Nothing about the machine or the person goes into it.
        machine = Path('/etc/machine-id')
        if machine.exists():
            self.assertNotIn(machine.read_text().strip()[:12], first)

    def test_anonymous_name_and_bare_version(self):
        body = reviews.submission(dict(self.FORM, name='', version=''), self.state)
        self.assertEqual((body['user_display'], body['version']), ('Anonymous', 'unknown'))
        self.assertEqual(reviews.clean_version('5.2.13'), '5.2.13')
        self.assertEqual(reviews.clean_version('2:1.0rc1-3'), '1.0rc1')

    def test_invalid_forms_send_nothing(self):
        service = Service()
        cases = [None, [], dict(self.FORM, rating=0), dict(self.FORM, rating=6), dict(self.FORM, rating=4.5),
                 dict(self.FORM, rating=True), dict(self.FORM, ids=[]), dict(self.FORM, ids=['bad id']),
                 dict(self.FORM, summary=''), dict(self.FORM, summary='.'), dict(self.FORM, summary='x' * 71),
                 dict(self.FORM, text=''), dict(self.FORM, text='x' * 3001)]
        for form in cases:
            with self.subTest(form=form):
                self.assertEqual(reviews.submit(form, cache=self.cache, state=self.state, opener=service)['code'],
                                 'invalid')
        for field in ('summary', 'text'):
            self.assertEqual(reviews.submit(dict(self.FORM, **{field: 'a <b> c'}), cache=self.cache,
                                            state=self.state, opener=service)['code'], 'markup')
        self.assertEqual(service.requests, [])
        self.assertFalse((self.state / 'odrs-user').exists())

    def test_service_refusals(self):
        def refusal(message):
            return HTTPError(reviews.SERVICE + '/submit', 400, 'Bad Request', {},
                             io.BytesIO(json.dumps(dict(success=False, msg=message)).encode()))
        answer = reviews.submit(dict(self.FORM), cache=self.cache, state=self.state,
                                opener=Service({'/submit': refusal('already reviewed this app')}))
        self.assertEqual(answer['code'], 'already')
        answer = reviews.submit(dict(self.FORM), cache=self.cache, state=self.state,
                                opener=Service({'/submit': refusal('account has been disabled due to abuse')}))
        self.assertEqual(answer['code'], 'refused')
        for broken in (URLError('offline'), HTTPError(reviews.SERVICE, 502, 'Bad Gateway', {}, io.BytesIO(b'')),
                       b'not json'):
            answer = reviews.submit(dict(self.FORM), cache=self.cache, state=self.state,
                                    opener=Service({'/submit': broken}))
            self.assertEqual(answer['code'], 'send-failed')
        answer = reviews.submit(dict(self.FORM), cache=self.cache, state=self.state,
                                opener=Service({'/submit': b'{"success": false}'}))
        self.assertEqual(answer['code'], 'refused')

    def test_a_sent_review_clears_the_reviews_cache(self):
        reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state, opener=Service())
        self.assertEqual(len(list(self.cache.glob('reviews-*.json'))), 1)
        reviews.submit(dict(self.FORM), cache=self.cache, state=self.state, opener=Service())
        self.assertEqual(list(self.cache.glob('reviews-*.json')), [])
        self.assertTrue((self.cache / 'ratings.json').exists())

    def test_a_review_goes_only_to_the_programs_own_id(self):
        body = reviews.submission(dict(self.FORM, ids=['io.elementary.code']), self.state)
        self.assertEqual(body['app_id'], 'io.elementary.code')

    def test_first_reviews_at_once_share_the_saved_identity(self):
        write, gate = reviews.write_private, threading.Barrier(6)

        def slow_write(path, data):
            # Widen the window between reading "no identity" and saving one.
            time.sleep(0.05)
            write(path, data)
        found = []

        def first():
            gate.wait()
            found.append(reviews.user_hash(self.state))
        with patch.object(reviews, 'write_private', slow_write):
            threads = [threading.Thread(target=first) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(len(found), 6)
        self.assertEqual(set(found), {(self.state / 'odrs-user').read_text()})

    def test_identity_failures_answer_in_json_and_reads_never_fail(self):
        # A state folder that cannot be made: the review is refused plainly and nothing is sent.
        blocked = Path(self.tmp.name) / 'file'
        blocked.write_text('')
        service = Service()
        answer = reviews.submit(dict(self.FORM), cache=self.cache, state=blocked / 'state', opener=service)
        self.assertEqual((answer['ok'], answer['code']), (False, 'identity'))
        self.assertEqual(answer['message'], reviews.MESSAGES['identity'])
        self.assertEqual(service.requests, [])
        # A damaged identity file: reading reviews still works and marks nothing as mine.
        self.state.mkdir(parents=True)
        (self.state / 'odrs-user').write_bytes(b'\xff\xfe' + b'a' * 38)
        answer = reviews.reviews(['org.kde.krita'], cache=self.cache, state=self.state, opener=Service())
        self.assertTrue(answer['ok'])
        self.assertFalse(any(review['mine'] for review in answer['reviews']))
        self.assertEqual(reviews.user_hash(self.state, create=False), '')
        # A review then saves a fresh identity in its place.
        body = reviews.submission(dict(self.FORM), self.state)
        self.assertEqual(body['user_hash'], (self.state / 'odrs-user').read_text())


class EngineTests(unittest.TestCase):
    """Catalog rows carry {average, count} from the cached index; nothing waits on the network."""
    def setUp(self):
        import test_programs_engine as base
        self.base = base
        base.EngineTests.setUp(self)

    def index(self, value):
        path = Path(self.tmp.name) / 'odrs/ratings.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_rows_carry_ratings_from_the_cache(self):
        result = self.engine.catalog()
        self.assertEqual({row['key']: row['rating'] for row in result['programs']},
                         {self.base.ARCH_KEY: None, self.base.FP_KEY: None})
        path = self.index({'org.example.Paint.desktop': dict(star1=1, star5=3, total=4)})
        result = self.engine.catalog()
        self.assertEqual([row['rating'] for row in result['programs']], [dict(average=4.0, count=4)] * 2)
        self.assertEqual(self.engine.details(self.base.ARCH_KEY)['program']['rating'], dict(average=4.0, count=4))
        # A newer index is read on the next request without reloading the catalog.
        path.write_text(json.dumps({'org.example.Paint': dict(star3=2, total=2)}))
        os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
        self.assertEqual(self.engine.catalog()['programs'][0]['rating'], dict(average=3.0, count=2))
        path.write_text('not json')
        os.utime(path, ns=(path.stat().st_mtime_ns + 2 * 10**9,) * 2)
        self.assertIsNone(self.engine.catalog()['programs'][0]['rating'])

    def test_rows_read_only_the_ids_they_provide(self):
        self.index({'firefox.desktop': dict(star4=5, total=5)})
        annotator = reviews.Annotator(Path(self.tmp.name) / 'odrs')
        rows = [dict(kind='arch', id='org.mozilla.firefox'),
                dict(kind='flatpak', id='org.mozilla.firefox', providedIds=['firefox.desktop'])]
        self.assertEqual([row['rating'] for row in annotator.annotate(rows)], [None, dict(average=4.0, count=5)])

    def test_package_rows_get_no_rating(self):
        self.index({'paint': dict(star5=9, total=9)})
        rows = self.engine.catalog(scope='packages')['programs']
        self.assertTrue(rows)
        self.assertTrue(all('rating' not in row for row in rows))

    def test_background_refresh_writes_the_cache_and_swallows_failures(self):
        cache = Path(self.tmp.name) / 'odrs'
        reviews.refresh_in_background(cache, opener=Service()).join(10)
        self.assertTrue((cache / 'ratings.json').exists())
        broken = Path(self.tmp.name) / 'broken'
        reviews.refresh_in_background(broken, opener=Service({'/ratings': URLError('offline')})).join(10)
        self.assertFalse((broken / 'ratings.json').exists())


class CommandTests(unittest.TestCase):
    def run_cli(self, *args, home):
        environment = dict(os.environ, XDG_CACHE_HOME=str(home / 'cache'), XDG_STATE_HOME=str(home / 'state'),
                           HOME=str(home))
        return subprocess.run([sys.executable, str(HERE.parent / 'programs.py'), *args], env=environment,
                              capture_output=True, text=True, timeout=60)

    def test_cli_answers_json_from_its_cache_and_refuses_bad_use(self):
        with tempfile.TemporaryDirectory(prefix='programs-reviews-cli-') as directory:
            home = Path(directory)
            cache = home / 'cache/emaki-update-manager/odrs'
            cache.mkdir(parents=True)
            (cache / 'ratings.json').write_bytes(RATINGS)
            result = self.run_cli('ratings', 'org.kde.krita', 'no.such.App', '--json', home=home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['ratings']['org.kde.krita']['count'], 495)
            # An invalid review is refused before any network use.
            result = self.run_cli('review', json.dumps(dict(ids=['org.kde.krita'], rating=9)), '--json', home=home)
            self.assertEqual((result.returncode, json.loads(result.stdout)['code']), (1, 'invalid'))
            result = self.run_cli('review', home=home)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((home / 'state').exists())
            # An identity that cannot be saved is a JSON answer, not a traceback.
            (home / 'state').write_text('')
            form = dict(ids=['org.kde.krita'], rating=4, summary='Good', text='Works well.')
            result = self.run_cli('review', json.dumps(form), '--json', home=home)
            self.assertEqual((result.returncode, json.loads(result.stdout)['code']), (1, 'identity'))
            self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main(verbosity=1)
