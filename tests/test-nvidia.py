#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise packaged graphics policy without hardware or host service calls."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'packaging/emaki-nvidia'
NAMES = ('NVD_BACKEND', 'LIBVA_DRIVER_NAME', '__GLX_VENDOR_LIBRARY_NAME')


class NvidiaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='emaki-nvidia-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.sysfs = self.base / 'sys'
        self.runtime_dir = self.base / 'runtime'
        self.runtime_dir.mkdir()
        for source in (PACKAGE / 'runtime.py', ROOT / 'installer/emaki_installer/graphics.py'):
            shutil.copyfile(source, self.runtime_dir / source.name)
        spec = importlib.util.spec_from_file_location('nvidia_fixture', self.runtime_dir / 'runtime.py')
        self.runtime = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', list(sys.path)), patch.dict(sys.modules):
            sys.modules.pop('graphics', None)
            spec.loader.exec_module(self.runtime)

    def gpu(self, card, vendor=0x10de, device=0x2684, driver='nvidia', active=False):
        pci = self.sysfs / f'bus/pci/devices/0000:0{card}:00.0'
        pci.mkdir(parents=True)
        for name, value in [('vendor', vendor), ('device', device), ('class', 0x030000)]:
            (pci / name).write_text(hex(value))
        if driver:
            (pci / 'driver').symlink_to(self.sysfs / 'bus/pci/drivers' / driver)
        drm = self.sysfs / f'class/drm/card{card}'
        drm.mkdir(parents=True)
        (drm / 'device').symlink_to(pci)
        connector = self.sysfs / f'class/drm/card{card}-DP-1'
        connector.mkdir()
        (connector / 'status').write_text('connected')
        (connector / 'enabled').write_text('enabled' if active else 'disabled')
        return pci, connector

    def test_hybrid_intel_scanout_keeps_automatic_video_selection(self):
        self.gpu(0, 0x8086, 0x5917, 'i915', True)
        self.gpu(1)
        self.assertEqual(self.runtime.environment(self.sysfs), {'NVD_BACKEND': 'direct'})

    def test_nvidia_desktop_and_connected_but_disabled_output(self):
        _, connector = self.gpu(0, active=True)
        self.assertEqual(self.runtime.environment(self.sysfs),
                         dict(zip(NAMES, ('direct', 'nvidia', 'nvidia'))))
        (connector / 'enabled').write_text('disabled')
        self.assertEqual(self.runtime.environment(self.sysfs), {'NVD_BACKEND': 'direct'})

    def test_non_open_device_does_not_get_session_policy(self):
        self.gpu(0, device=0x1b80, active=True)
        self.assertEqual(self.runtime.environment(self.sysfs), {})

    def test_mixed_active_displays_and_unplugging(self):
        self.gpu(0, 0x8086, 0x5917, 'i915', True)
        _, connector = self.gpu(1, active=True)
        self.assertEqual(self.runtime.environment(self.sysfs), {'NVD_BACKEND': 'direct'})
        (connector / 'status').write_text('disconnected')
        self.assertEqual(self.runtime.environment(self.sysfs), {'NVD_BACKEND': 'direct'})

    def test_no_nvidia_or_nouveau_has_no_environment(self):
        self.gpu(0, 0x1002, 0x73df, 'amdgpu', True)
        self.assertEqual(self.runtime.environment(self.sysfs), {})
        self.gpu(1, driver='nouveau', active=True)
        self.assertEqual(self.runtime.environment(self.sysfs), {})

    def kernels(self):
        for release, package in [('6.20-main', 'linux'), ('6.18-lts', 'linux-lts'), ('other', 'other')]:
            directory = self.base / 'usr/lib/modules' / release
            directory.mkdir(parents=True)
            (directory / 'pkgbase').write_text(package)
        (self.base / 'etc/modprobe.d').mkdir(parents=True)
        (self.base / 'etc/modprobe.d/blacklist.conf').write_text('blacklist nouveau\n')

    def test_guard_checks_all_four_modules_for_both_kernels_without_mutation(self):
        self.kernels()
        before = {str(p.relative_to(self.base)): p.read_bytes()
                  for p in (self.base / 'etc').rglob('*') if p.is_file()}
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=int(argv[-2:] == ['6.18-lts', 'nvidia_uvm']))
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(self.runtime.check(self.base, run=run), 1)
        self.assertEqual(len(calls), 8)
        self.assertEqual({tuple(call[:3]) for call in calls}, {('modinfo', '-b', str(self.base))})
        self.assertEqual({call[4] for call in calls}, {'6.20-main', '6.18-lts'})
        self.assertIn('6.18-lts', error.getvalue())
        self.assertEqual(before, {str(p.relative_to(self.base)): p.read_bytes()
                                 for p in (self.base / 'etc').rglob('*') if p.is_file()})
        self.assertEqual(self.runtime.check(self.base, run=lambda *a, **kw: SimpleNamespace(returncode=0)), 0)

    def test_removed_boot_action_cannot_gate_greeter(self):
        self.assertFalse((PACKAGE / 'greetd.conf').exists())
        with patch.object(sys, 'argv', ['runtime.py', 'boot']), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.runtime.main()
        self.assertEqual(error.exception.code, 2)

    def test_missing_installed_kernels_refuses_guard(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.runtime.check(self.base), 1)

    def executable(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def session_fixture(self):
        binary = self.base / 'bin'
        binary.mkdir()
        (binary / 'readlink').symlink_to(shutil.which('readlink'))
        # Every external session command resolves to a recorder, never the host.
        recorder = f'''#!{sys.executable}
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
base = pathlib.Path(os.environ['FIXTURE'])
with (base / 'calls').open('a') as stream:
    stream.write(json.dumps([name, args, dict(os.environ)]) + '\\n')
if name == 'systemctl' and args == ['--user', '--wait', 'start', 'niri-emaki.service']:
    (base / 'launched').write_text((base / 'manager').read_text())
if name == 'cat':
    print('fixture-session-token')
if name == 'systemctl' and 'is-active' in args:
    sys.exit(1)
if name in ('systemctl', 'dbus-update-activation-environment', 'emaki-session-import-environment'):
    path = base / ('dbus' if name.startswith('dbus') else 'manager')
    state = json.loads(path.read_text()) if path.exists() else {{}}
    if name == 'emaki-session-import-environment' or '--all' in args:
        state.update(os.environ)
    elif 'unset-environment' in args:
        for key in args[args.index('unset-environment') + 1:]:
            state.pop(key, None)
    else:
        for arg in args:
            if '=' in arg:
                key, value = arg.split('=', 1)
                state[key] = value
    path.write_text(json.dumps(state))
    # dbus-broker forwards UpdateActivationEnvironment to SetEnvironment.
    if name == 'dbus-update-activation-environment':
        manager_path = base / 'manager'
        manager = json.loads(manager_path.read_text()) if manager_path.exists() else {{}}
        if '--all' in args:
            manager.update(os.environ)
        else:
            manager.update(arg.split('=', 1) for arg in args if '=' in arg)
        manager_path.write_text(json.dumps(manager))
'''
        for name in ('niri-emaki', 'systemctl', 'dbus-update-activation-environment',
                     'emaki-session-import-environment', 'cat'):
            self.executable(binary / name, recorder)
        policy = self.base / 'session.sh'
        text = (PACKAGE / 'session.sh').read_text().replace(
            '/usr/lib/emaki/nvidia/runtime.py environment',
            shlex.quote(str(self.runtime_dir / 'runtime.py')) + ' environment --sysfs-root ' + shlex.quote(str(self.sysfs)))
        policy.write_text(text)
        wrapper = self.base / 'wrapper'
        self.executable(wrapper, (ROOT / 'scripts/niri-emaki-session').read_text().replace(
            '$EMAKI_DATADIR/nvidia/session.sh', str(policy)))
        shutil.copyfile(ROOT / 'scripts/paths', self.base / 'paths')
        env = {'PATH': str(binary), 'HOME': str(self.base / 'home'), 'SHELL': '',
               'FIXTURE': str(self.base)}
        return wrapper, env

    def test_actual_compositor_wrapper_sources_optional_policy(self):
        self.gpu(0, active=True)
        wrapper, env = self.session_fixture()
        subprocess.run(['/bin/bash', str(wrapper), '--compositor'], env=env, check=True, timeout=5)
        calls = [json.loads(line) for line in (self.base / 'calls').read_text().splitlines()]
        self.assertEqual([entry[0] for entry in calls], ['niri-emaki'])
        self.assertEqual({name: calls[-1][2][name] for name in NAMES},
                         dict(zip(NAMES, ('direct', 'nvidia', 'nvidia'))))
        (self.base / 'session.sh').unlink()
        subprocess.run(['/bin/bash', str(wrapper), '--compositor'], env=env, check=True, timeout=5)
        last = json.loads((self.base / 'calls').read_text().splitlines()[-1])
        self.assertTrue(all(name not in last[2] for name in NAMES))

    def test_relogin_clears_manager_without_empty_dbus_broker_assignments(self):
        pci, connector = self.gpu(0, active=True)
        wrapper, env = self.session_fixture()
        for state in ('manager', 'dbus'):
            (self.base / state).write_text(json.dumps(dict.fromkeys(NAMES, 'stale')))
        subprocess.run(['/bin/bash', str(wrapper)], env=env, check=True, timeout=5)
        (connector / 'status').write_text('disconnected')
        (pci / 'driver').unlink()
        subprocess.run(['/bin/bash', str(wrapper)], env=env, check=True, timeout=5)
        calls = [json.loads(line) for line in (self.base / 'calls').read_text().splitlines()]
        imports = [entry for entry in calls if entry[0] == 'emaki-session-import-environment']
        self.assertEqual(len(imports), 2)
        self.assertEqual({key: imports[0][2][key] for key in NAMES},
                         dict(zip(NAMES, ('direct', 'nvidia', 'nvidia'))))
        self.assertTrue(all(key not in imports[1][2] for key in NAMES))
        launched = json.loads((self.base / 'launched').read_text())
        self.assertTrue(all(key not in launched for key in NAMES))
        manager = json.loads((self.base / 'manager').read_text())
        dbus = json.loads((self.base / 'dbus').read_text())
        self.assertTrue(all(key not in manager for key in NAMES))
        self.assertEqual({key: dbus[key] for key in NAMES},
                         dict(zip(NAMES, ('direct', 'nvidia', 'nvidia'))))
        for imported in imports:
            prior = calls[calls.index(imported) - 1]
            self.assertEqual(prior[1][:2], ['--user', 'unset-environment'])
        self.assertTrue(all(entry[1] == ['--all'] for entry in calls
                            if entry[0] == 'dbus-update-activation-environment'))

    def test_profile_overrides_survive_compositor_and_manager_import(self):
        self.gpu(0, active=True)
        wrapper, env = self.session_fixture()
        overrides = dict(zip(NAMES, ('egl', 'iHD', 'mesa')))
        env.update(overrides)
        for args in (['--compositor'], []):
            subprocess.run(['/bin/bash', str(wrapper), *args], env=env, check=True, timeout=5)
        calls = [json.loads(line) for line in (self.base / 'calls').read_text().splitlines()]
        for entry in calls:
            if entry[0] in ('niri-emaki', 'emaki-session-import-environment'):
                self.assertEqual({name: entry[2][name] for name in NAMES}, overrides)
        launched = json.loads((self.base / 'launched').read_text())
        self.assertEqual({name: launched[name] for name in NAMES}, overrides)

    def test_inherited_policy_refreshes_but_changed_profile_value_survives(self):
        _, connector = self.gpu(0, active=True)
        wrapper, env = self.session_fixture()
        subprocess.run(['/bin/bash', str(wrapper), '--compositor'], env=env, check=True, timeout=5)
        last = json.loads((self.base / 'calls').read_text().splitlines()[-1])
        inherited = last[2]
        inherited['LIBVA_DRIVER_NAME'] = 'iHD'
        (connector / 'enabled').write_text('disabled')
        subprocess.run(['/bin/bash', str(wrapper), '--compositor'], env=inherited, check=True, timeout=5)
        last = json.loads((self.base / 'calls').read_text().splitlines()[-1])[2]
        self.assertEqual(last['LIBVA_DRIVER_NAME'], 'iHD')
        self.assertNotIn('__GLX_VENDOR_LIBRARY_NAME', last)
        self.assertNotIn('EMAKI_NVIDIA_AUTO_LIBVA_DRIVER_NAME', last)
        self.assertEqual(last['NVD_BACKEND'], 'direct')

    def test_login_profile_runs_before_automatic_graphics_policy(self):
        self.gpu(0, active=True)
        wrapper, env = self.session_fixture()
        binary = Path(env['PATH'])
        self.executable(binary / 'bash', '#!/bin/bash\nexec /bin/bash "$@"\n')
        self.executable(binary / 'grep', '#!/bin/bash\nexec /usr/bin/grep "$@"\n')
        profile = binary / 'profile-shell'
        self.executable(profile, '''#!/bin/bash
set -eu
[[ ! -v EMAKI_NVIDIA_AUTO_LIBVA_DRIVER_NAME ]]
export LIBVA_DRIVER_NAME=nvidia
exec /bin/bash -c "$2"
''')
        shells = self.base / 'shells'
        shells.write_text(str(profile) + '\n')
        wrapper.write_text(wrapper.read_text().replace('/etc/shells', str(shells)))
        env['SHELL'] = str(profile)
        subprocess.run(['/bin/bash', str(wrapper)], env=env, check=True, timeout=5)
        launched = json.loads((self.base / 'launched').read_text())
        self.assertEqual(launched['LIBVA_DRIVER_NAME'], 'nvidia')
        self.assertNotIn('EMAKI_NVIDIA_AUTO_LIBVA_DRIVER_NAME', launched)

    def test_hibernation_keeps_native_hooks_and_modules_without_early_nvidia(self):
        script = '''modinfo() { return 0; }
KERNELVERSION=working
MODULES=(btrfs i915)
HOOKS=(base udev autodetect emaki-resume filesystems fsck)
source "$1"
printf '%s\\n' "${MODULES[*]}" "${HOOKS[*]}"
'''
        result = subprocess.run(
            ['/bin/bash', '-eu', '-c', script, 'fixture', str(PACKAGE / 'mkinitcpio.conf')],
            check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(),
                         ['btrfs i915', 'base udev autodetect emaki-resume filesystems fsck'])

    def test_mkinitcpio_preserves_native_modules_and_is_kernel_specific(self):
        script = '''modinfo() { [[ $2 != broken || $3 != "$MISSING" ]]; }
for KERNELVERSION in working broken working-lts ''; do
    MODULES=(btrfs i915)
    HOOKS=(base udev filesystems)
    source "$1"
    printf '%s:' "$KERNELVERSION"
    printf ' %s' "${MODULES[@]}"
    printf '\\n'
done
'''
        added = ' btrfs i915 nvidia nvidia_modeset nvidia_uvm nvidia_drm'
        for missing in self.runtime.MODULES:
            with self.subTest(missing=missing):
                result = subprocess.run(
                    ['/bin/bash', '-eu', '-c', script, 'fixture', str(PACKAGE / 'mkinitcpio.conf')],
                    env={'MISSING': missing}, check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout.splitlines(),
                                 ['working:' + added, 'broken: btrfs i915',
                                  'working-lts:' + added, ': btrfs i915'])

    def test_package_stages_exact_owned_payload(self):
        source = self.base / 'src/emaki-nvidia'
        shutil.copytree(PACKAGE, source / 'packaging/emaki-nvidia')
        (source / 'installer/emaki_installer').mkdir(parents=True)
        shutil.copyfile(ROOT / 'installer/emaki_installer/graphics.py', source / 'installer/emaki_installer/graphics.py')
        shutil.copyfile(ROOT / 'LICENSE', source / 'LICENSE')
        start = source / 'packaging/emaki-nvidia'
        (start / 'SOURCE-COMMIT').write_text('a' * 40)
        destination = self.base / 'pkg'
        command = 'startdir=$1; srcdir=$2; pkgdir=$3; source "$startdir/PKGBUILD"; package'
        result = subprocess.run(['/bin/bash', '-eu', '-c', command, 'fixture', str(start), str(self.base / 'src'),
                                 str(destination)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {
            'usr/lib/emaki/nvidia/graphics.py', 'usr/lib/emaki/nvidia/runtime.py',
            'usr/share/emaki/nvidia/session.sh', 'usr/lib/modprobe.d/emaki-nvidia.conf',
            'etc/mkinitcpio.conf.d/60-emaki-nvidia.conf',
            'usr/share/libalpm/hooks/92-emaki-nvidia-guard.hook',
            'etc/nvidia/nvidia-application-profiles-rc.d/50-emaki-niri.json',
            'usr/share/licenses/emaki-nvidia/LICENSE',
        }
        self.assertEqual({str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()}, expected)
        for path in expected:
            self.assertEqual((destination / path).stat().st_mode & 0o777, 0o644)
        # NVIDIA's profile format permits hash comments outside JSON strings.
        profile = json.loads('\n'.join(line for line in (PACKAGE / 'niri-profile.json').read_text().splitlines()
                                       if not line.startswith('#')))
        self.assertEqual({rule['pattern']['matches'] for rule in profile['rules']}, {'niri', 'niri-emaki'})
        self.assertEqual(profile['profiles'][0]['settings'], [{'key': 'GLVidHeapReuseRatio', 'value': 0}])
        self.assertIn('options nvidia_drm modeset=1', (PACKAGE / 'modprobe.conf').read_text())
        hook = (PACKAGE / '92-emaki-nvidia-guard.hook').read_text()
        self.assertIn('When = PostTransaction', hook)
        self.assertIn('Operation = Remove', hook)
        self.assertIn('Target = usr/lib/modules/*/pkgbase', hook)
        self.assertIn('Exec = /usr/bin/python3 -I /usr/lib/emaki/nvidia/runtime.py check', hook)
        self.assertNotIn('greetd.service.d', (PACKAGE / 'PKGBUILD').read_text())
        for name in ('packaging/emaki-desktop/PKGBUILD', 'packaging/emaki-config/PKGBUILD',
                     'iso/packages-extra.txt', 'iso/profile/packages.x86_64'):
            self.assertNotIn('emaki-nvidia', (ROOT / name).read_text())


if __name__ == '__main__':
    unittest.main()
