#!/usr/bin/env python3
"""Rollback safety tests; no mounts, privilege changes or host system writes."""
import importlib.machinery
import importlib.util
import json
import shutil
from pathlib import Path
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('rollback', str(ROOT / 'scripts/emaki-rollback'))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
UUID = '11111111-2222-3333-4444-555555555555'
FSTAB = ''.join(f'UUID={UUID} {target} btrfs rw,subvol={name} 0 0\n'
                for target, name in {'/': '@', **m.SHARED}.items()) + 'UUID=1234-ABCD /efi vfat defaults 0 2\n'
SWAP = f'UUID={UUID} /swap btrfs rw,noatime,subvol=/@swap 0 0\n'
GRUB = (f'set default="0"\nmenuentry normal {{\n linux /@/boot/vmlinuz-linux root=UUID={UUID} rw rootflags=subvol=@\n'
        ' initrd /@/boot/initramfs-linux.img\n}\n')


class Safety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.top = Path(self.tmp.name)
        self.current = self.top / '@'
        self.source = self.top / '@snapshots/7/snapshot'
        for root in (self.current, self.source):
            (root / 'etc').mkdir(parents=True)
            (root / 'var/lib/pacman').mkdir(parents=True)
            (root / 'boot/grub').mkdir(parents=True)
            (root / 'etc/fstab').write_text(FSTAB)
            (root / 'boot/grub/grub.cfg').write_text(GRUB)
            (root / 'boot/vmlinuz-linux').write_bytes(b'kernel')
            (root / 'boot/initramfs-linux.img').write_bytes(b'initramfs')

    def test_status(self):
        normal = {'fstype': 'btrfs', 'fsroot': '/@'}
        self.assertEqual(m.boot_status(normal, 'rootflags=subvol=@')['mode'], 'normal')
        for fs in ('ext4', 'overlay'):
            self.assertEqual(m.boot_status({'fstype': fs}, '')['mode'], 'unsupported')
        recovery = {'fstype': 'overlay', 'fsroot': '/'}
        self.assertEqual(m.boot_status(recovery, 'rootflags=subvol=/@snapshots/7/snapshot')['snapshot'], '7')
        for flags in ('rootflags=subvol=@snapshots/0/snapshot', 'rootflags=subvol=@snapshots/../snapshot',
                      'rootflags=subvol=@snapshots/7/snapshot,subvol=@', 'rootflags=subvol=@'):
            self.assertEqual(m.boot_status(recovery, flags)['mode'], 'unsupported')
        pending = {'fstype': 'btrfs', 'fsroot': '/@emaki-kept-20260101T000000Z-12345678'}
        self.assertEqual(m.boot_status(pending, '')['mode'], 'pending')

    def test_ext4_refuses_without_mounting(self):
        with patch.object(m, 'mount_info', return_value={'fstype': 'ext4'}), \
                patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'top_mount') as mount, \
                patch('sys.argv', ['emaki-rollback', 'snapshot', '7']):
            with self.assertRaisesRegex(m.Refused, 'Snapshots are unavailable'):
                m.main()
            mount.assert_not_called()

    def test_fstab_contract(self):
        self.assertEqual(len(m.fstab_rows(FSTAB, UUID)), 6)
        for text in (FSTAB.replace('subvol=@ ', 'subvol=@,subvolid=256 '),
                     FSTAB.replace('subvol=@ ', 'subvol=old '),
                     FSTAB.replace('rw,subvol=@ ', 'ro,subvol=@ '),
                     FSTAB + 'UUID=other /boot ext4 defaults 0 2\n',
                     FSTAB.replace('UUID=' + UUID, 'UUID=other', 1),
                     FSTAB + FSTAB.splitlines()[0] + '\n',
                     FSTAB.replace(' /.snapshots ', ' /somewhere ')):
            with self.subTest(text=text), self.assertRaises(m.Refused):
                m.fstab_rows(text, UUID)
        # Hibernation installs add @swap at /swap; installs without it stay valid.
        self.assertEqual(len(m.fstab_rows(FSTAB + SWAP, UUID)), 7)
        self.assertEqual(len(m.fstab_rows(FSTAB + SWAP.replace('subvol=/@swap', 'subvol=@swap'), UUID)), 7)
        for text in (FSTAB + SWAP.replace('UUID=' + UUID, 'UUID=other'),
                     FSTAB + SWAP.replace('subvol=/@swap', 'subvol=/@home2'),
                     FSTAB + SWAP.replace('rw,noatime', 'rw,noatime,noauto'),
                     FSTAB + SWAP.replace(' btrfs ', ' ext4 '),
                     FSTAB + SWAP.replace(' /swap ', ' /swap2 ')):
            with self.subTest(text=text), self.assertRaises(m.Refused):
                m.fstab_rows(text, UUID)

    def test_layout_checks_swap_mount_only_when_listed(self):
        mounts = {target: {'uuid': UUID, 'fsroot': '/' + name, 'fstype': 'btrfs'}
                  for target, name in {**m.SHARED, '/swap': '@swap'}.items()}
        mounts['/efi'] = {'uuid': '1234-ABCD', 'fsroot': '/', 'fstype': 'vfat'}
        asked = []
        def info(path):
            asked.append(path)
            return mounts[path]
        absent = unittest.mock.Mock(returncode=1)
        with patch.object(m, 'mount_info', side_effect=info), patch.object(m, 'subvolume', return_value=256), \
                patch.object(m, 'empty_children', return_value=[]), \
                patch.object(m, 'run', return_value='Total devices 1 FS bytes used 1.00GiB'), \
                patch.object(m.subprocess, 'run', return_value=absent):
            self.assertEqual(len(m.check_layout(self.top, UUID)), 6)
            self.assertNotIn('/swap', asked)
            (self.current / 'etc/fstab').write_text(FSTAB + SWAP)
            self.assertEqual(len(m.check_layout(self.top, UUID)), 7)
            self.assertIn('/swap', asked)
            mounts['/swap'] = dict(mounts['/swap'], fsroot='/@home')
            with self.assertRaisesRegex(m.Refused, '/swap is not mounted from the expected shared subvolume'):
                m.check_layout(self.top, UUID)

    def test_grub_requires_matching_boot_files_and_root(self):
        m.check_grub(self.source, UUID)
        for text in (GRUB.replace('rootflags=subvol=@', 'rootflags=subvolid=256'),
                     GRUB.replace('/@/boot/', '/@snapshots/7/snapshot/boot/'),
                     GRUB.replace('root=UUID=' + UUID, 'root=UUID=other'),
                     GRUB.replace('set default="0"', 'set default="1"')):
            (self.source / 'boot/grub/grub.cfg').write_text(text)
            with self.assertRaises(m.Refused):
                m.check_grub(self.source, UUID)
        (self.source / 'boot/grub/grub.cfg').write_text(GRUB)
        (self.source / 'boot/vmlinuz-linux').unlink()
        with self.assertRaisesRegex(m.Refused, 'Missing boot file'):
            m.check_grub(self.source, UUID)

    def test_symlink_escape(self):
        (self.source / 'etc/fstab').unlink()
        (self.source / 'etc/fstab').symlink_to(self.current / 'etc/fstab')
        with self.assertRaises(m.Refused):
            m.safe_path(self.source, 'etc/fstab')

    def test_real_atomic_exchange(self):
        before = self.current.stat().st_ino
        after = self.source.stat().st_ino
        m.atomic_exchange(self.current, self.source)
        self.assertEqual(self.current.stat().st_ino, after)
        self.assertEqual(self.source.stat().st_ino, before)
        m.atomic_exchange(self.current, self.source)
        self.assertEqual(self.current.stat().st_ino, before)

    def test_prepare_failure_never_exchanges(self):
        (self.top / '.emaki-rollback').mkdir()
        calls = []
        def fail(*args):
            calls.append(args)
            if args[:3] == ('btrfs', 'subvolume', 'snapshot'):
                raise m.Refused('No space left')
            return ''
        with patch.object(m, 'check_layout', return_value=m.fstab_rows(FSTAB, UUID)), \
                patch.object(m, 'run', side_effect=fail), patch.object(m, 'subvolume', return_value=256), \
                patch.object(m, 'atomic_exchange') as exchange:
            with self.assertRaisesRegex(m.Refused, 'No space left'):
                m.promote(self.top, self.source, UUID, 'boot', {})
            exchange.assert_not_called()
        self.assertTrue(self.current.exists())

    def test_package_guard_follows_kept_root(self):
        with m.package_guard(self.current):
            self.assertTrue((self.current / 'var/lib/pacman/db.lck').exists())
            with self.assertRaises(m.Refused):
                with m.package_guard(self.current):
                    self.fail('second guard entered')
            m.atomic_exchange(self.current, self.source)
            (self.current / 'var/lib/pacman/db.lck').write_text('another transaction')
        self.assertFalse((self.source / 'var/lib/pacman/db.lck').exists())
        self.assertEqual((self.current / 'var/lib/pacman/db.lck').read_text(), 'another transaction')

    def test_package_guard_removes_lock_when_token_write_fails(self):
        write = m.os.write
        def full(fd, data):
            if data.startswith(b'emaki-rollback:'):
                raise OSError(28, 'No space left on device')
            return write(fd, data)
        with patch.object(m.os, 'write', side_effect=full), patch.object(m.os, 'close', wraps=m.os.close) as close:
            with self.assertRaises(OSError) as caught:
                with m.package_guard(self.current):
                    self.fail('guard entered without a written token')
        self.assertEqual(caught.exception.errno, 28)
        self.assertFalse((self.current / 'var/lib/pacman/db.lck').exists())
        # Both the lock and the directory descriptor are closed.
        self.assertEqual(close.call_count, 2)

    def test_failures_around_commit_retain_both_roots(self):
        for fail_after_exchange in (False, True):
            with self.subTest(after_exchange=fail_after_exchange):
                old_inode = self.current.stat().st_ino
                source_inode = self.source.stat().st_ino
                (self.top / '.emaki-rollback').mkdir(exist_ok=True)
                syncs = 0
                def commands(*args):
                    nonlocal syncs
                    if args[:3] == ('btrfs', 'subvolume', 'snapshot'):
                        shutil.copytree(args[3], args[4])
                    if args[:3] == ('btrfs', 'filesystem', 'sync'):
                        syncs += 1
                        if syncs == (2 if fail_after_exchange else 1):
                            raise m.Refused('injected I/O failure')
                    return ''
                with patch.object(m, 'check_layout', return_value=m.fstab_rows(FSTAB, UUID)), \
                        patch.object(m, 'subvolume', side_effect=lambda p, **kw: p.stat().st_ino), \
                        patch.object(m, 'run', side_effect=commands), patch.object(m.signal, 'pthread_sigmask'):
                    with self.assertRaisesRegex(m.Refused, 'injected'):
                        m.promote(self.top, self.source, UUID, 'boot', {})
                record = json.loads(sorted((self.top / '.emaki-rollback').glob('*.json'), key=lambda p: p.stat().st_mtime_ns)[-1].read_text())
                kept = self.top / record['kept']
                self.assertTrue(self.current.is_dir() and kept.is_dir())
                self.assertEqual(self.source.stat().st_ino, source_inode)
                self.assertEqual(self.current.stat().st_ino, record['new_id'] if fail_after_exchange else old_inode)
                self.assertEqual(kept.stat().st_ino, old_inode if fail_after_exchange else record['new_id'])
                self.assertFalse((kept / 'var/lib/pacman/db.lck').exists())
                self.assertFalse((self.current / 'var/lib/pacman/db.lck').exists())

    def test_snapshot_lock_requires_transaction_boundary(self):
        lock = self.source / 'var/lib/pacman/db.lck'
        lock.touch()
        with patch.object(m, 'check_layout', return_value=m.fstab_rows(FSTAB, UUID)), \
                patch.object(m, 'run', return_value=''), patch.object(m, 'subvolume', return_value=256):
            with self.assertRaisesRegex(m.Refused, 'outside a pre/post'):
                m.promote(self.top, self.source, UUID, 'boot', {})
            for kind in ('pre', 'post'):
                (self.source.parent / 'info.xml').write_text('<snapshot><type>' + kind + '</type></snapshot>')
                # The boot guard is after snapshot validation, before copying.
                with self.assertRaisesRegex(m.Refused, 'Restart'):
                    m.promote(self.top, self.source, UUID, 'boot', {'kept': {'boot_id': 'boot'}})

    def test_unknown_nested_subvolumes_refused(self):
        with patch.object(m, 'run', return_value='ID 400 gen 1 top level 256 path var/lib/other'):
            with self.assertRaisesRegex(m.Refused, 'Unknown nested'):
                m.empty_children(self.current)

    def test_pending_boot_refuses_before_copy(self):
        with patch.object(m, 'check_layout', return_value=m.fstab_rows(FSTAB, UUID)), \
                patch.object(m, 'run', return_value='') as command, patch.object(m, 'subvolume', return_value=256):
            with self.assertRaisesRegex(m.Refused, 'Restart'):
                m.promote(self.top, self.source, UUID, 'boot', {'kept': {'boot_id': 'boot'}})
            self.assertFalse(any(c.args[:3] == ('btrfs', 'subvolume', 'snapshot') for c in command.call_args_list))

    def test_cleanup_protects_latest_only_and_mounted(self):
        names = ['@emaki-kept-20260101T000000Z-12345678', '@emaki-kept-20260102T000000Z-12345678']
        history = {}
        for i, name in enumerate(names):
            (self.top / name).mkdir()
            history[name] = {'previous_id': i + 256, 'filesystem': UUID}
        root = {'fstype': 'btrfs', 'fsroot': '/@', 'options': 'rw', 'maj:min': '0:99'}
        with patch.object(m, 'check_layout'), patch.object(m, 'check_grub'), \
                patch.object(m, 'subvolume', side_effect=lambda p: history[p.name]['previous_id']), \
                patch.object(m, 'run', return_value='ID 5 (FS_TREE)') as command:
            with self.assertRaisesRegex(m.Refused, 'newest'):
                m.delete_kept(self.top, names[1], history, root, UUID)
            with self.assertRaisesRegex(m.Refused, 'newest'):
                m.delete_kept(self.top, names[0], {names[0]: history[names[0]]}, root, UUID)
            mountinfo = self.top / 'mountinfo'
            mountinfo.write_text('1 2 0:123 /' + names[0] + ' / rw - btrfs /dev/test rw\n')
            with patch.object(m.Path, 'glob', return_value=[mountinfo]), self.assertRaisesRegex(m.Refused, 'still mounted'):
                m.delete_kept(self.top, names[0], history, root, UUID)
            self.assertFalse(any(c.args[:3] == ('btrfs', 'subvolume', 'delete') for c in command.call_args_list))

    def test_failed_preparation_is_recorded_and_can_be_retried(self):
        (self.top / '.emaki-rollback').mkdir()
        broken = True
        def commands(*args):
            if args[:3] == ('btrfs', 'subvolume', 'snapshot'):
                shutil.copytree(args[3], args[4])
            if args[0] == 'grub-editenv' and broken:
                raise m.Refused('grub-editenv failed: Permission denied')
            return ''
        with patch.object(m, 'check_layout', return_value=m.fstab_rows(FSTAB, UUID)), \
                patch.object(m, 'run', side_effect=commands), patch.object(m.signal, 'pthread_sigmask'), \
                patch.object(m, 'subvolume', side_effect=lambda p, **kw: p.stat().st_ino), \
                patch.object(m, 'atomic_exchange') as exchange:
            with self.assertRaisesRegex(m.Refused, 'grub-editenv'):
                m.promote(self.top, self.source, UUID, 'boot', {})
            exchange.assert_not_called()
            receipts = list((self.top / '.emaki-rollback').glob('*.json'))
            self.assertEqual(len(receipts), 1)
            record = json.loads(receipts[0].read_text())
            self.assertEqual((record['kept'], record['state']), (receipts[0].stem, 'preparing'))
            self.assertEqual(record['previous_id'], self.current.stat().st_ino)
            self.assertNotIn('new_id', record)
            self.assertTrue((self.top / record['kept']).is_dir())
            # Nothing was exchanged, so the same boot may try again; the failed copy
            # stays behind with its receipt until `delete` removes it.
            broken = False
            m.promote(self.top, self.source, UUID, 'boot', {record['kept']: record})
            exchange.assert_called_once()
            done = [json.loads(p.read_text()) for p in (self.top / '.emaki-rollback').glob('*.json')
                    if p.stem != record['kept']]
            self.assertEqual(len(done), 1)
            self.assertNotIn('state', done[0])
            self.assertEqual(done[0]['new_id'], (self.top / done[0]['kept']).stat().st_ino)

    def incomplete_fixture(self):
        kept = '@emaki-kept-20260101T000000Z-12345678'
        staged = '@emaki-kept-20260102T000000Z-12345678'
        gone = '@emaki-kept-20260103T000000Z-12345678'
        history = {kept: {'kept': kept, 'previous_id': 256, 'new_id': 290, 'filesystem': UUID},
                   staged: {'kept': staged, 'previous_id': 257, 'new_id': 300, 'filesystem': UUID},
                   gone: {'kept': gone, 'previous_id': 257, 'filesystem': UUID, 'state': 'preparing'}}
        (self.top / '.emaki-rollback').mkdir()
        for name, record in history.items():
            if name != gone:
                (self.top / name).mkdir()
            (self.top / '.emaki-rollback' / (name + '.json')).write_text(json.dumps(record))
        ids = {kept: 256, staged: 300, '@': 257}
        return kept, staged, gone, history, ids

    def test_delete_removes_incomplete_preparations_but_keeps_undo_copies(self):
        kept, staged, gone, history, ids = self.incomplete_fixture()
        root = {'fstype': 'btrfs', 'fsroot': '/@', 'options': 'rw,subvolid=257,subvol=/@', 'maj:min': '0:99'}
        mountinfo = self.top / 'mountinfo'
        mountinfo.write_text('1 2 0:123 /@ / rw - btrfs /dev/test rw\n')
        calls = []
        def commands(*args):
            calls.append(args)
            return 'ID 257 gen 9 top level 5 path @' if args[:3] == ('btrfs', 'subvolume', 'get-default') else ''
        with patch.object(m, 'check_layout'), patch.object(m, 'check_grub'), \
                patch.object(m, 'subvolume', side_effect=lambda p, **kw: ids[p.name]), \
                patch.object(m, 'run', side_effect=commands), patch.object(m.Path, 'glob', return_value=[mountinfo]):
            # The only real kept root is never deleted, incomplete or not.
            with self.assertRaisesRegex(m.Refused, 'newest'):
                m.delete_kept(self.top, kept, history, root, UUID)
            m.delete_kept(self.top, staged, history, root, UUID)
            self.assertEqual([c for c in calls if c[:3] == ('btrfs', 'subvolume', 'delete')],
                             [('btrfs', 'subvolume', 'delete', self.top / staged)])
            self.assertFalse((self.top / '.emaki-rollback' / (staged + '.json')).exists())
            # A receipt whose copy never came to exist is removed without any btrfs command.
            before = len(calls)
            m.delete_kept(self.top, gone, history, root, UUID)
            self.assertFalse(any(c[0] == 'btrfs' and c[2] == 'delete' for c in calls[before:]))
            self.assertFalse((self.top / '.emaki-rollback' / (gone + '.json')).exists())
            self.assertTrue((self.top / '.emaki-rollback' / (kept + '.json')).exists())
            with self.assertRaisesRegex(m.Refused, 'not a recorded kept root'):
                m.delete_kept(self.top, '@emaki-kept-20260104T000000Z-12345678', history, root, UUID)

    def fake_proc(self, processes):
        """A /proc with {pid: {tid: (mountinfo text or errno, has a mount namespace)}}."""
        proc, failures = self.top / 'proc', {}
        for pid, threads in processes.items():
            for tid, (mounts, namespace) in threads.items():
                for task in ((proc / pid,) if tid == pid else ()) + (proc / pid / 'task' / tid,):
                    (task / 'ns').mkdir(parents=True, exist_ok=True)
                    if namespace:
                        (task / 'ns/mnt').symlink_to('mnt:[4026531841]')
                    if isinstance(mounts, str):
                        (task / 'mountinfo').write_text(mounts)
                    else:
                        failures[task / 'mountinfo'] = mounts
        read = m.Path.read_text
        def read_text(path, *args, **kwargs):
            if path in failures:
                raise OSError(failures[path], m.os.strerror(failures[path]))
            return read(path, *args, **kwargs)
        listed = sorted(proc / pid / 'mountinfo' for pid in processes)
        return patch.object(m.Path, 'glob', return_value=listed), \
            patch.object(m.Path, 'read_text', autospec=True, side_effect=read_text)

    def delete_with_proc(self, processes):
        kept, staged, gone, history, ids = self.incomplete_fixture()
        root = {'fstype': 'btrfs', 'fsroot': '/@', 'options': 'rw,subvolid=257,subvol=/@', 'maj:min': '0:99'}
        self.calls = []
        def commands(*args):
            self.calls.append(args)
            return 'ID 257 gen 9 top level 5 path @' if args[:3] == ('btrfs', 'subvolume', 'get-default') else ''
        listing, reading = self.fake_proc(processes)
        with patch.object(m, 'check_layout'), patch.object(m, 'check_grub'), \
                patch.object(m, 'subvolume', side_effect=lambda p, **kw: ids[p.name]), \
                patch.object(m, 'run', side_effect=commands), listing, reading:
            m.delete_kept(self.top, staged, history, root, UUID)
        return staged, self.calls

    def test_delete_skips_processes_that_hold_no_mount_namespace(self):
        # A zombie's mountinfo read fails with EINVAL and it has no ns/mnt (seen on a live
        # system); a process that vanished mid-scan fails with ENOENT. Neither holds mounts.
        import errno
        unrelated = '1 2 0:123 /@ / rw - btrfs /dev/test rw\n'
        staged, calls = self.delete_with_proc({
            '100': {'100': (unrelated, True)},
            '200': {'200': (errno.EINVAL, False)},
            '300': {'300': (errno.ENOENT, False)}})
        self.assertIn(('btrfs', 'subvolume', 'delete', self.top / staged), calls)

    def test_delete_reads_the_threads_of_an_exited_leader_and_refuses_unknown_mounts(self):
        import errno
        staged = '@emaki-kept-20260102T000000Z-12345678'
        mounted = '1 2 0:123 /' + staged + ' /mnt rw - btrfs /dev/test rw\n'
        for name, processes, message in (
                # The leader is a zombie, another thread still runs with the root mounted.
                ('zombie leader', {'200': {'200': (errno.EINVAL, False), '201': (mounted, True)}},
                 'still mounted'),
                # Mounts unreadable while the process still has a mount namespace.
                ('unreadable', {'400': {'400': (errno.EACCES, True)}}, 'Cannot read the mounts of process 400'),
                ('exiting', {'500': {'500': (errno.ENOENT, True)}}, 'Cannot read the mounts of process 500')):
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                self.top = Path(temp)
                with self.assertRaisesRegex(m.Refused, message):
                    self.delete_with_proc(processes)
                self.assertFalse(any(c[:3] == ('btrfs', 'subvolume', 'delete') for c in self.calls))
                self.assertTrue((self.top / '.emaki-rollback' / (staged + '.json')).exists())

    def test_delete_refuses_the_running_root_and_the_default_subvolume(self):
        kept, staged, gone, history, ids = self.incomplete_fixture()
        for root_options, default in (('rw,subvolid=300,subvol=/@', 'ID 257 gen 9 top level 5 path @'),
                                      ('rw,subvolid=257,subvol=/@', 'ID 300 gen 9 top level 5 path ' + staged)):
            root = {'fstype': 'btrfs', 'fsroot': '/@', 'options': root_options, 'maj:min': '0:99'}
            with self.subTest(root=root_options, default=default), \
                    patch.object(m, 'check_layout'), patch.object(m, 'check_grub'), \
                    patch.object(m, 'subvolume', side_effect=lambda p, **kw: ids[p.name]), \
                    patch.object(m, 'run', return_value=default) as command:
                with self.assertRaisesRegex(m.Refused, 'running root|default subvolume'):
                    m.delete_kept(self.top, staged, history, root, UUID)
                self.assertFalse(any(c.args[:3] == ('btrfs', 'subvolume', 'delete') for c in command.call_args_list))
                self.assertTrue((self.top / '.emaki-rollback' / (staged + '.json')).exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
