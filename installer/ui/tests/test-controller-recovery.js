// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise the production controller's navigation with asynchronous inventory replies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../InstallerController.qml"), "utf8");
const protocol = fs.readFileSync(path.join(__dirname, "../Protocol.js"), "utf8");
const P = vm.createContext({});
require("./translations.js").install(P);
vm.runInContext(protocol, P);
function controller() {
    const c = vm.createContext({
        Protocol: P, session: P.initial(), mode: "manual", step: "disk", diskId: "disk",
        manualAssignments: false, editorPending: false, inventoryRefresh: false, editorLayout: "", diskRefreshNotice: "",
        encryptedConfirmed: true, manualReady: false,
        agreed: true, mounts: [{partition_id: "stale"}], partitionEdits: {delete: ["stale"]},
        diskReasons: {}, timezone: "UTC", steps: ["disk", "encryption"], focusLogin: false,
        timezoneGuessPending: false, applyingTimezone: "", timezoneInfo: null,
        messages: [], publish() {}, clearPasswords() {}, messageReceived() {},
        acceptTimezoneGuess() {}, clearJoinResult() {}, applyTimezone() {}, defaultAlongsideSize() { return 0; }, callHelper() {},
        resetEdits() { c.partitionEdits = {delete: [], create: [], new_table: false}; },
        write(message) { c.messages.push(message); }
    });
    require("./translations.js").install(c);
    Object.defineProperty(c, "locked", {get() { return c.session.probing; }});
    let currentStep = c.step;
    Object.defineProperty(c, "step", {get() { return currentStep; }, set(value) {
        if (value !== currentStep) {
            currentStep = value;
            if (c.stepChanged) c.stepChanged();
        }
    }});
    const stepHandler = source.match(/    onStepChanged: {[^]*?\n    }/)[0];
    vm.runInContext(stepHandler.replace("onStepChanged:", "function stepChanged()"), c);
    c.session.ready = true;
    c.session.inventory = {disks: [{id: "disk", partition_table: "gpt", layout_id: "identity", partitions: [{id: "new", start_bytes: 1048576, size_bytes: 1073741824}]}]};
    Object.defineProperty(c, "selectedDisk", {get() { return c.session.inventory.disks.find(d => d.id === c.diskId) || null; }});
    for (const name of ["send", "probe", "next", "edit", "retry", "receive", "openEditor"]) {
        const match = source.match(new RegExp("    function " + name + "\\([^]*?\\n    }"));
        assert.ok(match, name);
        vm.runInContext(match[0].replace(/: (string|var|void|bool)/g, ""), c);
    }
    return c;
}
function inventory(c, disks = c.session.inventory.disks) {
    const request = c.messages.at(-1);
    assert.equal(request.type, "probe");
    c.receive({type: "inventory", id: request.id, disks});
}
const opening = controller();
opening.session.plan = {token: "old"};
opening.next();
assert.equal(opening.session.probing, true);
assert.equal(opening.manualAssignments, false);
assert.equal(opening.mounts.length, 0);
assert.equal(opening.session.plan, null);
opening.next();
assert.equal(opening.step, "disk");
inventory(opening);
assert.equal(opening.manualAssignments, true);
assert.match(opening.session.notice, /disk was read again/);
assert.equal(opening.session.inventory.disks[0].partitions[0].id, "new");
opening.step = "encryption";
assert.equal(opening.session.notice, "");
opening.edit("disk");
assert.equal(opening.session.probing, true);
assert.equal(opening.manualAssignments, false);
inventory(opening);
assert.equal(opening.manualAssignments, true);
opening.session.notice = "Keep the USB connected.";
opening.edit("encryption");
assert.equal(opening.session.notice, "Keep the USB connected.");
const retry = controller();
retry.manualAssignments = true;
retry.session.error = {code: "disk_changed", retryable: true};
retry.session.outcome = "error";
retry.session.plan = {token: "stale"};
retry.retry();
assert.equal(retry.session.probing, true);
assert.equal(retry.session.plan, null);
inventory(retry);
assert.equal(retry.session.error, null);
assert.equal(retry.manualAssignments, true);
const unsolicited = controller();
unsolicited.session.inventory = {disks: []};
unsolicited.receive({type: "inventory", id: "unknown", disks: []});
assert.equal(unsolicited.mounts.length, 1);
const refused = controller();
refused.next();
refused.receive({type: "reply", id: refused.messages.at(-1).id, ok: false, msg: "Cannot read disk."});
assert.equal(refused.manualAssignments, false);
assert.equal(refused.editorPending, false);
assert.equal(refused.session.notice, "Cannot read disk.");
// Returning to the editor preserves the draft only after an identical fresh read.
function drafted() {
    const c = controller();
    c.step = "review";
    c.manualAssignments = true;
    c.mounts = [{partition_id: "draft", mountpoint: "/", format: true}];
    c.partitionEdits = {delete: ["new"], create: [{id: "draft", start_bytes: 1048576, size_bytes: 1073741824}], new_table: false};
    c.session.plan = {token: "expired"};
    return c;
}
const unchanged = drafted();
const draft = JSON.stringify([unchanged.mounts, unchanged.partitionEdits]);
unchanged.edit("disk");
assert.equal(unchanged.session.plan, null);
inventory(unchanged, JSON.parse(JSON.stringify(unchanged.session.inventory.disks)));
assert.equal(JSON.stringify([unchanged.mounts, unchanged.partitionEdits]), draft);
assert.equal(unchanged.manualAssignments, true);
for (const mutate of [
    d => d.layout_id = "replacement",
    d => delete d.layout_id,
    d => d.partitions[0].start_bytes += 1048576,
    d => d.partitions[0].uuid = "replacement-filesystem",
    d => d.partitions.push({id: "extra"}),
    d => d.partition_table = "dos",
    d => d.id = "other-disk"
]) {
    const changed = drafted();
    changed.edit("disk");
    const disks = JSON.parse(JSON.stringify(changed.session.inventory.disks));
    mutate(disks[0]);
    inventory(changed, disks);
    assert.equal(changed.mounts.length, 0);
    assert.equal(changed.partitionEdits.delete.length, 0);
    assert.equal(changed.partitionEdits.create.length, 0);
}
const retryDraft = drafted();
retryDraft.session.error = {code: "disk_changed", retryable: true};
retryDraft.retry();
inventory(retryDraft);
assert.equal(retryDraft.mounts.length, 0);
assert.equal(retryDraft.partitionEdits.create.length, 0);
const pending = drafted();
pending.probe(true);
pending.openEditor(true);
assert.equal(pending.messages.length, 1);
inventory(pending);
assert.equal(pending.manualAssignments, true);
assert.equal(JSON.stringify([pending.mounts, pending.partitionEdits]), draft);
const refreshed = drafted();
refreshed.probe(false);
refreshed.openEditor(true);
assert.equal(refreshed.messages.length, 1);
inventory(refreshed);
assert.equal(refreshed.manualAssignments, true);
assert.equal(refreshed.mounts.length, 0);
const failedDraft = drafted();
failedDraft.edit("disk");
failedDraft.receive({type: "reply", id: failedDraft.messages.at(-1).id, ok: false, msg: "Cannot read disk."});
assert.equal(failedDraft.mounts.length, 0);
assert.equal(failedDraft.editorLayout, "");
console.log("controller recovery: OK");
