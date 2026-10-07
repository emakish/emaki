#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run both real user generators against isolated, installed XDG paths."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENV_GENERATOR = '/usr/lib/systemd/user-environment-generators/30-systemd-environment-d-generator'
AUTOSTART_GENERATOR = '/usr/lib/systemd/user-generators/systemd-xdg-autostart-generator'
POLICY = ROOT / 'packaging/emaki-config/60-emaki-xdg.conf'
UNIT = 'app-org.kde.kdeconnect.daemon@autostart.service'


def probe(work, config_dirs):
    env = {'PATH': '/usr/bin', 'HOME': str(work / 'home'),
           'XDG_CONFIG_HOME': str(work / 'home/.config')}
    if config_dirs != 'unset':
        env['XDG_CONFIG_DIRS'] = config_dirs
    generated = subprocess.run([ENV_GENERATOR], env=env, text=True, capture_output=True, check=True)
    for assignment in shlex.split(generated.stdout):
        key, value = assignment.split('=', 1)
        env[key] = value
    subprocess.run([AUTOSTART_GENERATOR, *[str(work / 'output')] * 3], env=env, check=True)
    return {'config_dirs': env.get('XDG_CONFIG_DIRS'),
            'units': sorted(path.name for path in (work / 'output').glob('*.service'))}


class AutostartTests(unittest.TestCase):
    def generated(self, config_dirs, policy=True, installed=None):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='autostart-', dir=evidence) as temporary:
            work = Path(temporary)
            for name in ('environment', 'empty', 'xdg/autostart', 'vendor/autostart',
                         'custom/autostart', 'home/.config', 'output'):
                (work / name).mkdir(parents=True)
            # Missing policy behaves like the old package, so the regression fails
            # on generation of the actual unit rather than on a fixture assertion.
            policy_source = installed / 'usr/lib/environment.d' / POLICY.name if installed else POLICY
            if policy and policy_source.exists():
                shutil.copyfile(policy_source, work / 'environment' / POLICY.name)
            desktop = 'org.kde.kdeconnect.daemon.desktop'
            vendor = (installed / 'usr/share/emaki/xdg/autostart' / desktop if installed
                      else ROOT / 'packaging/emaki-config' / desktop)
            shutil.copyfile(vendor, work / 'vendor/autostart' / desktop)
            entry = '[Desktop Entry]\nType=Application\nName=Fixture\nExec=/usr/bin/true\n'
            if installed:
                shutil.copyfile(installed / 'etc/xdg/autostart' / desktop,
                                work / 'xdg/autostart' / desktop)
            else:
                (work / 'xdg/autostart' / desktop).write_text(entry)
            (work / 'xdg/autostart/unrelated.desktop').write_text(entry)
            (work / 'custom/autostart/custom.desktop').write_text(entry)
            command = ['bwrap', '--die-with-parent', '--ro-bind', '/', '/',
                       '--bind', str(work), str(work), '--tmpfs', '/etc',
                       '--tmpfs', '/run', '--tmpfs', '/usr/local', '--tmpfs', '/usr/share']
            for source, destination in (
                ('environment', '/usr/lib/environment.d'), ('empty', '/usr/local/lib/environment.d'),
                ('empty', '/etc/environment.d'), ('empty', '/run/environment.d'),
                ('xdg', '/etc/xdg'), ('vendor', '/usr/share/emaki/xdg'), ('custom', '/run/custom-xdg'),
            ):
                command += ['--ro-bind', str(work / source), destination]
            result = subprocess.run(command + [sys.executable, str(Path(__file__).resolve()),
                                               '--probe', str(work), config_dirs],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_first_login_uses_environment_generator_policy(self):
        # The environment.d fallback handles unset values; an explicitly empty
        # value retains systemd's existing empty-search-path semantics. The
        # direct session wrapper also normalizes that separate login case.
        for configured in ('unset', '/run/custom-xdg:/etc/xdg'):
            with self.subTest(configured=configured):
                result = self.generated(configured)
                self.assertNotIn(UNIT, result['units'])
                self.assertIn('app-unrelated@autostart.service', result['units'])
                suffix = '/etc/xdg' if configured == 'unset' else configured
                self.assertEqual(result['config_dirs'], '/usr/share/emaki/xdg:' + suffix)
                if configured.startswith('/run/custom-xdg'):
                    self.assertIn('app-custom@autostart.service', result['units'])

    def test_old_package_generates_kdeconnect_unit(self):
        result = self.generated('unset', policy=False)
        self.assertIn(UNIT, result['units'])
        self.assertIn('app-unrelated@autostart.service', result['units'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--probe':
        print(json.dumps(probe(Path(sys.argv[2]), sys.argv[3])))
    else:
        unittest.main()
