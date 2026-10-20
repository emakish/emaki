#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared local notes/calendar storage and persistent reminder delivery."""
import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid

MAX_FILE = 1 << 20
REMINDER_GRACE = 15 * 60
JOURNAL_AGE = 60 * 86400
WATCH_RETRY = 10
IN_MASK = 0x00000FCC  # close-write, moves, create/delete, directory replacement, attribute changes
IN_DELETE_SELF, IN_MOVE_SELF, IN_IGNORED, IN_ONLYDIR = 0x400, 0x800, 0x8000, 0x01000000


class Conflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(value).hexdigest()


def atomic_write(path, data):
    """Replace a complete file; readers never see half a document."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.emaki-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_file(path):
    """Read a regular file of at most MAX_FILE bytes; never follow a symlink or block on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Not a regular file.')
        raw = stream.read(MAX_FILE + 1) if info.st_size <= MAX_FILE else b''
    if info.st_size > MAX_FILE or len(raw) > MAX_FILE:
        raise ValueError('Larger than 1 MiB; open it in a text editor.')
    return raw, info.st_mtime


def escape(text):
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def unescape(text):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m[1].lower() == 'n' else m[1], text)


def fold(line):
    # RFC 5545 counts octets and permits a continuation space.
    result, part, size = [], '', 0
    for char in line:
        width = len(char.encode('utf-8'))
        if size + width > 75:
            result.append(part)
            part, size = ' ', 1
        part += char
        size += width
    return '\r\n'.join(result + [part])


def event_title(value):
    if not isinstance(value, str):
        raise ValueError('Enter an event title on one line.')
    # Pasted Unicode line separators become spaces; CR/LF and other controls are refused.
    title = re.sub(r'[\t\x0b\x0c\x1c-\x1e\x85  ]', ' ', value).strip()
    if not title or any(unicodedata.category(char) == 'Cc' for char in title):
        raise ValueError('Enter an event title on one line.')
    return title


def event_bytes(event):
    date = dt.date.fromisoformat(event['date'])
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Emaki//Calendar//EN',
             'CALSCALE:GREGORIAN', 'BEGIN:VEVENT', 'UID:' + event['id'] + '@emaki',
             'DTSTAMP:' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
             'SUMMARY:' + escape(event['title'])]
    if event['duration'] == 'all-day':
        lines += ['DTSTART;VALUE=DATE:' + date.strftime('%Y%m%d'),
                  'DTEND;VALUE=DATE:' + (date + dt.timedelta(days=1)).strftime('%Y%m%d')]
    else:
        start = dt.datetime.fromisoformat(event['date'] + 'T' + event['start'])
        lines += ['DTSTART:' + start.strftime('%Y%m%dT%H%M%S'),
                  'DTEND:' + (start + dt.timedelta(minutes=event['duration'])).strftime('%Y%m%dT%H%M%S')]
    if event['reminder'] is not None:
        lines += ['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + escape(event['title']),
                  'TRIGGER:-PT' + str(event['reminder']) + 'M', 'END:VALARM']
    lines += ['END:VEVENT', 'END:VCALENDAR']
    return ('\r\n'.join(fold(line) for line in lines) + '\r\n').encode('utf-8')


def parse_event(raw, identity):
    text = re.sub(r'\r?\n[ \t]', '', raw.decode('utf-8'))
    props = {}
    # Only CRLF/LF end content lines; str.splitlines() also splits on U+2028, \x0b and others.
    for line in re.split(r'\r?\n', text):
        if ':' in line:
            key, value = line.split(':', 1)
            props[key] = value
    if 'DTSTART;VALUE=DATE' in props:
        date = dt.datetime.strptime(props['DTSTART;VALUE=DATE'], '%Y%m%d').date()
        start, duration = '00:00', 'all-day'
    else:
        begin = dt.datetime.strptime(props['DTSTART'], '%Y%m%dT%H%M%S')
        end = dt.datetime.strptime(props['DTEND'], '%Y%m%dT%H%M%S')
        date, start, duration = begin.date(), begin.strftime('%H:%M'), int((end - begin).total_seconds() / 60)
    trigger = props.get('TRIGGER')
    reminder = None
    if trigger is not None:
        match = re.fullmatch(r'-PT(\d+)M', trigger)
        if not match:
            raise ValueError('Unsupported reminder trigger.')
        reminder = int(match[1])
    return dict(id=identity, title=unescape(props.get('SUMMARY', '')), date=date.isoformat(),
                start=start, duration=duration, reminder=reminder, revision=digest(raw))


