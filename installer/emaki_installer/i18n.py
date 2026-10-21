# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone installer message catalogue and supported target locales."""
import gettext
import os
from pathlib import Path

LOCALES = ('en_US.UTF-8', 'es_MX.UTF-8', 'pt_BR.UTF-8')


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
    if value.startswith('es'):
        return 'es_MX.UTF-8'
    if value.startswith('pt'):
        return 'pt_BR.UTF-8'
    return 'en_US.UTF-8'


_source_locale = Path(__file__).resolve().parents[2] / 'i18n' / 'locale'
_boot_locale = '@EMAKI_DATADIR@/locale'
_default_locale = (Path(_boot_locale) if not _boot_locale.startswith('@') else
                   _source_locale if _source_locale.is_dir() else Path(__file__).resolve().parent / 'locale')
_catalogue = gettext.translation(
    'installer', localedir=os.environ.get('EMAKI_LOCALE_DIR', str(_default_locale)),
    languages=selected_languages(),
    fallback=True)
_ = _catalogue.gettext
ngettext = _catalogue.ngettext


def available_locales():
    directory = os.environ.get('EMAKI_LOCALE_DIR', str(_default_locale))
    return [value for value in LOCALES if value.startswith('en_')
            or gettext.find('installer', localedir=directory, languages=[value])]
