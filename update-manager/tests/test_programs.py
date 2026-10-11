#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed programs: preview parsing, refusal rules and the privileged helper's guards, offline."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'update-manager'))
sys.path.insert(0, str(ROOT / 'installer'))
import programs_catalog as catalog
import programs
from emaki_installer import constants

spec = importlib.util.spec_from_file_location('programs_remove', ROOT / 'update-manager/programs_remove.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

# Captured from pacman 7.1.0 (LC_ALL=C) on a private root.
OWNERS = '''/usr/share/applications/org.kde.gwenview.desktop is owned by gwenview 26.08.1-1
/usr/share/applications/libreoffice-calc.desktop is owned by libreoffice-fresh 26.8.1-1
/usr/share/applications/libreoffice-writer.desktop is owned by libreoffice-fresh 26.8.1-1
/usr/share/applications/kitty.desktop is owned by kitty 0.43.1-1
/usr/share/applications/emaki-update-manager.desktop is owned by emaki-config 0.6.0-4
/usr/share/applications/nvidia-settings.desktop is owned by nvidia-settings 580.1-1
'''
INFO = '''Name            : gwenview
Version         : 26.08.1-1
Optional Deps   : None
Required By     : None
Optional For    : emaki-apps
Install Reason  : Installed as a dependency for another package

Name            : libreoffice-fresh
Version         : 26.8.1-1
Required By     : None
Optional For    : emaki-apps  other-suite
Install Reason  : Explicitly installed

Name            : kitty
Version         : 0.43.1-1
Required By     : emaki-desktop
Optional For    : None
Install Reason  : Explicitly installed
'''
# pacman prints the error to stderr and the broken dependencies to stdout.
FAILED = 'error: failed to prepare transaction (could not satisfy dependencies)\n'
BREAKS = ''':: removing kitty breaks dependency 'kitty' required by emaki-config
:: removing kitty breaks dependency 'kitty' required by emaki-desktop
'''
APPS = [
    {'id': 'org.kde.gwenview.desktop', 'name': 'Gwenview', 'filename': '/usr/share/applications/org.kde.gwenview.desktop'},
    {'id': 'libreoffice-writer.desktop', 'name': 'LibreOffice Writer', 'filename': '/usr/share/applications/libreoffice-writer.desktop'},
    {'id': 'libreoffice-calc.desktop', 'name': 'LibreOffice Calc', 'filename': '/usr/share/applications/libreoffice-calc.desktop'},
    {'id': 'kitty.desktop', 'name': 'kitty', 'filename': '/usr/share/applications/kitty.desktop'},
    {'id': 'emaki-update-manager.desktop', 'name': 'Updates', 'filename': '/usr/share/applications/emaki-update-manager.desktop'},
    {'id': 'nvidia-settings.desktop', 'name': 'NVIDIA Settings', 'filename': '/usr/share/applications/nvidia-settings.desktop'},
    {'id': 'mine.desktop', 'name': 'Mine', 'filename': '/home/person/.local/share/applications/mine.desktop'},
]


def recipe_names():
    names = set()
    for recipe in (ROOT / 'packaging').glob('*/PKGBUILD'):
        for line in recipe.read_text().splitlines():
            match = re.match(r'pkgname=\(?([^)]+)\)?$', line)
            if match:
                names.update(name.strip("'\"") for name in match[1].split())
    return names


class Parsing(unittest.TestCase):
    def test_owners_info_and_preview(self):
        owned = catalog.owners(OWNERS)
        self.assertEqual(owned['/usr/share/applications/libreoffice-calc.desktop'], ('libreoffice-fresh', '26.8.1-1'))
        details = catalog.info(INFO)
        self.assertEqual(details['gwenview']['Optional For'], ['emaki-apps'])
        self.assertEqual(details['libreoffice-fresh']['Optional For'], ['emaki-apps', 'other-suite'])
        self.assertEqual(details['kitty']['Required By'], ['emaki-desktop'])
        self.assertEqual(details['gwenview']['Required By'], [])
        self.assertEqual(catalog.preview_names('gwenview\nkimageannotator\n\n'), ['gwenview', 'kimageannotator'])
        for bad in ('gwenview\ngwenview\n', 'Gwen View\n', '-rf\n'):
            with self.assertRaises(ValueError):
                catalog.preview_names(bad)
        self.assertEqual(catalog.required_by(BREAKS), ['emaki-config', 'emaki-desktop'])

    def test_names_and_tokens(self):
        for name in ('gwenview', 'libreoffice-fresh', 'gst-plugins-good', 'lib32-mesa', 'c++utilities', 'r'):
            self.assertEqual(catalog.valid_name(name), name)
        for name in ('', '-Rdd', '--cascade', 'a b', 'A', '../x', 'x' * 200, None):
            with self.assertRaises(ValueError):
                catalog.valid_name(name)
        self.assertEqual(catalog.token(['b', 'a']), 'a,b')
        self.assertFalse(catalog.TOKEN.fullmatch('-c,x'))


class Refusals(unittest.TestCase):
    def test_ready_preview_lists_everything(self):
        answer = catalog.assess('gwenview', 0, 'gwenview\nkimageannotator\n', '')
        self.assertTrue(answer['ok'])
        self.assertEqual(answer['code'], 'ready')
        self.assertEqual(answer['packages'], ['gwenview', 'kimageannotator'])
        self.assertEqual(answer['token'], 'gwenview,kimageannotator')

    def test_required_by_emaki_or_other_software(self):
        answer = catalog.assess('kitty', 1, BREAKS, FAILED)
        self.assertEqual((answer['ok'], answer['code'], answer['requiredBy']),
                         (False, 'emaki', ['emaki-config', 'emaki-desktop']))
        other = BREAKS.replace('emaki-desktop', 'some-suite').replace('emaki-config', 'other-suite')
        self.assertEqual(catalog.assess('kitty', 1, other, FAILED)['code'], 'required')
        self.assertEqual(catalog.assess('kitty', 1, other, FAILED)['requiredBy'], ['other-suite', 'some-suite'])
        self.assertEqual(catalog.assess('kitty', 1, other, FAILED)['token'], '')
        # One Emaki package among the blockers is enough to name Emaki.
        mixed = BREAKS.replace('emaki-desktop', 'some-suite')
        self.assertEqual(catalog.assess('kitty', 1, mixed, FAILED)['code'], 'emaki')

    def test_cascade_into_emaki_or_system_is_refused(self):
        emaki = catalog.assess('thing', 0, 'thing\nemaki-apps\n', '')
        self.assertEqual((emaki['ok'], emaki['code'], emaki['blocked']), (False, 'emaki', ['emaki-apps']))
        for name in ('flatpak', 'linux-lts', 'linux-firmware-intel', 'intel-ucode', 'grub-btrfs', 'snap-pac',
                     'networkmanager', 'pipewire-jack', 'xdg-desktop-portal-gtk', 'nvidia-utils', 'mkinitcpio'):
            answer = catalog.assess('thing', 0, f'thing\n{name}\n', '')
            self.assertEqual((answer['ok'], answer['code'], answer['blocked']), (False, 'system', [name]), name)
            self.assertEqual(answer['token'], '')

    def test_other_failures(self):
        self.assertEqual(catalog.assess('nothere', 1, '', 'error: target not found: nothere\n')['code'], 'not-installed')
        self.assertEqual(catalog.assess('x', 1, '', 'error: failed to init transaction (unable to lock database)\n')['code'], 'busy')
        self.assertEqual(catalog.assess('x', 1, '', 'error: something else\n')['code'], 'failed')
        self.assertEqual(catalog.assess('x', 0, 'y\n', '')['code'], 'failed')
        self.assertEqual(catalog.assess('x', 0, 'x\nx\n', '')['code'], 'failed')

    def test_every_installed_base_package_is_protected(self):
        base = set(constants.PACKAGES) | set(constants.SNAPSHOT_PACKAGES) | {'mkinitcpio'}
        unprotected = {name for name in base if not (catalog.is_system(name) or catalog.is_emaki(name))}
        self.assertEqual(unprotected, set())

    def test_every_emaki_package_is_an_emaki_component(self):
        names = recipe_names()
        self.assertTrue({'emaki', 'emaki-apps', 'niri-emaki', 'quickshell-emaki'} <= names)
        self.assertEqual({name for name in names if not catalog.is_emaki(name)}, set())
        for app in ('gwenview', 'kate', 'libreoffice-fresh', 'obs-studio', 'thunderbird', 'keepassxc'):
            self.assertFalse(catalog.is_emaki(app) or catalog.is_system(app), app)

    def test_messages_are_marked_placeholders(self):
        self.assertTrue(all(text.startswith('[PLACEHOLDER] ') for text in catalog.MESSAGES.values()))


class Listing(unittest.TestCase):
    def test_programs_grouped_by_package(self):
        rows = catalog.programs(APPS, catalog.owners(OWNERS), catalog.info(INFO))
        # kitty is required by emaki-desktop, Updates is Emaki's, NVIDIA Settings is a system
        # package and the personal entry has no package: none of them is listed.
        self.assertEqual([row['package'] for row in rows], ['gwenview', 'libreoffice-fresh'])
        office = rows[1]
        self.assertEqual(office['names'], ['LibreOffice Calc', 'LibreOffice Writer'])
        self.assertEqual(office['ids'], ['libreoffice-calc.desktop', 'libreoffice-writer.desktop'])
        self.assertTrue(office['preinstalled'])
        # A package -Qi did not describe is not listed either.
        self.assertEqual(catalog.programs(APPS, catalog.owners(OWNERS), {}), [])

    def test_listing_uses_fixed_queries(self):
        calls = []

        def fake(*args):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1 if args[0] == '-Qo' else 0,
                                               OWNERS if args[0] == '-Qo' else INFO, '')
        with patch.object(programs, 'desktop_apps', return_value=APPS), patch.object(programs, 'pacman', fake):
            answer = programs.listing()
        self.assertTrue(answer['ok'])
        self.assertEqual([call[0] for call in calls], ['-Qo', '-Qi'])
        self.assertEqual(calls[1][1:], ('gwenview', 'kitty', 'libreoffice-fresh'))


