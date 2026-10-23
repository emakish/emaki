# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicitly requested website shortcuts in private, independent Firefox profiles.

No background networking: create_app requests the page and optionally one same-site
icon. Redirects are not followed; the two requests share a bounded read deadline.
Errors expose protocol codes; the calling interface supplies translated messages.
"""

import base64
import configparser
import fcntl
import hashlib
from html import escape
from html.parser import HTMLParser
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import time
import uuid
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class WebAppError(ValueError):
    pass


_ID = re.compile(r"org\.emaki\.webapp\.[a-f0-9]{32}\Z")
_LIMIT = 1024 * 1024
_ICON_LIMIT = 128 * 1024
_PREFS = '''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.page", 0);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.tabs.warnOnClose", true);
'''
_CSS = '''/* Copyright (C) 2026 Artur Yakymenko
 * SPDX-License-Identifier: GPL-3.0-or-later */
/* Ctrl+L or the top edge reveals navigation; popups retain their controls. */
:root:not([chromehidden]) #navigator-toolbox:not(:focus-within):not(:hover) {
  max-height: 2px !important;
  overflow: hidden !important;
}
:root:not([chromehidden]) #navigator-toolbox:focus-within,
:root:not([chromehidden]) #navigator-toolbox:hover {
  max-height: none !important;
}
'''


def validate_url(url):
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise WebAppError('website-invalid-url')
    if any(ord(c) < 33 or ord(c) == 127 for c in url) or '\\' in url:
        raise WebAppError('website-invalid-url')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError
        parsed.port
        parsed.hostname.encode('idna')
    except (ValueError, UnicodeError):
        raise WebAppError('website-invalid-url') from None
    return url


def _text(value):
    clean = ''.join(c if c.isprintable() else ' ' for c in str(value))
    return ' '.join(clean.split())[:160]


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title = ''
        self.icon = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self.in_title = True
        if tag == 'link' and 'icon' in attrs.get('rel', '').lower().split():
            self.icon = attrs.get('href', '')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data[:1024]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _read_bounded(response, limit, deadline):
    data = bytearray()
    while True:
        remaining = _remaining(deadline)
        # HTTPResponse.read1 does at most one underlying read. Reduce that socket's
        # timeout as the shared deadline approaches, including on trickling bodies.
        sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if sock is not None:
            sock.settimeout(remaining)
        chunk = response.read1(min(16384, limit + 1 - len(data)))
        _remaining(deadline)
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError('response-too-large')
        if not chunk:
            return bytes(data)


def _image_dimensions(raw):
    # Check dimensions before handing compressed bytes to launcher image decoders.
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(raw) >= 33 and raw[8:16] == b'\x00\x00\x00\rIHDR':
            return int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
    elif raw.startswith(b'\xff\xd8\xff'):
        offset = 2
        while offset + 4 <= len(raw) and raw[offset] == 255:
            marker = raw[offset + 1]
            if marker == 255:
                offset += 1
                continue
            length = int.from_bytes(raw[offset + 2:offset + 4], 'big')
            if length < 2 or offset + 2 + length > len(raw):
                break
            if marker in (0xc0, 0xc1, 0xc2):
                if length >= 8:
                    return (int.from_bytes(raw[offset + 7:offset + 9], 'big'),
                            int.from_bytes(raw[offset + 5:offset + 7], 'big'))
                break
            if marker == 0xda:
                break
            offset += length + 2
    return 0, 0


def _bounded_dimensions(dimensions):
    width, height = dimensions
    return 0 < width <= 1024 and 0 < height <= 1024


def _raster_icon(raw):
    if len(raw) > _ICON_LIMIT:
        return None
    if raw.startswith(b'\x00\x00\x01\x00'):
        count = int.from_bytes(raw[4:6], 'little')
        if not 0 < count <= 64 or len(raw) < 6 + count * 16:
            return None
        for index in range(count):
            entry = raw[6 + index * 16:22 + index * 16]
            size = int.from_bytes(entry[8:12], 'little')
            offset = int.from_bytes(entry[12:16], 'little')
            if offset < 6 + count * 16 or size < 16 or offset + size > len(raw):
                return None
            payload = raw[offset:offset + size]
            if payload.startswith(b'\x89PNG\r\n\x1a\n'):
                dimensions = _image_dimensions(payload)
            elif len(payload) >= 40 and int.from_bytes(payload[:4], 'little') >= 40:
                dimensions = (int.from_bytes(payload[4:8], 'little'),
                              int.from_bytes(payload[8:12], 'little') // 2)
            else:
                return None
            if dimensions != (entry[0] or 256, entry[1] or 256):
                return None
        return raw
    return raw if _bounded_dimensions(_image_dimensions(raw)) else None


def _metadata(url):
    page = _Page()
    deadline = time.monotonic() + 10
    opener = build_opener(_NoRedirect())
    try:
        request = Request(url, headers={'User-Agent': 'Emaki-Website-Apps/1.0',
                                       'Accept': 'text/html'})
        with opener.open(request, timeout=_remaining(deadline)) as response:
            data = _read_bounded(response, _LIMIT, deadline)
            if response.headers.get_content_type() != 'text/html':
                return '', None, True
            page.feed(data.decode(response.headers.get_content_charset() or 'utf-8', 'replace'))
    except (OSError, ValueError, LookupError, HTTPException):
        return '', None, True
    icon = None
    match = re.fullmatch(r'data:image/(png|jpeg|x-icon|vnd.microsoft.icon);base64,([A-Za-z0-9+/=\s]+)', page.icon)
    if match:
        try:
            raw = base64.b64decode(match[2], validate=False)
            icon = _raster_icon(raw)
        except ValueError:
            pass
    elif page.icon:
        try:
            icon_url = validate_url(urljoin(url, page.icon))
            # A page cannot opt the person into contacting another site's server.
            if urlsplit(icon_url).hostname == urlsplit(url).hostname:
                request = Request(icon_url, headers={'User-Agent': 'Emaki-Website-Apps/1.0',
                                                    'Accept': 'image/png,image/x-icon,image/jpeg'})
                with opener.open(request, timeout=_remaining(deadline)) as response:
                    raw = _read_bounded(response, _ICON_LIMIT, deadline)
                    if response.headers.get_content_type() in (
                            'image/png', 'image/jpeg', 'image/x-icon', 'image/vnd.microsoft.icon'):
                        icon = _raster_icon(raw)
        except (OSError, ValueError, LookupError, HTTPException):
            pass
    return _text(page.title), icon, False


def _fallback_icon(name):
    # Use the shell's Rose base/text tokens for a local initial when no icon loads.
    initial = escape(name[:1].upper()[:1])
    return ('<!-- Copyright (C) 2026 Artur Yakymenko -->\n'
            '<!-- SPDX-License-Identifier: GPL-3.0-or-later -->\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">'
            '<rect width="128" height="128" rx="28" fill="#e780aa"/>'
            '<text x="64" y="88" text-anchor="middle" font-family="sans-serif" '
            'font-size="72" font-weight="600" fill="#300c1c">' + initial + '</text></svg>\n')


def _data_home(data_home=None):
    path = Path(data_home or os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    if not path.is_absolute() or any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise WebAppError('website-unsafe-path')
    return path


def _safe(path):
    # Refuse symlinks anywhere in managed paths, including an XDG directory override.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise WebAppError('website-unsafe-path')


def _exec_arg(value):
    # Desktop Entry strings are unescaped before Exec quoting is interpreted.
    value = str(value).replace('%', '%%')
    value = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    return ('"' + value + '"').replace('\\', '\\\\')


def _entry(app_id, name, url, profile, icon):
    command = ' '.join(_exec_arg(arg) for arg in
                       ['firefox', '--profile', profile,
                        '--name', app_id, '--class', app_id, url])
    # Names cannot create new Desktop Entry keys; percent is only special in Exec.
    name = _text(name).replace('\\', '\\\\')
    icon = str(icon).replace('\\', '\\\\')
    return ('[Desktop Entry]\nType=Application\nVersion=1.0\n'
            f'Name={name}\nExec={command}\nIcon={icon}\n'
            f'StartupWMClass={app_id}\nTerminal=false\nCategories=Network;\n')


def list_apps(data_home=None):
    home = _data_home(data_home)
    root = home / 'emaki/webapps'
    _safe(root)
    if not root.exists():
        return []
    result = []
    for child in sorted(root.iterdir()):
        if not _ID.fullmatch(child.name):
            continue
        try:
            record = _record(home, child.name)
            result.append({key: record[key] for key in ('id', 'name', 'url', 'metadataWarning')})
        except (WebAppError, OSError, ValueError, KeyError):
            continue
    return result


def _record(home, app_id):
    if not isinstance(app_id, str) or not _ID.fullmatch(app_id):
        raise WebAppError('website-invalid-id')
    root = home / 'emaki/webapps' / app_id
    _safe(root / 'app.json')
    try:
        record = json.loads((root / 'app.json').read_text())
    except (OSError, ValueError):
        raise WebAppError('website-not-found') from None
    if not isinstance(record, dict) or record.get('id') != app_id or record.get('format') != 1:
        raise WebAppError('website-not-found')
    return record


def create_app(url, name='', data_home=None):
    validate_url(url)
    if not isinstance(name, str):
        raise WebAppError('website-invalid-name')
    home = _data_home(data_home)
    root = home / 'emaki/webapps'
    applications = home / 'applications'
    _safe(root)
    _safe(applications)
    for existing in list_apps(home):
        if existing['url'] == url:
            return existing
    title, icon, warning = _metadata(url)
    name = _text(name) or title or urlsplit(url).hostname
    app_id = 'org.emaki.webapp.' + uuid.uuid4().hex
    directory = root / app_id
    desktop = applications / (app_id + '.desktop')
    directory.mkdir(parents=True, mode=0o700)
    desktop_created = False
    try:
        profile = directory / 'profile'
        (profile / 'chrome').mkdir(parents=True, mode=0o700)
        (profile / 'user.js').write_text(_PREFS)
        (profile / 'chrome/userChrome.css').write_text(_CSS)
        if icon:
            suffix = 'png' if icon.startswith(b'\x89PNG') else 'jpg' if icon.startswith(b'\xff\xd8') else 'ico'
            icon_path = directory / ('icon.' + suffix)
            icon_path.write_bytes(icon)
        else:
            icon_path = directory / 'icon.svg'
            icon_path.write_text(_fallback_icon(name))
        entry = _entry(app_id, name, url, profile, icon_path)
        record = dict(format=1, id=app_id, name=name, url=url, metadataWarning=warning,
                      desktopHash=hashlib.sha256(entry.encode()).hexdigest())
        (directory / 'app.json').write_text(json.dumps(record))
        applications.mkdir(parents=True, exist_ok=True)
        with desktop.open('x') as output:
            desktop_created = True
            output.write(entry)
        return {key: record[key] for key in ('id', 'name', 'url', 'metadataWarning')}
    except Exception:
        if desktop_created:
            desktop.unlink(missing_ok=True)
        shutil.rmtree(directory)
        raise


def _owned_launcher(desktop, app_id, profile):
    if not desktop.is_file() or desktop.stat().st_size > 64 * 1024:
        return False
    try:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(desktop.read_text())
        entry = parser['Desktop Entry']
        if entry.get('StartupWMClass') != app_id:
            return False
        command = entry.get('Exec', '').replace('\\\\', '\\')
        argv = [arg.replace('%%', '%') for arg in shlex.split(command)]
        return any(argv[index:index + 2] == ['--profile', str(profile)]
                   for index in range(len(argv) - 1))
    except (OSError, ValueError, KeyError, configparser.Error):
        return False


def remove_app(app_id, data_home=None):
    home = _data_home(data_home)
    _record(home, app_id)
    directory = home / 'emaki/webapps' / app_id
    desktop = home / 'applications' / (app_id + '.desktop')
    _safe(desktop)
    if desktop.exists() and not _owned_launcher(desktop, app_id, directory / 'profile'):
        raise WebAppError('website-modified')
    # Firefox's Unix profile lock is an advisory fcntl lock; do not remove a live profile.
    lock = directory / 'profile/.parentlock'
    _safe(lock)
    if not lock.parent.exists():
        desktop.unlink(missing_ok=True)
        shutil.rmtree(directory)
        return {'id': app_id}
    try:
        with lock.open('a') as handle:
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise WebAppError('website-running') from None
            # rmtree does not follow profile symlinks; never use paths from app.json.
            desktop.unlink(missing_ok=True)
            shutil.rmtree(directory)
    except FileNotFoundError:
        raise WebAppError('website-not-found') from None
    return {'id': app_id}
