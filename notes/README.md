<!-- Copyright (C) 2026 Artur Yakymenko -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->

# Notes and calendar storage

`backend.py serve` serves newline-delimited JSON over standard input/output. Each window
and the desktop shell run a client process. `snapshot` prints one snapshot; `remind` checks
persisted calendar reminders without starting a window or the shell.

Notes are UTF-8 Markdown files in `~/Notes/`; their filename stem is their identifier.
The first line supplies the title. Existing Markdown files appear automatically.
`~/Notes/.emaki-notes.json` stores each identifier's `pinned` boolean and `order` number
(the pin timestamp). Notes are sorted pinned first, pin order next, then most recently
updated. The body contains no private metadata. Symlinks are neither listed nor edited.
A damaged sidecar or entry is ignored for listing and reported in `errors`; pinning then
fails until it is restored. Files over 1 MiB are reported in `errors` and not sent.

Calendar events are individual RFC 5545 `.ics` files in
`$XDG_DATA_HOME/emaki/calendar/` (default `~/.local/share/emaki/calendar/`). Each contains
one VEVENT, with a VALARM when a reminder is set. Event times are floating local wall
clock times; all-day events start at local midnight. Files use CRLF, escaped text and
UTF-8-safe 75-octet folding; content lines end only at CRLF or LF. Titles turn tabs and
Unicode line separators into spaces and refuse other control characters; each file is
parsed back before it is written. This version reads the single-event format it writes;
general calendar import, recurrence and time-zone selection are not implemented.

## Protocol

The initial response and each storage change produce:

```json
{"type":"snapshot","notes":[{"id":"filename-stem","title":"Title","body":"Title\n- [ ] Task\n","revision":"opaque-hash","pinned":false,"updated":1792411200}],"events":[{"id":"event-id","title":"Meeting","date":"2026-10-19","start":"12:30","duration":60,"reminder":10,"revision":"opaque-hash"}],"errors":[]}
```

`updated` is Unix time in seconds. `duration` is integer minutes or `"all-day"`;
`reminder` is integer minutes before the start or `null`. Note task line indices are
zero-based. Requests carry a caller-chosen `id` and one of these actions:

| Action | Fields |
| --- | --- |
| `notes.create` | `body` |
| `notes.save` | `noteId`, `body`, `revision` |
| `notes.delete` | `noteId`, `revision` |
| `notes.pin` | `noteId`, `pinned`, `revision` |
| `notes.toggle` | `noteId`, `line`, `revision` |
| `notes.addTask` | `noteId`, `text`, `revision` |
| `calendar.save` | optional `eventId`, `title`, `date`, `start`, `duration`, `reminder`; `revision` when editing |
| `calendar.delete` | `eventId`, `revision` |

Responses are `{"type":"result","id":1,"ok":true,"noteId":"…","revision":"…"}`
(or `eventId`). Delete responses omit revision. Failures have `ok:false`, an English
`error`, and `conflict:true` for concurrent changes. A fresh snapshot precedes each request response, including conflicts.
Clients must keep unsaved text on conflict and offer reload or saving a new note;
a newer snapshot must not silently replace a dirty editor's text or its base revision.

Writers share an advisory lock under `$XDG_STATE_HOME/emaki/notes/` (default
`~/.local/state/emaki/notes/`). Revisions hash file contents only, so pinning in one
client never makes another client's unsaved draft conflict. A stale save, toggle, task,
pin or delete fails without changing the stored item. File replacement is atomic and
durable. The service sleeps on directory inotify watches, which catch in-place editor
saves, additions, deletion and atomic renames; it reads the stores only after a change
or a request. A deleted, moved or replaced store directory is watched again at its path;
while one is missing the service checks for it every 10 seconds. Any failure while
reading or handling a request is reported and the service keeps running. External editors do not participate in
the advisory lock: their completed edits are detected by revision checks, but an
external write in the final check/replace interval cannot be serialized.

## Reminders

The user timer runs at each minute boundary (all editor times have minute precision),
with one-second accuracy and `Persistent=true`. Package installation enables the timer
through `graphical-session.target.wants`; nothing starts a service from the app.
`libnotify` supplies `notify-send`, which sends normal desktop notifications. A missing
notification server or failed notification retries on the next timer run.

Successful delivery is recorded atomically in `$XDG_STATE_HOME/emaki/notes/delivered.json`.
The key combines event ID with the wall-clock date, start and reminder offset: reopening
the shell, signing back in, changing a title or the time zone does not send it again;
changing its schedule permits a new reminder. Keys of the earlier ID-and-due-time form
are still honoured. A reminder fires only while relevant: from its due time until 15
minutes later or the event's end, whichever is later (an all-day event ends at the next
local midnight). One missed for longer, such as while signed out or for an event entered
in the past, is recorded as handled and never fires. Entries older than 60 days are
pruned; a damaged journal is moved to `delivered.json.bad` and rebuilt. Reminders read
the calendar files only. A separate `remind.lock` is held for the whole run, so
concurrent timer calls never duplicate delivery, while the store lock is held only to
read events, so notifications never block saves. There is an unavoidable crash window between notification
server acceptance and journal persistence; the desktop notification protocol offers no
transaction joining those operations.

Run `python3 notes/tests/test_backend.py`. Tests use temporary home/data/state directories,
a fake notifier and a subprocess protocol client; they do not contact the live desktop.