class Helper(unittest.TestCase):
    def run_helper(self, name, token, preview=('gwenview\n', '', 0), owns=True, after=None):
        calls, removed = [], []
        installed = {'gwenview', 'kimageannotator'}

        def fake(*args, timeout=60):
            calls.append(args)
            if args[0] == '-Qlq':
                lines = '/usr/share/applications/org.kde.gwenview.desktop\n' if owns else '/usr/lib/libx.so\n'
                return subprocess.CompletedProcess(args, 0, '/usr/bin/x\n' + lines, '')
            if args[0] == '-Q':
                present = args[1] in installed and (after is None or args[1] in after)
                return subprocess.CompletedProcess(args, 0 if present else 1, '', '')
            if args[0] == '-Rs':
                return subprocess.CompletedProcess(args, preview[2], preview[0], preview[1])
            raise AssertionError(args)

        class Process:
            returncode = 0

            def __init__(self, argv, **options):
                removed.append(argv)
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def communicate(self):
                return ('removing gwenview...\n', None)

        lines = []
        with patch.object(helper, 'pacman', fake), patch.object(helper.subprocess, 'Popen', Process), \
                patch.object(helper.signal, 'signal'), \
                patch('builtins.print', lambda text, flush=False: lines.append(json.loads(text))):
            helper.remove(name, token)
        return calls, removed, lines[-1]

    def test_removes_only_the_reviewed_preview(self):
        calls, removed, reply = self.run_helper('gwenview', 'gwenview', after=set())
        self.assertEqual(removed, [['/usr/bin/pacman', '-Rn', '--noconfirm', 'gwenview']])
        self.assertEqual((reply['ok'], reply['code'], reply['packages']), (True, 'removed', ['gwenview']))
        self.assertIn(('-Rs', '--print', '--print-format', '%n', 'gwenview'), calls)

    def test_commits_every_reviewed_dependency_without_recursion(self):
        _, removed, reply = self.run_helper(
            'gwenview', 'gwenview,kimageannotator',
            preview=('gwenview\nkimageannotator\n', '', 0), after=set())
        self.assertEqual(removed, [['/usr/bin/pacman', '-Rn', '--noconfirm', 'gwenview', 'kimageannotator']])
        self.assertEqual((reply['code'], reply['packages']), ('removed', ['gwenview', 'kimageannotator']))

    def test_changed_preview_removes_nothing(self):
        _, removed, reply = self.run_helper('gwenview', 'gwenview', preview=('gwenview\nkimageannotator\n', '', 0))
        self.assertEqual(removed, [])
        self.assertEqual((reply['ok'], reply['code'], reply['token']), (False, 'changed', 'gwenview,kimageannotator'))

    def test_shrunken_preview_removes_nothing(self):
        _, removed, reply = self.run_helper('gwenview', 'gwenview,kimageannotator')
        self.assertEqual(removed, [])
        self.assertEqual((reply['code'], reply['token']), ('changed', 'gwenview'))

    def test_refusals_remove_nothing(self):
        for preview, code in ((('gwenview\nflatpak\n', '', 0), 'system'),
                              (('gwenview\nemaki-apps\n', '', 0), 'emaki'),
                              ((BREAKS, FAILED, 1), 'emaki')):
            _, removed, reply = self.run_helper('gwenview', 'gwenview', preview=preview)
            self.assertEqual((removed, reply['code']), ([], code))
        _, removed, reply = self.run_helper('gwenview', 'gwenview', owns=False)
        self.assertEqual((removed, reply['code']), ([], 'not-program'))

    def test_partial_or_failed_removal_is_reported(self):
        _, _, reply = self.run_helper('gwenview', 'gwenview')
        self.assertEqual(reply['code'], 'not-removed')

    def test_pending_restore_removes_nothing(self):
        for mode, code in (('pending', 'restart'), ('snapshot', 'restart'), ('unknown', 'failed')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                lines, calls = [], []
                status = subprocess.CompletedProcess([], 0, json.dumps({'mode': mode}), '')
                with patch.object(helper, 'LOCK', str(Path(directory) / 'lock')), \
                        patch.object(helper.os, 'geteuid', return_value=0), \
                        patch.object(helper.os, 'umask'), \
                        patch.object(helper, 'run_status', lambda command: calls.append(command) or status), \
                        patch.object(helper, 'remove', side_effect=AssertionError('must not remove')), \
                        patch('builtins.print', lambda text, flush=False: lines.append(json.loads(text))):
                    self.assertEqual(helper.main(['gwenview', 'gwenview']), 1)
                self.assertEqual((lines[-1]['ok'], lines[-1]['code']), (False, code))
                self.assertEqual(calls[0][1:], ['status', '--json'])
        with tempfile.TemporaryDirectory() as directory:
            removed = []
            status = subprocess.CompletedProcess([], 0, json.dumps({'mode': 'normal'}), '')
            with patch.object(helper, 'LOCK', str(Path(directory) / 'lock')), \
                    patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper.os, 'umask'), \
                    patch.object(helper, 'run_status', lambda command: status), \
                    patch.object(helper, 'remove', lambda *args: removed.append(args)):
                self.assertEqual(helper.main(['gwenview', 'gwenview']), 0)
            self.assertEqual(removed, [('gwenview', 'gwenview')])

    def test_arguments_and_root_are_required(self):
        for argv in ([], ['gwenview'], ['-Rdd', 'x'], ['gwenview', '--cascade'], ['a', 'b', 'c']):
            lines = []
            with patch('builtins.print', lambda text, flush=False: lines.append(json.loads(text))):
                self.assertEqual(helper.main(argv), 1)
            self.assertEqual(lines[-1]['code'], 'failed')

    def test_never_cascades_or_overrides_dependencies(self):
        source = (ROOT / 'update-manager/programs_remove.py').read_text()
        code = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('#'))
        for flag in ("'-Rc", "'-Rd", "'--cascade'", "'--nodeps'", "'-c'", "'-d'", "'-dd'", 'shell=True'):
            self.assertNotIn(flag, code)


