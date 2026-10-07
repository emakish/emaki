# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import io
from pathlib import Path
from ssl import SSLCertVerificationError, SSLError
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError

from emaki_installer.clock import ensure_clock, http_date, newest_build_date, time_urls
from emaki_installer.errors import InstallError


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.current = 1800000000
        self.elapsed = 0
        self.messages = []
        self.runner = Mock()
        self.runner.run.side_effect = self.command

    def command(self, argv, **kwargs):
        if argv[1] == 'show':
            return 'yes\n'
        if argv[0] == 'date':
            self.current = float(argv[3].removeprefix('@'))
        return ''

    def certificate_error(self, code=9):
        error = SSLCertVerificationError('certificate verification failed')
        error.verify_code = code
        return URLError(error)

    def mirrorlist(self, text):
        path = self.root / 'etc/pacman.d/mirrorlist'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def database(self, builds):
        with tarfile.open(self.repo / 'offline.db', 'w:gz') as archive:
            for index, value in enumerate(builds):
                data = f'%NAME%\npackage-{index}\n\n%BUILDDATE%\n{value}\n'.encode()
                entry = tarfile.TarInfo(f'package-{index}/desc')
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))

    def sleep(self, seconds):
        self.elapsed += seconds
        self.current += seconds

    def check(self, **kwargs):
        return ensure_clock(self.runner, repo='/repo', root=self.root,
                            now=lambda: self.current, notice=self.messages.append,
                            monotonic=lambda: self.elapsed, sleep=self.sleep, **kwargs)

    def test_healthy_online_clock_does_not_change_settings(self):
        self.assertFalse(self.check(online=True, source=lambda: self.current + 40))
        self.runner.run.assert_not_called()
        self.assertEqual(self.messages, [])

    def test_online_skew_in_both_directions(self):
        for skew in (-86400, 86400):
            with self.subTest(skew=skew):
                target = self.current + skew
                self.assertTrue(self.check(online=True, source=lambda: target))
                self.assertEqual(self.current, target)
                self.assertEqual([call.args[0] for call in self.runner.run.call_args_list], [
                    ['timedatectl', 'show', '--property=NTP', '--value'],
                    ['timedatectl', 'set-ntp', 'false'],
                    ['date', '-u', '-s', f'@{float(target)}'],
                    ['timedatectl', 'set-ntp', 'true'],
                ])
                self.runner.reset_mock()
                self.assertIn('corrected', self.messages[-1])

    def test_offline_repo_metadata_sets_lower_bound_only(self):
        self.database([1700000000, 'invalid', 1850000000])
        self.assertEqual(newest_build_date('/repo', root=self.root), 1850000000)
        self.assertTrue(self.check(online=False))
        self.assertEqual(self.current, 1850000300)
        self.runner.reset_mock()
        self.assertFalse(self.check(online=False))
        self.runner.run.assert_not_called()

    def test_offline_ahead_is_not_moved_back_to_build_time(self):
        self.database([1700000000])
        self.assertFalse(self.check(online=False))
        self.runner.run.assert_not_called()

    def test_unreachable_time_source_uses_offline_bound_then_retries(self):
        self.database([1850000000])
        source = Mock(side_effect=[URLError('connection refused'), 1850010000])
        self.assertTrue(self.check(online=True, source=source))
        self.assertEqual(self.current, 1850010000)
        self.assertEqual(source.call_count, 2)

    def test_unavailable_or_malformed_sources_never_change_clock(self):
        (self.repo / 'broken.db').write_bytes(b'not a tar archive')
        for value in (None, float('nan'), float('inf'), -1):
            self.assertFalse(self.check(online=True, source=lambda: value))
        self.runner.run.assert_not_called()
        self.assertEqual(self.messages, [])
        self.assertEqual(self.elapsed, 0)

    def test_correction_failure_stops_with_plain_message_and_restores_ntp(self):
        def fail(argv, **kwargs):
            if argv[0] == 'date':
                raise OSError('read-only clock')
            return self.command(argv, **kwargs)
        self.runner.run.side_effect = fail
        with self.assertRaisesRegex(InstallError, 'disk has not been changed'):
            self.check(online=True, source=lambda: 1850000000)
        self.assertEqual(self.runner.run.call_args.args[0], ['timedatectl', 'set-ntp', 'true'])

    def test_successful_command_with_unchanged_clock_is_failure(self):
        self.runner.run.side_effect = None
        self.runner.run.return_value = 'no'
        with self.assertRaisesRegex(InstallError, 'clock is still incorrect'):
            self.check(online=True, source=lambda: 1850000000)

    def test_manual_clock_stays_manual(self):
        def manual(argv, **kwargs):
            return 'no' if argv[1] == 'show' else self.command(argv, **kwargs)
        self.runner.run.side_effect = manual
        self.assertTrue(self.check(online=True, source=lambda: 1850000000))
        self.assertFalse(any(call.args[0][1] == 'set-ntp' for call in self.runner.run.call_args_list))

    @patch('emaki_installer.clock.urlopen')
    def test_http_date_uses_bounded_https_request(self, opener):
        response = opener.return_value.__enter__.return_value
        response.url = 'https://archlinux.org/'
        response.headers = {'Date': 'Wed, 07 Oct 2026 12:00:00 GMT'}
        self.assertEqual(http_date('https://mirror.invalid/'), 1791374400)
        self.assertEqual(opener.call_args.kwargs['timeout'], 5)
        self.assertEqual(opener.call_args.args[0].method, 'HEAD')
        self.assertEqual(opener.call_args.args[0].full_url, 'https://mirror.invalid/')
        self.assertNotIn('context', opener.call_args.kwargs)
        response.url = 'http://archlinux.org/'
        with self.assertRaises(ValueError):
            http_date('https://mirror.invalid/')

    def test_ntp_bootstraps_tls_for_future_and_old_clocks_without_repo(self):
        for initial in (1000000000, 2300000000):
            with self.subTest(initial=initial):
                self.current = initial
                def ntp(argv, **kwargs):
                    if argv[1] == 'show' and '--property=NTP' in argv:
                        return 'no'
                    if '--property=NTPSynchronized' in argv:
                        self.current = 1800000000
                        return 'yes'
                    return self.command(argv, **kwargs)
                self.runner.run.side_effect = ntp
                source = Mock(side_effect=[self.certificate_error(9 if initial < 1800000000 else 10),
                                           1800000000])
                self.assertTrue(self.check(online=True, source=source))
                self.assertEqual(self.current, 1800000000)
                self.assertEqual(source.call_count, 2)
                self.assertEqual(self.runner.run.call_args.args[0],
                                 ['timedatectl', 'set-ntp', 'false'])

    def test_ntp_timeout_is_bounded_and_does_not_claim_correction(self):
        def unsynchronized(argv, **kwargs):
            if argv[1] == 'show':
                return 'no'
            return ''
        self.runner.run.side_effect = unsynchronized
        source = Mock(side_effect=self.certificate_error())
        self.assertFalse(self.check(online=True, source=source))
        self.assertEqual(self.elapsed, 10)
        self.assertIn('could not be checked', self.messages[-1])
        self.assertFalse(any('was corrected' in message for message in self.messages))
        self.assertEqual(self.runner.run.call_args.args[0],
                         ['timedatectl', 'set-ntp', 'false'])

    def test_unavailable_ntp_does_not_crash_or_claim_correction(self):
        self.runner.run.side_effect = OSError('time service unavailable')
        source = Mock(side_effect=self.certificate_error())
        self.assertFalse(self.check(online=True, source=source))
        self.assertEqual(source.call_count, 2)
        self.assertFalse(any('was corrected' in message for message in self.messages))


    def test_connection_and_non_date_tls_errors_do_not_trigger_recovery(self):
        for error in (URLError('connection refused'), TimeoutError(),
                      SSLError('TLS handshake failed'), self.certificate_error(20),
                      self.certificate_error(62), OSError('certificate date')):
            with self.subTest(error=error):
                source = Mock(side_effect=error)
                self.assertFalse(self.check(online=True, source=source))
                source.assert_called_once_with()
        self.runner.run.assert_not_called()
        self.assertEqual(self.elapsed, 0)
        self.assertEqual(self.messages, [])

    def test_mirror_origin_precedes_independent_hosts(self):
        self.mirrorlist('# Server = https://comment.invalid/$repo/os/$arch\n'
                        'Server = http://insecure.invalid/$repo/os/$arch\n'
                        'Server = https://mirror.invalid/arch/$repo/os/$arch # primary\n'
                        'Server = https://unused.invalid/$repo/os/$arch\n')
        self.assertEqual(time_urls(self.root), ['https://mirror.invalid/',
                                              'https://archlinux.org/', 'https://www.kernel.org/'])
        self.mirrorlist('Server = https://archlinux.org/$repo/os/$arch\n')
        self.assertEqual(time_urls(self.root), ['https://archlinux.org/', 'https://www.kernel.org/'])

    @patch('emaki_installer.clock.urlopen')
    def test_configured_mirror_checks_healthy_clock_without_fallback(self, opener):
        self.mirrorlist('Server = https://mirror.invalid/$repo/os/$arch\n')
        response = opener.return_value.__enter__.return_value
        response.url = 'https://mirror.invalid/'
        response.headers = {'Date': 'Wed, 07 Oct 2026 12:00:00 GMT'}
        self.current = 1791374400
        self.assertFalse(self.check(online=True))
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(opener.call_args.args[0].full_url, 'https://mirror.invalid/')
        self.runner.run.assert_not_called()
        self.assertEqual(self.messages, [])

    @patch('emaki_installer.clock.http_date')
    def test_fallback_healthy_clock_suppresses_failed_host_recovery(self, reader):
        self.mirrorlist('Server = https://mirror.invalid/$repo/os/$arch\n')
        for error in (URLError('unreachable'), self.certificate_error(10)):
            with self.subTest(error=error):
                reader.reset_mock()
                reader.side_effect = [error, URLError('unreachable'), self.current]
                self.assertFalse(self.check(online=True))
                self.assertEqual([call.args[0] for call in reader.call_args_list],
                                 ['https://mirror.invalid/', 'https://archlinux.org/',
                                  'https://www.kernel.org/'])
        self.runner.run.assert_not_called()
        self.assertEqual(self.messages, [])
        self.assertEqual(self.elapsed, 0)

    @patch('emaki_installer.clock.http_date')
    def test_all_hosts_unreachable_leave_clock_and_notices_untouched(self, reader):
        self.mirrorlist('Server = https://mirror.invalid/$repo/os/$arch\n')
        reader.side_effect = URLError('unreachable')
        self.assertFalse(self.check(online=True))
        self.assertEqual(reader.call_count, 3)
        self.runner.run.assert_not_called()
        self.assertEqual(self.messages, [])
        self.assertEqual(self.elapsed, 0)

    @patch('emaki_installer.clock.http_date')
    def test_fallback_disagreement_corrects_clock(self, reader):
        reader.side_effect = [URLError('unreachable'), self.current + 86400]
        self.assertTrue(self.check(online=True))
        self.assertEqual(self.current, 1800086400)
        self.assertEqual(reader.call_count, 2)
        self.assertIn('corrected', self.messages[-1])


class ClockBoundaryTests(unittest.TestCase):
    def test_clock_failure_precedes_repository_commands(self):
        from unittest.mock import patch
        from emaki_installer.worker import preflight_repo
        from emaki_installer.errors import Code, InstallError
        from support import RecordingRunner
        runner = RecordingRunner()
        failure = InstallError(Code.CLOCK_SKEW, 'Clock correction failed; disk untouched.')
        with patch('emaki_installer.worker.ensure_clock', side_effect=failure), \
                patch('emaki_installer.worker.offline_config') as repository:
            with self.assertRaises(InstallError):
                preflight_repo(runner, online=True)
        repository.assert_not_called()
        self.assertEqual(runner.commands, [])
