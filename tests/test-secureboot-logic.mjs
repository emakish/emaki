// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// When Settings offers a new Secure Boot confirmation and when the session shows its notice.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../shell/SecureBoot.js', import.meta.url), 'utf8');
const logic = vm.createContext({});
vm.runInContext(source.replace(/^\.pragma library\s*$/m, ''), logic);

const status = (secure_boot, key, enrollment) => ({schema_version: 1, secure_boot, key, enrollment, signed: {}});
const cases = [
    // secure_boot, key, enrollment -> shown, offer, notice
    [false, false, null, false, false, false],
    [null, null, null, false, false, false],
    [true, true, 'enrolled', true, false, false],
    [false, true, 'enrolled', true, false, false],
    [true, true, 'missing', true, true, false],
    [false, true, 'missing', true, true, true],
    [null, true, 'missing', true, true, false],
    [false, true, 'pending', true, true, true],
    [true, true, 'pending', true, false, false],
    [false, true, null, true, false, false],
];
for (const [secureBoot, key, enrollment, shown, offer, notice] of cases) {
    const value = status(secureBoot, key, enrollment);
    assert.equal(logic.shown(value), shown, JSON.stringify(value));
    assert.equal(logic.offer(value), offer, JSON.stringify(value));
    assert.equal(logic.notice(value), notice, JSON.stringify(value));
    assert.equal(typeof logic.stateText(value), 'string');
}
for (const broken of [null, {}, [], {schema_version: 2, key: true, secure_boot: false, enrollment: 'missing'},
    {schema_version: 1, key: 'true', secure_boot: false, enrollment: 'missing'},
    {schema_version: 1, key: true, secure_boot: false, enrollment: 'unknown'}])
    assert.equal(logic.offer(broken), false, JSON.stringify(broken));
assert.equal(logic.stateText(status(true, true, 'enrolled')).startsWith('Secure Boot is on.'), true);
assert.equal(logic.stateText(status(false, true, 'pending')), 'A confirmation is waiting for the next restart.');
assert.equal(logic.stateText(status(false, true, null)), 'The Secure Boot state could not be read.');
for (const password of ['1234567', '123456789', '1234 5678', 12345678, 'abcdefgh'])
    assert.equal(logic.validPassword({schema_version: 1, status: 'queued', password}), false, String(password));
assert.equal(logic.validPassword({schema_version: 1, status: 'queued', password: '01234567'}), true);
assert.equal(logic.validPassword({schema_version: 1, status: 'error', password: '01234567'}), false);
// A kernel an update could not sign: the unsigned notice wins over the unconfirmed key, whatever
// the firmware switch says, and only on a machine with the key.
const KERNEL = '/boot/vmlinuz-linux';
const FWUPD = '/usr/lib/fwupd/efi/fwupdx64.efi.signed';
const withSigned = (secure_boot, key, enrollment, signed) => ({...status(secure_boot, key, enrollment), signed});
for (const secureBoot of [true, false, null]) {
    for (const enrollment of ['enrolled', 'missing', 'pending', null]) {
        const value = withSigned(secureBoot, true, enrollment, {[KERNEL]: false, '/boot/vmlinuz-linux-lts': true, [FWUPD]: false});
        assert.deepEqual([...logic.unsigned(value)], [KERNEL], JSON.stringify(value));
        assert.equal(logic.noticeKind(value), 'unsigned', JSON.stringify(value));
        assert.equal(logic.stateText(value), 'Some startup files are not signed, so Emaki will not start with Secure Boot on. Free some disk space, then choose Sign again.');
    }
}
// fwupd's loader alone stops firmware updates, not Emaki: no claim that Emaki will not start.
for (const [secureBoot, key, enrollment, , , notice] of cases) {
    const value = withSigned(secureBoot, key, enrollment, {[KERNEL]: true, [FWUPD]: false});
    assert.deepEqual([...logic.unsigned(value)], [], JSON.stringify(value));
    assert.equal(logic.noticeKind(value), notice ? 'confirm' : '', JSON.stringify(value));
}
for (const [secureBoot, key, enrollment] of [[true, false, null], [false, null, null], [null, false, 'missing']]) {
    const value = withSigned(secureBoot, key, enrollment, {[KERNEL]: false});
    assert.deepEqual([...logic.unsigned(value)], [], JSON.stringify(value));
    assert.equal(logic.noticeKind(value), '', JSON.stringify(value));
}
// A malformed signed map makes the whole answer unreadable: no notice, no card.
for (const signed of [undefined, null, [], [false], 'no', {[KERNEL]: 'false'}, {[KERNEL]: 0}, {[KERNEL]: null}]) {
    const value = withSigned(false, true, 'missing', signed);
    assert.equal(logic.valid(value), false, JSON.stringify(signed));
    assert.deepEqual([...logic.unsigned(value)], [], JSON.stringify(signed));
    assert.equal(logic.noticeKind(value), '', JSON.stringify(signed));
}
// Everything signed: the notice and the state words are the ones before.
for (const [secureBoot, key, enrollment, , , notice] of cases) {
    const value = withSigned(secureBoot, key, enrollment, {[KERNEL]: true, [FWUPD]: true});
    assert.deepEqual([...logic.unsigned(value)], [], JSON.stringify(value));
    assert.equal(logic.noticeKind(value), notice ? 'confirm' : '', JSON.stringify(value));
    assert.equal(logic.stateText(value), logic.stateText(status(secureBoot, key, enrollment)));
}
// Root's copy not written yet: the session asks again; a known key (either way) or a broken
// answer stops the asking.
for (const secureBoot of [true, false, null])
    assert.equal(logic.unpublished(status(secureBoot, null, null)), true, String(secureBoot));
for (const value of [status(false, true, 'missing'), status(false, false, null), null, {},
    {schema_version: 1, secure_boot: false, key: null, enrollment: null}])
    assert.equal(logic.unpublished(value), false, JSON.stringify(value));
// The steps name what MokManager shows, in its order.
const steps = logic.steps;
const order = ['blue screen', 'press any key', 'Enroll MOK', 'Continue', 'Yes', 'password', 'Reboot'].map(word => steps.indexOf(word));
assert.deepEqual([...order].sort((a, b) => a - b), order);
assert.equal(order.includes(-1), false);
console.log('PASS: Secure Boot offer, notices, unsigned files, state words and password shape');
