# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Website profile creation is isolated and never launches a browser."""

from email.message import Message
import base64
import io
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from gi.repository import GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_webapps as web

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII=')


class WebsiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.fetch = patch.object(web, '_metadata', return_value=('Example', None, False)).start()
        self.addCleanup(patch.stopall)

    def create(self, url='https://example.org/path?q=1', name=''):
        return web.create_app(url, name, self.home)

    def test_list_does_not_create_or_fetch(self):
        self.assertEqual(web.list_apps(self.home), [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.fetch.assert_not_called()

    def test_create_profile_and_identity_then_remove(self):
        app = self.create()
        directory = self.home / 'emaki/webapps' / app['id']
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        entry = desktop.read_text()
        self.assertNotIn('--new-instance', entry)
        self.assertIn('"--name" "' + desktop.stem + '"', entry)
        self.assertIn('"--class" "' + desktop.stem + '"', entry)
        self.assertIn('StartupWMClass=' + desktop.stem, entry)
        self.assertIn('"--profile" "' + str(directory / 'profile') + '"', entry)
        self.assertIn('toolkit.legacyUserProfileCustomizations.stylesheets', (directory / 'profile/user.js').read_text())
        self.assertIn(':focus-within', (directory / 'profile/chrome/userChrome.css').read_text())
        self.assertEqual(web.list_apps(self.home), [app])
        unrelated = self.home / 'applications/keep.desktop'
        unrelated.write_text('keep')
        web.remove_app(app['id'], self.home)
        self.assertFalse(desktop.exists())
        self.assertFalse(directory.exists())
        self.assertEqual(unrelated.read_text(), 'keep')

    def test_distinct_profiles(self):
        first, second = self.create(), self.create('https://second.example.org/')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(web.list_apps(self.home)), 2)

    def test_duplicate_add_reuses_profile_without_fetch(self):
        first = self.create()
        self.fetch.reset_mock()
        self.assertEqual(self.create(), first)
        self.assertEqual(len(web.list_apps(self.home)), 1)
        self.fetch.assert_not_called()

    def test_fallback_icon_has_escaped_initial_and_rose_accent(self):
        app = self.create(name='<Example')
        directory = self.home / 'emaki/webapps' / app['id']
        icon = directory / 'icon.svg'
        tree = ET.fromstring(icon.read_text())
        self.assertEqual(tree.find('{http://www.w3.org/2000/svg}text').text, '<')
        self.assertEqual(tree.find('{http://www.w3.org/2000/svg}rect').get('fill'), '#e780aa')
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        self.assertIn('Icon=' + str(icon), desktop.read_text())

    def test_renamed_launcher_is_still_removable(self):
        app = self.create()
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        desktop.write_text(desktop.read_text().replace('Name=Example', 'Name=Personal name'))
        web.remove_app(app['id'], self.home)
        self.assertFalse(desktop.exists())
        self.assertEqual(web.list_apps(self.home), [])

    def test_missing_profile_is_still_removable(self):
        app = self.create()
        directory = self.home / 'emaki/webapps' / app['id']
        shutil.rmtree(directory / 'profile')
        web.remove_app(app['id'], self.home)
        self.assertFalse(directory.exists())

    def test_modified_profile_argument_refuses_removal(self):
        app = self.create()
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        desktop.write_text(desktop.read_text().replace('"--profile"', '"--other"'))
        with self.assertRaisesRegex(web.WebAppError, 'website-modified'):
            web.remove_app(app['id'], self.home)

    def test_nonregular_launcher_refuses_removal(self):
        app = self.create()
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        desktop.unlink()
        desktop.mkdir()
        with self.assertRaisesRegex(web.WebAppError, 'website-modified'):
            web.remove_app(app['id'], self.home)

    def test_url_validation(self):
        for value in ('file:///tmp/file', 'javascript:alert(1)', 'ftp://example.org',
                      'https://', '--help', 'https://user:pass@example.org',
                      'https://example.org\nExec=evil', 'https://example.org/ a',
                      'https://example.org\\evil', 'https://[bad]',
                      'https://example.org:99999', None):
            with self.subTest(value=value), self.assertRaises(web.WebAppError):
                self.create(value)
        self.fetch.assert_not_called()
        for value in ('http://localhost:8080/', 'https://example.org/?q=%20',
                      'https://[::1]/', 'https://例子.测试/'):
            self.assertEqual(web.validate_url(value), value)

    def test_exec_quoting_and_name_cannot_inject_keys(self):
        url = 'https://example.org/?q="$HOME`cmd`%20&x=1'
        app = self.create(url, 'Site\nExec=bad\\text')
        entry = (self.home / 'applications' / (app['id'] + '.desktop')).read_text()
        self.assertEqual(sum(line.startswith('Exec=') for line in entry.splitlines()), 1)
        command = next(line[5:] for line in entry.splitlines() if line.startswith('Exec='))
        # The desktop parser first unescapes backslashes, then tokenizes Exec.
        _, argv = GLib.shell_parse_argv(command.replace('\\\\', '\\'))
        self.assertEqual(argv[-1].replace('%%', '%'), url)
        self.assertEqual(argv[0], 'firefox')

    def test_metadata_fallback(self):
        self.fetch.return_value = ('', None, True)
        app = self.create()
        self.assertEqual(app['name'], 'example.org')
        self.assertTrue(app['metadataWarning'])

    def test_embedded_icon(self):
        self.fetch.return_value = ('Example', PNG, False)
        app = self.create()
        directory = self.home / 'emaki/webapps' / app['id']
        self.assertEqual((directory / 'icon.png').read_bytes(), PNG)

    def test_modified_launcher_refused(self):
        app = self.create()
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        desktop.write_text('personal edit')
        with self.assertRaisesRegex(web.WebAppError, 'website-modified'):
            web.remove_app(app['id'], self.home)
        self.assertEqual(desktop.read_text(), 'personal edit')

    def test_removal_ignores_paths_in_manifest(self):
        app = self.create()
        directory = self.home / 'emaki/webapps' / app['id']
        record = json.loads((directory / 'app.json').read_text())
        outside = self.home / 'keep'
        outside.mkdir()
        (outside / 'private').write_text('keep')
        record['profile'] = str(outside)
        (directory / 'app.json').write_text(json.dumps(record))
        (directory / 'profile/link').symlink_to(outside, target_is_directory=True)
        web.remove_app(app['id'], self.home)
        self.assertEqual((outside / 'private').read_text(), 'keep')

    def test_symlink_launcher_refused(self):
        app = self.create()
        desktop = self.home / 'applications' / (app['id'] + '.desktop')
        desktop.unlink()
        target = self.home / 'keep'
        target.write_text('keep')
        desktop.symlink_to(target)
        with self.assertRaisesRegex(web.WebAppError, 'website-unsafe-path'):
            web.remove_app(app['id'], self.home)
        self.assertEqual(target.read_text(), 'keep')

    def test_parent_symlink_refused_before_fetch(self):
        target = self.home / 'elsewhere'
        target.mkdir()
        (self.home / 'applications').symlink_to(target, target_is_directory=True)
        with self.assertRaises(web.WebAppError):
            self.create()
        self.fetch.assert_not_called()

    def test_invalid_id_cannot_traverse(self):
        for value in ('../../keep', '/tmp/file', '', 'org.emaki.webapp.' + 'g' * 32):
            with self.subTest(value=value), self.assertRaises(web.WebAppError):
                web.remove_app(value, self.home)

    def test_running_profile_lock_refused(self):
        app = self.create()
        lock = self.home / 'emaki/webapps' / app['id'] / 'profile/.parentlock'
        script = ('import fcntl,sys; f=open(sys.argv[1],"w"); '
                  'fcntl.lockf(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()')
        process = subprocess.Popen([sys.executable, '-c', script, str(lock)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with self.assertRaisesRegex(web.WebAppError, 'website-running'):
                web.remove_app(app['id'], self.home)
        finally:
            process.communicate('', timeout=5)
        web.remove_app(app['id'], self.home)


class MetadataTests(unittest.TestCase):
    @staticmethod
    def response(data, content_type='text/html'):
        response = io.BytesIO(data)
        response.headers = Message()
        response.headers['Content-Type'] = content_type
        return response

    def test_relative_site_icon_is_one_additional_bounded_request(self):
        page = self.response(b'<title>Example</title><link rel="icon" href="/icon.png">')
        png = PNG
        with patch.object(web, 'build_opener') as opener:
            opener.return_value.open.side_effect = [page, self.response(png, 'image/png')]
            self.assertEqual(web._metadata('https://example.org/path'), ('Example', png, False))
            self.assertEqual(opener.return_value.open.call_count, 2)
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url,
                             'https://example.org/icon.png')

    def test_unsafe_icon_urls_never_requested(self):
        for url in ('file:///tmp/icon.png', 'ftp://example.org/icon.png',
                    'https://user:password@example.org/icon.png', 'https://elsewhere/icon.png'):
            with self.subTest(url=url), patch.object(web, 'build_opener') as opener:
                opener.return_value.open.return_value = self.response(
                    ('<link rel="icon" href="' + url + '">').encode())
                self.assertEqual(web._metadata('https://example.org'), ('', None, False))
                opener.return_value.open.assert_called_once()

    def test_oversized_icon_rejected(self):
        page = self.response(b'<title>Example</title><link rel="icon" href="/icon.png">')
        with patch.object(web, 'build_opener') as opener:
            opener.return_value.open.side_effect = [page, self.response(
                b'\x89PNG\r\n\x1a\n' + b'x' * (128 * 1024), 'image/png')]
            self.assertEqual(web._metadata('https://example.org'), ('Example', None, False))
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_small_file_with_huge_png_dimensions_rejected(self):
        raw = PNG[:16] + (65535).to_bytes(4, 'big') * 2 + PNG[24:]
        self.assertIsNone(web._raster_icon(raw))

    def test_truncated_raster_signature_is_not_an_icon(self):
        for raw in (b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'\x00\x00\x01\x00'):
            with self.subTest(raw=raw):
                self.assertIsNone(web._raster_icon(raw))

    def test_ico_payload_dimensions_must_match_directory(self):
        header = b'\x00\x00\x01\x00\x01\x00'
        entry = b'\x01\x01\x00\x00\x01\x00\x20\x00' + len(PNG).to_bytes(4, 'little') + (22).to_bytes(4, 'little')
        icon = header + entry + PNG
        self.assertEqual(web._raster_icon(icon), icon)
        huge = PNG[:16] + (65535).to_bytes(4, 'big') * 2 + PNG[24:]
        self.assertIsNone(web._raster_icon(header + entry + huge))

    def test_jpeg_frame_dimensions_are_bounded(self):
        small = b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xd9'
        self.assertEqual(web._raster_icon(small), small)
        self.assertIsNone(web._raster_icon(small[:7] + b'\xff\xff\xff\xff' + small[11:]))

    def test_icon_failure_keeps_page_title_and_never_retries(self):
        page = self.response(b'<title>Example</title><link rel="icon" href="/icon.png">')
        with patch.object(web, 'build_opener') as opener:
            opener.return_value.open.side_effect = [page, OSError('offline')]
            self.assertEqual(web._metadata('https://example.org'), ('Example', None, False))
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_slow_body_stops_at_total_deadline(self):
        class Trickle(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 1))

            read1 = read

        response = Trickle(b'<title>Slow</title>')
        response.headers = Message()
        response.headers['Content-Type'] = 'text/html'
        with patch.object(web, 'build_opener') as opener, patch.object(web, 'time', create=True) as clock:
            clock.monotonic.side_effect = range(0, 100, 3)
            opener.return_value.open.return_value = response
            self.assertEqual(web._metadata('https://example.org'), ('', None, True))

    def test_single_request_no_external_icon(self):
        response = io.BytesIO(b'<title>A &amp; B</title><link rel="icon" href="https://other/icon.png">')
        response.headers = Message()
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        with patch.object(web, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            title, icon, warning = web._metadata('https://example.org')
            self.assertEqual(title, 'A & B')
            self.assertIsNone(icon)
            self.assertFalse(warning)
            opener.return_value.open.assert_called_once()
            self.assertIsInstance(opener.call_args.args[0], web._NoRedirect)

    def test_redirect_handler_does_not_follow(self):
        self.assertIsNone(web._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other'))

    def test_network_failure_returns_fallback(self):
        with patch.object(web, 'build_opener') as opener:
            opener.return_value.open.side_effect = OSError('offline')
            self.assertEqual(web._metadata('https://example.org'), ('', None, True))

    def test_large_response_bounded(self):
        response = io.BytesIO(b'x' * (web._LIMIT + 2))
        with patch.object(web, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(web._metadata('https://example.org'), ('', None, True))


if __name__ == '__main__':
    unittest.main()
