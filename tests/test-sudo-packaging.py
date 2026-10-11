#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate PAM selectors, optional policy and relocatable native install inputs."""
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent

# The real library operates only on this test's private root and package database.
# A private user namespace runs the shipped removal hook in that root. Check
# every installation and removal progress event, including a locally edited PAM
# file's move to .pacsave; neither the host root nor its package database is used.
REMOVE_PROBE = r'''
#include <alpm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct watch { char pam[4096], vendor[4096], selector[4096]; int checkpoints, installs, unsafe; };
static void progress(void *context, alpm_progress_t event, const char *package,
                     int percent, size_t total, size_t current) {
    (void)package; (void)percent; (void)total; (void)current;
    if(event != ALPM_PROGRESS_REMOVE_START && event != ALPM_PROGRESS_ADD_START) return;
    struct watch *watch = context;
    if(event == ALPM_PROGRESS_ADD_START) watch->installs++;
    else watch->checkpoints++;
    if(access(watch->selector, F_OK) == 0 &&
       (event == ALPM_PROGRESS_REMOVE_START ||
        (access(watch->pam, F_OK) != 0 && access(watch->vendor, F_OK) != 0)))
        watch->unsafe++;
}
#define CHECK(expression) do { if((expression) < 0) { \
    fprintf(stderr, "%s: %s\n", #expression, alpm_strerror(alpm_errno(handle))); \
    return 1; } } while(0)
int main(int argc, char **argv) {
    if(argc != 5 || argv[1][0] != '/' || !strcmp(argv[1], "/")) return 2;
    alpm_errno_t error;
    alpm_handle_t *handle = alpm_initialize(argv[1], argv[2], &error);
    if(!handle) { fprintf(stderr, "%s\n", alpm_strerror(error)); return 1; }
    int flags = ALPM_TRANS_FLAG_NOSCRIPTLET;
    alpm_pkg_t *pkg = NULL;
    alpm_list_t *data = NULL;
    CHECK(alpm_option_set_hookdirs(handle, NULL));
    char hooks[4096];
    snprintf(hooks, sizeof(hooks), "%s/usr/share/libalpm/hooks/", argv[1]);
    CHECK(alpm_option_add_hookdir(handle, hooks));
    CHECK(alpm_option_set_default_siglevel(handle, 0));
    struct watch watch = {0};
    snprintf(watch.pam, sizeof(watch.pam), "%s/etc/pam.d/emaki-sudo", argv[1]);
    snprintf(watch.vendor, sizeof(watch.vendor), "%s/usr/lib/pam.d/emaki-sudo", argv[1]);
    snprintf(watch.selector, sizeof(watch.selector), "%s/etc/sudoers.d/20-emaki-pam", argv[1]);
    CHECK(alpm_option_set_progresscb(handle, progress, &watch));
    CHECK(alpm_trans_init(handle, flags));
    CHECK(alpm_pkg_load(handle, argv[3], 1, 0, &pkg));
    CHECK(alpm_add_pkg(handle, pkg));
    CHECK(alpm_trans_prepare(handle, &data));
    CHECK(alpm_trans_commit(handle, &data));
    CHECK(alpm_trans_release(handle));
    if(access(watch.pam, F_OK) || access(watch.vendor, F_OK) || access(watch.selector, F_OK)) return 3;
    if(!strcmp(argv[4], "modified")) {
        FILE *file = fopen(watch.pam, "a");
        if(!file) return 4;
        fputs("# local configuration\n", file);
        fclose(file);
    }
    if(!strcmp(argv[4], "deleted") && unlink(watch.pam)) return 6;
    if(!strcmp(argv[4], "failure") && unlink(strcat(strcpy(hooks, argv[1]), "/usr/bin/rm"))) return 7;
    CHECK(alpm_trans_init(handle, flags));
    pkg = alpm_db_get_pkg(alpm_get_localdb(handle), "emaki-config");
    if(!pkg) return 5;
    CHECK(alpm_remove_pkg(handle, pkg));
    CHECK(alpm_trans_prepare(handle, &data));
    if(!strcmp(argv[4], "failure")) {
        if(alpm_trans_commit(handle, &data) == 0) return 8;
        puts("hook execution failed: transaction refused before removing policy files");
        return watch.unsafe || access(watch.pam, F_OK) || access(watch.vendor, F_OK) ||
               access(watch.selector, F_OK) || watch.checkpoints;
    }
    CHECK(alpm_trans_commit(handle, &data));
    CHECK(alpm_trans_release(handle));
    CHECK(alpm_release(handle));
    printf("libalpm %s: %d safe install, %d safe removal checkpoints\n", alpm_version(), watch.installs, watch.checkpoints);
    return watch.unsafe || watch.installs < 3 || watch.checkpoints < 3 ||
           access(watch.pam, F_OK) == 0 || access(watch.vendor, F_OK) == 0 ||
           access(watch.selector, F_OK) == 0;
}
'''


