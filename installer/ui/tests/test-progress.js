// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const P = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../Protocol.js"), "utf8"), P);
// A skipped snapshot remains skipped after later phases and after replay.
const skippedSnapshot = P.initial();
P.receive(skippedSnapshot, {type: "state", job_id: "ext4", phase: "snapshot", phase_pct: null, indeterminate: false}, 1);
P.receive(skippedSnapshot, {type: "state", job_id: "ext4", phase: "finish", phase_pct: 100, indeterminate: false}, 2);
assert.equal(P.phaseSkipped(skippedSnapshot, "snapshot"), true);
assert.equal(P.phaseSkipped(skippedSnapshot, "settings"), false);
const resumedSnapshot = P.initial();
P.receive(resumedSnapshot, {type: "state", job_id: "ext4", phase: "finish", phase_pct: 100, skipped_phases: ["snapshot", "update"]}, 3);
assert.equal(P.phaseSkipped(resumedSnapshot, "snapshot"), true);
const otherJob = P.initial();
otherJob.jobId = "old";
otherJob.skippedPhases = ["snapshot"];
P.receive(otherJob, {type: "hello", proto: 1, busy_job: "new"}, 3);
assert.equal(P.phaseSkipped(otherJob, "snapshot"), false);
const newJob = P.request(resumedSnapshot, "confirm");
P.receive(resumedSnapshot, {type: "reply", id: newJob.id, ok: true, job_id: "btrfs"}, 4);
assert.equal(P.phaseSkipped(resumedSnapshot, "snapshot"), false);

// Evaluate the actual row binding so the displayed reason cannot drift from its helper.
const view = fs.readFileSync(path.join(__dirname, "../InstallerView.qml"), "utf8");
const statusBinding = view.match(/text: (phaseRow.skipped[^\n]*session.indeterminate[^\n]*)/)[1];
function status(phase, session) {
    return vm.runInNewContext(statusBinding, {Protocol: P, phaseRow: {skipped: P.phaseSkipped(session, phase), modelData: phase}, view: {controller: {session}}});
}
assert.equal(status("snapshot", skippedSnapshot), "Skipped: snapshots need btrfs");
const replay = P.initial();
P.receive(replay, {type: "state", job_id: "ext4", phase: "finish", phase_pct: 100, skipped_phases: ["snapshot", "update"]}, 5);
assert.equal(status("snapshot", replay), "Skipped: snapshots need btrfs");
assert.equal(status("update", replay), "Skipped");
console.log("phase progress: OK");
