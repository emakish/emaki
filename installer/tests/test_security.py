import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from emaki_installer.arch_backend import load_archinstall
from emaki_installer.daemon import export_log, peer_allowed
from emaki_installer.errors import Code, InstallError
from emaki_installer.runtime import Redactor
from support import RecordingRunner


class SecurityTests(unittest.TestCase):
    def test_peer_auth_root_primary_and_supplementary_group(self):
        def sock(uid, gid):
            return SimpleNamespace(getsockopt=lambda *args: struct.pack('3i', 123, uid, gid))
        self.assertTrue(peer_allowed(sock(0, 0), 999))
        self.assertTrue(peer_allowed(sock(1000, 999), 999))
        with patch('emaki_installer.daemon.pwd.getpwuid', return_value=SimpleNamespace(pw_name='live', pw_gid=1000)), \
                patch('emaki_installer.daemon.os.getgrouplist', return_value=[1000, 999]):
            self.assertTrue(peer_allowed(sock(1000, 1000), 999))
        with patch('emaki_installer.daemon.pwd.getpwuid', side_effect=KeyError):
            self.assertFalse(peer_allowed(sock(1000, 1000), 999))

    def test_version_rejected_before_loading_installer_modules(self):
        parted = SimpleNamespace(getAllDevices=lambda: [], getDevice=lambda x: None, IOException=OSError)
        with patch.dict('sys.modules', {'parted': parted, 'archinstall': SimpleNamespace(__version__='4.6')}):
            with self.assertRaises(InstallError) as error:
                load_archinstall()
        self.assertEqual(error.exception.code, Code.UNSUPPORTED_VERSION)
        self.assertIn('4.5', error.exception.message)

    def test_save_log_rejects_host_dest_and_traversal_before_commands(self):
        for dest in ('/etc/shadow', '/run/media/live/../../etc/shadow', 'relative.log', None):
            runner = RecordingRunner()
            with self.assertRaises(InstallError):
                export_log(dest, runner, Redactor())
            self.assertEqual(runner.commands, [])

    def test_save_log_confined_no_overwrite_and_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media, source = root / 'media', root / 'install.log'
            usb = media / 'USB'
            usb.mkdir(parents=True)
            source.write_text('secret-line\nnormal line\n')
            destination = usb / 'saved.log'
            runner, redactor = RecordingRunner(), Redactor()
            redactor.add('secret-line')
            dev = usb.stat().st_dev
            runner.responses[('findmnt', '-J', '-T', str(usb), '-o', 'TARGET,SOURCE,MAJ:MIN')] = json.dumps({
                'filesystems': [{'target': str(usb), 'source': '/dev/test', 'maj:min': f'{os.major(dev)}:{os.minor(dev)}'}]})
            runner.responses[('lsblk', '--tree', '-s', '-J', '-o', 'TYPE,RM,TRAN', '/dev/test')] = json.dumps({
                'blockdevices': [{'type': 'part', 'rm': False, 'children': [{'type': 'disk', 'tran': 'usb'}]}]})
            export_log(str(destination), runner, redactor, source, media)
            self.assertEqual(destination.read_text(), '[REDACTED]\nnormal line\n')
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(InstallError):
                export_log(str(destination), runner, redactor, source, media)
            escape = media / 'escape'
            escape.symlink_to(root)
            with self.assertRaises(InstallError):
                export_log(str(escape / 'bad.log'), runner, redactor, source, media)
            self.assertFalse((root / 'bad.log').exists())


if __name__ == '__main__':
    unittest.main()
