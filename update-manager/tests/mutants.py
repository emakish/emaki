#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove the checks reject broken parsing and expanded privilege boundaries."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CASES = (
    ('apply.py', "if arguments != ['apply']:", 'if False:',
     'Authorization.test_only_exact_apply_argument_is_accepted'),
    ('apply.py', 'validate(sys.argv[1:])', 'validate(sys.argv[1:2])',
     'Authorization.test_rejected_arguments_execute_nothing'),
    ('apply.py', 'if os.geteuid() != 0:', 'if False:',
     'Authorization.test_nonroot_helper_executes_nothing'),
    ('apply.py', 'fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)', 'pass',
     'Authorization.test_concurrent_helper_refuses_before_inspecting_or_starting_service'),
    ('backend.py', "base = ['--dbpath', str(private), '--logfile'", "base = ['--logfile'",
     'Checking.test_discovery_copies_local_database_and_never_syncs_system_database'),
    ('backend.py', "'--disable-sandbox-filesystem', *base])", "'--disable-sandbox-filesystem'])",
     'Checking.test_discovery_copies_local_database_and_never_syncs_system_database'),
    ('backend.py', '            time.sleep(1)\n', '            pass\n',
     'Checking.test_aur_batches_are_complete_bounded_and_throttled'),
    ('backend.py', 'urlopen(request, timeout=15)', 'urlopen(request)',
     'Checking.test_fetch_is_cached_bounded_and_uses_stale_data_on_network_failure'),
    ('backend.py', "if not response.url.startswith('https://'):", 'if False:',
     'Checking.test_fetch_rejects_insecure_redirect_before_reading_or_caching'),
    ('backend.py', "['/usr/bin/pkexec', '/usr/libexec/emaki/emaki-update-apply', 'apply']",
     "['pkexec', '/usr/libexec/emaki/emaki-update-apply', 'apply']",
     'Checking.test_apply_uses_absolute_authorization_and_helper_paths'),
    ('backend.py', "if (database / 'db.lck').exists() or fingerprint(database) != initial:", 'if False:',
     'Checking.test_check_rejects_database_change_or_new_lock_before_network'),
    ('catalog.py', "title = ' '.join(re.sub('<[^>]*>', '', title).split())[:300]", 'title = title[:300]',
     'Parsing.test_news_titles_are_plain_normalized_text'),
    ('emaki-update.service', 'ExecStart=/usr/bin/emaki-update --noninteractive',
     'ExecStart=/usr/bin/emaki-update --noninteractive\nExecStartPre=/usr/bin/pacman -Sy --noconfirm',
     'Authorization.test_service_and_policy_bind_fixed_root_owned_paths'),
    ('../scripts/emaki-update', "['/usr/bin/pacman', '-Syu']", "['/usr/bin/pacman', '-Sy']",
     'Authorization.test_noninteractive_wrapper_keeps_full_upgrade_and_failed_hooks'),
    ('../scripts/emaki-update', "(['--noconfirm'] if args.noninteractive else [])",
     "(['--noconfirm', '--ignore', 'linux'] if args.noninteractive else [])",
     'Authorization.test_noninteractive_wrapper_keeps_full_upgrade_and_failed_hooks'),
    ('../scripts/emaki-update', 'if not status and hook_failed and fail_on_hook_error:', 'if False:',
     'Authorization.test_noninteractive_wrapper_keeps_full_upgrade_and_failed_hooks'),
    ('backend.py', "shutil.copytree(database / 'local', private / 'local', symlinks=True)",
     "(private / 'local').symlink_to(database / 'local')",
     'Checking.test_discovery_copies_local_database_and_never_syncs_system_database'),
    ('backend.py', "    if (database / 'db.lck').exists():\n        raise", '    if False:\n        raise',
     'Checking.test_active_package_lock_refuses_before_sync'),
    ('org.emaki.update.policy', '<allow_active>auth_admin</allow_active>', '<allow_active>yes</allow_active>',
     'Authorization.test_service_and_policy_bind_fixed_root_owned_paths'),
    ('org.emaki.update.policy', '<allow_inactive>no</allow_inactive>', '<allow_inactive>auth_admin</allow_inactive>',
     'Authorization.test_service_and_policy_bind_fixed_root_owned_paths'),
    ('apply.py', "cwd='/'", "cwd='/tmp'",
     'Authorization.test_helper_environment_does_not_inherit_caller_commands'),
    ('catalog.py', 'compare(version, installed[name]) > 0', 'compare(version, installed[name]) >= 0',
     'Parsing.test_aur_v5_missing_foreign_and_unknown_size'),
    ('catalog.py', 'published <= since', 'published >= since',
     'Parsing.test_news_after_last_completed_full_upgrade'),
    ('catalog.py', "parsed.netloc != 'archlinux.org'", 'False',
     'Parsing.test_news_rejects_entities_external_links_and_invalid_dates'),
    ('catalog.py', 'downloadSize=int(size)', 'downloadSize=0',
     'Parsing.test_pacman_formats_versions_sources_and_downloads'),
    ('backend.py', "str(private), '--logfile'", "str(database), '--logfile'",
     'Checking.test_discovery_copies_local_database_and_never_syncs_system_database'),
    ('emaki-update.service', '/usr/bin/emaki-update --noninteractive', '/usr/bin/pacman -Sy',
     'Authorization.test_service_and_policy_bind_fixed_root_owned_paths'),
)


def main():
    for filename, old, new, test in CASES:
        with tempfile.TemporaryDirectory(prefix='emaki-update-mutant-') as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'update-manager', root / 'update-manager',
                            ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
            (root / 'packaging/emaki-config').mkdir(parents=True)
            shutil.copy2(ROOT / 'packaging/emaki-config/zzz-emaki-session-update.hook',
                         root / 'packaging/emaki-config/zzz-emaki-session-update.hook')
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/emaki-update', root / 'scripts/emaki-update')
            (root / 'installer/emaki_installer').mkdir(parents=True)
            shutil.copy2(ROOT / 'installer/emaki_installer/update_errors.py', root / 'installer/emaki_installer/update_errors.py')
            path = root / 'update-manager' / filename
            source = path.read_text()
            assert old in source, (filename, old)
            path.write_text(source.replace(old, new, 1))
            result = subprocess.run([sys.executable, str(root / 'update-manager/tests/test_updates.py'), test],
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode != 0 and ('FAIL:' in result.stderr or 'ERROR:' in result.stderr), (filename, result.stdout, result.stderr)
            print('PASS rejected ' + filename + ' mutation: ' + test)


if __name__ == '__main__':
    main()
