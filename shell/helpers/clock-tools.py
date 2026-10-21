#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent clock tools. The shell owns notification delivery and sound."""
import argparse
import copy
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import stat
import sys
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo, available_timezones

# The packaged helper has a sibling translation module; the source tree uses scripts.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from emaki_i18n import translation
_ = translation('shell').gettext

# Exceptions carry stable codes; only the protocol's display message is translated.
ERRORS = {
    'invalid_duration': _('Invalid duration'),
    'expected_a_whole_number': _('Expected a whole number'),
    'expected_true_or_false': _('Expected true or false'),
    'clock_state_directory_must_not_be_writable_by_others': _('Clock state directory must not be writable by others'),
    'unsafe_clock_state_file': _('Unsafe clock state file'),
    'clock_state_is_too_large': _('Clock state is too large'),
    'invalid_repeat_days': _('Invalid repeat days'),
    'invalid_alarm_label': _('Invalid alarm label'),
    'unsupported_clock_state_version': _('Unsupported clock state version'),
    'invalid_timer_state': _('Invalid timer state'),
    'invalid_laps': _('Invalid laps'),
    'too_many_clock_entries': _('Too many clock entries'),
    'invalid_alarm_identifier': _('Invalid alarm identifier'),
    'invalid_notice': _('Invalid notice'),
    'invalid_clock_state': _('Invalid clock state'),
    'expected_a_clock_command': _('Expected a clock command'),
    'the_stopwatch_has_100_laps_reset_to_record_more': _('The stopwatch has 100 laps; reset to record more'),
    'the_alarm_limit_is_64': _('The alarm limit is 64'),
    'alarm_was_not_found': _('Alarm was not found'),
    'invalid_time_zone_search': _('Invalid time zone search'),
    'unknown_time_zone': _('Unknown time zone'),
    'the_world_clock_limit_is_24': _('The world clock limit is 24'),
    'unknown_clock_command': _('Unknown clock command'),
    'unsafe_clock_state_lock': _('Unsafe clock state lock'),
    'clock_command_is_too_large': _('Clock command is too large'),
}

MAX_SECONDS = 7 * 86400
MAX_STATE = 262144
MAX_NOTICES = 64


class InvalidState(ValueError):
    """Saved contents cannot be read; preserve them until recovery is requested."""


def plain_error(error):
    if isinstance(error, BlockingIOError):
        return _("Clock tools are already running in another session.")
    if isinstance(error, InvalidState):
        return _("Your saved clocks could not be read. Start over to keep a backup and reset them.")
    if isinstance(error, OSError):
        return _("Clock tools could not read or save their settings. Check access and available space.")
    if isinstance(error, ValueError) and str(error) in ERRORS:
        return ERRORS[str(error)]
    return _("Clock tools could not read that data. Please try again.")


def number(value, low=0, high=MAX_SECONDS):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high or not math.isfinite(value):
        raise ValueError("invalid_duration")
    return value


def integer(value, low, high):
    number(value, low, high)
    if not isinstance(value, int):
        raise ValueError("expected_a_whole_number")
    return value


def boolean(value):
    if not isinstance(value, bool):
        raise ValueError("expected_true_or_false")
    return value


def identifier():
    return uuid.uuid4().hex


def defaults():
    return {"version": 1, "timer": {"status": "idle", "duration": 300,
            "remaining": 300, "deadline": None, "pomodoro": False,
            "phase": "work", "cycle": 1},
            "stopwatch": {"running": False, "elapsed": 0, "started": None, "laps": []},
            "alarms": [], "world": [], "notices": []}


