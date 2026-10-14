#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed programs: preview parsing, refusal rules and the privileged helper's guards, offline."""
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
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

Name            : emaki-config
Required By     : emaki-desktop

Name            : nvidia-settings
Required By     : None
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

    def test_emaki_optional_packages_may_go_but_nothing_they_pull_from_emaki(self):
        answer = catalog.assess('emaki-sudo-key', 0, 'emaki-sudo-key\n', '')
        self.assertEqual((answer['ok'], answer['packages']), (True, ['emaki-sudo-key']))
        refused = catalog.assess('emaki-sudo-key', 0, 'emaki-sudo-key\nemaki-config\n', '')
        self.assertEqual((refused['code'], refused['blocked']), ('emaki', ['emaki-config']))
        self.assertTrue(catalog.assess('emaki-nvidia', 0, 'emaki-nvidia\n', '')['ok'])

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

    def test_messages_are_final_text(self):
        self.assertTrue(all(text and ('[PLACE' + 'HOLDER]') not in text for text in catalog.MESSAGES.values()))


class Listing(unittest.TestCase):
    def test_programs_grouped_by_package(self):
        rows = catalog.programs(APPS, catalog.owners(OWNERS), catalog.info(INFO))
        self.assertEqual([row['package'] for row in rows],
                         ['gwenview', 'kitty', 'libreoffice-fresh', 'nvidia-settings', 'emaki-config'])
        for row in rows:
            protected = row['package'] in ('kitty', 'nvidia-settings', 'emaki-config')
            self.assertEqual(row['protected'], protected)
            self.assertEqual(row['actions'], [] if protected else ['remove'])
        office = rows[2]
        self.assertEqual(office['names'], ['LibreOffice Calc', 'LibreOffice Writer'])
        self.assertEqual(office['ids'], ['libreoffice-calc.desktop', 'libreoffice-writer.desktop'])
        self.assertTrue(office['preinstalled'])
        # A package -Qi did not describe is not listed either.
        self.assertEqual(catalog.programs(APPS, catalog.owners(OWNERS), {}), [])

    def test_required_programs_remain_visible_without_removal(self):
        for name, blockers, protected in (
                ('firefox', ['emaki-desktop'], True),
                ('dolphin', ['emaki-desktop'], True),
                ('cups', ['emaki-apps', 'other-suite'], True),
                ('mpv', ['mpvqt'], False),
                ('avahi', ['cups', 'libcups', 'ostree'], False),
                ('hwloc', ['onetbb'], False),
                ('v4l-utils', ['ffmpeg'], False)):
            path = '/applications/' + name + '.desktop'
            rows = catalog.programs([dict(id=name + '.desktop', name=name, filename=path)],
                                    {path: (name, '1')},
                                    {name: {'Required By': blockers}})
            self.assertEqual(len(rows), 1, name)
            self.assertEqual(rows[0]['actions'], [], name)
            self.assertFalse(rows[0]['removable'], name)
            self.assertEqual(rows[0]['requiredBy'], sorted(blockers), name)
            self.assertEqual(rows[0]['protected'], protected, name)

    def test_optional_and_unneeded_programs_can_be_removed(self):
        for name in ('firefox', 'dolphin', 'mpv'):
            path = '/applications/' + name + '.desktop'
            rows = catalog.programs([dict(id=name + '.desktop', name=name, filename=path)],
                                    {path: (name, '1')},
                                    {name: {'Required By': [], 'Optional For': ['emaki-desktop']}})
            self.assertTrue(rows[0]['preinstalled'])
            self.assertFalse(rows[0]['protected'])
            self.assertTrue(rows[0]['removable'])
            self.assertEqual(rows[0]['actions'], ['remove'])

    def test_desktop_programs_installed_explicitly_and_optional(self):
        from emaki_installer.worker import software_packages
        recipe = ROOT / 'packaging/emaki-desktop/PKGBUILD'
        result = subprocess.run(['bash', '-c', 'source "$1"; printf "%s\\n" "${depends[@]}"; '
                                 'printf "OPTIONAL\\n"; printf "%s\\n" "${optdepends[@]}"',
                                 '_', str(recipe)], capture_output=True, text=True, check=True)
        required, optional = result.stdout.split('OPTIONAL\n')
        for name in constants.DESKTOP_APPS:
            self.assertNotIn(name, required.splitlines())
            self.assertIn(name, optional.splitlines())
            for edition in ('minimal', 'rich'):
                self.assertIn(name, software_packages(edition))
        self.assertIn('kitty', required.splitlines())

    def test_listing_uses_fixed_queries(self):
        calls = []

        def fake(*args):
            calls.append(args)
            if args[0] == '-Qmq':
                return subprocess.CompletedProcess(args, 0, 'gwenview\nyay\n', '')
            return subprocess.CompletedProcess(args, 1 if args[0] == '-Qo' else 0,
                                               OWNERS if args[0] == '-Qo' else INFO, '')
        with patch.object(programs, 'desktop_apps', return_value=APPS), patch.object(programs, 'pacman', fake):
            answer = programs.listing()
        self.assertTrue(answer['ok'])
        self.assertEqual([call[0] for call in calls], ['-Qo', '-Qi', '-Qmq'])
        self.assertEqual(calls[1][1:], ('emaki-config', 'gwenview', 'kitty', 'libreoffice-fresh', 'nvidia-settings'))
        # A package no configured repository has is marked: the window keeps its AUR identity.
        self.assertEqual({row['package']: row['foreign'] for row in answer['programs']},
                         {'gwenview': True, 'kitty': False, 'libreoffice-fresh': False,
                          'nvidia-settings': False, 'emaki-config': False})

    def test_no_foreign_packages_or_an_unreadable_answer_marks_nothing(self):
        # pacman -Qmq exits 1 with no output when nothing is foreign; any failure marks nothing either.
        for code, stdout in ((1, ''), (1, 'gwenview\n'), (0, '')):
            def fake(*args, code=code, stdout=stdout):
                if args[0] == '-Qmq':
                    return subprocess.CompletedProcess(args, code, stdout, 'error')
                return subprocess.CompletedProcess(args, 1 if args[0] == '-Qo' else 0,
                                                   OWNERS if args[0] == '-Qo' else INFO, '')
            with self.subTest(code=code, stdout=stdout), patch.object(programs, 'desktop_apps', return_value=APPS), \
                    patch.object(programs, 'pacman', fake):
                rows = programs.listing()['programs']
            self.assertEqual([row['foreign'] for row in rows], [False] * 5)
        rows = catalog.programs(APPS, catalog.owners(OWNERS), catalog.info(INFO))
        self.assertEqual([row['foreign'] for row in rows], [False] * 5)