class Packaging(unittest.TestCase):
    def command(self, argv, **kwargs):
        result = subprocess.run(argv, cwd=kwargs.pop('cwd', ROOT), capture_output=True, text=True,
                                timeout=60, **kwargs)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_turn_reporting_install_is_relocatable_and_opt_in(self):
        with tempfile.TemporaryDirectory(prefix='sudo-turn-package-') as temp:
            stage = Path(temp) / 'stage'
            self.command(['make', 'install-sudo-turn-integration', 'DESTDIR=' + str(stage),
                          'PREFIX=/opt/emaki-test'])
            prefix = stage / 'opt/emaki-test'
            self.assertEqual((prefix / 'bin/emaki-sudo-turn').stat().st_mode & 0o777, 0o755)
            for relative in ('turn-reporting.md', 'integrations/claude-hooks.json',
                             'integrations/codex-notify.toml'):
                self.assertEqual((prefix / 'share/emaki/sudo' / relative).read_text(),
                                 (ROOT / 'sudo' / relative).read_text().replace(
                                     '@EMAKI_BINDIR@', '/opt/emaki-test/bin'))
            helper = (prefix / 'bin/emaki-sudo-turn').read_text()
            self.assertTrue(helper.startswith('#!/opt/emaki-test/bin/python3 -I\n'))
            self.assertIn("'/opt/emaki-test/bin/emaki-sudo'", helper)
            self.assertNotIn('@EMAKI_', helper)
            self.assertFalse((stage / 'home').exists())
            self.assertFalse((stage / 'etc').exists())
        makefile = (ROOT / 'Makefile').read_text()
        self.assertIn('$(BIN)/emaki-sudo $(BIN)/emaki-sudo-turn', makefile)

    def test_upgrade_restarts_only_running_broker(self):
        script = (ROOT / 'packaging/emaki-config/emaki-config.install').read_text()
        body = re.search(r'^post_upgrade\(\) \{(.*?)^\}', script, re.M | re.S).group(1)
        # Retain the real upgrade body, replacing unrelated absolute migration
        # programs with no-ops. A shell function captures systemctl without ever
        # contacting the host's service manager.
        body = body.replace('/usr/share/libalpm/scripts/emaki-initramfs-refresh --install-hooks', ':')
        body = body.replace('/usr/bin/python3 -I /usr/bin/emaki-migrate-installer-config', ':')
        body = body.replace('/usr/bin/python3 -IB /usr/bin/emaki-shell-generations install', ':')
        output = self.command(['bash', '-eu', '-c',
                               'systemctl() { printf "%s\\n" "$*"; }\n' + body])
        self.assertEqual(output.splitlines(), ['daemon-reload',
                                               'try-restart emaki-sudo.service',
                                               'start emaki-sudo-cleanup.timer'])
        failed_reload = self.command(['bash', '-eu', '-c',
            'systemctl() { printf "%s\\n" "$*"; [[ $1 != daemon-reload ]]; }\n' + body])
        self.assertEqual(failed_reload.splitlines(), ['daemon-reload'])
        install = re.search(r'^post_install\(\) \{(.*?)^\}', script, re.M | re.S).group(1)
        self.assertNotIn('emaki-sudo.service', install)

    def test_warning_generation_survives_upgrades(self):
        with tempfile.TemporaryDirectory(prefix='sudo-warning-package-') as temp:
            root = Path(temp)
            state, marker = root / 'state', root / 'marker'
            marker.write_text('installed\n')
            script = (ROOT / 'packaging/emaki-sudo-key/emaki-sudo-key.install').read_text()
            script = script.replace("'/var/lib/emaki-sudo'", repr(str(state)))
            script = script.replace("'/usr/share/emaki/sudo-key.installed'", repr(str(marker)))
            script = script.replace('identity.st_uid != 0', f'identity.st_uid != {os.getuid()}')
            script += '\nsystemctl() { return 0; }\n'
            def run(action):
                self.command(['bash', '-eu', '-c', script + action])
            old = marker.stat()
            legacy = f'1 {old.st_ino} {old.st_ctime_ns // 1000000000} {old.st_ctime_ns % 1000000000}\n'
            # First upgrade runs before extraction: keep old accepted records valid.
            run('pre_upgrade')
            generation = state / 'installation'
            self.assertEqual(generation.read_text(), legacy)
            replacement = root / 'replacement'
            replacement.write_text('new package\n')
            replacement.replace(marker)
            run('pre_upgrade')
            self.assertEqual(generation.read_text(), legacy)
            self.assertEqual(generation.stat().st_mode & 0o777, 0o600)
            run('post_remove')
            self.assertFalse(generation.exists())
            run('post_install')
            self.assertNotEqual(generation.read_text(), legacy)
            # An unsafe state directory must never be written through.
            state.chmod(0o777)
            result = subprocess.run(['bash', '-eu', '-c', script + 'post_install'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_timestamp_cleanup_service_and_removal(self):
        unit = (ROOT / 'sudo/emaki-sudo.service.in').read_text()
        self.assertEqual(re.findall(r'^ReadWritePaths=(.*)$', unit, re.M), ['/run/sudo/ts'])
        self.assertIn('RuntimeDirectoryPreserve=yes', unit)
        self.assertIn('ExecStopPost=@EMAKI_LIBEXECDIR@/emaki-sudo-broker --cleanup', unit)
        self.assertIn('systemd-tmpfiles-setup.service', unit)
        retry = (ROOT / 'sudo/emaki-sudo-cleanup.service.in').read_text()
        self.assertIn('ExecStart=@EMAKI_LIBEXECDIR@/emaki-sudo-broker --cleanup-if-idle', retry)
        self.assertIn('RuntimeDirectoryPreserve=yes', retry)
        self.assertIn('ReadWritePaths=/run/sudo/ts', retry)
        self.assertIn('OnUnitInactiveSec=15s',
                      (ROOT / 'sudo/emaki-sudo-cleanup.timer').read_text())
        self.assertIn('install=emaki-sudo-key.install',
                      (ROOT / 'packaging/emaki-sudo-key/PKGBUILD').read_text())
        for package, action, first in (('emaki-config', 'pre_remove', 'stop'),
                                       ('emaki-sudo-key', 'post_remove', 'try-restart')):
            source = (ROOT / f'packaging/{package}/{package}.install').read_text()
            body = re.search(rf'^{action}\(\) \{{(.*?)^\}}', source, re.M | re.S).group(1)
            body = body.replace('_warning_generation remove', ':')
            body = body.replace('[[ -d /run/emaki-sudo ]]', 'true')
            body = body.replace('/usr/libexec/emaki/emaki-sudo-broker', 'cleanup')
            body = body.replace('/usr/bin/emaki-greeter-provision --root / --purge-published', ':')
            for active in (False, True):
                script = ('systemctl() { printf "%s\\n" "$*"; '
                          'if [[ $1 == is-active ]]; then return ' + ('0' if active else '1') +
                          '; fi; return 0; }\n'
                          'cleanup() { printf "cleanup %s\\n" "$*"; }\n' + body)
                output = self.command(['bash', '-eu', '-c', script]).splitlines()
                expected = first + ' emaki-sudo.service'
                if package == 'emaki-config':
                    expected += ' emaki-sudo-cleanup.timer emaki-sudo-cleanup.service'
                self.assertEqual(output[0], expected)
                self.assertEqual('cleanup --cleanup' in output,
                                 package == 'emaki-config' or not active)

    def test_removal_guard_uses_relocated_cleanup_program(self):
        with tempfile.TemporaryDirectory(prefix='sudo-cleanup-package-') as temp:
            stage = Path(temp)
            self.command(['make', 'install-upkeep', 'DESTDIR=' + str(stage),
                          'PREFIX=/opt/emaki-test', 'LIBEXECDIR=/opt/emaki-test/helpers'])
            migration = stage / 'opt/emaki-test/share/libalpm/scripts/emaki-system-migrate'
            text = migration.read_text()
            self.assertIn("SUDO_BROKER = '/opt/emaki-test/helpers/emaki-sudo-broker'", text)
            self.assertNotIn('@EMAKI_LIBEXECDIR@', text)

    def test_sudoers_options_preserve_stock_timestamps(self):
        visudo = shutil.which('visudo')
        if not visudo:
            self.skipTest('visudo is unavailable; installed sudoers parser not proven')
        # Parsing only; never invoke sudo or read/write the host policy.
        for source in ('sudo/20-emaki-pam',):
            self.command([visudo, '-cf', str(ROOT / source)])
        base = (ROOT / 'sudo/20-emaki-pam').read_text()
        self.assertIn('pam_service="emaki-sudo", pam_login_service="emaki-sudo"', base)
        self.assertNotIn('timestamp_timeout', base)
        self.assertFalse((ROOT / 'packaging/emaki-sudo-key/zz-emaki-sudo-key').exists())
        for recipe in ('emaki', 'emaki-config', 'emaki-desktop'):
            text = (ROOT / 'packaging' / recipe / 'PKGBUILD').read_text()
            self.assertNotIn("'emaki-sudo-key", text)
        self.assertNotIn('/usr/share/emaki/sudo-key.installed',
                         (ROOT / 'packaging/emaki-config/expected-files.list').read_text())

    def test_optional_package_owns_marker_binding_and_turn_setup(self):
        # The recipe's prepare() archives from git; a package build's check() runs from an extracted
        # source without one, and that build itself installs the real package.
        if not (ROOT / '.git').exists():
            self.skipTest('needs the git checkout the recipe archives from')
        recipe = ROOT / 'packaging/emaki-sudo-key'
        with tempfile.TemporaryDirectory(prefix='sudo-package-') as temp:
            stage = Path(temp) / 'root'
            source = Path(temp) / 'source'
            source.mkdir()
            for name in ('sudo-key.installed', 'sudo-key.kdl'):
                shutil.copyfile(recipe / name, source / name)
            self.command(['bash', '-eu', '-c',
                          'startdir=$1; srcdir=$2; pkgdir=$3; source "$startdir/PKGBUILD"; prepare; package',
                          'package', str(recipe), str(source), str(stage)])
            self.assertEqual((stage / 'usr/bin/emaki-sudo-turn').stat().st_mode & 0o777, 0o755)
            helper = (stage / 'usr/bin/emaki-sudo-turn').read_text()
            self.assertTrue(helper.startswith('#!/usr/bin/python3 -I\n'))
            self.assertIn("'/usr/bin/emaki-sudo'", helper)
            self.assertNotIn('@EMAKI_', helper)
            for path in (source / 'emaki-sudo-key/sudo').rglob('*'):
                if path.is_file():
                    self.assertEqual((stage / 'usr/share/emaki/sudo' / path.relative_to(source / 'emaki-sudo-key/sudo')).read_text(),
                                     path.read_text().replace('@EMAKI_BINDIR@', '/usr/bin'))
            actual = sorted('/' + p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file())
            self.assertEqual(actual, [p for p in (recipe / 'expected-files.list').read_text().splitlines() if p.startswith('/')])
            self.assertFalse((stage / 'etc').exists())
            self.assertEqual((stage / 'usr/share/emaki/sudo-key.installed').stat().st_mode & 0o777, 0o644)
            binding = stage / 'usr/share/emaki/niri/sudo-key.kdl'
            self.assertIn('Super+Ctrl+Escape', binding.read_text())
            self.assertIn('spawn "/usr/bin/emaki-sudo" "revoke-all" "--json"', binding.read_text())
            self.assertNotIn('@EMAKI_', binding.read_text())
            self.assertNotIn('emaki-shell', binding.read_text())
            self.command(['niri', 'validate', '-c', str(binding)])
        text = (recipe / 'PKGBUILD').read_text()
        for name in ('sudo-key.installed', 'sudo-key.kdl'):
            self.assertIn(hashlib.sha256((recipe / name).read_bytes()).hexdigest(), text)
        defaults = (ROOT / 'niri/default.kdl').read_text()
        self.assertNotIn('revokeAll', defaults)
        self.assertNotIn('Revoke administrator access', defaults)
        self.assertIn('include optional=true "sudo-key.kdl"', defaults)

    def policy_archive(self, temp):
        for tool in ('makepkg', 'bsdtar'):
            if not shutil.which(tool):
                self.skipTest(tool + ' unavailable; archive ordering not proven')
        build, stage = temp / 'build', temp / 'stage'
        self.command(['cmake', '-S', str(ROOT / 'sudo'), '-B', str(build)])
        self.command(['make', 'install-sudo-policy', 'SUDO_BUILD=' + str(build),
                      'DESTDIR=' + str(stage)])
        recipe = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        backup = re.search(r'backup=\((.*?)\)', recipe, re.S).group(1)
        self.assertIn('etc/pam.d/emaki-sudo', shlex.split(backup))
        # This is a built policy-only package: production install target, package
        # backup metadata and installed makepkg's actual archive-list function.
        (stage / '.PKGINFO').write_text(
            'pkgname = emaki-config\npkgver = 1-1\narch = any\n'
            'pkgdesc = PAM service ordering regression\nsize = 1024\n'
            'backup = etc/pam.d/emaki-sudo\n')
        makepkg = Path(shutil.which('makepkg')).read_text()
        listing = re.search(r'^list_package_files\(\) \{.*?^\}', makepkg, re.M | re.S)
        self.assertIsNotNone(listing, 'makepkg archive API changed; recheck extraction order')
        archive = temp / 'policy.pkg.tar'
        self.command(['bash', '-euo', 'pipefail', '-c', listing.group(0) +
                      '\nlist_package_files | bsdtar --no-fflags --no-read-sparse -cnf "$1" --null --files-from -',
                      'archive', str(archive)], cwd=stage)
        return archive

    def test_built_archive_extracts_pam_service_before_selector(self):
        with tempfile.TemporaryDirectory(prefix='sudo-archive-') as temp:
            archive = self.policy_archive(Path(temp))
            names = self.command(['bsdtar', '-tf', str(archive)]).splitlines()
            self.assertLess(names.index('etc/pam.d/emaki-sudo'),
                            names.index('etc/sudoers.d/20-emaki-pam'))
            # Removal cannot rely on these file positions: the pre-transaction hook
            # must remove the selector before reverse ordering reaches the vendor.
            self.assertIn('usr/lib/pam.d/emaki-sudo', names)

    def test_vendor_service_is_the_same_stack_and_uninstalled(self):
        with tempfile.TemporaryDirectory(prefix='sudo-vendor-') as temp:
            build, stage = Path(temp) / 'build', Path(temp) / 'stage'
            self.command(['cmake', '-S', str(ROOT / 'sudo'), '-B', str(build)])
            self.command(['make', 'install-sudo-policy', 'SUDO_BUILD=' + str(build), 'DESTDIR=' + str(stage)])
            vendor, override = stage / 'usr/lib/pam.d/emaki-sudo', stage / 'etc/pam.d/emaki-sudo'
            self.assertEqual(vendor.read_bytes(), override.read_bytes())
            self.assertEqual(vendor.stat().st_mode & 0o777, 0o644)
        makefile = (ROOT / 'Makefile').read_text()
        self.assertIn('rm -f $(PAMDIR)/emaki-sudo $(DESTDIR)/etc/pam.d/emaki-sudo', makefile)
        recipe = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        backup = shlex.split(re.search(r'backup=\((.*?)\)', recipe, re.S).group(1))
        self.assertNotIn('usr/lib/pam.d/emaki-sudo', backup)

    def test_make_install_and_uninstall_keep_selector_order_with_deleted_override(self):
        with tempfile.TemporaryDirectory(prefix='sudo-make-order-') as directory:
            temp = Path(directory)
            build, tools = temp / 'build', temp / 'tools'
            tools.mkdir()
            self.command(['cmake', '-S', str(ROOT / 'sudo'), '-B', str(build)])
            for command in ('install', 'rm'):
                wrapper = tools / command
                wrapper.write_text('#!' + sys.executable + '\n' +
                    'import os, pathlib, subprocess, sys\n'
                    'root = pathlib.Path(os.environ["POLICY_ROOT"])\n'
                    'selector = root / "etc/sudoers.d/20-emaki-pam"\n'
                    'services = [root / "etc/pam.d/emaki-sudo", root / "usr/lib/pam.d/emaki-sudo"]\n'
                    'args = sys.argv[1:]\n' +
                    ('assert not selector.exists() or not any(str(p) in args for p in services)\n'
                     if command == 'rm' else
                     'assert str(selector) not in args or all(p.exists() for p in services)\n') +
                    'result = subprocess.run([' + repr(shutil.which(command)) + '] + args)\n'
                    'assert not selector.exists() or any(p.exists() for p in services)\n'
                    'sys.exit(result.returncode)\n')
                wrapper.chmod(0o755)
            for mode in ('stock', 'modified', 'deleted'):
                with self.subTest(mode=mode):
                    stage = temp / mode
                    environment = os.environ | {'PATH': str(tools) + os.pathsep + os.environ['PATH'],
                                                'POLICY_ROOT': str(stage)}
                    self.command(['make', 'install-sudo-policy', 'SUDO_BUILD=' + str(build),
                                  'DESTDIR=' + str(stage)], env=environment)
                    override = stage / 'etc/pam.d/emaki-sudo'
                    if mode == 'deleted':
                        override.unlink()
                    elif mode == 'modified':
                        override.write_text(override.read_text() + '# local override\n')
                    self.command(['make', 'uninstall', 'DESTDIR=' + str(stage)], env=environment)
                    for name in ('etc/sudoers.d/20-emaki-pam', 'etc/pam.d/emaki-sudo',
                                 'usr/lib/pam.d/emaki-sudo'):
                        self.assertFalse((stage / name).exists())

    def hook_runtime(self, root):
        # The hook invokes only rm. libalpm uses a private chroot for it.
        binary = Path(shutil.which('rm')).resolve()
        for source in [str(binary)] + re.findall(r'(/[^\s()]+)', self.command(['ldd', str(binary)])):
            dest = root / source.lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        (root / 'dev').mkdir()
        (root / 'dev/null').touch()
        (root / 'tmp').mkdir()

    def test_real_libalpm_removes_selector_before_pam_service(self):
        if not shutil.which('cc') or not shutil.which('pkg-config'):
            self.skipTest('C toolchain unavailable; live libalpm removal not proven')
        flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'libalpm'],
                               capture_output=True, text=True)
        if flags.returncode:
            self.skipTest('libalpm development files unavailable; live removal not proven')
        if subprocess.run(['unshare', '--user', '--map-root-user', 'true'], capture_output=True).returncode:
            self.skipTest('user namespace unavailable; live hook execution not proven')
        with tempfile.TemporaryDirectory(prefix='sudo-removal-') as directory:
            temp = Path(directory)
            archive = self.policy_archive(temp)
            source, probe = temp / 'remove.c', temp / 'remove'
            source.write_text(REMOVE_PROBE)
            self.command(['cc', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(probe)] +
                         shlex.split(flags.stdout))
            for mode in ('stock', 'modified', 'deleted', 'failure'):
                with self.subTest(mode=mode):
                    root, database = temp / mode, temp / (mode + '-db')
                    root.mkdir()
                    database.mkdir()
                    self.hook_runtime(root)
                    evidence = self.command(['unshare', '--user', '--map-root-user', str(probe), str(root), str(database), str(archive), mode])
                    print(mode + ': ' + evidence.strip())
                    self.assertEqual((root / 'etc/pam.d/emaki-sudo.pacsave').exists(),
                                     mode == 'modified')

    def test_native_paths_and_pam_stack_are_relocatable(self):
        with tempfile.TemporaryDirectory(prefix='sudo-prefix-') as temp:
            build = Path(temp) / 'build'
            prefix = Path(temp) / 'prefix'
            data = Path(temp) / 'data'
            libexec = Path(temp) / 'tools'
            self.command(['cmake', '-S', str(ROOT / 'sudo'), '-B', str(build),
                          '-DCMAKE_INSTALL_PREFIX=' + str(prefix),
                          '-DEMAKI_DATADIR=' + str(data), '-DEMAKI_LIBEXECDIR=' + str(libexec)])
            pam = (build / 'emaki-sudo-pam').read_text()
            self.assertIn(str(prefix / 'lib/security/pam_emaki_sudo.so'), pam)
            self.assertIn('auth [success=done auth_err=die perm_denied=die abort=die default=ignore]', pam)
            for kind in ('auth', 'account', 'password', 'session'):
                self.assertIn(kind + ' include sudo', pam)
            self.assertNotIn('pam_exec', pam)
            self.assertNotIn('system-auth', pam)
            unit = (build / 'emaki-sudo.service').read_text()
            self.assertIn('ExecStart=' + str(libexec / 'emaki-sudo-broker') + ' --serve', unit)
            self.assertIn('RuntimeDirectory=emaki-sudo', unit)
            self.assertNotIn('EMAKI_SUDO_REVOKE_ON_LOCK', unit)
            self.assertNotIn('PrivateUsers=', unit)
            self.assertNotIn('ProtectProc=', unit)
            # Discovery uses the session user's filesystem IDs, without DAC bypass.
            capabilities = next(line.split('=', 1)[1].split() for line in unit.splitlines()
                                if line.startswith('CapabilityBoundingSet='))
            self.assertEqual(set(capabilities), {'CAP_SETUID', 'CAP_SETGID',
                             'CAP_DAC_READ_SEARCH', 'CAP_SYS_PTRACE', 'CAP_IPC_LOCK',
                             'CAP_CHECKPOINT_RESTORE'})
            self.assertIn('ProtectSystem=strict', unit)
            commands = (build / 'CMakeFiles/sudo-core.dir/flags.make').read_text()
            self.assertIn(str(data / 'sudo-key.installed'), commands)
            self.assertIn(str(Path(shutil.which('qs') or shutil.which('quickshell')).resolve()), commands)
            self.assertIn('EMAKI_NIRI_PATH=', commands)
            self.assertIn(str(Path(shutil.which('niri-emaki')).resolve()), commands)


if __name__ == '__main__':
    unittest.main()
