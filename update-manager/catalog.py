# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse package metadata and advisory news without trusting remote markup."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from fnmatch import fnmatchcase
import re
import shlex
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

NAME = re.compile(r'[a-zA-Z0-9@_+][a-zA-Z0-9@_.+:-]*\Z')
VERSION = re.compile(r'[^\s|\x00-\x1f]{1,256}\Z')
AUR_SOURCE = 'AUR — update with your AUR tool'
# Keep this list identical to zzz-emaki-session-update.hook.
SESSION_TARGETS = ('emaki-config', 'niri', 'niri-emaki', 'quickshell-emaki', 'qt6-*')


def session_changes(names):
    return any(fnmatchcase(name, target) for name in names for target in SESSION_TARGETS)


def packages(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or not NAME.fullmatch(fields[0]) or not VERSION.fullmatch(fields[1]):
            raise ValueError('The installed package list could not be read.')
        result[fields[0]] = fields[1]
    return result


def upgrade_lines(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if (len(fields) not in (4, 5) or fields[2] != '->'
                or not NAME.fullmatch(fields[0]) or not VERSION.fullmatch(fields[1])
                or not VERSION.fullmatch(fields[3])
                or (len(fields) == 5 and fields[4] != '[ignored]')):
            raise ValueError('The available package list could not be read.')
        result[fields[0]] = dict(old=fields[1], new=fields[3], ignored=len(fields) == 5)
    return result


def source_label(repo):
    if repo == 'emaki':
        return 'Emaki'
    if repo in ('core', 'extra', 'multilib', 'core-testing', 'extra-testing', 'multilib-testing'):
        return 'Arch ' + repo
    return 'Repository ' + repo


def transaction(text, installed):
    """pacman -Sup --print-format '%r|%n|%v|%s', in the C locale."""
    result = []
    seen = set()
    for line in text.splitlines():
        fields = line.split('|')
        if (len(fields) != 4 or not NAME.fullmatch(fields[0]) or not NAME.fullmatch(fields[1])
                or not VERSION.fullmatch(fields[2]) or not fields[3].isascii()
                or not fields[3].isdigit() or fields[1] in seen):
            raise ValueError('The update download list could not be read.')
        repo, name, version, size = fields
        seen.add(name)
        result.append(dict(source=source_label(repo), name=name,
                           old=installed.get(name, 'Not installed'), new=version,
                           downloadSize=int(size), aur=False))
    return result


def aur_updates(payload, installed, compare):
    if (not isinstance(payload, dict) or payload.get('version') != 5
            or payload.get('type') != 'multiinfo' or not isinstance(payload.get('results'), list)
            or payload.get('resultcount') != len(payload['results'])):
        raise ValueError('The AUR package information could not be read.')
    result, found = [], set()
    for item in payload['results']:
        if not isinstance(item, dict):
            raise ValueError('The AUR package information could not be read.')
        name, version = item.get('Name'), item.get('Version')
        if (not isinstance(name, str) or not NAME.fullmatch(name) or name not in installed
                or name in found or not isinstance(version, str) or not VERSION.fullmatch(version)):
            raise ValueError('The AUR package information could not be read.')
        found.add(name)
        if compare(version, installed[name]) > 0:
            result.append(dict(source=AUR_SOURCE, name=name, old=installed[name], new=version,
                               downloadSize=None, aur=True))
    return result, found


def utc(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Missing timezone')
    return value.astimezone(timezone.utc)


def last_upgrade(text):
    pending = False
    last = None
    for line in text.splitlines():
        match = re.match(r'^\[([^]]+)\] \[([^]]+)\] (.*)$', line)
        if not match:
            continue
        stamp, actor, message = match.groups()
        if actor == 'PACMAN' and message.startswith("Running '"):
            pending = False
            try:
                words = shlex.split(message[9:-1])
                pending = bool(words and words[0].rsplit('/', 1)[-1] == 'pacman' and any(
                    word == '--sysupgrade' or (word.startswith('-') and not word.startswith('--')
                                               and 'u' in word) for word in words[1:]))
            except ValueError:
                pass
        elif pending and actor == 'ALPM' and message == 'transaction completed':
            try:
                last = utc(stamp)
            except ValueError:
                pass
            pending = False
    return last


def news_items(raw, since):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('The Arch news feed could not be read.')
    root = ET.fromstring(raw)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('The Arch news feed could not be read.')
    result = []
    for item in root.findall('./channel/item'):
        title = item.findtext('title', '')
        body = item.findtext('description', '')
        if not re.search(r'manual\s+intervention', title + ' ' + re.sub('<[^>]*>', ' ', body), re.I):
            continue
        try:
            published = parsedate_to_datetime(item.findtext('pubDate', ''))
            if published.tzinfo is None or (since and published <= since):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        link = item.findtext('link', '')
        parsed = urlsplit(link)
        if (parsed.scheme != 'https' or parsed.netloc != 'archlinux.org'
                or not parsed.path.startswith('/news/') or parsed.query or parsed.fragment):
            continue
        title = ' '.join(re.sub('<[^>]*>', '', title).split())[:300]
        if title:
            result.append(dict(title=title, link=link))
    return result


def warnings_for(updates):
    warnings = ['Read the package list and notices before updating. These checks cannot guarantee that an update will work.']
    names = {item['name'] for item in updates}
    if any(re.fullmatch(r'linux(?:-lts|-zen|-hardened|-rt|-rt-lts)?', name) for name in names):
        warnings.append('The kernel will change. Restart the computer after updating.')
    if session_changes(names):
        warnings.append('The desktop will change. Save your work, sign out and sign in again after updating.')
    if any(item['aur'] for item in updates):
        warnings.append('Packages from AUR are not checked by Arch or Emaki. Update them with your AUR tool; this window does not build them.')
    return warnings


def summary(updates, news, warnings, checked_at):
    source_order = ('Arch core', 'Arch core-testing', 'Arch extra',
                    'Arch extra-testing', 'Arch multilib', 'Arch multilib-testing', 'Emaki')
    def order(item):
        source = item['source']
        return (item['aur'], source_order.index(source) if source in source_order else len(source_order),
                source, item['name'])
    updates = sorted(updates, key=order)
    groups = {}
    for item in updates:
        groups[item['source']] = groups.get(item['source'], 0) + 1
    return dict(updates=updates,
                groups=[dict(name=name, count=count) for name, count in groups.items()],
                downloadSize=sum(item['downloadSize'] or 0 for item in updates),
                news=news, warnings=list(dict.fromkeys(warnings)), checkedAt=checked_at, error='')
