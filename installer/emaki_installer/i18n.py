# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone installer message catalogue and supported target locales."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import gettext
import os
from pathlib import Path

LOCALES = ('en_US.UTF-8', 'es_MX.UTF-8', 'pt_BR.UTF-8',
           'de_DE.UTF-8', 'fr_FR.UTF-8', 'ru_RU.UTF-8')


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


def system_locale():
    value = os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG', '')
    language = value.split('.')[0].split('@')[0].split('_')[0]
    for locale in LOCALES:
        if locale.split('_')[0] == language:
            return locale
    return 'en_US.UTF-8'


_source_locale = Path(__file__).resolve().parents[2] / 'i18n' / 'locale'
_boot_locale = '@EMAKI_DATADIR@/locale'
_default_locale = (Path(_boot_locale) if not _boot_locale.startswith('@') else
                   _source_locale if _source_locale.is_dir() else Path(__file__).resolve().parent / 'locale')
_catalogue = gettext.translation(
    'installer', localedir=os.environ.get('EMAKI_LOCALE_DIR', str(_default_locale)),
    languages=selected_languages(),
    fallback=True)
_current_catalogue = ContextVar('installer_catalogue', default=None)
_current_language = ContextVar('installer_language', default=None)


def _(message):
    return (_current_catalogue.get() or _catalogue).gettext(message)


def ngettext(singular, plural, count):
    return (_current_catalogue.get() or _catalogue).ngettext(singular, plural, count)


def N_(message):
    """Mark a deferred message while retaining its English source string."""
    return message


@lru_cache(maxsize=16)
def language_catalogue(language, directory):
    return gettext.translation('installer', localedir=directory,
                               languages=[language], fallback=True)


@contextmanager
def using_language(language=None):
    """Scope a request or worker's messages without changing the live locale."""
    if language is not None and language not in LOCALES:
        raise ValueError(_('Invalid config values.'))
    catalogue = (language_catalogue(language, os.environ.get('EMAKI_LOCALE_DIR', str(_default_locale)))
                 if language is not None else None)
    token = _current_catalogue.set(catalogue)
    language_token = _current_language.set(language)
    try:
        yield
    finally:
        _current_catalogue.reset(token)
        _current_language.reset(language_token)


def available_locales():
    directory = os.environ.get('EMAKI_LOCALE_DIR', str(_default_locale))
    return [value for value in LOCALES if value.startswith('en_')
            or gettext.find('installer', localedir=directory, languages=[value])]


def format_number(value, decimals=0, *, grouping=False):
    """Format display numbers within a request, without changing process locale.

    The standalone installer carries the six supported languages itself. Keep
    ungrouped decimals and English output identical to the previous format specs.
    """
    language = message_language()
    spec = 'd' if isinstance(value, int) and decimals == 0 else '.' + str(decimals) + 'f'
    text = format(value, (',' if grouping else '') + spec)
    if language == 'en':
        return text
    separator = {'fr': '\u202f', 'ru': '\u00a0'}.get(language, '.')
    integer, dot, fraction = text.partition('.')
    return integer.replace(',', separator) + (',' + fraction if dot else '')


def message_language():
    """The supported language used for request-local display values."""
    languages = [_current_language.get()] if _current_language.get() else selected_languages()
    language = 'en'
    for candidate in languages:
        candidate = candidate.replace('-', '_').split('.')[0].split('@')[0].split('_')[0]
        if candidate in ('en', 'es', 'pt', 'de', 'fr', 'ru'):
            language = candidate
            break
    return language
