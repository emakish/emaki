#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated storage, IPC, editor-watch and reminder regression tests."""
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1] / 'backend.py'
spec = importlib.util.spec_from_file_location('notes_backend', BACKEND)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class Lines:
    """Newline-delimited JSON read from a pipe with a deadline."""
    def __init__(self, test, fd):
        self.test, self.fd, self.pending = test, fd, b''

    def receive(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if b'\n' in self.pending:
                line, self.pending = self.pending.split(b'\n', 1)
                value = json.loads(line)
                if predicate(value):
                    return value
                continue
            if select.select([self.fd], [], [], .2)[0]:
                self.pending += os.read(self.fd, 65536)
        self.test.fail('No expected backend response')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = backend.Store(root, root / 'data', root / 'state')

    def request(self, action, **fields):
        result = self.store.request(dict(id=1, action=action, **fields))
        self.assertTrue(result['ok'], result)
        return result

    def create(self, body='Shopping\n- [ ] Bread\n- [x] Tea\n'):
        self.request('notes.create', body=body)
        return self.store.snapshot()['notes'][0]

    def test_markdown_checklist_and_sidecar(self):
        note = self.create()
        changed = self.request('notes.toggle', noteId=note['id'], revision=note['revision'], line=1)
        self.assertEqual(self.store.snapshot()['notes'][0]['body'], 'Shopping\n- [x] Bread\n- [x] Tea\n')
        changed = self.request('notes.addTask', noteId=note['id'], revision=changed['revision'], text='Milk')
        changed = self.request('notes.pin', noteId=note['id'], revision=changed['revision'], pinned=True)
        self.assertTrue(self.store.snapshot()['notes'][0]['pinned'])
        self.assertIn('- [ ] Milk\n', (self.store.notes / (note['id'] + '.md')).read_text())
        self.assertTrue(json.loads(self.store.sidecar.read_text())[note['id']]['pinned'])
        changed = self.request('notes.save', noteId=note['id'], revision=changed['revision'], body='Changed\n')
        self.assertEqual(self.store.snapshot()['notes'][0]['title'], 'Changed')
        self.request('notes.delete', noteId=note['id'], revision=changed['revision'])
        self.assertEqual(self.store.snapshot()['notes'], [])

    def test_stale_write_delete_and_pin_do_not_overwrite(self):
        note = self.create()
        path = self.store.notes / (note['id'] + '.md')
        backend.atomic_write(path, b'External edit\n')
        for action, extra in [('notes.save', {'body': 'Stale draft'}), ('notes.delete', {}), ('notes.pin', {'pinned': True})]:
            result = self.store.request(dict(action=action, noteId=note['id'], revision=note['revision'], **extra))
            self.assertFalse(result['ok'])
            self.assertTrue(result['conflict'])
            self.assertEqual(path.read_text(), 'External edit\n')

    def test_pin_elsewhere_keeps_a_draft_revision_valid(self):
        note = self.create()
        pinned = self.request('notes.pin', noteId=note['id'], revision=note['revision'], pinned=True)
        self.assertEqual(pinned['revision'], note['revision'])
        self.assertEqual(self.store.snapshot()['notes'][0]['revision'], note['revision'])
        saved = self.request('notes.save', noteId=note['id'], revision=note['revision'], body='Draft\n- [ ] Bread\n')
        self.request('notes.toggle', noteId=note['id'], revision=saved['revision'], line=1)
        current = self.store.snapshot()['notes'][0]
        self.assertTrue(current['pinned'])
        self.request('notes.pin', noteId=note['id'], revision=current['revision'], pinned=False)
        self.request('notes.addTask', noteId=note['id'], revision=current['revision'], text='Milk')
        self.assertEqual(self.store.snapshot()['notes'][0]['body'], 'Draft\n- [x] Bread\n- [ ] Milk\n')

    def test_toggle_numbers_lines_like_the_shell(self):
        note = self.create('Mixed separators\r\n- [ ] Task\n')
        self.request('notes.toggle', noteId=note['id'], revision=note['revision'], line=1)
        self.assertEqual(self.store.snapshot()['notes'][0]['body'], 'Mixed separators\r\n- [x] Task\n')
        self.assertEqual(self.store.snapshot()['notes'][0]['title'], 'Mixed separators')

    def test_damaged_metadata_and_bad_fields_do_not_break_listing(self):
        for name in ('a', 'b', 'c'):
            (self.store.notes / (name + '.md')).write_text(name + '\n')
        for sidecar in ('{"a": {"order": null, "pinned": "yes"}, "b": {"order": "x", "pinned": true}, "c": 5}',
                        '{"a": {"order": NaN}, "b": {"order": 1e999, "pinned": 1}}', '[1]', '"text"', '', '{bad'):
            self.store.sidecar.write_text(sidecar)
            snapshot = self.store.snapshot()
            self.assertEqual(sorted(note['id'] for note in snapshot['notes']), ['a', 'b', 'c'], sidecar)
            self.assertTrue(all(note['pinned'] in (True, False) for note in snapshot['notes']))
        self.assertTrue(self.store.snapshot()['errors'])
        note = self.store.snapshot()['notes'][0]
        result = self.store.request(dict(action='notes.pin', noteId=note['id'], revision=note['revision'], pinned=True))
        self.assertFalse(result['ok'])
        self.assertEqual(self.store.sidecar.read_text(), '{bad')
        self.store.sidecar.write_text('{"a": {"order": null, "pinned": "yes"}, "c": 5}')
        note = next(note for note in self.store.snapshot()['notes'] if note['id'] == 'c')
        self.request('notes.pin', noteId='c', revision=note['revision'], pinned=True)
        self.assertEqual(self.store.snapshot()['notes'][0]['id'], 'c')
        for fields in ({'action': 'notes.addTask', 'noteId': 'c', 'revision': note['revision'], 'text': 5},
                       {'action': 'notes.save', 'noteId': 'c', 'revision': note['revision'], 'body': None},
                       {'action': 'notes.toggle', 'noteId': 'c', 'revision': note['revision'], 'line': True},
                       {'action': 'calendar.save', 'title': 7, 'date': '2026-10-19'},
                       {'action': 'calendar.save', 'title': 'x', 'date': 20261019},
                       {'action': 'calendar.save', 'title': 'x', 'date': '9999-12-31', 'duration': 'all-day'},
                       {'action': 'calendar.save', 'title': 'a\x00b', 'date': '2026-10-19'}):
            self.assertFalse(self.store.request(dict(fields, id=2))['ok'], fields)
        self.assertEqual(self.store.snapshot()['events'], [])

    def test_dangling_notes_link_is_reported_not_fatal(self):
        root = Path(self.temp.name) / 'linked'
        root.mkdir()
        (root / 'Notes').symlink_to(root / 'missing')
        store = backend.Store(root, root / 'data', root / 'state')
        snapshot = store.snapshot()
        self.assertEqual(snapshot['notes'], [])
        self.assertTrue(snapshot['errors'])
        self.assertFalse(store.request(dict(action='notes.create', body='x'))['ok'])

    def test_large_notes_are_listed_as_errors(self):
        (self.store.notes / 'big.md').write_bytes(b'x' * (backend.MAX_FILE + 1))
        note = self.create('Small\n')
        snapshot = self.store.snapshot()
        self.assertEqual([item['id'] for item in snapshot['notes']], [note['id']])
        self.assertTrue(any(error.startswith('big.md') for error in snapshot['errors']))
        result = self.store.request(dict(action='notes.save', noteId=note['id'], revision=note['revision'],
                                         body='x' * (backend.MAX_FILE + 1)))
        self.assertFalse(result['ok'])
        self.assertFalse(self.store.request(dict(action='notes.save', noteId='big', revision='x', body='y'))['ok'])

    def test_removed_note_is_conflict(self):
        note = self.create()
        (self.store.notes / (note['id'] + '.md')).unlink()
        result = self.store.request(dict(action='notes.save', noteId=note['id'], revision=note['revision'], body='Stale'))
        self.assertTrue(result['conflict'])

    def test_personal_markdown_and_unsafe_paths(self):
        (self.store.notes / 'My own file.md').write_text('# Existing\n- [ ] Done?\n')
        note = self.store.snapshot()['notes'][0]
        self.assertEqual(note['id'], 'My own file')
        self.assertEqual(note['title'], 'Existing')
        for identity in ('../escape', '/tmp/escape', '.', '..'):
            result = self.store.request(dict(action='notes.save', noteId=identity, revision=note['revision'], body='x'))
            self.assertFalse(result['ok'])
        outside = Path(self.temp.name) / 'outside'
        outside.write_text('Preserved')
        (self.store.notes / 'link.md').symlink_to(outside)
        result = self.store.request(dict(action='notes.save', noteId='link', revision='anything', body='x'))
        self.assertFalse(result['ok'])
        self.assertEqual(outside.read_text(), 'Preserved')

    def event(self, **fields):
        return self.request('calendar.save', **dict(dict(title='Lunch, with friends; café', date='2026-10-19',
                           start='12:30', duration=60, reminder=10), **fields))

    def test_calendar_crud_conflict_ics_and_all_day(self):
        created = self.event()
        event = self.store.snapshot()['events'][0]
        self.assertEqual(event['title'], 'Lunch, with friends; café')
        raw = (self.store.calendar / (created['eventId'] + '.ics')).read_bytes()
        self.assertIn(b'BEGIN:VCALENDAR\r\n', raw)
        self.assertIn(b'TRIGGER:-PT10M', raw)
        changed = self.event(eventId=event['id'], revision=event['revision'], title='Holiday', duration='all-day', reminder=None)
        current = self.store.snapshot()['events'][0]
        self.assertEqual(current['duration'], 'all-day')
        self.assertEqual(current['start'], '00:00')
        self.assertIsNone(current['reminder'])
        stale = self.store.request(dict(action='calendar.delete', eventId=event['id'], revision=event['revision']))
        self.assertTrue(stale['conflict'])
        self.request('calendar.delete', eventId=event['id'], revision=changed['revision'])
        self.assertEqual(self.store.snapshot()['events'], [])

    def test_ics_folding_unicode_and_escaped_values(self):
        title = 'é,;\\' * 60
        created = self.event(title=title)
        self.assertEqual(self.store.snapshot()['events'][0]['title'], title)
        raw = (self.store.calendar / (created['eventId'] + '.ics')).read_bytes()
        self.assertTrue(all(len(line) <= 75 for line in raw.split(b'\r\n')))

    def test_invalid_event_input_does_not_create_files(self):
        for fields in ({'duration': -1}, {'duration': 0}, {'date': '2026-02-31'}, {'start': '25:99'},
                       {'reminder': -1}, {'title': 'Title\r\nBEGIN:VEVENT'}):
            result = self.store.request(dict(action='calendar.save', **dict(dict(title='Test', date='2026-10-19',
                                        start='12:30', duration=60, reminder=None), **fields)))
            self.assertFalse(result['ok'])
        self.assertEqual(self.store.snapshot()['events'], [])

    def test_reminder_due_once_across_restart_and_failed_delivery(self):
        self.event()
        due = dt.datetime(2026, 10, 19, 12, 20).timestamp()
        delivered = []
        notify = lambda event: delivered.append(event['id']) or True
        self.assertEqual(self.store.remind(due - 1, notify), 0)
        self.assertEqual(self.store.remind(due, lambda event: False), 0)
        self.assertEqual(self.store.remind(due, notify), 1)
        self.assertEqual(self.store.remind(due + 60, notify), 0)
        root = Path(self.temp.name)
        restarted = backend.Store(root, root / 'data', root / 'state')
        self.assertEqual(restarted.remind(due + 3600, notify), 0)
        self.assertEqual(len(delivered), 1)
        event = self.store.snapshot()['events'][0]
        self.event(eventId=event['id'], revision=event['revision'], start='14:30')
        self.assertEqual(restarted.remind(due + 7200, notify), 1)

    def test_missed_reminder_catches_up_and_deleted_events_do_not_fire(self):
        first = self.event()
        self.event(title='No reminder', reminder=None)
        self.request('calendar.delete', eventId=first['eventId'], revision=first['revision'])
        during = dt.datetime(2026, 10, 19, 13, 29).timestamp()
        self.assertEqual(self.store.remind(during, lambda event: True), 0)
        self.event()
        self.assertEqual(self.store.remind(during, lambda event: True), 1)

    def test_stale_reminders_are_skipped_once_and_never_flood(self):
        self.event(title='Short', start='12:30', duration=5, reminder=10)
        self.event(title='Long', start='12:30', duration=120, reminder=10)
        self.event(title='Today', duration='all-day', reminder=60)
        self.event(title='Yesterday', date='2026-10-18', duration='all-day', reminder=0)
        self.event(title='Last week', date='2026-10-12')
        sent = []
        notify = lambda event: sent.append(event['title']) or True
        # Short is due 12:20 and relevant for 15 minutes; Long until it ends at 14:30.
        self.assertEqual(self.store.remind(dt.datetime(2026, 10, 19, 13, 0).timestamp(), notify), 2)
        self.assertEqual(sorted(sent), ['Long', 'Today'])
        root = Path(self.temp.name)
        restarted = backend.Store(root, root / 'data', root / 'state')
        self.assertEqual(restarted.remind(dt.datetime(2026, 10, 19, 13, 1).timestamp(), notify), 0)
        journal = json.loads((self.store.state / 'delivered.json').read_text())
        self.assertEqual(len(journal), 5)
        self.event(title='Entered late', date='2026-10-19', start='12:31', duration=1, reminder=0)
        self.assertEqual(restarted.remind(dt.datetime(2026, 10, 19, 12, 40).timestamp(), notify), 1)
        self.assertEqual(restarted.remind(dt.datetime(2026, 10, 19, 23, 0).timestamp(), notify), 0)
        self.assertEqual(len(sent), 3)

    def test_reminder_survives_time_zone_change_and_old_keys(self):
        previous = os.environ.get('TZ')
        def restore():
            if previous is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = previous
            time.tzset()
        self.addCleanup(restore)
        os.environ['TZ'] = 'UTC'
        time.tzset()
        created = self.event()
        due = dt.datetime(2026, 10, 19, 12, 20).timestamp()
        self.assertEqual(self.store.remind(due, lambda event: True), 1)
        os.environ['TZ'] = 'Asia/Tokyo'
        time.tzset()
        self.assertEqual(self.store.remind(dt.datetime(2026, 10, 19, 12, 21).timestamp(), lambda event: True), 0)
        legacy = dt.datetime(2026, 10, 19, 12, 20).timestamp()
        backend.atomic_write(self.store.state / 'delivered.json',
                             json.dumps({created['eventId'] + ':' + str(legacy): legacy}).encode())
        self.assertEqual(self.store.remind(legacy + 60, lambda event: True), 0)

    def test_reminders_send_without_the_store_lock_and_ignore_notes(self):
        self.event()
        self.store.sidecar.write_text('not json')
        self.store.snapshot = lambda: 1 / 0
        def notify(event):
            with (self.store.state / 'store.lock').open('a') as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (self.store.state / 'remind.lock').open('a') as stream:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        self.assertEqual(self.store.remind(dt.datetime(2026, 10, 19, 12, 20).timestamp(), notify), 1)

    def test_damaged_journal_is_set_aside_and_old_entries_pruned(self):
        self.event()
        journal = self.store.state / 'delivered.json'
        journal.write_text('')
        due = dt.datetime(2026, 10, 19, 12, 20).timestamp()
        self.assertEqual(self.store.remind(due, lambda event: True), 1)
        self.assertTrue((self.store.state / 'delivered.json.bad').exists())
        entries = json.loads(journal.read_text())
        entries.update(old=due - 61 * 86400, recent=due - 86400)
        journal.write_text(json.dumps(entries))
        self.assertEqual(self.store.remind(due + 60, lambda event: True), 0)
        self.assertEqual(sorted(json.loads(journal.read_text())), sorted(set(entries) - {'old'}))

    def test_titles_parse_only_on_crlf_and_stay_valid_ics(self):
        created = self.event(title='Lunch with\x0bBob\tx TRIGGER:bogus')
        event = self.store.snapshot()['events'][0]
        self.assertEqual(event['title'], 'Lunch with Bob x TRIGGER:bogus')
        self.assertEqual(event['reminder'], 10)
        # A file written before titles were cleaned still reads whole and stays editable.
        legacy = dict(id=created['eventId'], title='Lunch with\x0bBob\x1cTRIGGER:bogus', date='2026-10-19',
                      start='12:30', duration=60, reminder=10)
        path = self.store.calendar / (created['eventId'] + '.ics')
        backend.atomic_write(path, backend.event_bytes(legacy))
        event = self.store.snapshot()['events'][0]
        self.assertEqual(event['title'], legacy['title'])
        self.assertEqual(event['reminder'], 10)
        self.event(eventId=event['id'], revision=event['revision'], title='🙂é' * 40)
        raw = path.read_bytes()
        self.assertTrue(raw.endswith(b'\r\n'))
        for line in raw[:-2].split(b'\r\n'):
            self.assertLessEqual(len(line), 75)
            line.decode('utf-8')
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertIn(b'BEGIN:VALARM\r\n', raw)
        self.assertEqual(self.store.snapshot()['events'][0]['title'], '🙂é' * 40)
        for title in ('a\x00b', 'a\x1bb', 'a\rb', ' \x85'):
            self.assertFalse(self.store.request(dict(action='calendar.save', title=title, date='2026-10-19'))['ok'], title)

    def test_external_atomic_rename_wakes_inotify(self):
        watcher = backend.Watcher((self.store.notes, self.store.calendar))
        self.addCleanup(watcher.close)
        backend.atomic_write(self.store.notes / 'external.md', b'From editor')
        self.assertTrue(select.select([watcher.fd], [], [], 1)[0])
        watcher.drain()
        self.assertEqual(self.store.snapshot()['notes'][0]['body'], 'From editor')

    def test_watcher_follows_replaced_and_moved_directories(self):
        watcher = backend.Watcher((self.store.notes, self.store.calendar))
        self.addCleanup(watcher.close)
        shutil.rmtree(self.store.calendar)
        self.assertTrue(select.select([watcher.fd], [], [], 1)[0])
        watcher.drain()
        self.assertFalse(watcher.complete)
        self.store.calendar.mkdir()
        self.assertTrue(watcher.refresh())
        self.assertTrue(watcher.complete)
        self.event()
        self.assertTrue(select.select([watcher.fd], [], [], 1)[0])
        watcher.drain()
        moved = self.store.notes.with_name('Moved')
        self.store.notes.rename(moved)
        self.store.notes.mkdir()
        self.assertTrue(select.select([watcher.fd], [], [], 1)[0])
        watcher.drain()
        self.assertTrue(watcher.complete)
        (moved / 'old.md').write_text('Not watched')
        time.sleep(.2)
        watcher.drain()
        self.assertFalse(select.select([watcher.fd], [], [], .3)[0])
        (self.store.notes / 'new.md').write_text('Watched')
        self.assertTrue(select.select([watcher.fd], [], [], 1)[0])

    def test_serve_sleeps_until_a_change(self):
        calls = []
        snapshot = self.store.snapshot
        self.store.snapshot = lambda: calls.append(1) or snapshot()
        input_read, input_write = os.pipe()
        output_read, output_write = os.pipe()
        stdin, stdout = os.fdopen(input_read, 'rb'), os.fdopen(output_write, 'w')
        for patch in (mock.patch.object(sys, 'stdin', stdin), mock.patch.object(sys, 'stdout', stdout),
                      mock.patch.object(backend, 'WATCH_RETRY', .2)):
            patch.start()
        thread = threading.Thread(target=backend.serve, args=(self.store,))
        thread.start()
        def cleanup():
            os.close(input_write)
            thread.join(5)
            mock.patch.stopall()
            stdin.close()
            stdout.close()
            os.close(output_read)
        self.addCleanup(cleanup)
        lines = Lines(self, output_read)
        lines.receive(lambda value: value['type'] == 'snapshot')
        time.sleep(2.5)
        self.assertEqual(len(calls), 1)
        shutil.rmtree(self.store.calendar)
        time.sleep(.5)
        self.store.calendar.mkdir()
        time.sleep(.5)
        self.event()
        lines.receive(lambda value: value['type'] == 'snapshot' and len(value['events']) == 1)
        self.assertTrue(thread.is_alive())

    def test_protocol_survives_damaged_metadata_and_bad_fields(self):
        root = Path(self.temp.name)
        (self.store.notes / 'a.md').write_text('A\n')
        self.store.sidecar.write_text('{"a": {"order": null, "pinned": "yes"}, "b": {"order": "x"}}')
        env = dict(os.environ, HOME=str(root), XDG_DATA_HOME=str(root / 'data'), XDG_STATE_HOME=str(root / 'state'))
        process = subprocess.Popen([sys.executable, str(BACKEND), 'serve'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        def cleanup():
            process.terminate()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        lines = Lines(self, process.stdout.fileno())
        note = lines.receive(lambda value: value['type'] == 'snapshot')['notes'][0]
        def send(request):
            process.stdin.write(json.dumps(request).encode() + b'\n')
            process.stdin.flush()
            return lines.receive(lambda value: value['type'] == 'result' and value['id'] == request['id'])
        self.assertFalse(send(dict(id=1, action='notes.addTask', noteId='a', revision=note['revision'], text=5))['ok'])
        self.assertFalse(send(dict(id=2, action='calendar.save', title=7, date='2026-10-19'))['ok'])
        self.store.sidecar.write_text('[1]')
        self.assertTrue(lines.receive(lambda value: value['type'] == 'snapshot' and value['errors'])['notes'])
        self.assertTrue(send(dict(id=3, action='notes.create', body='Still serving'))['ok'])
        self.assertIsNone(process.poll())

    def test_protocol_watches_both_stores_and_returns_errors(self):
        root = Path(self.temp.name)
        env = dict(os.environ, HOME=str(root), XDG_DATA_HOME=str(root / 'data'), XDG_STATE_HOME=str(root / 'state'),
                   XDG_RUNTIME_DIR=str(root / 'runtime'))
        process = subprocess.Popen([sys.executable, str(BACKEND), 'serve'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        def cleanup():
            process.terminate()
            process.communicate(timeout=5)
        self.addCleanup(cleanup)
        pending = b''
        def receive(predicate):
            nonlocal pending
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    value = json.loads(line)
                    if predicate(value):
                        return value
                    continue
                if select.select([process.stdout], [], [], .2)[0]:
                    pending += os.read(process.stdout.fileno(), 65536)
            self.fail('No expected backend response')
        receive(lambda value: value['type'] == 'snapshot')
        backend.atomic_write(self.store.notes / 'external.md', b'Editor change')
        receive(lambda value: value['type'] == 'snapshot' and len(value['notes']) == 1)
        self.event()
        receive(lambda value: value['type'] == 'snapshot' and len(value['events']) == 1)
        process.stdin.write(b'{"id":9,"action":"notes.create","body":"From app"}\n')
        process.stdin.flush()
        receive(lambda value: value['type'] == 'snapshot' and len(value['notes']) == 2)
        result = receive(lambda value: value['type'] == 'result' and value['id'] == 9)
        self.assertTrue(result['ok'])
        process.stdin.write(b'not json\n')
        process.stdin.flush()
        self.assertFalse(receive(lambda value: value['type'] == 'result')['ok'])


if __name__ == '__main__':
    unittest.main()
