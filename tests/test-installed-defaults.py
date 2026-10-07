#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reject leaked live privileges and substituted wallpaper evidence without a VM."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installed_defaults', ROOT / 'tests/vm/check-installed-defaults.py')
defaults = importlib.util.module_from_spec(spec)
spec.loader.exec_module(defaults)


def result(code=0, output='', error=''):
    return subprocess.CompletedProcess([], code, output, error)


class InstalledDefaultsTests(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.work = tempfile.TemporaryDirectory(prefix='installed-defaults-', dir=evidence)
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.home = self.root / 'home/test'
        self.put('etc/sudoers', 'root ALL=(ALL:ALL) ALL\n# NOPASSWD: example only\n')
        self.put('etc/sudoers.d/10-wheel', '%wheel ALL=(ALL:ALL) ALL\n')

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_password_required_policy_passes(self):
        defaults.sudo_files(self.root)

    def test_live_rule_in_any_policy_location_fails(self):
        for name in ('etc/sudoers', 'etc/sudoers.d/unexpected', 'etc/emaki/sudoers.d/wheel'):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_text() if path.exists() else None
                self.put(name, 'live ALL=(ALL) NOPASSWD: ALL\n')
                with self.assertRaisesRegex(ValueError, 'Passwordless'):
                    defaults.sudo_files(self.root)
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original)

    def test_live_file_and_missing_policy_fail(self):
        live = self.put('etc/sudoers.d/10-live', '# stale live policy\n')
        with self.assertRaisesRegex(ValueError, 'Live policy'):
            defaults.sudo_files(self.root)
        live.unlink()
        (self.root / 'etc/sudoers').unlink()
        with self.assertRaises(OSError):
            defaults.sudo_files(self.root)

    def test_effective_denial_requires_password_and_cleared_timestamp(self):
        runner = Mock(side_effect=[result(), result(1, error='sudo: a password is required\n')])
        defaults.sudo_user(runner)
        self.assertEqual([call.args[0] for call in runner.call_args_list],
                         [['sudo', '-K'], ['sudo', '-n', '/usr/bin/true']])
        for responses in ([result(1)], [result(), result()],
                          [result(), result(1, error='not in the sudoers file')],
                          [result(), result(127, error='sudo: not found')]):
            with self.subTest(responses=responses), self.assertRaises(ValueError):
                defaults.sudo_user(Mock(side_effect=responses))

    def wallpaper(self):
        config = (ROOT / 'etc-skel/.config/wpaperd/config.toml').read_text()
        self.put('etc/skel/.config/wpaperd/config.toml', config)
        self.put('home/test/.config/wpaperd/config.toml', config)
        image = self.root / 'usr/share/emaki/wallpaper/fallback.png'
        image.parent.mkdir(parents=True)
        Image.new('RGB', (32, 20), 'red').save(image)
        return image

    def test_shipped_wallpaper_is_read_and_substitution_is_rejected(self):
        image = self.wallpaper()
        defaults.wallpaper_files(self.root, self.home)
        self.put('home/test/.config/wpaperd/config.toml', '[any]\npath="/other.png"\n')
        with self.assertRaisesRegex(ValueError, 'shipped settings'):
            defaults.wallpaper_files(self.root, self.home)
        self.put('home/test/.config/wpaperd/config.toml',
                 (self.root / 'etc/skel/.config/wpaperd/config.toml').read_text())
        image.write_bytes(b'not an image')
        with self.assertRaises(OSError):
            defaults.wallpaper_files(self.root, self.home)

    def test_running_default_process_and_texture_journal(self):
        self.put('proc/123/cmdline', 'wpaperd\0')
        self.put('proc/123/environ', 'HOME=' + str(self.home) + '\0')
        def run(*responses):
            defaults.wallpaper_runtime(Mock(side_effect=responses), self.root / 'proc', self.home)
        run(result(output='123\n'), result(),
            result(output=json.dumps({'Virtual-1': {'current_mode': 0}})),
            result(output=json.dumps([{'output': 'Virtual-1', 'namespace': 'wpaperd-Virtual-1',
                                       'layer': 'Background'}])))
        for responses in ((result(1),), (result(output='123\n'), result(1)),
                          (result(output='123\n'), result(output='Failed to pass the image data to the texture'))):
            with self.subTest(responses=responses), self.assertRaises(ValueError):
                run(*responses)
        self.put('proc/123/cmdline', 'wpaperd\0--config\0/other.toml\0')
        with self.assertRaisesRegex(ValueError, 'overrides'):
            run(result(output='123\n'))
        self.put('proc/123/cmdline', 'wpaperd\0')
        self.put('proc/123/environ', 'XDG_CONFIG_HOME=/other\0')
        with self.assertRaisesRegex(ValueError, 'overrides'):
            run(result(output='123\n'))

    def test_wallpaper_requires_a_background_surface_on_every_active_output(self):
        self.put('proc/123/cmdline', 'wpaperd\0')
        self.put('proc/123/environ', 'HOME=' + str(self.home) + '\0')
        outputs = {'Virtual-1': {'current_mode': 0}, 'Virtual-2': {'current_mode': 1},
                   'Disabled': {'current_mode': None}}
        layers = [{'output': name, 'namespace': 'wpaperd-' + name, 'layer': 'Background'}
                  for name in ('Virtual-1', 'Virtual-2')]

        def run(output_response, layer_response):
            runner = Mock(side_effect=[result(output='123\n'), result(), output_response, layer_response])
            defaults.wallpaper_runtime(runner, self.root / 'proc', self.home)
            self.assertEqual([call.args[0] for call in runner.call_args_list[-2:]],
                             [['niri', 'msg', '--json', 'outputs'], ['niri', 'msg', '--json', 'layers']])

        run(result(output=json.dumps(outputs)), result(output=json.dumps(layers)))
        for bad_layers in ([], layers[:1], [dict(layer, layer='Overlay') for layer in layers],
                           [dict(layer, namespace='another-wallpaper') for layer in layers],
                           [dict(layer, output='Disabled') for layer in layers]):
            with self.subTest(layers=bad_layers), self.assertRaisesRegex(ValueError, 'not mapped'):
                run(result(output=json.dumps(outputs)), result(output=json.dumps(bad_layers)))
        for bad_outputs in ({}, {'Disabled': {'current_mode': None}}):
            with self.subTest(outputs=bad_outputs), self.assertRaisesRegex(ValueError, 'No active output'):
                run(result(output=json.dumps(bad_outputs)), result(output=json.dumps(layers)))
        for output_response, layer_response in (
                (result(1), result()), (result(output='invalid'), result()),
                (result(output=json.dumps(outputs)), result(1)),
                (result(output=json.dumps(outputs)), result(output='invalid')),
                (result(output='[]'), result(output='[]')),
                (result(output='{"Virtual-1": {}}'), result(output='[]')),
                (result(output='{"Virtual-1": {"current_mode": false}}'), result(output='[]')),
                (result(output=json.dumps(outputs)), result(output='{}')),
                (result(output=json.dumps(outputs)), result(output='[null]'))):
            with self.subTest(outputs=output_response, layers=layer_response), self.assertRaises(ValueError):
                run(output_response, layer_response)

    def test_boot_acceptance_invokes_privilege_checks_before_privileged_work(self):
        source = (ROOT / 'tests/vm/iso-boot-check.py').read_text()
        user = source.index("defaults_check('sudo-user')")
        files = source.index("defaults_check('sudo-files', privileged=True)")
        self.assertLess(user, files)
        self.assertLess(files, source.index("check('failed-units'"))
        self.assertIn("defaults_check('wallpaper')", source)
        self.assertIn("{args.session}", source)

    def test_boot_acceptance_rejects_the_wrong_filesystem(self):
        tree = ast.parse((ROOT / 'tests/vm/iso-boot-check.py').read_text())
        check = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == 'check'
                     and node.args and isinstance(node.args[0], ast.Constant)
                     and node.args[0].value == 'root-filesystem')
        predicate = next(keyword.value for keyword in check.keywords if keyword.arg == 'predicate')
        for expected, other in (('btrfs', 'ext4'), ('ext4', 'btrfs')):
            evaluate = eval(compile(ast.Expression(predicate), '<filesystem predicate>', 'eval'),
                            {'config': {'fs': expected}})
            self.assertTrue(evaluate(expected + '\n'))
            self.assertFalse(evaluate(other + '\n'))
            self.assertFalse(evaluate(''))


if __name__ == '__main__':
    unittest.main()