class Caller(unittest.TestCase):
    def test_pkexec_runs_only_the_fixed_helper(self):
        seen = []

        class Process:
            def __init__(self, argv, **options):
                seen.append(argv)
                self.stdout = iter([json.dumps(dict(type='finished', **catalog.result('removed', packages=['gwenview']))) + '\n'])

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def wait(self):
                return 0
        with patch.object(programs.subprocess, 'Popen', Process):
            reply = programs.authorized_removal('gwenview', 'gwenview')
        self.assertEqual(seen, [['/usr/bin/pkexec', programs.HELPER, 'gwenview', 'gwenview']])
        self.assertEqual(reply['code'], 'removed')
        self.assertNotIn('type', reply)

    def test_removal_waits_in_its_own_session(self):
        # pkexec authorizes its parent; a closing settings window kills the caller, so
        # the process that waits for pkexec must lead a session of its own.
        def authorized(name, expected):
            return dict(catalog.result('removed', packages=[name]), sid=os.getsid(0), pid=os.getpid())
        with patch.object(programs, 'authorized_removal', authorized):
            reply = programs.remove('gwenview', 'gwenview')
        self.assertEqual(reply['code'], 'removed')
        self.assertNotEqual(reply['pid'], os.getpid())
        self.assertEqual(reply['sid'], reply['pid'])
        self.assertNotEqual(reply['sid'], os.getsid(0))

    def test_cancelled_authentication(self):
        class Process:
            def __init__(self, argv, **options):
                self.stdout = iter([])

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def wait(self):
                return 126
        with patch.object(programs.subprocess, 'Popen', Process):
            self.assertEqual(programs.authorized_removal('gwenview', 'gwenview')['code'], 'not-authorized')
            self.assertEqual(programs.remove('gwenview', 'gwenview')['code'], 'not-authorized')
        with self.assertRaises(ValueError):
            programs.remove('gwenview', '--cascade')

    def test_command_line(self):
        lines = []
        with patch('builtins.print', lambda text, file=None: lines.append(text)), \
                patch.object(programs, 'preview', return_value=catalog.result('ready', packages=['x'], token='x')):
            self.assertEqual(programs.main(['preview', 'x', '--json']), 0)
            self.assertEqual(programs.main(['preview', 'x']), 2)
            self.assertEqual(programs.main(['remove', 'x', '--json']), 2)
        self.assertEqual(json.loads(lines[0])['code'], 'ready')

    def test_legacy_json_also_cleans_program_labels_and_diagnostics(self):
        dirty = 'Paint\u202e\u2066\u200b\ufeff'
        lines = []
        with patch('builtins.print', lambda text: lines.append(text)), \
                patch.object(programs, 'listing', return_value=dict(ok=True, programs=[
                    dict(name=dirty, version=dirty, desktopIds=[dirty])], warnings=[dirty])):
            self.assertEqual(programs.main(['list', '--json']), 0)
        answer = json.loads(lines[0])
        self.assertEqual(answer['programs'], [dict(name='Paint', version='Paint', desktopIds=['Paint'])])
        self.assertEqual(answer['warnings'], ['Paint'])


