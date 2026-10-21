#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated locale precedence and wrapper failure boundaries."""
import importlib.util
import importlib.machinery
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location('locale_test', ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Backends(unittest.TestCase):
    def test_installed_boot_catalogue_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / 'prefix/lib/emaki/boot/emaki_boot'
            package.mkdir(parents=True)
            directory = root / 'prefix/share/emaki/locale'
            installed = package / 'i18n.py'
            installed.write_text((ROOT / 'installer/emaki_installer/i18n.py').read_text().replace(
                '@EMAKI_DATADIR@', str(directory.parent)))
            with patch.dict(os.environ, {'EMAKI_LANGUAGE': 'en_XA'}, clear=True):
                with patch('gettext.translation') as translation:
                    load(installed)
                self.assertEqual(translation.call_args.kwargs['localedir'], str(directory))

    def test_system_locale_ignores_pam_override_without_evaluating_code(self):
        module = load('scripts/emaki_i18n.py')
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'locale.conf'
            config.write_text('LANG="pt_BR.UTF-8"\nLC_MESSAGES=es_MX.UTF-8\n# retained comment\n')
            with patch.dict(os.environ, {'LC_MESSAGES': 'C', 'LANG': 'C'}, clear=True):
                self.assertEqual(module.system_languages(config), ['es_MX.UTF-8'])
            with patch.dict(os.environ, {'EMAKI_LANGUAGE': 'en_XA'}, clear=True):
                self.assertEqual(module.system_languages(config), ['en_XA'])

    def test_lock_keeps_ui_language_before_pam_override(self):
        loader = importlib.machinery.SourceFileLoader('locale_lock', str(ROOT / 'scripts/emaki-lock'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        lock = importlib.util.module_from_spec(spec)
        loader.exec_module(lock)
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = lock.Supervisor(Path(tmp))
            self.addCleanup(supervisor.selector.close)
            for environment, expected in [({'LANG': 'pt_BR.UTF-8'}, 'pt_BR.UTF-8'),
                                          ({'LANG': 'en_US.UTF-8', 'LANGUAGE': 'pt_BR:es'}, 'pt_BR:es:en_US.UTF-8'),
                                          ({'LC_ALL': 'C', 'LANGUAGE': 'pt_BR'}, 'en'),
                                          ({'LC_ALL': 'C', 'EMAKI_LANGUAGE': 'en_XA'}, 'en_XA')]:
                with patch.dict(os.environ, environment, clear=True), \
                        patch.object(lock.subprocess, 'Popen') as spawn, \
                        patch.object(supervisor, 'record_child'):
                    supervisor.spawn()
                child_env = spawn.call_args.kwargs['env']
                self.assertEqual(child_env['EMAKI_LANGUAGE'], expected)
                self.assertEqual(child_env['LC_MESSAGES'], 'C')
                self.assertNotIn('LC_ALL', child_env)

    def test_locale_precedence(self):
        for path in ('scripts/emaki_i18n.py', 'installer/emaki_installer/i18n.py'):
            module = load(path)
            cases = [({'LC_ALL': 'C', 'LANGUAGE': 'en_XA'}, ['en']),
                     ({'LC_ALL': 'C.UTF-8', 'LANGUAGE': 'en_XA'}, ['en']),
                     ({'LC_MESSAGES': 'POSIX', 'LANGUAGE': 'en_XA'}, ['en']),
                     ({'LC_ALL': 'en_US.UTF-8', 'LANGUAGE': 'pt_BR:es'},
                      ['pt_BR', 'es', 'en_US.UTF-8']),
                     ({'LC_ALL': 'C', 'EMAKI_LANGUAGE': 'en_XA'}, ['en_XA']),
                     ({'EMAKI_LANGUAGE': 'pt_BR:es'}, ['pt_BR', 'es']),
                     ({}, ['en'])]
            for env, expected in cases:
                with self.subTest(path=path, env=env), patch.dict(os.environ, env, clear=True):
                    self.assertEqual(module.selected_languages(), expected)

    def test_wrappers_ignore_working_directory_and_survive_translation_failure(self):
        for name in ('notes/emaki-notes', 'update-manager/emaki-update-manager', 'installer/ui/emaki-install'):
            with self.subTest(wrapper=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wrapper = root / name
                wrapper.parent.mkdir(parents=True)
                shutil.copyfile(ROOT / name, wrapper)
                scripts = root / 'scripts'
                scripts.mkdir()
                shutil.copyfile(ROOT / 'scripts/emaki_i18n.py', scripts / 'emaki_i18n.py')
                shutil.copytree(ROOT / 'installer/emaki_installer', root / 'installer/emaki_installer', dirs_exist_ok=True)
                marker = root / 'imported'
                (root / 'gettext.py').write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n')
                env = dict(os.environ, HOME=tmp, EMAKI_LOCALE_DIR=str(root / 'locale'),
                           LC_ALL='C', PYTHONPATH=tmp)
                env.pop('XDG_RUNTIME_DIR', None)
                for python in (sys.executable, shutil.which('false')):
                    (scripts / 'paths').write_text('EMAKI_PYTHON=' + python + '\n')
                    # The installer uses python3 from PATH; its missing-module path exercises fallback.
                    if name.startswith('installer') and python != sys.executable:
                        shutil.rmtree(root / 'installer/emaki_installer')
                    args = ['bash', str(wrapper)] + ([] if name.startswith('installer') else ['--invalid'])
                    result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=5)
                    self.assertEqual(result.returncode, 1 if name.startswith('installer') else 2, result.stderr)
                    self.assertIn('XDG_RUNTIME_DIR: The installer needs a user runtime directory'
                                  if name.startswith('installer') else 'Usage: emaki-', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
