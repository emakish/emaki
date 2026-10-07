# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real subprocess boundaries and nested package steps, without elapsed estimates."""
import unittest

from emaki_installer.worker import Worker


class CopyProgressTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.worker = Worker(None, None, None,
                             lambda kind, **event: self.events.append(event), lambda line: None)
        self.worker.phase = 'copy_packages'
        self.worker.package_total = 10
        self.pacstrap = ['pacstrap', '-K', '/target', 'base']

    def line(self, line):
        self.worker.progress(line=line)

    def test_counted_checks_end_at_the_next_real_step(self):
        self.worker.progress(command=self.pacstrap)
        self.line(':: Retrieving packages...')
        before = self.worker.phase_pct
        self.line('checking keyring...')
        self.assertGreater(self.worker.phase_pct, before)
        self.line('checking package integrity...')
        event = self.events[-1]
        self.assertEqual(event['step']['text'], 'Checking package files (pacman).')
        self.assertEqual(event['step']['state'], 'running')
        self.line('unrecognized diagnostic')
        self.worker.progress(99)
        self.assertEqual(self.events[-1], event)
        self.line('loading package files...')
        self.assertGreater(self.worker.phase_pct, event['phase_pct'])
        self.assertNotEqual(self.events[-1]['step']['id'], event['step']['id'])

    def test_hooks_and_each_transaction_preset_have_distinct_boundaries(self):
        for transaction in range(2):
            self.worker.progress(command=self.pacstrap)
            self.line('( 1/16) Generating the configured locale...')
            self.assertIn('step 1 of 16', self.events[-1]['step']['text'])
            self.line('(15/16) Updating linux initcpios...')
            for kernel in ('linux-lts', 'linux'):
                self.line(f"==> Building image from preset: /etc/mkinitcpio.d/{kernel}.preset: 'default'")
                event = self.events[-1]
                self.assertEqual(event['step']['text'],
                                 f'Building the startup image (mkinitcpio: {kernel}: default).')
                self.line('  -> Running build hook: [base]')
                self.assertIs(self.events[-1], event)
                self.line('==> Initcpio image generation successful')
                self.assertGreater(self.worker.phase_pct, event['phase_pct'])
                self.assertEqual(self.events[-1]['step']['name'], 'hook')
                completed = self.worker.phase_pct
                self.line('==> Initcpio image generation successful')
                self.assertEqual(self.worker.phase_pct, completed)
            self.worker.progress(command_done=self.pacstrap)
            self.assertIsNone(self.events[-1]['step'])
        self.assertEqual(len(self.worker.copy_progress.images), 4)
        self.assertEqual(self.worker.copy_progress.hooks_completed, 2)

    def test_explicit_locale_and_keyring_names_last_until_command_returns(self):
        expected = (
            (['locale-gen'], 'Preparing language support (locale-gen).', 4),
            (['pacman-key', '--init'], 'Creating the package trust store (pacman-key --init).', 1),
            (['pacman-key', '--populate', 'archlinux', 'emaki'],
             'Adding trusted package keys (pacman-key --populate).', 1),
        )
        for command, sentence, points in expected:
            with self.subTest(command=command):
                argv = ['arch-chroot', '/target', *command]
                before = self.worker.phase_pct
                self.worker.progress(command=argv)
                self.assertEqual(self.events[-1]['step']['text'], sentence)
                self.assertEqual(self.worker.phase_pct, before)
                self.worker.progress(command_done=argv)
                self.assertEqual(self.worker.phase_pct, before + points)
                self.assertIsNone(self.events[-1]['step'])

    def test_other_phases_and_invalid_hook_counters_are_ignored(self):
        for line in ('( 0/16) Invalid', '(17/16) Invalid', '(1/0) Invalid'):
            self.line(line)
        self.assertEqual(self.events, [])
        self.worker.phase = 'bootloader'
        self.worker.progress(command=['arch-chroot', '/target', 'locale-gen'])
        self.line('installing filesystem...')
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