class Installation(unittest.TestCase):
    def test_policy_authorizes_only_the_fixed_helper(self):
        root = ET.parse(ROOT / 'update-manager/org.emaki.programs.policy').getroot()
        actions = root.findall('action')
        self.assertEqual([action.get('id') for action in actions], ['org.emaki.programs.remove'])
        action = actions[0]
        self.assertEqual(action.findtext('defaults/allow_active'), 'auth_admin')
        self.assertEqual(action.findtext('defaults/allow_any'), 'no')
        path = action.find("annotate[@key='org.freedesktop.policykit.exec.path']").text
        self.assertEqual(path, '@EMAKI_LIBEXECDIR@/emaki-programs-remove')
        makefile = (ROOT / 'Makefile').read_text()
        self.assertIn('update-manager/programs_remove.py $(DESTDIR)$(LIBEXECDIR)/emaki-programs-remove', makefile)
        self.assertIn('update-manager/programs.py $(BIN)/emaki-programs', makefile)
        inventory = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        for path in ('/usr/bin/emaki-programs', '/usr/libexec/emaki/emaki-programs-remove',
                     '/usr/libexec/emaki/programs_catalog.py', '/usr/share/polkit-1/actions/org.emaki.programs.policy'):
            self.assertIn(path, inventory)


if __name__ == '__main__':
    unittest.main()
