# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Review and both worker gates compare disk evidence independently of display language."""
import copy
import gettext
import itertools
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from emaki_installer.errors import Code, InstallError
from emaki_installer.constants import GIB
from emaki_installer.i18n import LOCALES, _, using_language
from emaki_installer.inventory import encrypted_warning, unconfirmed_identity_warning
from emaki_installer.planner import alongside_fingerprint, deletion_identity, fingerprint, make_plan
from emaki_installer.protocol import Controller, Job
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import FakeInventory, RecordingRunner, alongside_on, config, inventory
from test_alongside import windows


class LocalizedInventory(FakeInventory):
    def probe(self):
        machine = super().probe()
        for disk in machine['disks']:
            for volume in disk.get('closed_encrypted', []):
                volume['warning'] = (encrypted_warning(volume['path'], volume['type']) if volume['uuid']
                                     else unconfirmed_identity_warning(volume['path']))
            for part in disk['partitions']:
                for key in ('shrink', '_shrink_probe'):
                    if part.get(key, {}).get('reason'):
                        part[key]['reason'] = _(part[key]['reason'])
        return machine


class LanguageIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='installer-language-identity-')
        root = Path(__file__).resolve().parents[2]
        for language in ('es', 'pt_BR'):
            target = Path(cls.directory.name) / language / 'LC_MESSAGES' / 'installer.mo'
            target.parent.mkdir(parents=True)
            subprocess.run(['msgfmt', '--check', '-o', str(target),
                            str(root / 'i18n/installer' / (language + '.po'))], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        environment = patch.dict(os.environ, {'EMAKI_LOCALE_DIR': self.directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        # A missing request language reproduces the live USB's English CLI review.
        catalogue = patch('emaki_installer.i18n._catalogue', gettext.NullTranslations())
        catalogue.start()
        self.addCleanup(catalogue.stop)

    def locked(self, kind, uuid='locked-uuid'):
        machine = inventory(partitions=True)
        disk = machine['disks'][0]
        disk['partitions'][1]['fs'] = kind
        disk['closed_encrypted'] = [{'path': '/dev/vda2', 'type': kind, 'uuid': uuid}]
        return LocalizedInventory(machine)

    def test_encrypted_fingerprints_ignore_language_in_both_directions(self):
        for kind, uuid in itertools.product(('crypto_LUKS', 'BitLocker', 'cs_fvault2', 'apfs'),
                                            ('locked-uuid', None)):
            probe = self.locked(kind, uuid)
            disks = []
            for language in LOCALES:
                with using_language(language):
                    disks.append(probe.probe()['disks'][0])
            # Require real translations, so a missing catalogue cannot make this pass.
            self.assertEqual(len({d['closed_encrypted'][0]['warning'] for d in disks}), 3)
            for before, after in itertools.permutations(disks, 2):
                with self.subTest(kind=kind, uuid=uuid):
                    self.assertEqual(fingerprint(before), fingerprint(after))
                    self.assertEqual(deletion_identity(before['partitions'][1]),
                                     deletion_identity(after['partitions'][1]))

    def test_cli_and_ui_reviews_pass_both_worker_rechecks_after_language_changes(self):
        pairs = list(itertools.permutations(LOCALES, 2)) + [(None, lang) for lang in LOCALES[1:]]
        for kind, (review_language, job_language) in itertools.product(
                ('crypto_LUKS', 'BitLocker', 'cs_fvault2', 'apfs'), pairs):
            with self.subTest(kind=kind, review=review_language, job=job_language):
                probe = self.locked(kind)
                value = config()
                value.update(language=job_language, confirmed_encrypted=[
                    dict(probe.data['disks'][0]['closed_encrypted'][0])])
                controller = Controller(probe, None)
                request = dict(type='plan', id='review', config=value)
                if review_language is not None:
                    request['language'] = review_language
                reply = controller.handle(request)[0]
                self.assertEqual(reply['errors'], [])
                plan = controller.pending['plan']
                events = []
                worker = Worker(None, probe, Redactor(), lambda kind, **kw: events.append((kind, kw)),
                                lambda line: None, runner_factory=RecordingRunner)
                # Stop at the mocked backend, after both real identity gates and before any write.
                worker.backend = Mock()
                worker.backend.prepare.side_effect = InstallError(Code.CANCELLED, 'test boundary')
                worker.capture_live_wifi = lambda joined: worker.prepare_disk()
                controller.worker_factory = lambda broker: worker
                controller.job = Job('identity-test')
                thread = controller.start(plan)
                thread.join(5)
                self.assertFalse(thread.is_alive())
                errors = [event for kind, event in events if kind == 'error']
                self.assertEqual([event['code'] for event in errors], [Code.CANCELLED.value])
                worker.backend.prepare.assert_called_once_with()

    def test_encrypted_identity_changes_are_still_detected(self):
        disk = self.locked('crypto_LUKS').probe()['disks'][0]
        before = fingerprint(disk)
        for key, value in (('path', '/dev/vda3'), ('type', 'BitLocker'), ('uuid', 'changed'), ('uuid', None)):
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(disk)
                changed['closed_encrypted'][0][key] = value
                self.assertNotEqual(before, fingerprint(changed))
        for key, value in (('start_bytes', 0), ('size_bytes', 42), ('fs', 'ext4')):
            changed = copy.deepcopy(disk)
            changed['partitions'][1][key] = value
            self.assertNotEqual(before, fingerprint(changed))
        changed = copy.deepcopy(disk)
        changed['_identity']['diskseq'] += 1
        self.assertNotEqual(before, fingerprint(changed))

    def alongside(self):
        value, machine = windows()
        disk = machine['disks'][0]
        extra = copy.deepcopy(disk['partitions'][1])
        extra.update(id='/dev/vda3', path='/dev/vda3', number=3,
                     start_bytes=98 * GIB, size_bytes=GIB)
        extra['shrink'] = dict(min_bytes=0, max_free_bytes=0,
                              reason='Only the largest Windows/NTFS partition is considered; this partition is not offered.')
        extra['_shrink_probe'] = dict(min_bytes=0, max_free_bytes=0,
                                     reason='The Windows partition or its disk is mounted, held, or in use.')
        disk['partitions'].append(extra)
        return value, LocalizedInventory(machine)

    def test_alongside_review_ignores_both_translated_refusal_reasons(self):
        value, probe = self.alongside()
        plans = []
        with alongside_on():
            for language in LOCALES:
                with using_language(language):
                    plans.append(make_plan(value, probe.probe()))
        for key in ('shrink', '_shrink_probe'):
            self.assertEqual(len({p.disk['partitions'][-1][key]['reason'] for p in plans}), 3)
        for before, after in itertools.permutations(plans, 2):
            self.assertEqual(before.fingerprint, after.fingerprint)

    def test_alongside_preserves_refusal_state_bounds_and_metadata(self):
        _, probe = self.alongside()
        disk = probe.probe()['disks'][0]
        before = alongside_fingerprint(disk)
        snapshot = copy.deepcopy(disk)
        for key in ('shrink', '_shrink_probe'):
            for field, value in (('reason', None), ('min_bytes', 1), ('max_free_bytes', 1)):
                with self.subTest(key=key, field=field):
                    changed = copy.deepcopy(disk)
                    changed['partitions'][-1][key][field] = value
                    self.assertNotEqual(before, alongside_fingerprint(changed))
        for key, value in (('_gpt', {}), ('_esp_free_bytes', 0), ('_efi_loaders', {}),
                           ('_partuuid', 'changed'), ('_parttype', 'changed'), ('_partlabel', 'changed')):
            with self.subTest(key=key):
                changed = copy.deepcopy(disk)
                changed['partitions'][0][key] = value
                self.assertNotEqual(before, alongside_fingerprint(changed))
        self.assertEqual(disk, snapshot)
