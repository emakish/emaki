# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Review numbers and complete disk messages follow each request's language."""
import concurrent.futures
import gettext
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import locale
import unittest
from unittest.mock import patch

from emaki_installer import i18n, inventory, render
from emaki_installer.planner import make_plan
from support import config, inventory as machine


class LocalizedReviewTests(unittest.TestCase):
    def test_numbers_follow_nested_request_contexts_without_process_mutation(self):
        before = locale.setlocale(locale.LC_NUMERIC)
        with i18n.using_language('en_US.UTF-8'):
            self.assertEqual(i18n.format_number(1234.5, 2), '1234.50')
            self.assertEqual(i18n.format_number(17179869184, grouping=True), '17,179,869,184')
            for language, decimal, grouped in (
                ('es_MX.UTF-8', '1234,50', '17.179.869.184'),
                ('pt_BR.UTF-8', '1234,50', '17.179.869.184'),
                ('de_DE.UTF-8', '1234,50', '17.179.869.184'),
                ('fr_FR.UTF-8', '1234,50', '17\u202f179\u202f869\u202f184'),
                ('ru_RU.UTF-8', '1234,50', '17\u00a0179\u00a0869\u00a0184'),
            ):
                with i18n.using_language(language):
                    self.assertEqual(i18n.format_number(1234.5, 2), decimal)
                    self.assertEqual(i18n.format_number(17179869184, grouping=True), grouped)
            self.assertEqual(i18n.format_number(1234.5, 2), '1234.50')
        self.assertEqual(locale.setlocale(locale.LC_NUMERIC), before)

    def test_concurrent_requests_keep_their_numbers(self):
        def numbers(language):
            with i18n.using_language(language):
                return [i18n.format_number(4.5, 2) for _ in range(100)]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            english = pool.submit(numbers, 'en_US.UTF-8')
            french = pool.submit(numbers, 'fr_FR.UTF-8')
            self.assertEqual(set(english.result()), {'4.50'})
            self.assertEqual(set(french.result()), {'4,50'})

    def test_plan_numbers_follow_request_language(self):
        with i18n.using_language('fr_FR.UTF-8'):
            plan = make_plan(config(), machine())
            self.assertTrue(any('1,00' in line for line in plan.summary), plan.summary)
        with i18n.using_language('en_US.UTF-8'):
            plan = make_plan(config(), machine())
            self.assertTrue(any('1.00 GiB' in line for line in plan.summary), plan.summary)

    def test_encrypted_warning_translates_complete_sentences(self):
        for kind in inventory.ENCRYPTED_TYPES:
            calls = []
            with patch.object(inventory, 'translate', side_effect=lambda text: calls.append(text) or text), \
                 patch.object(inventory, '_', side_effect=lambda text: calls.append(text) or text):
                warning = inventory.encrypted_warning('/dev/test', kind)
            self.assertIn('Type ERASE', warning)
            self.assertTrue(any('contains a ' in text and 'contents' in text and '{value1}' in text
                                and '{value2}' not in text for text in calls), calls)
            self.assertFalse(any('locked {description}' == text for text in calls))

    def test_review_keyboard_uses_translated_known_names_or_codes(self):
        rules = '! layout\n us English (US)\n ru Russian\n zz Untranslated external name\n'
        with i18n.using_language('de_DE.UTF-8'), patch.object(render, '_', side_effect=lambda text: {'English (US)': 'US label', 'Russian': 'RU label'}.get(text, text)):
            self.assertEqual(render.keyboard_summary(['us', 'ru'], rules),
                             'Keyboard: US label (default), RU label. Super+Space switches.')
            self.assertEqual(render.keyboard_summary(['zz'], rules), 'Keyboard: zz.')

    def test_integer_bytes_are_not_rounded_to_float_precision(self):
        with i18n.using_language('en_US.UTF-8'):
            self.assertEqual(i18n.format_number(9007199254740993, grouping=True), '9,007,199,254,740,993')

    def test_active_device_actions_are_complete_translation_units(self):
        for kind, command in [('crypt', 'cryptsetup close'), ('lvm', 'lvchange -an'), ('raid1', 'mdadm --stop')]:
            calls = []
            with patch.object(inventory, '_', side_effect=lambda text: calls.append(text) or text):
                message = inventory.busy_reason([{'path': '/dev/test', 'type': kind}], {})
            self.assertIn(command, message)
            self.assertEqual(len(calls), 1)
            self.assertIn('then try again.', calls[0])
            self.assertNotIn('{value3}', calls[0])

    def test_member_reasons_translate_whole_messages_for_each_type(self):
        for kind in ('btrfs', 'LVM2_member', 'linux_raid_member'):
            for count in (None, 1, 2):
                calls = []
                with patch.object(inventory, '_', side_effect=lambda text: calls.append(text) or text):
                    message = inventory.Inventory._set_reason('/dev/test', kind, count)
                if count == 1:
                    self.assertIsNone(message)
                else:
                    self.assertIn('/dev/test', message)
                    self.assertTrue(all('filesystem' in text or 'group' in text or 'array' in text for text in calls))
                    self.assertFalse(any('{value3}' in text for text in calls))

    def test_gpt_failures_translate_the_field_in_the_complete_message(self):
        from emaki_installer.errors import InstallError
        rows = [
            ('GUID', 'Partition unique GUID: 11111111-1111-1111-1111-111111111111'),
            ('type', 'Partition GUID code: 22222222-2222-2222-2222-222222222222'),
            ('start', 'First sector: 2048'), ('end', 'Last sector: 4095'),
            ('attributes', 'Attribute flags: 0000000000000000'), ('name', "Partition name: 'data'"),
        ]
        for field, row in rows:
            translated = 'translated field: ' + field
            with patch.object(inventory, '_', side_effect=lambda text: translated if text == 'Cannot verify GPT partition ' + field + '.' else text):
                with self.assertRaises(InstallError) as error:
                    inventory.parse_gpt_info('\n'.join(text for _, text in rows if text != row))
            self.assertEqual(error.exception.message, translated)

    def test_manual_partition_encryption_is_in_the_complete_message(self):
        from emaki_installer import planner
        from emaki_installer.constants import GIB
        for create in ('linux', None):
            for encrypted in (False, True):
                part = planner.Partition('/dev/test1', 1, 0, GIB, 'ext4', True,
                                         mountpoint='/', create=create)
                calls = []
                with patch.object(planner, '_', side_effect=lambda text: calls.append(text) or text):
                    line = planner.manual_summary({'partitions': []}, [part], None, encrypted)[0]
                self.assertEqual('encrypted (LUKS2)' in line, encrypted)
                sentence = next(text for text in calls if text.startswith(('Create partition ', 'Format partition ')))
                self.assertEqual('encrypted (LUKS2)' in sentence, encrypted)
                self.assertNotIn(', encrypted (LUKS2)', calls)

    def test_new_partition_size_refusals_pass_through_translation(self):
        from emaki_installer import planner
        from emaki_installer.constants import MIB
        from emaki_installer.errors import InstallError
        from test_partition_editor import free_start, new, shared_disk
        disk = shared_disk()['disks'][0]
        start = free_start({'disks': [disk]})
        for kind, text in [('swap', 'A swap partition needs at least 256 MiB.'),
                           ('linux', 'A new partition needs at least 1 GiB.')]:
            with patch.object(planner, '_', side_effect=lambda message: 'translated minimum' if message == text else message):
                with self.assertRaises(InstallError) as error:
                    planner.partition_edits({'create': [new('new-1', kind, start, MIB)]}, disk)
            self.assertEqual(error.exception.message, 'translated minimum')

    def test_country_warning_is_one_translated_message(self):
        from emaki_installer.worker import Worker
        from emaki_installer.runtime import TargetFiles
        from emaki_installer import worker
        root = Path(__file__).resolve().parents[2]
        message = 'Wi-Fi country left unset: timezone {timezone} has no country.'
        class Logged(Exception):
            pass
        for language in ('en', 'es', 'pt_BR', 'de', 'fr', 'ru'):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)
                compiled = target / 'installer.mo'
                subprocess.run(['msgfmt', '-o', str(compiled), str(root / 'i18n/installer' / (language + '.po'))], check=True)
                with compiled.open('rb') as stream:
                    catalogue = gettext.GNUTranslations(stream)
                if language != 'en':
                    self.assertNotEqual(catalogue.gettext(message), message)
                def log(line):
                    raise Logged(line)
                instance = SimpleNamespace(plan=SimpleNamespace(config=config()), test_mode=False,
                                           files=TargetFiles(target), log=log)
                instance.plan.config['timezone'] = 'Etc/UTC'
                with patch.object(worker, '_', catalogue.gettext), self.assertRaises(Logged) as logged:
                    Worker.settings(instance)
                self.assertEqual(str(logged.exception), catalogue.gettext(message).format(timezone='Etc/UTC'))

    def test_review_keyboard_uses_compiled_russian_and_english_names(self):
        root = Path(__file__).resolve().parents[2]
        for language, expected in [('en', 'Keyboard: English (US) (default), Russian. Super+Space switches.'),
                                   ('ru', 'Клавиатура: Английская (США) (по умолчанию), Русская. Переключение Super+Space.')]:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as temporary:
                compiled = Path(temporary) / 'installer.mo'
                subprocess.run(['msgfmt', '-o', str(compiled), str(root / 'i18n/installer' / (language + '.po'))], check=True)
                with compiled.open('rb') as stream:
                    catalogue = gettext.GNUTranslations(stream)
                with patch.object(render, '_', catalogue.gettext):
                    self.assertEqual(render.keyboard_summary(['us', 'ru']), expected)
