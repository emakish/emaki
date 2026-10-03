import io
import json
import unittest

from emaki_installer.constants import MAX_FRAME
from emaki_installer.errors import InstallError
from emaki_installer.protocol import Controller, Job, decode_frame, encode_frame
from support import FakeInventory, config


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
