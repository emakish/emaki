"""Live clock validation and offline software selection, without host mutations."""
from datetime import datetime
import io
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan, validate_config
from emaki_installer.protocol import Controller, Job
from emaki_installer.runtime import Redactor
from emaki_installer.timezones import set_live_timezone, validate_timezone
from emaki_installer.worker import Worker, preflight_repo
from support import FakeInventory, RecordingRunner, config, inventory


class TimezoneTests(unittest.TestCase):
    def test_timezone_guess_request_uses_cache_without_probing_disks(self):
        inventory = Mock()
        inventory.timezone_guess.side_effect = [dict(tz_guess=None, pending=True),
                                               dict(tz_guess="Europe/Berlin", pending=False)]
        broker = Controller(inventory, None)
        first = broker.handle(dict(type='get_timezone_guess', id='guess-1'))[0]
        second = broker.handle(dict(type='get_timezone_guess', id='guess-2'))[0]
        self.assertTrue(first['pending'])
        self.assertIsNone(first['tz_guess'])
        self.assertFalse(second['pending'])
        self.assertEqual(second['tz_guess'], 'Europe/Berlin')
        inventory.probe.assert_not_called()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.zones = self.root / 'zoneinfo'
        for zone in ('UTC', 'Europe/Berlin', 'Asia/Kathmandu'):
            dest = self.zones / zone
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path('/usr/share/zoneinfo') / zone, dest)
        (self.zones / 'zone.tab').write_text('DE\t+5230+01322\tEurope/Berlin\n')
        (self.zones / 'zone1970.tab').write_text('NP\t+2743+08519\tAsia/Kathmandu\n')
        self.boot = self.root / 'bootmnt'
        self.boot.mkdir()
        self.release = self.root / 'os-release'
        self.release.write_text('ID=arch\nIMAGE_ID=emaki\n')
        self.wireless = self.root / 'ieee80211'
        (self.wireless / 'phy0').mkdir(parents=True)
        self.runner = RecordingRunner()

    def change(self, name):
        return set_live_timezone(name, self.runner, root=self.zones,
                                 boot=self.boot, release=self.release, wireless=self.wireless)

    def test_table_union_and_utc(self):
        for zone in ('UTC', 'Europe/Berlin', 'Asia/Kathmandu'):
            self.assertEqual(validate_timezone(zone, self.zones).key, zone)

    def test_rejects_injection_traversal_alias_and_wrong_types_before_command(self):
        for value in (None, [], {}, 4, '../UTC', '/UTC', 'Europe/../UTC', 'UTC\n',
                      'UTC;id', '--help', 'posix/Europe/Berlin', 'Etc/GMT+4', 'Europe/Missing'):
            with self.subTest(value=value), patch('emaki_installer.timezones.os.geteuid', return_value=0):
                with self.assertRaises(InstallError):
                    self.change(value)
        self.assertEqual(self.runner.commands, [])

    def test_rejects_symlink_escape_and_non_tzif(self):
        utc = self.zones / 'UTC'
        utc.unlink()
        outside = self.root / 'outside'
        shutil.copyfile('/usr/share/zoneinfo/UTC', outside)
        utc.symlink_to(outside)
        with self.assertRaises(InstallError):
            validate_timezone('UTC', self.zones)
        utc.unlink()
        utc.write_bytes(b'not zone data')
        with self.assertRaises(InstallError):
            validate_timezone('UTC', self.zones)

    def test_live_iso_and_root_required_on_every_request(self):
        with patch('emaki_installer.timezones.os.geteuid', return_value=1000):
            with self.assertRaises(InstallError):
                self.change('UTC')
        with patch('emaki_installer.timezones.os.geteuid', return_value=0):
            self.boot.rmdir()
            with self.assertRaises(InstallError):
                self.change('UTC')
            self.boot.mkdir()
            self.release.write_text('ID=arch\n')
            with self.assertRaises(InstallError):
                self.change('UTC')
        self.assertEqual(self.runner.commands, [])

    def test_fixed_argv_dst_and_fractional_offsets(self):
        for zone, month, offset in [('Europe/Berlin', 7, 7200), ('Europe/Berlin', 1, 3600),
                                    ('Asia/Kathmandu', 7, 20700), ('UTC', 7, 0)]:
            instant = datetime(2026, month, 1, 12, tzinfo=ZoneInfo(zone))
            with patch('emaki_installer.timezones.os.geteuid', return_value=0), \
                    patch('emaki_installer.timezones.datetime') as clock:
                clock.now.return_value = instant
                reply = self.change(zone)
            self.assertEqual(reply['timezone'], zone)
            self.assertEqual(reply['offset_seconds'], offset)
            self.assertEqual(reply['unix_ms'], int(instant.timestamp() * 1000))
            self.assertEqual(self.runner.commands[-1][0], ['timedatectl', 'set-timezone', zone])

    def test_live_country_uses_single_country_zone_table_and_preserves_utc(self):
        with patch('emaki_installer.timezones.os.geteuid', return_value=0):
            self.change('Europe/Berlin')
            self.assertIn((['iw', 'reg', 'set', 'DE'], {}), self.runner.commands)
            self.runner.commands.clear()
            self.change('UTC')
            self.assertEqual([cmd for cmd, _ in self.runner.commands], [['timedatectl', 'set-timezone', 'UTC']])

    def test_failed_regulatory_change_still_sets_timezone(self):
        for error in (OSError('missing iw'), InstallError(Code.COMMAND_FAILED, 'iw failed')):
            with self.subTest(error=error):
                self.runner = RecordingRunner(log=Mock())
                original = self.runner.run
                def run(argv, **kwargs):
                    if argv[0] == 'iw':
                        raise error
                    return original(argv, **kwargs)
                self.runner.run = run
                with patch('emaki_installer.timezones.os.geteuid', return_value=0):
                    reply = self.change('Europe/Berlin')
                self.assertEqual(reply['timezone'], 'Europe/Berlin')
                self.assertEqual(self.runner.commands[-1][0],
                                 ['timedatectl', 'set-timezone', 'Europe/Berlin'])
                self.runner.log.assert_called_once()

    def test_wired_only_machine_can_set_timezone_without_a_radio(self):
        (self.wireless / 'phy0').rmdir()
        with patch('emaki_installer.timezones.os.geteuid', return_value=0):
            self.change('Europe/Berlin')
        self.assertEqual([cmd for cmd, _ in self.runner.commands],
                         [['timedatectl', 'set-timezone', 'Europe/Berlin']])

    def test_worker_invalidates_plan_rejects_busy_and_propagates_failure(self):
        callback = Mock(return_value=dict(timezone='UTC', offset_seconds=0, unix_ms=0, abbreviation='UTC'))
        stream = io.StringIO()
        broker = Controller(FakeInventory(), None, set_timezone=callback, log_stream=stream)
        ack = broker.handle(dict(type='plan', id='plan', config=config()))[0]
        held = broker.pending['plan']
        reply = broker.handle(dict(type='set_timezone', id='clock', timezone='UTC'))[0]
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['for_id'], 'clock')
        self.assertEqual(held.config['user']['password'], '')
        self.assertIsNone(broker.pending)
        self.assertFalse(broker.handle(dict(type='confirm', id='confirm', plan_id=ack['plan_id'], token=ack['token']))[0]['ok'])
        callback.side_effect = InstallError(Code.COMMAND_FAILED, 'Clock unavailable.')
        self.assertEqual(broker.handle(dict(type='set_timezone', id='clock', timezone='UTC'))[0]['code'], 'command_failed')
        broker.job = Job('busy')
        callback.reset_mock()
        self.assertEqual(broker.handle(dict(type='set_timezone', id='clock', timezone='UTC'))[0]['code'], 'busy')
        callback.assert_not_called()
        self.assertNotIn('a-secret-for-tests', stream.getvalue())


