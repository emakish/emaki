# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate the target language boundary without changing the host locale."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.errors import InstallError
from emaki_installer.i18n import LOCALES, available_locales, system_locale
from emaki_installer.planner import validate_config
from support import config


class LanguageTests(unittest.TestCase):
    def test_supported_languages_survive_plan_validation(self):
        for language in LOCALES:
            value = config()
            value['language'] = language
            self.assertEqual(validate_config(value)['language'], language)

    def test_default_follows_environment_with_message_precedence(self):
        with patch.dict(os.environ, {'LANG': 'es_AR.UTF-8'}, clear=True):
            self.assertEqual(validate_config(config())['language'], 'es_MX.UTF-8')
            with patch.dict(os.environ, {'LC_MESSAGES': 'pt_BR.UTF-8'}):
                self.assertEqual(system_locale(), 'pt_BR.UTF-8')
            with patch.dict(os.environ, {'LC_ALL': 'C'}):
                self.assertEqual(system_locale(), 'en_US.UTF-8')

    def test_language_is_validated_before_target_file_generation(self):
        for language in ('en_US.UTF-8\nLC_ALL=C', '../es_MX', 'pt_BR', ['es_MX.UTF-8'], [], False, None):
            value = config()
            value['language'] = language
            with self.assertRaises(InstallError):
                validate_config(value)

    def test_portuguese_environment_selects_brazilian_target_locale(self):
        for language in ('pt', 'pt.UTF-8', 'pt_BR.UTF-8', 'pt_PT.UTF-8'):
            with self.subTest(language=language):
                with patch.dict(os.environ, {'LANG': language}, clear=True):
                    self.assertEqual(system_locale(), 'pt_BR.UTF-8')
                    self.assertEqual(validate_config(config())['language'], 'pt_BR.UTF-8')

    def test_portuguese_is_offered_only_with_its_compiled_catalogue(self):
        source = Path(__file__).resolve().parents[2] / 'i18n' / 'installer' / 'pt_BR.po'
        with tempfile.TemporaryDirectory(prefix='emaki-installer-languages-') as directory:
            with patch.dict(os.environ, {'EMAKI_LOCALE_DIR': directory}):
                self.assertEqual(available_locales(), ['en_US.UTF-8'])
                target = Path(directory) / 'pt_BR' / 'LC_MESSAGES' / 'installer.mo'
                target.parent.mkdir(parents=True)
                subprocess.run(['msgfmt', '--check', '-o', str(target), str(source)], check=True)
                self.assertEqual(available_locales(), ['en_US.UTF-8', 'pt_BR.UTF-8'])
                target.unlink()
                self.assertEqual(available_locales(), ['en_US.UTF-8'])
