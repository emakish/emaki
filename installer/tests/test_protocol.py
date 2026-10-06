import io
import json
import unittest

import emaki_installer
from emaki_installer.constants import GIB, MAX_FRAME
from emaki_installer.errors import InstallError
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

    def test_resume_replays_only_new_logs_and_current_state(self):
        self.controller.job = Job('job-test')
        self.controller.emit('state', phase='copy_packages', phase_pct=1, total_pct=5.6, indeterminate=False)
        first = self.controller.emit('log', line='first')
        second = self.controller.emit('log', line='second')
        result = self.request('resume', job_id='job-test', since_seq=first['seq'])
        self.assertEqual([r['line'] for r in result if r['type'] == 'log'], ['second'])
        self.assertEqual(result[0]['seq'], second['seq'])
        self.assertEqual(result[-1]['type'], 'state')
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