class SoftwareTests(unittest.TestCase):
    def test_defaults_validation_and_review(self):
        self.assertEqual(validate_config(config())['software'], 'rich')
        for value in ('rich', 'minimal'):
            plan = make_plan(dict(config(), software=value), inventory())
            self.assertTrue(any('Software: ' + value.capitalize() in line for line in plan.summary))
        for value in (None, [], {}, True, 'full', 'emaki-apps;id'):
            with self.subTest(value=value), self.assertRaises(InstallError):
                validate_config(dict(config(), software=value))

    def test_copy_packages_uses_same_offline_backend_for_both_choices(self):
        for value in ('rich', 'minimal'):
            # copy_packages completes /etc/vconsole.conf in the target.
            target = self.enterContext(tempfile.TemporaryDirectory())
            worker = Worker(SimpleNamespace(locale=Mock()), None, Redactor(), Mock(), Mock(), target=Path(target))
            worker.plan = make_plan(dict(config(), software=value), inventory())
            worker.backend = Mock()
            worker.populate_keyring = Mock()
            worker.copy_packages()
            packages = worker.backend.instance.pacman.strap.call_args.args[0]
            self.assertEqual('emaki-apps' in packages, value == 'rich')
            self.assertIn('emaki-desktop', packages)

    def test_snapshot_packages_follow_root_filesystem_through_copy_and_preflight(self):
        snapshots = {'snapper', 'snap-pac', 'grub-btrfs'}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'package.pkg.tar.zst'
            archive.write_bytes(b'fixture')
            Path(str(archive) + '.sig').write_bytes(b'fixture signature')
            for fs in ('btrfs', 'ext4'):
                for software in ('rich', 'minimal'):
                    with self.subTest(fs=fs, software=software):
                        worker = Worker(SimpleNamespace(locale=Mock()), None, Redactor(), Mock(), Mock(),
                                        target=root / fs / software)
                        worker.plan = make_plan(dict(config(fs=fs), software=software), inventory())
                        worker.backend = Mock()
                        worker.populate_keyring = Mock()
                        worker.copy_packages()
                        installed = worker.backend.instance.pacman.strap.call_args.args[0]
                        expected = snapshots if fs == 'btrfs' else set()
                        self.assertEqual(snapshots.intersection(installed), expected)
                        runner = Mock()
                        runner.run.return_value = archive.as_uri() + '\n'
                        with patch('emaki_installer.worker.offline_config', return_value=root / 'offline.conf'):
                            preflight_repo(runner, software=software, btrfs=worker.plan.btrfs)
                        resolved = runner.run.call_args.args[0]
                        self.assertEqual(snapshots.intersection(resolved), expected)
                        self.assertTrue(set(installed) <= set(resolved))

    def test_preflight_resolves_selected_set_and_missing_rich_never_touches_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'package.pkg.tar.zst'
            package.write_bytes(b'fixture')
            Path(str(package) + '.sig').write_bytes(b'fixture signature')
            calls = []

            def run(argv):
                calls.append(argv)
                return package.as_uri() + '\n' if '-Sp' in argv else ''

            runner = SimpleNamespace(run=run)
            with patch('emaki_installer.worker.offline_config', return_value=root / 'offline.conf'):
                for value in ('rich', 'minimal'):
                    preflight_repo(runner, software=value)
                    self.assertEqual('emaki-apps' in calls[-1], value == 'rich')
                    self.assertIn('--config', calls[-1])
                backend, events = Mock(), []

                def missing(argv):
                    if '-Sp' in argv and 'emaki-apps' in argv:
                        raise InstallError(Code.COMMAND_FAILED, 'target not found: emaki-apps')
                    return ''

                worker = Worker(None, FakeInventory(), Redactor(), lambda kind, **kw: events.append((kind, kw)),
                                Mock(), target=root / 'target', backend_factory=backend,
                                runner_factory=lambda *args: SimpleNamespace(run=missing))
                with patch('emaki_installer.worker.cleanup') as clean:
                    worker.run(make_plan(config(), inventory()), threading.Event())
                backend.assert_not_called()
                clean.assert_not_called()
                self.assertFalse(worker.disk_changed)
                self.assertEqual(events[-1][1]['code'], 'offline_repo_incomplete')
                self.assertIn('emaki-apps', events[-1][1]['message'])
