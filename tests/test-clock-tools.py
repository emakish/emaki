#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clock tools timing, civil-time transitions, protocol and private persistence."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from zoneinfo import ZoneInfo

HELPER = Path(__file__).resolve().parents[1] / "shell/helpers/clock-tools.py"
SPEC = importlib.util.spec_from_file_location("clock_tools", HELPER)
clock_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clock_tools)


class Clocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "emaki/clock-tools.json"
        self.now = dt.datetime(2026, 1, 5, 12, tzinfo=dt.timezone.utc).timestamp()
        self.engine = self.restore()

    def restore(self, zone="UTC"):
        return clock_tools.ClockTools(self.path, clock=lambda: self.now, zone=ZoneInfo(zone))

    def command(self, op, **values):
        return self.engine.command({"op": op, **values})

    def advance(self, seconds):
        self.now += seconds
        self.engine.tick()
        return self.engine.snapshot()

    def test_timer_pause_resume_restart_and_notice_persistence(self):
        self.command("timer_start", seconds=60)
        self.advance(20)
        self.command("timer_pause")
        self.advance(100)
        self.engine = self.restore()
        self.assertEqual(self.engine.snapshot()["timer"]["remaining"], 40)
        self.command("timer_resume")
        result = self.advance(41)
        self.assertEqual(result["timer"]["status"], "finished")
        self.assertEqual(len(result["notices"]), 1)
        self.engine = self.restore()
        self.advance(100)
        self.assertEqual(len(self.engine.snapshot()["notices"]), 1)
        self.command("timer_restart")
        self.assertEqual(self.engine.snapshot()["timer"]["remaining"], 60)
        self.assertEqual(self.engine.snapshot()["notices"], [])
        self.command("timer_reset")
        self.assertEqual(self.engine.snapshot()["timer"]["status"], "idle")

    def test_running_timer_survives_restart(self):
        self.command("timer_start", seconds=300)
        self.now += 30
        self.engine = self.restore()
        self.assertEqual(self.engine.snapshot()["timer"]["remaining"], 270)
        self.advance(300)
        self.assertEqual(self.engine.snapshot()["notices"][0]["kind"], "timer")

    def test_pomodoro_phases_and_long_absence(self):
        self.command("timer_start", pomodoro=True)
        state = self.advance(1500)
        self.assertEqual((state["timer"]["phase"], state["timer"]["cycle"]), ("break", 1))
        self.assertEqual(state["timer"]["remaining"], 300)
        state = self.advance(300)
        self.assertEqual((state["timer"]["phase"], state["timer"]["cycle"]), ("work", 2))
        self.now += 1800 * 10000 + 100
        self.engine = self.restore()
        state = self.advance(0)
        self.assertEqual(state["timer"]["cycle"], 10002)
        self.assertEqual(state["timer"]["remaining"], 1400)
        self.assertEqual(len(state["notices"]), 1)
        self.command("timer_reset")
        self.assertEqual(self.engine.snapshot()["timer"]["remaining"], 1500)

    def test_stopwatch_laps_and_restart(self):
        self.command("stopwatch_start")
        self.advance(2.5)
        self.command("stopwatch_lap")
        self.engine = self.restore()
        self.advance(3)
        state = self.command("stopwatch_stop")["stopwatch"]
        self.assertEqual(state["elapsed"], 5.5)
        self.assertEqual(state["laps"], [2.5])
        self.advance(60)
        self.command("stopwatch_start")
        self.advance(4)
        self.assertEqual(self.command("stopwatch_stop")["stopwatch"]["elapsed"], 9.5)
        self.assertEqual(self.command("stopwatch_reset")["stopwatch"]["laps"], [])

    def test_alarm_one_shot_snooze_stop(self):
        state = self.command("alarm_add", hour=12, minute=1, label="Tea")
        alarm_id = state["alarms"][0]["id"]
        state = self.advance(60)
        self.assertFalse(state["alarms"][0]["enabled"])
        self.assertEqual(state["notices"][0]["label"], "Tea")
        self.command("alarm_snooze", id=alarm_id)
        self.engine = self.restore()
        self.assertEqual(self.advance(299)["notices"], [])
        self.assertEqual(len(self.advance(1)["notices"]), 1)
        self.command("alarm_stop", id=alarm_id)
        self.assertEqual(self.advance(600)["notices"], [])

    def test_alarm_edit_and_reenable_clear_same_day_marker(self):
        alarm_id = self.command("alarm_add", hour=12, minute=1)["alarms"][0]["id"]
        self.advance(60)
        updated = self.command("alarm_update", id=alarm_id, minute=2)["alarms"][0]
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["lastDate"], "")
        self.assertEqual(len(self.advance(60)["notices"]), 2)
        self.command("alarm_update", id=alarm_id, enabled=True)
        self.assertEqual(len(self.advance(0)["notices"]), 3)

    def test_repeat_days_edit_after_stop_does_not_ring_again(self):
        alarm_id = self.command("alarm_add", hour=12, minute=1, days=[0])["alarms"][0]["id"]
        self.assertEqual(len(self.advance(60)["notices"]), 1)
        self.command("alarm_stop", id=alarm_id)
        self.command("alarm_update", id=alarm_id, days=[0, 1])
        self.engine = self.restore()
        state = self.advance(1)
        self.assertEqual(state["alarms"][0]["lastDate"], "2026-01-05")
        self.assertEqual(state["notices"], [])
        self.assertEqual(len(self.advance(86400)["notices"]), 1)

    def test_idle_wait_checks_alarm_and_snooze_after_suspend(self):
        alarm_id = self.command("alarm_add", hour=12, minute=1)["alarms"][0]["id"]
        # A selector timeout counts awake time, so its remainder follows the wall-clock jump.
        wait = self.engine.next_wake()
        state = self.advance(65 + wait)
        self.assertEqual(len(state["notices"]), 1)
        self.command("alarm_snooze", id=alarm_id)
        self.advance(230)
        wait = self.engine.next_wake()
        state = self.advance(80 + wait)
        self.assertEqual(len(state["notices"]), 1)
        self.assertIsNone(state["alarms"][0]["snooze"])

    def test_local_zone_refreshes_without_worker_restart(self):
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            self.engine = clock_tools.ClockTools(self.path, clock=lambda: self.now)
            self.command("world_add", zone="UTC")
            self.command("alarm_add", hour=7, minute=1)
            self.assertEqual(self.engine.snapshot()["world"][0]["offset"], "+00:00")
            os.environ["TZ"] = "America/New_York"
            state = self.advance(60)
            self.assertEqual(len(state["notices"]), 1)
            self.assertEqual(state["world"][0]["offset"], "+05:00")
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()

    def test_wake_deadlines_and_display_seconds(self):
        self.assertEqual(self.engine.next_wake(), 5)
        self.command("timer_start", seconds=10)
        self.assertEqual(self.engine.next_wake(), 1)
        snapshot = self.engine.snapshot()
        self.now += 0.4
        self.assertEqual(self.engine.snapshot(), snapshot)
        self.assertAlmostEqual(self.engine.next_wake(), 0.6, places=5)
        self.advance(0.6)
        self.assertEqual(self.engine.snapshot()["timer"]["remaining"], 9)
        self.command("timer_reset")
        self.command("stopwatch_start")
        snapshot = self.engine.snapshot()
        self.now += 0.4
        self.assertEqual(self.engine.snapshot(), snapshot)

    def test_empty_zone_search(self):
        self.command("world_search", query="London")
        self.assertEqual(self.command("world_search", query="  ")["search"], [])

    def test_largest_generated_state_loads(self):
        for _ in range(64):
            self.command("alarm_add", hour=12, minute=1, label="🍵" * 120, days=list(range(7)))
        self.advance(60)
        self.advance(86400)
        self.assertLessEqual(self.path.stat().st_size, clock_tools.MAX_STATE)
        self.assertEqual(self.restore().state, self.engine.state)
        self.assertEqual(len(self.engine.state["notices"]), clock_tools.MAX_NOTICES)
        old = self.path.read_bytes()
        oversized = dict(self.engine.state, extra="x" * clock_tools.MAX_STATE)
        with self.assertRaises(ValueError):
            self.engine.store.save(oversized)
        self.assertEqual(self.path.read_bytes(), old)

    def test_notice_timestamp_survives_restore(self):
        self.command("timer_start", seconds=10)
        state = self.advance(50)
        self.assertEqual(state["notices"][0]["created"], self.now - 40)
        self.assertEqual(state["notices"][0]["issued"], self.now)
        self.assertEqual(self.restore().snapshot()["notices"], state["notices"])
        del self.engine.state["notices"][0]["created"]
        self.engine.store.save(self.engine.state)
        timestamp = self.restore().state["notices"][0]["created"]
        self.now += 100
        self.assertEqual(self.restore().state["notices"][0]["created"], timestamp)

    def test_invalid_state_recovery_keeps_original(self):
        self.path.parent.mkdir(mode=0o700)
        environment = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        for raw in (b"{broken", b"[" * 2000 + b"]" * 2000, b"\xff"):
            self.path.write_bytes(raw)
            self.path.chmod(0o600)
            result = subprocess.run([sys.executable, str(HELPER), "--serve"],
                                    input="", text=True, capture_output=True, env=environment, timeout=10)
            row = json.loads(result.stdout)
            self.assertFalse(row["ready"])
            self.assertTrue(row["recoverable"])
            self.assertIn("Start over", row["error"])
            self.assertEqual(result.stderr, "")
            result = subprocess.run([sys.executable, str(HELPER), "--serve", "--recover"],
                                    input="", text=True, capture_output=True, env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ready"])
            self.assertTrue(any(path.read_bytes() == raw for path in self.path.parent.glob("*.broken-*")))
            self.assertEqual(self.restore().state, clock_tools.defaults())

    def test_second_writer_and_bad_commands_are_plain(self):
        environment = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        with subprocess.Popen([sys.executable, str(HELPER), "--serve"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment) as first:
            self.assertTrue(json.loads(first.stdout.readline())["ready"])
            result = subprocess.run([sys.executable, str(HELPER), "--serve"], input="", text=True,
                                    capture_output=True, env=environment, timeout=10)
            self.assertEqual(json.loads(result.stdout)["error"], "Clock tools are already running in another session.")
            output, errors = first.communicate("{broken\n" + "[" * 2000 + "]" * 2000 + "\n", timeout=10)
            self.assertEqual(errors, "")
            self.assertTrue(all(json.loads(line)["error"] in ("Clock tools could not read that data. Please try again.",
                                                                   "Expected a clock command") for line in output.splitlines()))

    def test_repeat_days_and_missed_minute(self):
        self.command("alarm_add", hour=12, minute=1, days=[1])
        self.assertEqual(self.advance(60)["notices"], [])
        self.assertEqual(len(self.advance(86400)["notices"]), 1)
        self.command("alarm_add", hour=12, minute=2, days=[1])
        self.assertEqual(len(self.advance(120)["notices"]), 1)

    def test_dst_gap_and_fold(self):
        self.now = dt.datetime(2026, 3, 8, 6, 59, tzinfo=dt.timezone.utc).timestamp()
        self.engine = self.restore("America/New_York")
        self.command("alarm_add", hour=2, minute=30, days=[6])
        self.assertEqual(self.advance(31 * 60)["notices"], [])
        self.now = dt.datetime(2026, 11, 1, 5, 29, tzinfo=dt.timezone.utc).timestamp()
        self.command("alarm_add", hour=1, minute=30, days=[6])
        self.assertEqual(len(self.advance(60)["notices"]), 1)
        self.engine = self.restore("America/New_York")
        self.assertEqual(len(self.advance(3600)["notices"]), 1)

    def test_disable_cancels_snooze(self):
        alarm_id = self.command("alarm_add", hour=12, minute=1, days=[0])["alarms"][0]["id"]
        self.advance(60)
        self.command("alarm_snooze", id=alarm_id)
        self.command("alarm_update", id=alarm_id, enabled=False)
        self.assertEqual(self.advance(300)["notices"], [])

    def test_world_clock_offsets_follow_both_dst_rules(self):
        self.engine = self.restore("America/New_York")
        self.command("world_add", zone="Europe/London")
        self.assertEqual(self.engine.snapshot()["world"][0]["offset"], "+05:00")
        self.now = dt.datetime(2026, 3, 15, 12, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(self.engine.snapshot()["world"][0]["offset"], "+04:00")
        self.command("world_add", zone="Asia/Kathmandu")
        self.assertEqual(self.engine.snapshot()["world"][1]["offset"], "+09:45")
        result = self.command("world_search", query="new york")
        self.assertIn("America/New_York", result["search"])
        self.command("world_remove", zone="Europe/London")
        self.assertEqual(len(self.restore().state["world"]), 1)

    def test_invalid_command_is_transactional(self):
        self.command("timer_start", seconds=20)
        original = self.path.read_bytes()
        for bad in (float("nan"), -1, True, 10000000, 10 ** 400, "60"):
            with self.assertRaises(ValueError):
                self.command("timer_start", seconds=bad)
        with self.assertRaises(ValueError):
            self.command("alarm_add", hour=25, minute=0)
        self.assertEqual(self.path.read_bytes(), original)

    def test_private_atomic_state_and_corruption_preserved(self):
        self.command("timer_start", seconds=60)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.path.write_text('{"version":1,"timer":null}')
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual(self.path.read_bytes(), original)

    def test_existing_shared_state_directory(self):
        self.path.parent.mkdir(mode=0o755)
        self.command("timer_start", seconds=60)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.path.parent.chmod(0o777)
        with self.assertRaises(ValueError):
            self.command("timer_pause")

    def test_symlink_state_refused(self):
        self.path.parent.mkdir()
        target = Path(self.tmp.name) / "target"
        target.write_text("unchanged")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.restore()
        self.assertEqual(target.read_text(), "unchanged")

    def test_protocol_error_survives_ticks_until_success(self):
        environment = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        with subprocess.Popen([sys.executable, str(HELPER), "--serve"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=environment) as process:
            self.assertTrue(json.loads(process.stdout.readline())["ready"])
            process.stdin.write('{"op":"stopwatch_start"}\n{"op":"invalid"}\n')
            process.stdin.flush()
            time.sleep(1.2)
            process.stdin.write('{"op":"stopwatch_stop"}\n')
            process.stdin.flush()
            output, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors)
        rows = [json.loads(line) for line in output.splitlines()]
        failures = [row for row in rows if row["error"]]
        self.assertGreaterEqual(len(failures), 2)
        self.assertGreater(failures[-1]["stopwatch"]["elapsed"] - failures[0]["stopwatch"]["elapsed"], 0.25)
        self.assertTrue(all(row["error"] == "Unknown clock command" for row in rows[1:-1]))
        self.assertEqual(rows[-1]["error"], "")
        self.assertFalse(rows[-1]["stopwatch"]["running"])

    def test_protocol_suppresses_unchanged_idle_snapshots(self):
        environment = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        with subprocess.Popen([sys.executable, str(HELPER), "--serve"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=environment) as process:
            self.assertTrue(json.loads(process.stdout.readline())["ready"])
            time.sleep(0.6)
            output, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors)
        self.assertEqual(output, "")

    def test_json_line_protocol_and_read_only_status(self):
        environment = dict(os.environ, XDG_STATE_HOME=self.tmp.name)
        process = subprocess.run([sys.executable, str(HELPER), "--serve"],
                                 input='{"op":"timer_start","seconds":30}\n{"op":"stopwatch_start"}\n',
                                 text=True, capture_output=True, env=environment, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        rows = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertTrue(rows[-1]["stopwatch"]["running"])
        original = self.path.read_bytes()
        result = subprocess.run([sys.executable, str(HELPER), "status", "--json"],
                                text=True, capture_output=True, env=environment, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["timer"]["status"], "running")
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
