// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell
import Quickshell.Io

ShellRoot {
    id: root
    NightLight {
        id: night
        enabled: true
    }
    FileView {
        id: events
        path: Quickshell.env("NIGHT_FIXTURE") + "/events"
        blockLoading: true
        printErrors: false
    }
    FileView {
        id: failure
        path: Quickshell.env("NIGHT_FIXTURE") + "/fail"
        blockLoading: true
        printErrors: false
    }
    FileView {
        id: saved
        path: night.statePath
        blockLoading: true
        printErrors: false
    }
    TestCase {
        name: "NightSchedule"
        when: true
        onCompletedChanged: if (completed)
            console.log("NIGHT_RESULT " + qtest_results.failCount)
        function check(value, message) {
            if (!value)
                console.error("NIGHT_FAIL " + message);
            verify(value, message);
        }
        function records() {
            events.reload();
            const text = events.text().trim();
            return text ? text.split("\n").map(line => JSON.parse(line)) : [];
        }
        function args() {
            const rows = records();
            return rows.length ? rows[rows.length - 1][1] : [];
        }
        function persisted() {
            saved.reload();
            return JSON.parse(saved.text() || "{}");
        }
        function until(predicate, message) {
            let count = 0;
            while (!predicate() && count++ < 120)
                wait(50);
            check(predicate(), message);
        }
        function cleanup() {
            night.setOn(false);
            wait(700);
        }
        function test_schedule() {
            const mode = Quickshell.env("NIGHT_CASE");
            if (mode === "savefail") {
                until(() => night.state === "off", "save failure ready");
                check(night.setSchedule("manual"), "save failure request");
                until(() => night.error.indexOf("could not be saved") >= 0, "save failure visible");
                return;
            }
            if (mode === "foreign") {
                until(() => night.state === "off", "future session ready");
                check(night.setWarmth(30) && night.setSchedule("manual") && night.setStartTime("22:00") && night.setEndTime("08:00") && night.setOn(true), "future session editable");
                until(() => night.state === "on" && records().length > 0, "future session running");
                check(night.warmth === 30 && night.schedule === "manual" && night.startTime === "22:00" && night.endTime === "08:00", "future session values");
                wait(650);
                check(persisted().version === 99, "future state preserved");
                return;
            }
            if (mode === "same-day") {
                until(() => records().length === 1, "same-day child");
                check(night.schedule === "manual" && night.startTime === "01:00" && night.endTime === "06:00", "same-day hours restored");
                check(JSON.stringify(args()) === JSON.stringify(["-t", "6499", "-T", "6500", "-s", "01:00", "-S", "06:00", "-d", "0"]), "same-day arguments");
                check(night.setStartTime("02:00") && night.setEndTime("05:00"), "same-day edits accepted");
                until(() => args()[5] === "02:00" && args()[7] === "05:00", "same-day child updated");
                until(() => persisted().startTime === "02:00" && persisted().endTime === "05:00", "same-day hours persisted");
                return;
            }
            if (mode === "restore") {
                until(() => records().length === 1, "restored child");
                check(night.schedule === "manual" && night.startTime === "21:30" && night.warmth === 0, "schedule restored");
                check(JSON.stringify(args()) === JSON.stringify(["-t", "6499", "-T", "6500", "-s", "21:30", "-S", "07:00", "-d", "0"]), "restored arguments");
                return;
            }
            until(() => night.state === "off", "initial ready");
            night.installed = false;
            check(!night.setOn(true) && night.error.length > 0, "unavailable service visible");
            night.installed = true;
            check(night.setOn(true), "turn on");
            until(() => records().length === 1, "initial child");
            check(JSON.stringify(args()) === JSON.stringify(["-t", "4000", "-T", "4001"]), "legacy fixed temperature");
            check(night.setSchedule("manual"), "manual accepted");
            until(() => records().length === 2, "schedule restarts child");
            check(JSON.stringify(args()) === JSON.stringify(["-t", "4000", "-T", "6500", "-s", "20:00", "-S", "07:00", "-d", "0"]), "manual arguments");
            check(!night.setStartTime("25:00") && !night.setEndTime("20:00") && !night.setSchedule("sunset"), "reject invalid hours");
            check(night.error.length > 0, "invalid hours visible");
            check(night.setStartTime("21:30"), "start accepted");
            until(() => args()[5] === "21:30", "updated local start time");
            check(night.setWarmth(0), "zero accepted");
            until(() => args()[1] !== "4000", "zero warmth restarts");
            check(args()[1] === "6499" && args()[3] === "6500", "zero warmth valid temperatures");
            until(() => persisted().startTime === "21:30" && persisted().warmth === 0, "schedule persisted");
            failure.setText("fail");
            check(night.setWarmth(60), "failure request accepted");
            until(() => night.state === "failed", "unexpected child exit visible");
            check(night.error.length > 0, "failure explanation");
            until(() => args()[1] === "3500", "failure child recorded");
            let count = records().length;
            wait(800);
            check(records().length === count, "failed child does not restart automatically");
            failure.setText("");
            check(night.setOn(true), "retry accepted");
            until(() => records().length === count + 1 && night.state === "on", "explicit retry works");
            night.setOn(false);
            until(() => night.state === "off", "turn off");
        }
    }
}
