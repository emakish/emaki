#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real reactive notification policy and legacy migration in an isolated QML store."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
QML = '''import QtQuick
import Quickshell
Scope {
    id: fixture
    property int step: 0
    property bool failed: false
    property string mode: "@MODE@"
    QtObject {
        id: catalog
        property var values: []
        property int writes: 0
        function set(key, value) {
            fixture.check(key === "notifications.dnd" && value === "true", "migration request");
            writes++;
        }
    }
    NotificationStore { id: notes; catalog: catalog }
    function check(ok, label) {
        if (!ok) { failed = true; console.error("POLICY_FAIL", step, label); }
    }
    function rows(manual, overrideValue, until, schedule, rules, sounds) {
        catalog.values = [
            {key: "notifications.dnd", value: manual, override_value: overrideValue},
            {key: "notifications.until", value: String(until || 0)},
            {key: "notifications.schedule", value: JSON.stringify(schedule || {})},
            {key: "notifications.rules", value: JSON.stringify(rules || {})},
            {key: "sound.system_sounds", value: sounds !== false}
        ];
    }
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            const migrating = fixture.mode === "legacy";
            if (fixture.step === 0) {
                fixture.check(notes.effectiveDnd === ["legacy", "explicit", "marked"].includes(fixture.mode), "legacy before catalog");
                fixture.rows(false, fixture.mode === "explicit" ? false : null);
            } else if (fixture.step === 1) {
                fixture.check(catalog.writes === (migrating ? 1 : 0), "one migration only");
                fixture.check(notes.effectiveDnd === migrating, "loaded policy");
                if (migrating) fixture.rows(true, true);
            } else if (fixture.step === 2) {
                fixture.check(notes.dndMigrated, "migration acknowledged");
                fixture.rows(false, null);
            } else if (fixture.step === 3) {
                fixture.check(!notes.effectiveDnd, "reset respects false default");
                fixture.check(catalog.writes === (migrating ? 1 : 0), "reset not remigrated");
                fixture.rows(true, true);
            } else if (fixture.step === 4) {
                fixture.check(notes.effectiveDnd, "undo reactivates managed value");
                fixture.rows(false, null, Date.now() + 60000);
            } else if (fixture.step === 5) {
                fixture.check(notes.effectiveDnd, "until activates");
                notes.now = Date.now() + 120000;
            } else if (fixture.step === 6) {
                fixture.check(!notes.effectiveDnd, "until expires reactively");
                fixture.rows(false, null, 0, {enabled:true, start:"00:00", end:"00:00"});
            } else if (fixture.step === 7) {
                fixture.check(notes.effectiveDnd, "schedule activates reactively");
                fixture.rows(false, null, 0, {enabled:false,start:"00:00",end:"00:00"}, {"name:Mail":"silent"}, false);
            } else if (fixture.step === 8) {
                fixture.check(!notes.effectiveDnd && !notes.systemSounds && notes.ruleFor("name:Mail") === "silent", "policy fields react");
                fixture.rows(false, null);
            } else if (fixture.step === 9) {
                fixture.check(notes.systemSounds && notes.ruleFor("name:Mail") === "allow" && notes.soundAllowed("name:Mail"), "policy reset reacts");
                fixture.check(catalog.writes === (migrating ? 1 : 0), "undo not remigrated");
            } else if (fixture.step === 10 && fixture.mode === "fresh-save") {
                notes.local("Mail", "Saved notification", "History creates the first state file");
            } else if (fixture.step === 23) {
                console.log(fixture.failed ? "POLICY_FAILED" : "POLICY_PASS");
                Qt.quit();
            }
            fixture.step++;
        }
    }
}'''

with tempfile.TemporaryDirectory(prefix="notification-store-", dir="/tmp") as temporary:
    base = Path(temporary)
    for mode in ("legacy", "explicit", "marked", "empty-cache", "fresh", "fresh-save"):
        work = base / mode
        work.mkdir()
        for name in ("config", "state", "data", "cache", "runtime"):
            (work / name).mkdir(mode=0o700)
        state = work / "state/emaki/notifications.json"
        state.parent.mkdir()
        initial = json.dumps({"version":1, "dnd": mode in ("legacy", "explicit", "marked"), "dndMigrated": mode == "marked", "entries":[]})
        if not mode.startswith("fresh"):
            state.write_text(initial)
        shutil.copyfile(ROOT / "shell/NotificationStore.qml", work / "NotificationStore.qml")
        (work / "shell.qml").write_text(QML.replace("@MODE@", mode))
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
                   XDG_CONFIG_HOME=str(work/"config"), XDG_STATE_HOME=str(work/"state"), XDG_DATA_HOME=str(work/"data"),
                   XDG_CACHE_HOME=str(work/"cache"), XDG_RUNTIME_DIR=str(work/"runtime"),
                   DBUS_SESSION_BUS_ADDRESS="unix:path="+str(work/"missing"), DBUS_SYSTEM_BUS_ADDRESS="unix:path="+str(work/"missing"))
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "NIRI_SOCKET"):
            env.pop(key, None)
        result = subprocess.run(["qs", "-p", str(work / "shell.qml"), "--no-color"], env=env, capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and "POLICY_PASS" in output and "POLICY_FAIL" not in output, (mode, output)
        if mode == "fresh":
            assert not state.exists(), "pristine migration must not create a state file"
        elif mode == "empty-cache":
            assert state.read_text() == initial, "no-op migration must not rewrite an existing empty cache"
        else:
            saved = json.loads(state.read_text())
            assert saved["dndMigrated"] is True, mode
            if mode == "fresh-save":
                assert [entry["summary"] for entry in saved["entries"]] == ["Saved notification"], saved
                assert saved["applications"] == [{"id":"name:Mail", "name":"Mail"}], saved
print("PASS: real QML catalog reactivity, legacy migration, explicit false, durable marker, pristine no-write, history persistence, reset and undo")