class Helper(unittest.TestCase):
    def run_helper(self, name, token, preview=('gwenview\n', '', 0), owns=True, after=None, repository=False):
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
                patch.object(helper.arch, 'inventory', return_value=(
                    {'extra/' + name: {'package': name}} if repository else {}, {name: '1'})), \
                patch.object(helper.signal, 'signal'), \
                patch('builtins.print', lambda text, flush=False: lines.append(json.loads(text))):
            helper.remove(name, token)
        return calls, removed, lines[-1]

    def test_store_and_vital_target_refused_before_any_query(self):
        for name in ('emaki-config', 'emaki-desktop', 'kitty'):
            calls, removed, reply = self.run_helper(name, name)
            self.assertEqual(calls, [])
            self.assertEqual(removed, [])
            self.assertFalse(reply['ok'])
            self.assertEqual(reply['blocked'], [name])

    def test_store_in_transaction_refuses_otherwise_removable_target(self):
        _, removed, reply = self.run_helper('gwenview', 'emaki-config,gwenview',
                                           preview=('gwenview\nemaki-config\n', '', 0))
        self.assertEqual(removed, [])
        self.assertEqual(reply['blocked'], ['emaki-config'])

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

    def test_repository_package_without_desktop_uses_reviewed_removal(self):
        calls, removed, reply = self.run_helper('gwenview', 'gwenview', owns=False, repository=True, after=set())
        self.assertEqual(reply['code'], 'removed')
        self.assertEqual(removed[0][1:], ['-Rn', '--noconfirm', 'gwenview'])
        self.assertIn(('-Rs', '--print', '--print-format', '%n', 'gwenview'), calls)

    def test_repository_package_keeps_protections_and_token_revalidation(self):
        for preview, code in ((('gwenview\nflatpak\n', '', 0), 'system'),
                              (('gwenview\nemaki-apps\n', '', 0), 'emaki'),
                              ((BREAKS, FAILED, 1), 'emaki'),
                              (('gwenview\nkimageannotator\n', '', 0), 'changed')):
            _, removed, reply = self.run_helper('gwenview', 'gwenview', preview=preview,
                                                owns=False, repository=True)
            self.assertEqual((removed, reply['code']), ([], code))

    def test_repository_eligibility_requires_current_installed_and_enabled_inventory(self):
        for available, installed, expected in (({'extra/codec': {'package': 'codec'}}, {'codec': '1'}, True),
                                               ({}, {'codec': '1'}, False),
                                               ({'extra/codec': {'package': 'codec'}}, {}, False)):
            with patch.object(helper.arch, 'inventory', return_value=(available, installed)):
                self.assertEqual(helper.is_repository_package('codec'), expected)
        with patch.object(helper.arch, 'inventory', side_effect=RuntimeError('missing database')):
            self.assertFalse(helper.is_repository_package('codec'))

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
        # Every engine method the system map names is reachable from the command line.
        engine = SimpleNamespace(main=Mock(return_value=0))
        with patch.dict(sys.modules, programs_engine=engine):
            for method in ('catalog', 'search', 'details', 'related', 'plan', 'execute', 'updates', 'aur',
                           'popular', 'serve'):
                self.assertEqual(programs.main([method, '--json']), 0)
        self.assertEqual(engine.main.call_count, 10)

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