def read_event(path):
    return parse_event(read_file(path)[0], path.stem)


def reminder_window(event):
    """Unix times when a reminder is due and when it stops being relevant."""
    start = dt.datetime.fromisoformat(event['date'] + 'T' + event['start'])
    if event['duration'] == 'all-day':
        end = dt.datetime.fromisoformat(event['date']) + dt.timedelta(days=1)
    else:
        end = start + dt.timedelta(minutes=event['duration'])
    due = start.timestamp() - event['reminder'] * 60
    return due, max(due + REMINDER_GRACE, end.timestamp())


def reminder_key(event):
    # Wall-clock values: a time-zone change does not make delivered reminders new again.
    return '{id}@{date}T{start}-{reminder}m'.format(**event)


class Store:
    def __init__(self, home=None, data=None, state=None):
        home = Path(home or Path.home())
        self.notes = home / 'Notes'
        self.calendar = Path(data or os.environ.get('XDG_DATA_HOME', home / '.local/share')) / 'emaki/calendar'
        self.state = Path(state or os.environ.get('XDG_STATE_HOME', home / '.local/state')) / 'emaki/notes'
        for directory in (self.notes, self.calendar, self.state):
            # A dangling ~/Notes symlink must not stop listing; writes report it.
            with contextlib.suppress(OSError):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.sidecar = self.notes / '.emaki-notes.json'

    @contextlib.contextmanager
    def lock(self, name='store.lock'):
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.state / name).open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def sidecar_value(self):
        try:
            value = json.loads(self.sidecar.read_bytes())
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise ValueError('The notes metadata is not an object.')
        return value

    def metadata(self, errors=None):
        """Pin state per note; a damaged file or entry is ignored, never fatal."""
        try:
            value = self.sidecar_value()
        except (OSError, ValueError) as exc:
            if errors is not None:
                errors.append(self.sidecar.name + ': ' + str(exc))
            return {}
        result = {}
        for identity, info in value.items():
            if isinstance(info, dict):
                order = info.get('order', 0)
                if isinstance(order, bool) or not isinstance(order, (int, float)) or order != order:
                    order = 0
                result[identity] = dict(pinned=info.get('pinned') is True, order=order)
        return result

    def note_path(self, identity):
        if not isinstance(identity, str) or not identity or Path(identity).name != identity or identity in ('.', '..'):
            raise ValueError('Invalid note identifier.')
        path = self.notes / (identity + '.md')
        if path.is_symlink():
            raise ValueError('Symbolic links cannot be edited as notes.')
        return path

    def note(self, path, metadata):
        raw, updated = read_file(path)
        body = raw.decode('utf-8')
        # The revision covers the text only: pinning elsewhere never invalidates a draft.
        first = body.split('\n')[0]
        return dict(id=path.stem, title=first.lstrip('# ').strip() or 'Untitled note', body=body,
                    revision=digest(raw), pinned=metadata.get(path.stem, {}).get('pinned', False), updated=updated)

    def events(self):
        events, errors = [], []
        for path in sorted(self.calendar.glob('*.ics')):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                events.append(read_event(path))
            except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
                errors.append(path.name + ': ' + str(exc))
        events.sort(key=lambda e: (e['date'], e['start'], e['id']))
        return events, errors

    def snapshot(self):
        notes, errors = [], []
        if self.notes.is_symlink() and not self.notes.is_dir():
            errors.append(self.notes.name + ': the folder is a broken symbolic link.')
        metadata = self.metadata(errors)
        for path in sorted(self.notes.glob('*.md')):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                notes.append(self.note(path, metadata))
            except (OSError, ValueError) as exc:
                errors.append(path.name + ': ' + str(exc))
        notes.sort(key=lambda n: (not n['pinned'], metadata.get(n['id'], {}).get('order', 0), -n['updated'], n['id']))
        events, problems = self.events()
        return dict(type='snapshot', notes=notes, events=events, errors=errors + problems)

    def require_revision(self, current, request):
        if not request.get('revision') or current['revision'] != request['revision']:
            raise Conflict('This item changed elsewhere. Your edit has been kept; reload or save it as a new note.')

    def request(self, request):
        try:
            with self.lock():
                result = self.mutate(request)
            return dict(type='result', id=request.get('id'), ok=True, **result)
        except Exception as exc:  # one bad request never stops the service
            return dict(type='result', id=request.get('id'), ok=False, error=str(exc) or type(exc).__name__,
                        conflict=isinstance(exc, Conflict))

    def write_body(self, path, body):
        if not isinstance(body, str):
            raise ValueError('Note text must be text.')
        data = body.encode('utf-8')
        if len(data) > MAX_FILE:
            raise ValueError('A note cannot be larger than 1 MiB.')
        atomic_write(path, data)

    def mutate(self, request):
        action = request['action']
        if not isinstance(action, str):
            raise ValueError('Action must be text.')
        if action.startswith('notes.'):
            if action == 'notes.create':
                identity = uuid.uuid4().hex
                path = self.note_path(identity)
                self.notes.mkdir(mode=0o700, parents=True, exist_ok=True)
                self.write_body(path, request.get('body', ''))
                return dict(noteId=identity, revision=self.note(path, {})['revision'])
            path = self.note_path(request['noteId'])
            try:
                current = self.note(path, {})
            except FileNotFoundError:
                raise Conflict('This note was removed elsewhere. Save your edit as a new note.') from None
            self.require_revision(current, request)
            if action == 'notes.delete':
                path.unlink()
                # A damaged sidecar is left for the person to restore; the note is gone either way.
                with contextlib.suppress(OSError, ValueError):
                    metadata = self.sidecar_value()
                    if metadata.pop(current['id'], None) is not None:
                        atomic_write(self.sidecar, json.dumps(metadata).encode())
                return dict(noteId=current['id'])
            if action == 'notes.pin':
                try:
                    metadata = self.sidecar_value()
                except ValueError:
                    raise ValueError('The notes metadata is invalid; restore it before pinning.') from None
                info = metadata.get(current['id'])
                if not isinstance(info, dict):
                    info = metadata[current['id']] = {}
                order = info.get('order')
                if isinstance(order, bool) or not isinstance(order, (int, float)) or order != order:
                    order = time.time_ns()
                info.update(pinned=bool(request['pinned']), order=order)
                atomic_write(self.sidecar, json.dumps(metadata).encode())
            else:
                body = current['body']
                if action == 'notes.save':
                    body = request['body']
                elif action == 'notes.toggle':
                    # Lines are split on LF only, as the shell numbers them.
                    lines = body.split('\n')
                    line = request['line']
                    if isinstance(line, bool) or not isinstance(line, int) or line < 0 or line >= len(lines):
                        raise ValueError('Task line is missing.')
                    match = re.match(r'^(\s*[-*+] \[)([ xX])(\])', lines[line])
                    if not match:
                        raise ValueError('This line is not a task.')
                    lines[line] = lines[line][:match.start(2)] + ('x' if match[2] == ' ' else ' ') + lines[line][match.end(2):]
                    body = '\n'.join(lines)
                elif action == 'notes.addTask':
                    task = request['text']
                    if not isinstance(task, str) or not task.strip() or re.search(r'[\r\n\x0b\x0c\x1c-\x1e\x85  ]', task):
                        raise ValueError('Enter one task line.')
                    body += ('' if not body or body.endswith('\n') else '\n') + '- [ ] ' + task.strip() + '\n'
                else:
                    raise ValueError('Unknown notes action.')
                if not isinstance(body, str):
                    raise ValueError('Note text must be text.')
                # Recheck after preparing the edit, including changes by external editors.
                self.require_revision(self.note(path, {}), request)
                self.write_body(path, body)
            return dict(noteId=current['id'], revision=self.note(path, {})['revision'])
        if action not in ('calendar.save', 'calendar.delete'):
            raise ValueError('Unknown action.')
        identity = request.get('eventId') or uuid.uuid4().hex
        if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', identity):
            raise ValueError('Invalid event identifier.')
        path = self.calendar / (identity + '.ics')
        if path.is_symlink():
            raise ValueError('Symbolic links cannot be edited as events.')
        if request.get('eventId'):
            if not path.exists():
                raise Conflict('This event was removed elsewhere.')
            self.require_revision(read_event(path), request)
        if action == 'calendar.delete':
            if not request.get('eventId'):
                raise ValueError('An event identifier is required.')
            path.unlink()
            return dict(eventId=identity)
        title = event_title(request['title'])
        if not isinstance(request['date'], str):
            raise ValueError('Enter a date as YYYY-MM-DD.')
        date = dt.date.fromisoformat(request['date']).isoformat()
        duration = request.get('duration', 60)
        if duration != 'all-day':
            if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0 or duration > 525600:
                raise ValueError('Duration must be a positive number of minutes.')
        start = request.get('start') or '00:00'
        if not isinstance(start, str) or not re.fullmatch(r'\d{2}:\d{2}', start):
            raise ValueError('Enter a start time as HH:MM.')
        dt.time.fromisoformat(start)
        if duration == 'all-day':
            start = '00:00'
        reminder = request.get('reminder')
        if reminder is not None and (isinstance(reminder, bool) or not isinstance(reminder, int) or reminder < 0 or reminder > 525600):
            raise ValueError('Reminder must be minutes before the event.')
        event = dict(id=identity, title=title, date=date, start=start, duration=duration, reminder=reminder)
        # Validate the exact bytes before they replace anything.
        try:
            data = event_bytes(event)
            stored = parse_event(data, identity)
        except (ValueError, KeyError, OverflowError):
            stored = None
        if stored is None or any(stored[key] != event[key] for key in ('title', 'date', 'start', 'duration', 'reminder')):
            raise ValueError('This event cannot be stored; check its date and title.')
        self.calendar.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(path, data)
        return dict(eventId=identity, revision=read_event(path)['revision'])

    def journal(self):
        path = self.state / 'delivered.json'
        try:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError('not an object')
        except FileNotFoundError:
            return {}
        except ValueError:
            # Keep the damaged journal for inspection; the relevance window bounds what refires.
            with contextlib.suppress(OSError):
                os.replace(path, path.with_name('delivered.json.bad'))
            return {}
        return {key: when for key, when in value.items()
                if isinstance(when, (int, float)) and not isinstance(when, bool)}

    def remind(self, now=None, notify=None):
        now = time.time() if now is None else now
        notify = notify or desktop_notify
        delivered_path = self.state / 'delivered.json'
        count = 0
        # Held for the whole run, so two runs never send one reminder twice; the store lock
        # is held only while reading, so notifications never block saves.
        with self.lock('remind.lock'):
            with self.lock():
                events = self.events()[0]
            journal = self.journal()
            pending, live, changed = [], set(), False
            for event in events:
                if event['reminder'] is None:
                    continue
                try:
                    due, until = reminder_window(event)
                except (ValueError, OverflowError, OSError):
                    continue
                key = reminder_key(event)
                live.add(key)
                # The second key is the format before 0.10 (event ID and due Unix time).
                if due > now or key in journal:
                    continue
                if event['id'] + ':' + str(due) in journal:
                    # Carry it to the new key, which outlives the old one's pruning.
                    journal[key] = journal[event['id'] + ':' + str(due)]
                    changed = True
                    continue
                if now <= until:
                    pending.append((key, event))
                else:
                    # Missed while signed out: never fire it late.
                    journal[key] = now
                    changed = True
            for key in [key for key, when in journal.items() if when < now - JOURNAL_AGE and key not in live]:
                del journal[key]
                changed = True
            if changed:
                atomic_write(delivered_path, json.dumps(journal, sort_keys=True).encode())
            for key, event in pending:
                # Record only successful delivery; an unavailable notification server retries.
                if notify(event):
                    journal[key] = now
                    atomic_write(delivered_path, json.dumps(journal, sort_keys=True).encode())
                    count += 1
        return count


