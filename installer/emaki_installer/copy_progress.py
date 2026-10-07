# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Count package work and name the operation that owns each silent interval."""
from pathlib import Path
import re

from .constants import PHASES


class CopyProgress:
    # These are work weights, not time estimates. Package announcements account for
    # 60 points; two groups of package checks for 10; two hook groups for 20;
    # two default images in each transaction for 4; locale for 4;
    # the two explicit keyring commands for 2. Only the phase return reaches 100.
    def __init__(self, worker):
        self.worker = worker
        self.serial = 0
        self.step = None
        self.hook = None
        self.hook_fraction = 0
        self.hooks_completed = 0
        self.images = set()
        self.images_started = set()
        self.image = None
        self.locale_done = False
        self.keys = set()
        self.checks = set()
        self.check = None
        self.transaction = 0

    def emit(self):
        w = self.worker
        if w.package_total:
            points = (60 * min(w.installed, w.package_total) / w.package_total
                      + 5 * len(self.checks) / 9
                      + 10 * (self.hooks_completed + self.hook_fraction)
                      + (len(self.images_started) + len(self.images)) / 2
                      + 4 * self.locale_done + len(self.keys))
            w.phase_pct = max(w.phase_pct, min(99, points))
        w.emit('progress', phase=w.phase, phase_pct=w.phase_pct,
               total_pct=w.completed + PHASES[w.phase] * w.phase_pct / 100,
               indeterminate=not bool(w.package_total), step=self.step)

    def start(self, name, text, detail=''):
        # Every instance gets an identity, including two runs of the same command.
        self.serial += 1
        self.step = {'id': f'copy-{self.serial}', 'name': name, 'text': text,
                     'detail': detail, 'state': 'running'}
        self.emit()

    def clear(self):
        self.step = None
        self.emit()

    def resume_hook(self):
        if self.hook:
            index, total, detail = self.hook
            self.start('hook', f'Running package setup step {index} of {total} (pacman: {detail}).', detail)
        else:
            self.clear()

    def command(self, argv, *, done=False):
        command = list(map(str, argv))
        if Path(command[0]).name == 'arch-chroot':
            command = command[2:]
        if not command:
            return
        name = Path(command[0]).name
        if done:
            if name == 'pacstrap' and self.hook:
                self.hooks_completed = min(2, self.hooks_completed + 1)
                self.hook_fraction = 0
                self.hook = None
            if name == 'locale-gen':
                self.locale_done = True
            if name == 'pacman-key':
                self.keys.update(arg for arg in command if arg in ('--init', '--populate'))
            self.clear()
        elif name == 'pacstrap':
            self.transaction += 1
            self.hook, self.hook_fraction = None, 0
            self.start('prepare', 'Preparing packages (pacman).')
        elif name == 'locale-gen':
            self.start('locale', 'Preparing language support (locale-gen).')
        elif name == 'pacman-key' and '--init' in command:
            self.start('keyring-init', 'Creating the package trust store (pacman-key --init).')
        elif name == 'pacman-key' and '--populate' in command:
            self.start('keyring-populate', 'Adding trusted package keys (pacman-key --populate).')

    def line(self, line):
        preparations = {
            ':: Synchronizing package databases...': ('databases', 'Reading package lists (pacman).'),
            'resolving dependencies...': ('dependencies', 'Finding required packages (pacman).'),
            'looking for conflicting packages...': ('conflicts', 'Checking package compatibility (pacman).'),
            ':: Retrieving packages...': ('retrieve', 'Reading packages from the installation media (pacman).'),
            'checking keyring...': ('trust', 'Checking trusted package keys (pacman).'),
            'checking package integrity...': ('integrity', 'Checking package files (pacman).'),
            'loading package files...': ('load', 'Opening package files (pacman).'),
            'checking for file conflicts...': ('file-conflicts', 'Checking for conflicting files (pacman).'),
            'checking available disk space...': ('space', 'Checking available disk space (pacman).'),
            ':: Processing package changes...': ('prepare', 'Preparing packages (pacman).'),
            '==> Initializing pacman keyring...': ('keyring-init', 'Creating the package trust store (pacman-key --init).'),
            '==> Appending keys from archlinux.gpg...': ('keyring-populate', 'Adding trusted package keys (pacman-key --populate).'),
        }
        if line in preparations:
            name, text = preparations[line]
            if self.check:
                self.checks.add((self.transaction, self.check))
            self.check = name if name in ('databases', 'dependencies', 'conflicts', 'retrieve',
                                         'trust', 'integrity', 'load', 'file-conflicts', 'space') else None
            self.start(name, text)
            return
        package = re.fullmatch(r'installing ([A-Za-z0-9@._+:-]+)\.\.\.', line)
        if package:
            self.worker.installed += 1
            self.start('package', f'Installing a package (pacman: {package[1]}).', package[1])
            return
        hook = re.fullmatch(r'\(\s*(\d+)\s*/\s*(\d+)\) (.+)', line)
        if hook and 0 < int(hook[1]) <= int(hook[2]):
            index, total = int(hook[1]), int(hook[2])
            detail = hook[3].rstrip('.').replace('[', '(').replace(']', ')')[:160]
            self.hook = (index, total, detail)
            self.hook_fraction = (index - 1) / total
            self.resume_hook()
            return
        preset = re.fullmatch(r"==> Building image from preset: /etc/mkinitcpio\.d/([A-Za-z0-9_.+-]+)\.preset: '([A-Za-z0-9_-]+)'", line)
        if preset:
            self.image = (preset[1], preset[2])
            # Selecting the preset and finishing its image are separate observed
            # milestones. Both transactions may build the same kernel again.
            if self.image in (('linux', 'default'), ('linux-lts', 'default')):
                self.images_started.add((self.transaction, *self.image))
            detail = f'{preset[1]}: {preset[2]}'
            self.start('initramfs', f'Building the startup image (mkinitcpio: {detail}).', detail)
        elif line == '==> Initcpio image generation successful':
            if self.image in (('linux', 'default'), ('linux-lts', 'default')):
                self.images.add((self.transaction, *self.image))
            self.image = None
            self.resume_hook()
        elif line == 'Generating locales...':
            self.start('locale', 'Preparing language support (locale-gen).')
        elif line == 'Generation complete.':
            self.resume_hook()