class Popularity(unittest.TestCase):
    # Schema-derived fixtures, not captured live responses. Contracts are linked
    # beside the production endpoints; the test suite requires no network.
    ARCH = {'packagePopularities': [{'name': 'firefox', 'popularity': 43.2},
                                    {'name': 'gimp', 'popularity': 0}]}
    FLATHUB = {'hits': [{'app_id': 'org.mozilla.firefox', 'installs_last_month': 321}]}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = catalog.PopularityCache(self.temporary.name)
        self.cache.cache.mkdir()

    def test_source_contracts(self):
        self.assertEqual(catalog._parse_popularity(self.ARCH, 'arch'), {'firefox': 43.2, 'gimp': 0})
        self.assertEqual(catalog._parse_popularity(self.FLATHUB, 'flathub'), {'org.mozilla.firefox': 321})
        for source, key, identifier, field in (
                ('arch', 'packagePopularities', 'firefox', 'popularity'),
                ('flathub', 'hits', 'org.mozilla.firefox', 'installs_last_month')):
            name = 'name' if source == 'arch' else 'app_id'
            for score in (True, None, '42', -1, float('nan'), float('inf'), 10 ** 400):
                with self.subTest(source=source, score=str(score)):
                    with self.assertRaises(ValueError):
                        catalog._parse_popularity({key: [{name: identifier, field: score}]}, source)
            for value in ([], {}, {key: {}}, {key: [None]}, {key: [{}]},
                          {key: [{name: '../bad', field: 2}]}):
                with self.assertRaises(ValueError):
                    catalog._parse_popularity(value, source)
        with self.assertRaises(ValueError):
            catalog._parse_popularity({'packagePopularities': [{'name': 'firefox', 'popularity': 101}]}, 'arch')
        with patch.object(catalog, '_POPULARITY_ROWS', 1), self.assertRaises(ValueError):
            catalog._parse_popularity(self.ARCH, 'arch')

    def test_snapshot_launches_without_fetching_and_suppresses_repeat(self):
        with patch.object(catalog.subprocess, 'Popen') as launch, patch.object(catalog, '_fetch_popularity') as fetch:
            self.assertEqual(self.cache.snapshot(), {'arch': {}, 'flathub': {}})
            self.cache.snapshot()
            launch.assert_called_once()
            fetch.assert_not_called()
            options = launch.call_args.kwargs
            self.assertTrue(options['start_new_session'])
            self.assertTrue(options['close_fds'])
            for name in ('stdin', 'stdout', 'stderr'):
                self.assertEqual(options[name], subprocess.DEVNULL)

    def test_bad_rows_are_skipped_unless_they_are_the_majority(self):
        for source, key, name, field, identifiers in (
                ('arch', 'packagePopularities', 'name', 'popularity', ('firefox', 'gimp')),
                ('flathub', 'hits', 'app_id', 'installs_last_month', ('org.mozilla.firefox', 'org.gimp.GIMP'))):
            good = [{name: identifier, field: 5} for identifier in identifiers]
            for bad in (None, {}, [], {name: []}, {name: '../bad', field: 3},
                        {name: identifiers[0], field: None}, {name: identifiers[0], field: 8},
                        {name: 'x' * 256, field: 4}, {name: identifiers[0], field: float('nan')}):
                with self.subTest(source=source, bad=bad):
                    expected = dict.fromkeys(identifiers, 5)
                    self.assertEqual(catalog._parse_popularity({key: good + [bad]}, source), expected)
                    # Exactly half invalid remains usable; only a strict majority rejects it.
                    self.assertEqual(catalog._parse_popularity({key: good + [bad, bad]}, source), expected)
                    with self.assertRaises(ValueError):
                        catalog._parse_popularity({key: good + [bad, bad, bad]}, source)
            self.assertEqual(catalog._parse_popularity({key: []}, source), {})

    def test_cache_values_skip_bad_entries_and_keep_size_bounds(self):
        values = {'firefox': 4, 'gimp': 3, '../bad': 8}
        self.assertEqual(catalog._popularity_values(values, 'arch'), {'firefox': 4, 'gimp': 3})
        self.cache._write('arch', 100000, values)
        self.assertEqual(self.cache._read('arch', 100001), {'firefox': 4, 'gimp': 3})
        with self.assertRaises(ValueError):
            catalog._popularity_values({'firefox': 4, '../bad': 8, 'gimp': None}, 'arch')
        with patch.object(catalog, '_POPULARITY_ROWS', 2), self.assertRaises(ValueError):
            catalog._popularity_values(values, 'arch')

    def test_success_daily_expiry_and_atomic_records(self):
        def fetch(source):
            return catalog._parse_popularity(self.ARCH if source == 'arch' else self.FLATHUB, source)
        with patch.object(catalog.time, 'time', return_value=100000), patch.object(catalog, '_fetch_popularity', side_effect=fetch) as network:
            self.cache.refresh()
            self.cache.refresh()
            self.assertEqual(network.call_count, 2)
            with patch.object(catalog.subprocess, 'Popen') as launch:
                self.assertEqual(self.cache.snapshot()['arch']['gimp'], 0)
                launch.assert_not_called()
        self.assertEqual(sorted(p.name for p in self.cache.cache.iterdir()), ['arch.json', 'flathub.json', 'refresh.lock'])
        with patch.object(catalog.time, 'time', return_value=186400), patch.object(catalog.subprocess, 'Popen') as launch:
            self.assertEqual(self.cache.snapshot(), {'arch': {}, 'flathub': {}})
            launch.assert_called_once()

    def test_failure_is_recorded_before_fetch_and_throttled(self):
        def fail(source):
            self.assertEqual(self.cache._read(source, 100000), {})
            raise OSError('offline')
        with patch.object(catalog.time, 'time', return_value=100000), patch.object(catalog, '_fetch_popularity', side_effect=fail) as network:
            self.cache.refresh()
            catalog.PopularityCache(self.temporary.name).refresh()
            self.assertEqual(network.call_count, 2)
            with patch.object(catalog.subprocess, 'Popen') as launch:
                self.assertEqual(self.cache.snapshot(), {'arch': {}, 'flathub': {}})
                launch.assert_not_called()
        with patch.object(catalog.time, 'time', return_value=103599), patch.object(catalog, '_fetch_popularity') as network:
            self.cache.refresh()
            network.assert_not_called()
        with patch.object(catalog.time, 'time', return_value=103600), patch.object(catalog, '_fetch_popularity', return_value={}) as network:
            self.cache.refresh()
            self.assertEqual(network.call_count, 2)

    def test_killed_fetch_retries_after_one_hour(self):
        script = '''import os, signal, sys
import programs_catalog as catalog
catalog.time.time = lambda: 100000
def killed(source):
    os.kill(os.getpid(), signal.SIGKILL)
catalog._fetch_popularity = killed
catalog.PopularityCache(sys.argv[1]).refresh()
'''
        result = subprocess.run([sys.executable, '-c', script, self.temporary.name],
                                cwd=ROOT / 'update-manager', capture_output=True, timeout=10)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        record = json.loads((self.cache.cache / 'arch.json').read_text())
        self.assertTrue(record['failed'])
        self.assertEqual(record['values'], {})
        self.assertEqual(self.cache._read('arch', 103599), {})
        self.assertIsNone(self.cache._read('arch', 103600))
        with patch.object(catalog.time, 'time', return_value=103600), patch.object(catalog, '_fetch_popularity', return_value={}) as fetch:
            catalog.PopularityCache(self.temporary.name).refresh()
            self.assertEqual([call.args[0] for call in fetch.call_args_list], ['arch', 'flathub'])

    def test_serve_launch_throttle_expires_after_one_hour(self):
        with patch.object(catalog.time, 'time', return_value=100000) as now, patch.object(catalog.subprocess, 'Popen') as launch:
            self.cache.snapshot()
            now.return_value = 103599
            self.cache.snapshot()
            launch.assert_called_once()
            now.return_value = 103600
            self.cache.snapshot()
            self.assertEqual(launch.call_count, 2)

    def test_failed_worker_launch_retries_after_one_hour(self):
        with (patch.object(catalog.time, 'time', return_value=100000) as now,
              patch.object(catalog.subprocess, 'Popen', side_effect=OSError('unavailable')) as launch):
            self.assertEqual(self.cache.snapshot(), {'arch': {}, 'flathub': {}})
            now.return_value = 103599
            self.cache.snapshot()
            launch.assert_called_once()
            now.return_value = 103600
            self.cache.snapshot()
            self.assertEqual(launch.call_count, 2)

    def test_failed_source_retry_preserves_other_source_daily_hold(self):
        self.cache._write('arch', 100000, {}, failed=True)
        self.cache._write('flathub', 100000, {'org.mozilla.firefox': 3})
        with (patch.object(catalog.time, 'time', return_value=103600),
              patch.object(catalog, '_fetch_popularity', return_value={'firefox': 4}) as fetch):
            self.cache.refresh()
            fetch.assert_called_once_with('arch')
        self.assertEqual(self.cache._read('arch', 103600), {'firefox': 4})
        self.assertEqual(self.cache._read('flathub', 103600), {'org.mozilla.firefox': 3})

    def test_empty_success_holds_a_day_but_legacy_attempt_only_an_hour(self):
        self.cache._write('arch', 100000, {})
        self.assertEqual(self.cache._read('arch', 103600), {})
        self.assertEqual(self.cache._read('arch', 186399), {})
        self.assertIsNone(self.cache._read('arch', 186400))
        path = self.cache.cache / 'arch.json'
        path.write_text(json.dumps({'attempted': 100000, 'values': {}}))
        self.assertEqual(self.cache._read('arch', 103599), {})
        self.assertIsNone(self.cache._read('arch', 103600))
        path.write_text(json.dumps({'attempted': 100000, 'values': {'firefox': 3}}))
        self.assertEqual(self.cache._read('arch', 186399), {'firefox': 3})
        self.assertIsNone(self.cache._read('arch', 186400))

    def test_source_timeout_does_not_prevent_next_source(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status = 200
        def read(size):
            if opener.open.call_count == 1:
                os.kill(os.getpid(), signal.SIGALRM)
            return json.dumps(self.FLATHUB).encode()
        response.read.side_effect = read
        opener = Mock()
        opener.open.return_value = response
        previous = signal.getsignal(signal.SIGALRM)
        with (patch.object(catalog.urllib.request, 'build_opener', return_value=opener),
              patch.object(catalog.signal, 'alarm') as alarm,
              patch.object(catalog.time, 'time', return_value=100000)):
            self.cache.refresh()
        self.assertEqual(opener.open.call_count, 2)
        self.assertGreaterEqual(catalog._POPULARITY_DEADLINE, 60)
        self.assertEqual([call.args[0] for call in alarm.call_args_list],
                         [catalog._POPULARITY_DEADLINE, 0, catalog._POPULARITY_DEADLINE, 0])
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(self.cache._read('arch', 100000), {})
        self.assertEqual(self.cache._read('flathub', 100000), {'org.mozilla.firefox': 321})

    def test_lock_does_not_wait_or_fetch(self):
        with (self.cache.cache / 'refresh.lock').open('wb') as lock:
            catalog.fcntl.flock(lock, catalog.fcntl.LOCK_EX | catalog.fcntl.LOCK_NB)
            with patch.object(catalog, '_fetch_popularity') as network:
                self.cache.refresh()
                network.assert_not_called()

    def test_cache_rejects_future_nonfinite_oversize_and_special_files(self):
        path = self.cache.cache / 'arch.json'
        for timestamp in (True, float('nan'), float('inf'), 100001, -1, '100000'):
            path.write_text(json.dumps({'attempted': timestamp, 'values': {'firefox': 2}}))
            self.assertIsNone(self.cache._read('arch', 100000))
        path.write_text('x' * 100)
        with patch.object(catalog, '_POPULARITY_BYTES', 99):
            self.assertIsNone(self.cache._read('arch', 100000))
        path.unlink()
        os.mkfifo(path)
        self.assertIsNone(self.cache._read('arch', 100000))
        path.unlink()
        path.symlink_to(self.cache.cache / 'missing')
        self.assertIsNone(self.cache._read('arch', 100000))

    def test_failed_atomic_replace_preserves_previous_record(self):
        self.cache._write('arch', 100000, {'firefox': 1})
        with patch.object(catalog.os, 'replace', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                self.cache._write('arch', 100001, {'firefox': 2})
        self.assertEqual(self.cache._read('arch', 100002), {'firefox': 1})
        self.assertEqual([p.name for p in self.cache.cache.iterdir()], ['arch.json'])

    def test_response_size_and_http_status(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(catalog.urllib.request, 'build_opener', return_value=opener):
            response.status = 503
            with self.assertRaises(ValueError):
                catalog._fetch_popularity('arch')
            response.status = 200
            response.read.return_value = b'x' * 101
            with patch.object(catalog, '_POPULARITY_BYTES', 100), self.assertRaises(ValueError):
                catalog._fetch_popularity('arch')
            response.read.return_value = json.dumps(self.ARCH).encode()
            self.assertEqual(catalog._fetch_popularity('arch')['firefox'], 43.2)


if __name__ == '__main__':
    unittest.main()