class StateStore:
    def __init__(self, path):
        self.path = Path(path)

    def directory(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("clock_state_directory_must_not_be_writable_by_others")

    def load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return defaults()
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("unsafe_clock_state_file")
            raw = handle.read(MAX_STATE + 1)
        try:
            if len(raw) > MAX_STATE:
                raise ValueError("clock_state_is_too_large")
            state = json.loads(raw)
            validate_state(state)
            for notice in state["notices"]:
                notice.setdefault("created", info.st_mtime)
            return state
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise InvalidState() from error

    def recover(self):
        self.directory()
        try:
            self.load()
        except InvalidState:
            backup = self.path.with_name(self.path.name + ".broken-" + identifier())
            os.replace(self.path, backup)
            self.save(defaults())

    def save(self, state):
        self.directory()
        state["notices"] = state["notices"][-MAX_NOTICES:]
        validate_state(state)
        raw = json.dumps(state, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_STATE:
            raise ValueError("clock_state_is_too_large")
        fd, temporary = tempfile.mkstemp(prefix=".clock-tools-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def validate_alarm(alarm):
    integer(alarm["hour"], 0, 23)
    integer(alarm["minute"], 0, 59)
    if not isinstance(alarm["days"], list) or len(alarm["days"]) > 7:
        raise ValueError("invalid_repeat_days")
    for day in alarm["days"]:
        integer(day, 0, 6)
    if not isinstance(alarm["label"], str) or len(alarm["label"]) > 120 or any(ord(c) < 32 for c in alarm["label"]):
        raise ValueError("invalid_alarm_label")
    boolean(alarm["enabled"])


def validate_state(state):
    try:
        if state["version"] != 1:
            raise ValueError("unsupported_clock_state_version")
        timer = state["timer"]
        if timer["status"] not in ("idle", "running", "paused", "finished") or timer["phase"] not in ("work", "break"):
            raise ValueError("invalid_timer_state")
        number(timer["duration"], 1)
        number(timer["remaining"])
        boolean(timer["pomodoro"])
        integer(timer["cycle"], 1, 100000000)
        if timer["status"] == "running":
            number(timer["deadline"], 0, 1e12)
        watch = state["stopwatch"]
        boolean(watch["running"])
        number(watch["elapsed"], 0, 1e12)
        if watch["running"]:
            number(watch["started"], 0, 1e12)
        if not isinstance(watch["laps"], list) or len(watch["laps"]) > 100:
            raise ValueError("invalid_laps")
        for lap in watch["laps"]:
            number(lap, 0, 1e12)
        for field, limit in (("alarms", 64), ("world", 24), ("notices", 128)):
            if not isinstance(state[field], list) or len(state[field]) > limit:
                raise ValueError("too_many_clock_entries")
        for alarm in state["alarms"]:
            validate_alarm(alarm)
            if not isinstance(alarm["id"], str) or len(alarm["id"]) > 64:
                raise ValueError("invalid_alarm_identifier")
            if alarm.get("snooze") is not None:
                number(alarm["snooze"], 0, 1e12)
        for zone in state["world"]:
            ZoneInfo(zone)
        for notice in state["notices"]:
            if not isinstance(notice["id"], str) or notice["kind"] not in ("timer", "pomodoro", "alarm"):
                raise ValueError("invalid_notice")
    except (KeyError, TypeError) as error:
        raise ValueError("invalid_clock_state") from error


class ClockTools:
    def __init__(self, path, clock=time.time, zone=None):
        self.store = StateStore(path)
        self.state = self.store.load()
        self.clock = clock
        self.zone = zone
        self.search = []
        self.zone_minute = None
        self.zones = sorted(available_timezones())

    def local(self, now):
        return dt.datetime.fromtimestamp(now, self.zone).astimezone() if self.zone is None else dt.datetime.fromtimestamp(now, self.zone)

    def notice(self, kind, event_time=None, **fields):
        now = self.clock()
        self.state["notices"].append({"id": identifier(), "kind": kind,
                                      "created": now if event_time is None else event_time,
                                      "issued": now, **fields})
        self.state["notices"] = self.state["notices"][-MAX_NOTICES:]

    def clear_timer_notices(self):
        self.state["notices"] = [n for n in self.state["notices"] if n["kind"] == "alarm"]

    def tick(self):
        now = self.clock()
        minute = int(now // 60)
        if minute != self.zone_minute:
            time.tzset()
            self.zone_minute = minute
        before = copy.deepcopy(self.state)
        timer = self.state["timer"]
        if timer["status"] == "running" and now >= timer["deadline"]:
            if timer["pomodoro"]:
                # Skip full elapsed cycles arithmetically, with one current notice.
                cycles = int((now - timer["deadline"]) // 1800)
                timer["cycle"] += cycles
                timer["deadline"] += cycles * 1800
                while now >= timer["deadline"]:
                    if timer["phase"] == "work":
                        timer["phase"] = "break"
                        timer["duration"] = 300
                    else:
                        timer["phase"] = "work"
                        timer["duration"] = 1500
                        timer["cycle"] += 1
                    timer["deadline"] += timer["duration"]
                self.clear_timer_notices()
                self.notice("pomodoro", event_time=timer["deadline"] - timer["duration"],
                            phase=timer["phase"], cycle=timer["cycle"])
            else:
                deadline = timer["deadline"]
                timer.update(status="finished", remaining=0, deadline=None)
                self.notice("timer", event_time=deadline)
        local = self.local(now)
        date_key = local.date().isoformat()
        for alarm in self.state["alarms"]:
            snooze = alarm.get("snooze")
            if snooze is not None and now >= snooze:
                alarm["snooze"] = None
                if now - snooze < 60:
                    self.notice("alarm", event_time=snooze, alarmId=alarm["id"], label=alarm["label"])
            if (alarm["enabled"] and alarm.get("lastDate") != date_key
                    and local.hour == alarm["hour"] and local.minute == alarm["minute"]
                    and (not alarm["days"] or local.weekday() in alarm["days"])):
                alarm["lastDate"] = date_key
                if not alarm["days"]:
                    alarm["enabled"] = False
                self.notice("alarm", alarmId=alarm["id"], label=alarm["label"])
        if self.state != before:
            try:
                self.store.save(self.state)
            except Exception:
                self.state = before
                raise

    def next_wake(self):
        now = self.clock()
        waits = [5, 60 - now % 60]
        timer = self.state["timer"]
        if timer["status"] == "running":
            remaining = max(0, timer["deadline"] - now)
            waits.append(remaining - max(0, math.ceil(remaining) - 1))
        watch = self.state["stopwatch"]
        if watch["running"]:
            elapsed = watch["elapsed"] + max(0, now - watch["started"])
            waits.append(1 - elapsed % 1)
        waits.extend(max(0, alarm["snooze"] - now) for alarm in self.state["alarms"]
                     if alarm.get("snooze") is not None)
        return max(0.01, min(waits))

    def snapshot(self):
        now = self.clock()
        timer = copy.deepcopy(self.state["timer"])
        if timer["status"] == "running":
            timer["remaining"] = math.ceil(max(0, timer["deadline"] - now))
        watch = copy.deepcopy(self.state["stopwatch"])
        if watch["running"]:
            watch["elapsed"] = math.floor(watch["elapsed"] + max(0, now - watch["started"]))
        local_offset = self.local(now).utcoffset().total_seconds()
        world = []
        for zone in self.state["world"]:
            current = dt.datetime.fromtimestamp(now, ZoneInfo(zone))
            delta = int((current.utcoffset().total_seconds() - local_offset) / 60)
            world.append({"zone": zone, "time": current.strftime("%H:%M"),
                          "date": current.date().isoformat(),
                          "offset": f"{'+' if delta >= 0 else '-'}{abs(delta) // 60:02}:{abs(delta) % 60:02}"})
        return {"ready": True, "timer": timer, "stopwatch": watch,
                "alarms": copy.deepcopy(self.state["alarms"]), "world": world,
                "search": self.search, "notices": copy.deepcopy(self.state["notices"]), "error": ""}

    def command(self, command):
        self.tick()
        before = copy.deepcopy(self.state)
        try:
            self.apply(command)
            validate_state(self.state)
            if before != self.state:
                self.store.save(self.state)
        except Exception:
            self.state = before
            raise
        return self.snapshot()

    def apply(self, command):
        if not isinstance(command, dict):
            raise ValueError("expected_a_clock_command")
        op = command.get("op")
        now = self.clock()
        timer = self.state["timer"]
        watch = self.state["stopwatch"]
        if op in ("timer_start", "timer_restart"):
            pomodoro = boolean(command.get("pomodoro", timer["pomodoro"] if op == "timer_restart" else False))
            duration = 1500 if pomodoro else number(command.get("seconds", timer["duration"]), 1)
            timer.update(status="running", duration=duration, remaining=duration,
                         deadline=now + duration, pomodoro=pomodoro, phase="work", cycle=1)
            self.clear_timer_notices()
        elif op == "timer_pause":
            if timer["status"] == "running":
                timer.update(status="paused", remaining=max(0, timer["deadline"] - now), deadline=None)
        elif op == "timer_resume":
            if timer["status"] == "paused":
                timer.update(status="running", deadline=now + timer["remaining"])
        elif op == "timer_reset":
            if timer["pomodoro"]:
                timer.update(duration=1500, phase="work", cycle=1)
            timer.update(status="idle", remaining=timer["duration"], deadline=None)
            self.clear_timer_notices()
        elif op == "stopwatch_start":
            if not watch["running"]:
                watch.update(running=True, started=now)
        elif op == "stopwatch_stop":
            if watch["running"]:
                watch.update(elapsed=watch["elapsed"] + max(0, now - watch["started"]), running=False, started=None)
        elif op == "stopwatch_lap":
            if watch["running"]:
                if len(watch["laps"]) >= 100:
                    raise ValueError("the_stopwatch_has_100_laps_reset_to_record_more")
                watch["laps"].append(watch["elapsed"] + max(0, now - watch["started"]))
        elif op == "stopwatch_reset":
            watch.update(running=False, elapsed=0, started=None, laps=[])
        elif op == "alarm_add":
            if len(self.state["alarms"]) >= 64:
                raise ValueError("the_alarm_limit_is_64")
            alarm = {"id": identifier(), "hour": command.get("hour"), "minute": command.get("minute"),
                     "days": command.get("days", []), "label": command.get("label", ""),
                     "enabled": command.get("enabled", True), "lastDate": "", "snooze": None}
            validate_alarm(alarm)
            self.state["alarms"].append(alarm)
        elif op in ("alarm_update", "alarm_remove", "alarm_snooze", "alarm_stop"):
            alarm = next((a for a in self.state["alarms"] if a["id"] == command.get("id")), None)
            if alarm is None:
                raise ValueError("alarm_was_not_found")
            if op == "alarm_update":
                time_changed = any(key in command and command[key] != alarm[key]
                                   for key in ("hour", "minute"))
                switched_on = command.get("enabled") is True and not alarm["enabled"]
                for key in ("hour", "minute", "days", "label", "enabled"):
                    if key in command:
                        alarm[key] = command[key]
                if time_changed and not alarm["days"]:
                    alarm["enabled"] = True
                if time_changed or switched_on:
                    alarm["lastDate"] = ""
                    alarm["snooze"] = None
                validate_alarm(alarm)
                if not alarm["enabled"]:
                    alarm["snooze"] = None
            elif op == "alarm_remove":
                self.state["alarms"].remove(alarm)
            else:
                alarm["snooze"] = now + 300 if op == "alarm_snooze" else None
            if op != "alarm_update" or not alarm["enabled"]:
                self.state["notices"] = [n for n in self.state["notices"] if n.get("alarmId") != alarm["id"]]
        elif op == "world_search":
            query = command.get("query", "")
            if not isinstance(query, str) or len(query) > 100:
                raise ValueError("invalid_time_zone_search")
            query = query.strip().casefold().replace(" ", "_")
            self.search = [z for z in self.zones if query in z.casefold()][:40] if query else []
        elif op in ("world_add", "world_remove"):
            zone = command.get("zone")
            if zone not in self.zones:
                raise ValueError("unknown_time_zone")
            if op == "world_add" and zone not in self.state["world"]:
                if len(self.state["world"]) >= 24:
                    raise ValueError("the_world_clock_limit_is_24")
                self.state["world"].append(zone)
            elif op == "world_remove" and zone in self.state["world"]:
                self.state["world"].remove(zone)
        elif op == "notice_dismiss":
            self.state["notices"] = [n for n in self.state["notices"] if n["id"] != command.get("id")]
        elif op != "status":
            raise ValueError("unknown_clock_command")


def emit(snapshot):
    print(json.dumps(snapshot, separators=(",", ":"), allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["status"], default="status")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    path = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "emaki/clock-tools.json"
    lock = None
    try:
        if args.serve or args.recover:
            store = StateStore(path)
            store.directory()
            lock = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("unsafe_clock_state_lock")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.recover:
            store.recover()
        engine = ClockTools(path)
        if not args.serve:
            emit(engine.snapshot())
            return 0
        selector = selectors.DefaultSelector()
        selector.register(sys.stdin, selectors.EVENT_READ)
        buffer = b""
        last_snapshot = None
        command_error = ""

        def publish(snapshot):
            nonlocal last_snapshot
            snapshot["error"] = command_error
            serialized = json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
            if serialized != last_snapshot:
                print(serialized, flush=True)
                last_snapshot = serialized

        while True:
            engine.tick()
            publish(engine.snapshot())
            if not selector.select(engine.next_wake()):
                continue
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                return 0
            buffer += chunk
            if len(buffer) > 16384:
                raise ValueError("clock_command_is_too_large")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    snapshot = engine.command(json.loads(line))
                    command_error = ""
                except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
                    snapshot = engine.snapshot()
                    command_error = plain_error(error)
                publish(snapshot)
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        emit({"ready": False, "error": plain_error(error), "recoverable": isinstance(error, InvalidState)})
        return 1
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
