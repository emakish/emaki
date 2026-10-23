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

    def test_new_language_regional_defaults_and_safe_keyboards(self):
        for language, locale in [('de_AT.UTF-8', 'de_DE.UTF-8'), ('fr_CA.UTF-8', 'fr_FR.UTF-8'),
                                 ('ru', 'ru_RU.UTF-8')]:
            with self.subTest(language=language), patch.dict(os.environ, {'LANG': language}, clear=True):
                self.assertEqual(system_locale(), locale)
                value = config()
                value.pop('layouts', None)
                parsed = validate_config(value)
                self.assertEqual(parsed['layouts'], ['us', 'ru'] if language == 'ru' else ['us'])
        for layouts in (['ru'], ['ru', 'us'], ['ru', 'de']):
            value = config()
            value.update(language='ru_RU.UTF-8', layouts=layouts)
            with self.subTest(layouts=layouts), self.assertRaises(InstallError):
                validate_config(value)
        for layouts in (['us', 'ru'], ['de', 'ru'], ['fr', 'ru']):
            value = config()
            value.update(language='ru_RU.UTF-8', layouts=layouts)
            self.assertEqual(validate_config(value)['layouts'], layouts)

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


class RuntimeLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='installer-runtime-language-')
        root = Path(__file__).resolve().parents[2]
        for language in ('es', 'pt_BR', 'de', 'fr', 'ru'):
            target = Path(cls.directory.name) / language / 'LC_MESSAGES' / 'installer.mo'
            target.parent.mkdir(parents=True)
            subprocess.run(['msgfmt', '--check', '-o', str(target),
                            str(root / 'i18n/installer' / (language + '.po'))], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.environment = patch.dict(os.environ, {'EMAKI_LOCALE_DIR': self.directory.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def expected(self, language, source):
        import gettext
        return gettext.translation('installer', self.directory.name,
                                   languages=[language], fallback=True).gettext(source)

    def test_requests_and_imported_constants_follow_each_selection(self):
        from emaki_installer.i18n import _, using_language
        from emaki_installer.inventory import HIBERNATION_REFUSAL, ntfs_error
        from emaki_installer.network_profiles import UNAVAILABLE, WifiNotCopied
        from emaki_installer.protocol import Controller
        controller = Controller(None, None)
        for language in LOCALES:
            with self.subTest(language=language):
                reply = controller.handle(dict(type='unknown', id='test', language=language))[0]
                self.assertEqual(reply['msg'], self.expected(language, 'Unknown request type.'))
                with using_language(language):
                    self.assertEqual(ntfs_error('hibernated'), self.expected(language, HIBERNATION_REFUSAL))
                    self.assertIn(self.expected(language, UNAVAILABLE), WifiNotCopied('fixture').message)
                with using_language('en_US.UTF-8'):
                    self.assertEqual(_('Unknown request type.'), 'Unknown request type.')

    def test_review_and_target_share_the_selected_language(self):
        from emaki_installer.protocol import Controller
        from support import FakeInventory
        controller = Controller(FakeInventory(), None)
        source = 'Erase all partitions and create a new GPT partition table.'
        for language in LOCALES:
            value = config()
            value['language'] = language
            reply = controller.handle(dict(type='plan', id='review', language=language, config=value))[0]
            self.assertEqual(reply['errors'], [])
            self.assertIn(self.expected(language, source), reply['summary'])
            self.assertEqual(controller.pending['plan'].config['language'], language)

    def test_unsafe_startup_files_review_uses_selected_language(self):
        from emaki_installer.errors import Code
        from emaki_installer.inventory import EFI_MANIFEST_UNSAFE
        from emaki_installer.protocol import Controller
        from support import FakeInventory, inventory, manual
        machine = inventory(partitions=True)
        machine['disks'][0]['partitions'][0]['_efi_microsoft'] = None
        controller = Controller(FakeInventory(machine), None)
        for language in LOCALES:
            with self.subTest(language=language):
                reply = controller.handle(dict(type='plan', id='review', language=language,
                                               config=manual()))[0]
                expected = self.expected(language, EFI_MANIFEST_UNSAFE)
                if language != 'en_US.UTF-8':
                    self.assertNotEqual(expected, EFI_MANIFEST_UNSAFE)
                self.assertEqual(reply['errors'], [dict(code=Code.MANUAL_LAYOUT, msg=expected)])

    def test_unsafe_startup_files_worker_uses_selected_language(self):
        from emaki_installer.errors import Code
        from emaki_installer.i18n import using_language
        from emaki_installer.inventory import EFI_MANIFEST_UNSAFE
        from emaki_installer.planner import make_plan
        from emaki_installer.runtime import Redactor
        from emaki_installer.worker import Worker
        from support import inventory, manual
        with tempfile.TemporaryDirectory(prefix='installer-startup-language-') as target:
            worker = Worker(None, None, Redactor(), lambda *a, **kw: None,
                            lambda line: None, target=target)
            worker.plan = make_plan(manual(), inventory(partitions=True))
            with patch('emaki_installer.worker.alongside.efi_microsoft', return_value=None):
                for language in LOCALES:
                    with self.subTest(language=language), using_language(language):
                        with self.assertRaises(InstallError) as caught:
                            worker.shared_layout()
                        expected = self.expected(language, EFI_MANIFEST_UNSAFE)
                        if language != 'en_US.UTF-8':
                            self.assertNotEqual(expected, EFI_MANIFEST_UNSAFE)
                        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
                        self.assertEqual(str(caught.exception), expected)

    def test_worker_thread_uses_plan_language_not_other_requests(self):
        import threading
        from types import SimpleNamespace
        from emaki_installer.i18n import _
        from emaki_installer.protocol import Controller, Job
        for language in LOCALES:
            started, finish = threading.Event(), threading.Event()
            messages = []
            class Worker:
                def run(self, plan, cancelled):
                    started.set()
                    if not finish.wait(5):
                        raise RuntimeError('worker test timed out')
                    messages.append(_('Unknown request type.'))
            controller = Controller(None, lambda broker: Worker())
            controller.job = Job('language-test')
            plan = SimpleNamespace(config=dict(language=language, user=dict(password=''), disk_password=''))
            thread = controller.start(plan)
            try:
                self.assertTrue(started.wait(5))
                other = controller.handle(dict(type='unknown', id='other', language='en_US.UTF-8'))[0]
                self.assertEqual(other['msg'], 'Unknown request type.')
            finally:
                finish.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(messages, [self.expected(language, 'Unknown request type.')])

    def test_rejected_language_and_nested_context_do_not_leak(self):
        from emaki_installer.i18n import _, using_language
        from emaki_installer.protocol import Controller
        for language in ('../../es', [], False, 'es'):
            reply = Controller(None, None).handle(dict(type='unknown', id='test', language=language))[0]
            self.assertEqual(reply['code'], 'bad_request')
        with using_language('es_MX.UTF-8'):
            with self.assertRaises(ValueError):
                with using_language('invalid'):
                    pass
            self.assertEqual(_('Unknown request type.'), self.expected('es_MX.UTF-8', 'Unknown request type.'))

    def test_unprivileged_helper_uses_request_language(self):
        import json
        import sys
        helper = Path(__file__).resolve().parents[1] / 'ui/ui-helper.py'
        source = 'This operation is unavailable. Please try again.'
        for language in LOCALES:
            result = subprocess.run([sys.executable, '-I', '-B', str(helper)],
                                    input=json.dumps(dict(op='invalid', language=language)),
                                    capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)['message'], self.expected(language, source))
