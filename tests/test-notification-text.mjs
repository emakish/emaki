// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise incoming notification snapshots with blueman's pairing body templates.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../shell/NotificationStore.qml', import.meta.url), 'utf8');
function production(name, parameters) {
    const start = source.indexOf('{', source.indexOf('function ' + name + '('));
    assert.notEqual(start, -1);
    let depth = 1, end = start + 1;
    for (; depth && end < source.length; end++) {
        if (source[end] === '{') depth++;
        if (source[end] === '}') depth--;
    }
    assert.equal(depth, 0);
    return vm.runInContext('(function(' + parameters + '){' + source.slice(start + 1, end - 1) + '})', context);
}
const context = vm.createContext({});
context.plainBody = production('plainBody', 'value');
const snapshot = production('snapshot', 'n,time');
function received(body) {
    return snapshot({id: 1, appName: 'blueman', appIcon: 'blueman', summary: 'Bluetooth',
        body, expireTimeout: 0, actions: [{identifier: 'confirm', text: 'Confirm'}]}, 1000);
}
const device = '<b>Keys &amp; &lt;Home&gt; &quot;Desk&quot; &#x27;A&#x27;</b> (00:11:22:33:44:55)';
const plainDevice = 'Keys & <Home> "Desk" \'A\' (00:11:22:33:44:55)';
// Exact English templates from blueman's authentication module: ask_passkey,
// _on_request_confirmation, _on_display_pin_code and _on_display_passkey.
const cases = [
    [`Pairing request for:\n${device}\nConfirm value for authentication: <b>001234</b>`,
        `Pairing request for:\n${plainDevice}\nConfirm value for authentication: 001234`],
    [`Pairing request for ${device}`, `Pairing request for ${plainDevice}`],
    [`Pairing PIN code for ${device}: 001234`, `Pairing PIN code for ${plainDevice}: 001234`],
    [`Pairing passkey for ${device}: 00<b>1</b>234`, `Pairing passkey for ${plainDevice}: 001234`],
    ['<a href="https://example.invalid">Confirm</a><img src="file:///private/image.png"> <b>123456</b>', 'Confirm 123456'],
    ['<img alt="a > b" src="https://example.invalid/a">123456', '123456'],
    ['<!-- private -->&#48;&#x30;1234', '001234'],
    ['&lt;b&gt;literal device&lt;/b&gt; &amp;lt;b&amp;gt;', '<b>literal device</b> &lt;b&gt;'],
    ['&#x1f511; &#0; &#xD800; &#1114112; &unknown;', '🔑 &#0; &#xD800; &#1114112; &unknown;'],
    ['Code < 999999 and > 000000', 'Code < 999999 and > 000000'],
    ['x'.repeat(5000), 'x'.repeat(4096)]
];
for (const [input, expected] of cases) {
    const note = received(input);
    assert.equal(note.body, expected);
    assert.equal(note.critical, true);
    assert.equal(note.actions[0].id, 'confirm');
}
const receiver = fs.readFileSync(new URL('../shell/NotificationReceiver.qml', import.meta.url), 'utf8');
assert.match(receiver, /bodyMarkupSupported:\s*false/);
console.log('Notification text: real pairing templates, escaped names, safe plain text and snapshot integration: PASS');