def desktop_notify(event):
    try:
        result = subprocess.run(['notify-send', '--app-name=Emaki Calendar', '--icon=emaki-notes',
                                 '--hint=string:desktop-entry:emaki-notes',
                                 '--', event['title'], event['date'] + ' · ' +
                                 ('All day' if event['duration'] == 'all-day' else event['start'])],
                                timeout=15, check=False, capture_output=True)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Watcher:
    """Watch directories, so atomic editor saves are visible too."""
    def __init__(self, paths):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), 'Cannot watch notes')
        self.paths = [Path(path) for path in paths]
        self.watches = {}
        self.refresh()

    @property
    def complete(self):
        return len(self.watches) == len(self.paths)

    @property
    def blind(self):
        """A directory exists but could not be watched (watch limit, permissions): rescan it."""
        return any(path not in self.watches and path.is_dir() for path in self.paths)

    def refresh(self):
        """Watch each directory that exists now; return whether a watch was added."""
        added = False
        for path in self.paths:
            if path not in self.watches:
                wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), IN_MASK | IN_ONLYDIR)
                if wd >= 0:
                    self.watches[path] = wd
                    added = True
        return added

    def drain(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            if not data:
                break
            offset = 0
            while offset + 16 <= len(data):
                wd, mask, _, size = struct.unpack_from('iIII', data, offset)
                offset += 16 + size
                # A deleted, moved or replaced directory is watched again at its path.
                if mask & (IN_DELETE_SELF | IN_MOVE_SELF | IN_IGNORED):
                    for path in [path for path, known in self.watches.items() if known == wd]:
                        del self.watches[path]
                    if not mask & IN_IGNORED:
                        self.libc.inotify_rm_watch(self.fd, wd)
        self.refresh()

    def close(self):
        os.close(self.fd)


def serve(store):
    def emit(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)
    last = None
    def publish(always=False):
        nonlocal last
        try:
            snapshot = store.snapshot()
        except Exception as exc:  # report and keep serving
            emit(dict(type='error', error=str(exc) or type(exc).__name__))
            return
        if always or snapshot != last:
            emit(snapshot)
            last = snapshot
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.fileno(), selectors.EVENT_READ, 'input')
    watch = Watcher((store.notes, store.calendar))
    selector.register(watch.fd, selectors.EVENT_READ, 'watch')
    buffer = b''
    try:
        publish()
        while True:
            # Block on inotify; retry slowly only while a store directory is missing.
            ready = selector.select(None if watch.complete else WATCH_RETRY)
            changed = not ready and (watch.refresh() or watch.blind)
            for key, _ in ready:
                if key.data == 'watch':
                    watch.drain()
                    changed = True
                    continue
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    return
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        request = json.loads(line)
                        if not isinstance(request, dict):
                            raise ValueError('A request must be an object.')
                    except ValueError as exc:
                        emit(dict(type='result', id=None, ok=False, error=str(exc)))
                        continue
                    result = store.request(request)
                    publish(always=True)
                    emit(result)
            if changed:
                publish()
    finally:
        watch.close()
        selector.close()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('serve', 'remind', 'snapshot'):
        raise SystemExit('Usage: backend.py {serve|remind|snapshot}')
    store = Store()
    if sys.argv[1] == 'serve':
        serve(store)
    elif sys.argv[1] == 'remind':
        print(json.dumps(dict(delivered=store.remind())))
    else:
        print(json.dumps(store.snapshot()))


if __name__ == '__main__':
    main()
