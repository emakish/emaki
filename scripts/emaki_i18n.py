# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Gettext catalogues shared by the desktop's Python components."""
import gettext
import os
import shlex
from pathlib import Path


def selected_languages(environ=None):
    environ = os.environ if environ is None else environ
    override = environ.get('EMAKI_LANGUAGE')
    if override:
        return [value for value in override.split(':') if value]
    locale = (environ.get('LC_ALL') or environ.get('LC_MESSAGES')
              or environ.get('LANG') or 'C')
    if locale.split('.')[0].split('@')[0] in ('C', 'POSIX'):
        return ['en']
    return [item for item in environ.get('LANGUAGE', '').split(':') if item] + [locale]


def format_number(value, decimals=0, *, language=None, grouping=False):
    """Format display numbers without changing process-wide locale state.

    Precision and grouping are chosen by the caller to retain each surface's
    existing layout. An explicit language scopes formatting to one request;
    otherwise use the same language preferences as the message catalogue.
    """
    preferences = selected_languages() if language is None else language.split(':')
    selected = 'en'
    for preference in preferences:
        base = preference.replace('-', '_').split('.')[0].split('@')[0].split('_')[0]
        if base in ('en', 'es', 'pt', 'de', 'fr', 'ru', 'C', 'POSIX'):
            selected = base
            break
    spec = 'd' if isinstance(value, int) and decimals == 0 else f'.{decimals}f'
    text = format(value, (',' if grouping else '') + spec)
    if selected in ('es', 'pt', 'de', 'fr', 'ru'):
        separator = '\u202f' if selected == 'fr' else '\u00a0' if selected == 'ru' else '.'
        text = text.translate(str.maketrans({'.': ',', ',': separator}))
    return text


def translation(domain):
    directory = os.environ.get('EMAKI_LOCALE_DIR')
    if not directory:
        source = Path(__file__).resolve().parents[1] / 'i18n' / 'locale'
        if source.is_dir():
            directory = str(source)
        else:
            import emaki_paths
            directory = str(Path(emaki_paths.DATADIR) / 'locale')
    return gettext.translation(domain, localedir=directory,
                               languages=selected_languages(), fallback=True)


def system_languages(path=Path('/etc/locale.conf')):
    """Read the system locale without evaluating shell code or inheriting PAM's C override."""
    environment = {}
    try:
        for line in Path(path).read_text().splitlines():
            words = shlex.split(line, comments=True)
            if len(words) != 1 or '=' not in words[0]:
                continue
            key, value = words[0].split('=', 1)
            if key in ('LANG', 'LC_MESSAGES', 'LANGUAGE'):
                environment[key] = value
    except (OSError, ValueError):
        pass
    if os.environ.get('EMAKI_LANGUAGE'):
        environment['EMAKI_LANGUAGE'] = os.environ['EMAKI_LANGUAGE']
    return selected_languages(environment)
