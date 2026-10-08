import io
import json
from pathlib import Path
import unittest
import threading

import emaki_installer
from emaki_installer.constants import GIB, MAX_FRAME
from emaki_installer.errors import Code, InstallError
from emaki_installer.latin_layouts import CONSOLE_CHARS
from emaki_installer.planner import RESERVED_LOGINS
from emaki_installer.protocol import Controller, Job, decode_frame, encode_frame
from support import FakeInventory, config, inventory


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.log = io.StringIO()
        self.controller = Controller(FakeInventory(), None, clock=lambda: self.now, log_stream=self.log)

    def request(self, kind, **fields):
        return self.controller.handle({'type': kind, 'id': 'request-1', **fields})

    def plan(self):
        return self.request('plan', config=config())[0]

    def confirm(self, ack):
        return self.request('confirm', plan_id=ack['plan_id'], token=ack.get('token', ''))

    def test_review_renewal_rotates_token_without_resending_password(self):
        ack = self.plan()
        self.now += 550
        fresh = self.request('renew', plan_id=ack['plan_id'], token=ack['token'])[0]
        self.assertNotEqual(ack['token'], fresh['token'])
        self.now += 550
        self.assertTrue(self.confirm(fresh)[0]['ok'])

    def test_skip_update_is_separate_from_install_cancellation(self):
        self.confirm(self.plan())
        response = self.request('skip_update')[0]
        self.assertTrue(response['ok'])
        self.assertTrue(self.controller.job.skip_update.is_set())
        self.assertFalse(self.controller.job.cancelled.is_set())

    def test_frame_limit_in_bytes_including_newline(self):
        base = {'type': 'hello', 'id': '1', 'proto': 1, 'padding': ''}
        overhead = len(encode_frame(base))
        base['padding'] = 'x' * (MAX_FRAME - overhead)
        data = encode_frame(base)
        self.assertEqual(len(data), MAX_FRAME)
        self.assertEqual(decode_frame(data)['proto'], 1)
        with self.assertRaises(InstallError):
            decode_frame(data[:-1] + b'x\n')
        base['padding'] += 'é'
        with self.assertRaises(InstallError):
            encode_frame(base)

    def test_invalid_frames_and_nan(self):
        for data in (b'{}\n', b'[]\n', b'{}', b'\xff\n', b'{"type":"hello","id":"1","proto":NaN}\n'):
            with self.assertRaises(InstallError):
                decode_frame(data)

    def test_hello_reports_the_package_version(self):
        self.assertEqual(self.request('hello', proto=1)[0]['emaki_version'], emaki_installer.__version__)

    # The package's check() runs from the installer source archive, which has no iso/; the
    # checkout run (make check-installer) keeps the installer and the image version together.
    @unittest.skipUnless((Path(__file__).resolve().parents[2] / 'iso/VERSION').is_file(),
                         'iso/VERSION is outside the installer source archive')
    def test_hello_reports_the_candidate_version(self):
        expected = (Path(__file__).resolve().parents[2] / 'iso/VERSION').read_text().strip()
        self.assertEqual(self.request('hello', proto=1)[0]['emaki_version'], expected)

    def test_hello_carries_the_planner_reserved_logins(self):
        # The window refuses exactly the names validate_config refuses (installer/ui/Protocol.js).
        hello = self.request('hello', proto=1)[0]
        self.assertEqual(hello['reserved_logins'], list(RESERVED_LOGINS))
        # With the console table below the frame stays under a quarter of the limit.
        self.assertLess(len(encode_frame(hello)), MAX_FRAME // 4)

    def test_hello_carries_the_console_table(self):
        # The window names the password characters the text console types differently
        # (Protocol.js consoleUnsafeChars, planner.console_unsafe_chars); the password stays there.
        hello = json.loads(encode_frame(self.request('hello', proto=1)[0]))
        self.assertEqual(hello['console_chars'], {name: list(record) for name, record in CONSOLE_CHARS.items()})

    def test_reply_and_plan_echo_client_id(self):
        ack = self.plan()
        self.assertEqual(ack['id'], 'request-1')
        reply = self.request('cancel')[0]
        self.assertEqual(reply['for_id'], 'request-1')
        self.assertGreater(reply['seq'], ack['seq'])

    def test_token_expiry_at_600s(self):
        ack = self.plan()
        self.assertEqual(ack['expires_s'], 600)
        self.now += 600
        response = self.confirm(ack)[0]
        self.assertEqual(response['code'], 'token_expired')
        self.assertIsNone(self.controller.pending)

    def test_probe_and_new_plan_invalidate_token(self):
        for command in ('probe', 'plan'):
            ack = self.plan()
            if command == 'probe':
                self.request(command)
            else:
                self.plan()
            self.assertEqual(self.confirm(ack)[0]['code'], 'token_invalid')

    def test_busy_confirm_no_second_writer(self):
        ack = self.plan()
        responses = self.confirm(ack)
        self.assertTrue(responses[0]['ok'])
        self.assertTrue(callable(responses[1]))
        self.assertEqual(self.confirm(ack)[0]['code'], 'busy')
        self.assertEqual(self.request('plan', config=config())[0]['code'], 'busy')

    def test_pending_restart_blocks_installation_but_keeps_recovery_available(self):
        blocked = True
        checks, probes, exports, workers = [], [], [], []
        message = ('A previous restart is still pending. '
                   'Wait for it to finish before starting another installation.')

        def check():
            checks.append(1)
            if blocked:
                raise InstallError(Code.RESTART_PENDING, message)

        class Worker:
            def __init__(self, controller):
                workers.append('created')

            def run(self, plan, cancelled):
                workers.append('ran')

        self.controller = Controller(FakeInventory(), Worker, check_installation=check,
                                     save_log=lambda dest: exports.append(dest))
        self.controller.inventory.probe = lambda: probes.append(1) or inventory()
        for reply in (self.plan(), self.request('confirm', plan_id='old', token='old')[0]):
            self.assertFalse(reply['ok'])
            self.assertEqual(reply['code'], 'restart_pending')
            self.assertEqual(reply['msg'], message)
        self.assertIsNone(self.controller.pending)
        self.assertIsNone(self.controller.job)
        self.assertEqual(probes, [])
        self.assertEqual(workers, [])
        self.assertEqual(len(checks), 2)
        self.assertEqual(self.request('hello', proto=1)[0]['type'], 'hello')
        self.assertEqual(self.request('probe')[0]['type'], 'inventory')
        self.assertTrue(self.request('save_log', dest='usb')[0]['ok'])
        self.assertEqual(exports, ['usb'])
        self.assertEqual(len(checks), 2)

        blocked = False
        ack = self.plan()
        self.assertEqual(ack['errors'], [])
        reply, launch = self.confirm(ack)
        self.assertTrue(reply['ok'])
        thread = launch()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(workers, ['created', 'ran'])
        self.assertEqual(len(checks), 4)

    def test_confirm_rechecks_pending_restart_after_successful_plan(self):
        blocked = False
        checks = []

        def check():
            checks.append(1)
            if blocked:
                raise InstallError(Code.RESTART_PENDING, 'A previous restart is still pending.')

        self.controller.check_installation_fn = check
        ack = self.plan()
        blocked = True
        responses = self.confirm(ack)
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]['code'], 'restart_pending')
        self.assertIsNone(self.controller.job)
        self.assertIsNotNone(self.controller.pending)
        blocked = False
        self.assertTrue(self.confirm(ack)[0]['ok'])
        self.assertEqual(len(checks), 3)

    def test_invalid_plan_has_errors_and_no_token(self):
        c = config()
        c['disk_id'] = '/dev/absent'
        ack = self.request('plan', config=c)[0]
        self.assertEqual(ack['errors'][0]['code'], 'disk_not_found')
        self.assertNotIn('token', ack)

    def test_alongside_is_refused_before_any_probe(self):
        probes = []
        self.controller.inventory.probe = lambda: probes.append(1) or inventory()
        c = config('alongside')
        c.update(partition_id='/dev/vda2', shrink_bytes=40 * GIB)
        ack = self.request('plan', config=c)[0]
        self.assertEqual(ack['errors'][0]['code'], 'unsupported_mode')
        self.assertIn('not available', ack['errors'][0]['msg'])
        self.assertNotIn('token', ack)
        self.assertEqual(probes, [])

    def test_cancel_only_marks_job_until_boundary(self):
        self.controller.job = Job('job-test')
        result = self.request('cancel')
        self.assertEqual(result, [])
        self.assertTrue(self.controller.job.cancelled.is_set())
        self.assertIsNone(self.controller.job.terminal)

    def test_hardware_notice_survives_phase_change_and_reconnect(self):
        self.controller.job = Job('job-test')
        notice = 'The firmware used the fallback startup file (NVRAM).'
        self.controller.emit('progress', phase='bootloader', notice=notice)
        self.controller.emit('state', phase='account')
        result = self.request('resume', job_id='job-test', since_seq=0)
        self.assertEqual(result[-1]['notice'], notice)
        self.assertEqual(result[-1]['phase'], 'account')

    def test_resume_replays_only_new_logs_and_current_state(self):
        self.controller.job = Job('job-test')
        step = {'id': 'copy-17', 'name': 'initramfs', 'state': 'running',
                'text': 'Building the startup image (mkinitcpio: linux-lts: default).'}
        self.controller.emit('progress', phase='copy_packages', phase_pct=1, total_pct=5.6,
                             indeterminate=False, step=step)
        first = self.controller.emit('log', line='first')
        second = self.controller.emit('log', line='second')
        result = self.request('resume', job_id='job-test', since_seq=first['seq'])
        self.assertEqual([r['line'] for r in result if r['type'] == 'log'], ['second'])
        self.assertEqual(result[0]['seq'], second['seq'])
        self.assertEqual(result[-1]['type'], 'state')
        self.assertEqual(result[-1]['step'], step)
        self.controller.emit('done', seconds=1, log_path='/var/log/emaki-install.log')
        result = self.request('resume', job_id='job-test', since_seq=0)
        self.assertEqual(result[-1]['type'], 'done')
        self.assertEqual([r['seq'] for r in result], sorted(r['seq'] for r in result))

    def test_redaction_before_both_wire_and_disk(self):
        self.plan()
        self.controller.job = Job('job-test')
        captured = []
        self.controller.listeners.add(captured.append)
        self.controller.log('unexpected a-secret-for-tests output password=other-secret')
        wire = json.dumps(captured)
        self.assertNotIn('a-secret-for-tests', wire + self.log.getvalue())
        self.assertNotIn('other-secret', wire + self.log.getvalue())

    def full_disk_job(self, work):
        """Run one job whose log file is on a full file system."""
        class Full(io.StringIO):
            def write(self, text):
                raise OSError(28, 'No space left on device')

        class Worker:
            def __init__(self, controller):
                self.controller = controller

            def run(self, plan, cancelled):
                work(self.controller)

        controller = Controller(FakeInventory(), Worker, clock=lambda: self.now, log_stream=Full())
        c = config()
        c.update(encryption='separate', disk_password='a-disk-secret-for-tests')
        ack = controller.handle({'type': 'plan', 'id': 'p', 'config': c})[0]
        plan = controller.pending['plan']
        self.assertEqual(plan.config['disk_password'], 'a-disk-secret-for-tests')
        captured = []
        controller.listeners.add(captured.append)
        reply, launch = controller.handle({'type': 'confirm', 'id': 'c', 'plan_id': ack['plan_id'],
                                           'token': ack['token']})
        thread = launch()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((plan.config['user']['password'], plan.config['disk_password']), ('', ''))
        return controller, captured

    def test_full_log_file_does_not_hang_a_finishing_job(self):
        def work(controller):
            controller.log('one line')
            controller.log('another line')
            controller.emit('done', seconds=1, log_path='/var/log/emaki-install.log')
        controller, captured = self.full_disk_job(work)
        self.assertEqual(controller.job.terminal['type'], 'done')
        self.assertFalse(controller.busy)
        self.assertEqual([m['line'] for m in captured if m['type'] == 'log'], ['one line', 'another line'])
        self.assertIsNone(controller.log_stream)

    def test_full_log_file_does_not_hide_a_failing_job(self):
        def work(controller):
            controller.log('one line')
            raise RuntimeError('boom')
        controller, captured = self.full_disk_job(work)
        self.assertEqual(controller.job.terminal['type'], 'error')
        self.assertFalse(controller.busy)
        self.assertIn('Worker failed: boom', [m['line'] for m in captured if m['type'] == 'log'])

    def test_full_history_still_delivers_and_resumes(self):
        self.controller.job = Job('job-test')
        captured = []
        self.controller.listeners.add(captured.append)
        self.controller.emit('log', line='kept')
        real = self.controller.job.history.write
        self.controller.job.history.write = lambda text: (real(text[:5]), (_ for _ in ()).throw(OSError(28, 'full')))
        self.controller.emit('log', line='lost')
        self.controller.job.history.write = real
        self.controller.emit('log', line='after')
        self.controller.emit('done', seconds=1, log_path='/var/log/emaki-install.log')
        self.assertEqual([m.get('line') for m in captured], ['kept', 'lost', 'after', None])
        result = self.request('resume', job_id='job-test', since_seq=0)
        self.assertEqual([r['line'] for r in result if r['type'] == 'log'], ['kept'])
        self.assertEqual(result[-1]['type'], 'done')

    def test_secrets_are_forgotten_with_the_plan(self):
        self.plan()
        self.assertEqual(self.controller.redactor.secrets, {'a-secret-for-tests'})
        self.request('probe')
        self.assertEqual(self.controller.redactor.secrets, set())
        self.plan()
        self.request('cancel')
        self.assertEqual(self.controller.redactor.secrets, set())
        ack = self.plan()
        self.now += 600
        self.confirm(ack)
        self.assertEqual(self.controller.redactor.secrets, set())

    def test_secrets_are_forgotten_when_the_job_ends(self):
        seen = []

        class Worker:
            def __init__(self, controller):
                self.controller = controller

            def run(self, plan, cancelled):
                seen.append(set(self.controller.redactor.secrets))
                self.controller.emit('done', seconds=1, log_path='/var/log/emaki-install.log')

        self.controller.worker_factory = Worker
        self.confirm(self.plan())[1]().join(10)
        self.assertEqual(seen, [{'a-secret-for-tests'}])
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.redactor.secrets, set())

    def test_probe_during_a_job_keeps_redacting(self):
        ack = self.plan()
        self.assertTrue(self.confirm(ack)[0]['ok'])
        for kind in ('probe', 'plan', 'set_timezone'):
            self.assertEqual(self.request(kind, config=config())[0]['code'], 'busy')
        self.assertEqual(self.controller.redactor.secrets, {'a-secret-for-tests'})
        captured = []
        self.controller.listeners.add(captured.append)
        self.controller.log('tool printed a-secret-for-tests')
        self.assertEqual(captured[0]['line'], 'tool printed [REDACTED]')
        self.assertNotIn('a-secret-for-tests', self.log.getvalue())

    def test_wrong_resume_and_unknown_type_structured(self):
        result = self.request('resume', job_id='missing', since_seq=0)[0]
        self.assertEqual(result['code'], 'job_not_found')
        self.assertIn('log_path', result)
        self.assertEqual(self.request('unknown')[0]['code'], 'bad_request')

    def test_save_log_finishes_before_preparation_and_is_then_blocked(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def save(dest):
            entered.set()
            self.assertTrue(release.wait(2))
            calls.append('saved')
        self.controller.save_log_fn = save
        self.controller.prepare_reboot_fn = lambda: calls.append('prepared')
        self.controller.job = Job('finished')
        self.controller.emit('done', seconds=1)
        saver = threading.Thread(target=lambda: self.request('save_log', dest='usb'))
        preparer = threading.Thread(target=lambda: self.request('prepare_reboot'))
        saver.start()
        self.assertTrue(entered.wait(2))
        preparer.start()
        self.assertEqual(calls, [])
        release.set()
        saver.join(2)
        preparer.join(2)
        self.assertFalse(saver.is_alive() or preparer.is_alive())
        self.assertEqual(calls, ['saved', 'prepared'])
        self.assertEqual(self.request('save_log', dest='usb')[0]['code'], 'busy')
        self.assertEqual(calls, ['saved', 'prepared'])

    def test_failed_preparation_allows_log_export_without_reopening_installation(self):
        calls = []
        def fail():
            raise RuntimeError('preparation failed')
        self.controller.prepare_reboot_fn = fail
        self.controller.save_log_fn = lambda dest: calls.append(dest)
        self.controller.job = Job('finished')
        self.controller.emit('done', seconds=1)
        self.assertFalse(self.request('prepare_reboot')[0]['ok'])
        self.assertEqual(self.controller.restart_state, 'failed')
        self.assertTrue(self.controller.stopping)
        self.assertTrue(self.request('save_log', dest='usb')[0]['ok'])
        self.assertEqual(calls, ['usb'])
        self.assertEqual(self.request('probe')[0]['code'], 'busy')

    def test_log_export_refusal_describes_current_restart_state(self):
        calls = []
        self.controller.save_log_fn = lambda dest: calls.append(dest)
        self.controller.stopping = True
        for state, message in (
                ('preparing', 'Log export is unavailable during restart preparation.'),
                ('ready', 'Log export is unavailable after restart preparation.'),
                ('forced', 'Log export is unavailable after restart preparation.'),
                (None, 'Log export is unavailable while the worker is stopping.')):
            with self.subTest(state=state):
                self.controller.restart_state = state
                reply = self.request('save_log', dest='usb')[0]
                self.assertEqual(reply['code'], 'busy')
                self.assertEqual(reply['msg'], message)
        self.assertEqual(calls, [])

    def test_hello_retains_restart_state_and_original_deadline(self):
        self.controller.job = Job('finished')
        self.controller.emit('done', seconds=1)
        self.controller.prepare_reboot_fn = lambda: True
        self.controller.restart_deadline_fn = lambda: 23
        self.assertEqual(self.request('prepare_reboot')[0]['remaining_s'], 13)
        self.now = 15
        state = self.request('hello', proto=1)[0]['restart']
        self.assertEqual(state, {'state': 'forced', 'job_id': 'finished', 'remaining_s': 8})
        self.assertEqual(self.request('prepare_reboot')[0]['remaining_s'], 8)

    def test_missing_forced_deadline_does_not_invent_another_countdown(self):
        self.controller.job = Job('finished')
        self.controller.emit('done', seconds=1)
        self.controller.prepare_reboot_fn = lambda: True
        self.controller.restart_deadline_fn = lambda: None
        self.assertEqual(self.request('prepare_reboot')[0]['remaining_s'], 0)
        self.assertEqual(self.request('hello', proto=1)[0]['restart']['remaining_s'], 0)

    def test_reboot_only_after_done_and_blocks_new_jobs(self):
        calls = []
        self.controller.reboot_fn = lambda: calls.append('reboot')
        self.assertFalse(self.request('reboot')[0]['ok'])
        self.controller.job = Job('job')
        self.controller.emit('done', log_path='/var/log/emaki-install.log', seconds=1)
        self.assertTrue(self.request('reboot')[0]['ok'])
        self.assertEqual(calls, ['reboot'])
        self.assertEqual(self.request('plan', config=config())[0]['code'], 'busy')


if __name__ == '__main__':
    unittest.main()
