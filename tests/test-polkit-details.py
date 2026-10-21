#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy selection and argument display boundaries without a running authority."""
from pathlib import Path
import json
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
messages = []
program_messages = []
policy_texts = []
# Every action that asks for administrator authentication uses the same prompt.
for directory in ('polkit', 'update-manager'):
    for policy_file in sorted((ROOT / directory).glob('*.policy')):
        for action in ET.parse(policy_file).getroot().findall('action'):
            if action.attrib['id'].startswith('org.emaki.programs.'):
                program_messages.append((action.attrib['id'], action.findtext('message', '')))
            policy_texts.extend((action.attrib['id'], item.text or '') for item in action
                                if item.tag in ('description', 'message'))
            if any('auth_admin' in (value.text or '')
                   for value in action.findall('defaults/*')):
                message = action.findtext('message', '')
                messages.append(message)
                assert message.startswith('Enter an administrator password to '), (
                    policy_file.name, action.attrib['id'], message)

policy = ET.parse(ROOT / 'polkit/org.emaki.rollback.policy').getroot().find('action')
annotations = {item.attrib['key']: item.text for item in policy.findall('annotate')}
path = annotations['org.freedesktop.policykit.exec.path']
first = annotations['org.freedesktop.policykit.exec.argv1']
assert first == 'keep'
# pkexec chooses this action only when both the executable and argv[1] match.
for arguments in ([], ['restore', 'old'], ['snapshot', '4'], ['delete', 'old', '--yes'],
                  ['release', '--all'], ['pin', '4'], ['unpin', '4']):
    assert not (arguments and arguments[0] == first), arguments
assert path.endswith('/emaki-rollback')
assert '["pkexec", Platform.binDir + "/emaki-rollback", "keep"]' in (
    ROOT / 'shell/SnapshotRecovery.qml').read_text()

assert len(policy_texts) == 38, 'Every Emaki description and message must be checked'
command_cases = [line for line in (ROOT / 'tests/fixtures/polkit-command-lines.txt').read_text().splitlines()
                 if line and not line.startswith('#')]
copy_source = '\n'.join(line for line in (ROOT / 'shell/PolkitText.js').read_text().splitlines()
                        if not line.startswith('.'))
source = '\n'.join(line for line in (ROOT / 'shell/PolkitDetails.js').read_text().splitlines()
                   if not line.startswith('.'))
translation = (ROOT / 'tests/qml-translation.js').read_text()
script = translation + r'''
const assert = require('node:assert/strict');
const Copy = {};
require('node:vm').runInNewContext(TRANSLATION + '\n' + COPY_TEXT, Copy);
SOURCE
const paths = {binDir: '/opt/emaki/bin', libDir: '/opt/emaki/lib/emaki',
    libexecDir: '/opt/emaki/libexec/emaki'};
for (const [action, message] of POLICY_TEXTS) {
    for (const details of [{}, {program: '/opt/example', command_line: '/opt/example PRIVATE_VALUE'}]) {
        assert.equal(headline(action, message, details, paths.libexecDir), message);
        assert.equal(rows(action, message, details, '', paths)[2].value, message);
    }
}
const pidOnly = {'polkit.caller-pid': '4100', 'polkit.subject-pid': '4099'};
const regressionFailures = [];
function regression(label, check) {
    try { check(); } catch (error) { regressionFailures.push(label + ': ' + error.message); }
}
regression('B1: pid-only program-manager requests', () => {
    assert.equal(PROGRAM_MESSAGES.length, 7);
    for (const [action, message] of PROGRAM_MESSAGES) {
        assert(!message.includes('$('), action);
        assert(message.endsWith('.'), action);
        const expanded = message.replace(/\$\([^)]+\)/g, '/opt/helper --password=PRIVATE_VALUE');
        const display = rows(action, expanded, pidOnly, '', paths);
        assert.equal(headline(action, expanded, pidOnly, paths.libexecDir), message);
        assert.equal(display[2].value, message);
        assert(!JSON.stringify(display).includes('PRIVATE_VALUE'));
    }
});
regression('B3: own-message sanitization', () => {
    const message = 'Change basic system settings.\n\u001b\u202e\u200b\u200f\u061c\u{e0001}Continue.';
    const expected = 'Change basic system settings.' + ' '.repeat(7) + 'Continue.';
    assert.equal(headline('org.emaki.example', message, pidOnly), expected);
    assert.equal(rows('org.emaki.example', message, pidOnly, '')[2].value, expected);
});
regression('Format characters separate text while script joiners survive', () => {
    for (const character of ['\u00ad', '\u061c', '\u180e', '\u200b', '\u200e', '\u200f',
            '\u202a', '\u202b', '\u202c', '\u202d', '\u202e', '\u2066', '\u2069',
            '\u{110bd}', '\u{1d173}', '\u{e0001}', '\u{e007f}'])
        assert.equal(clean('before' + character + 'after'), 'before after');
    const text = 'සත්\u200dයාපනය می\u200cشود';
    assert.equal(clean(text), text);
    for (const action of ['org.emaki.example', 'org.example.action']) {
        assert.equal(headline(action, text, pidOnly), text);
        assert.equal(rows(action, text, pidOnly, '')[2].value, text);
    }
    assert.equal(executableName('/opt/a\u200cb\u200dc'), 'abc');
});
const execAction = 'org.freedesktop.policykit.exec';
function checkPrompt(action, message, details, expected) {
    assert.equal(headline(action, message, details, paths.libexecDir), expected);
    assert.equal(rows(action, message, details, '', paths)[2].value, expected);
}
regression('B2/B6: fixed pkexec template with pid-only details', () => {
    for (const [command, name] of [
            ['/opt/nmcli wifi-sec.psk PRIVATE_VALUE', 'nmcli'],
            ['/opt/nm\u200b\u200f\u061ccli PRIVATE_VALUE', 'nmcli'],
            ['/opt/' + 'x'.repeat(100) + ' PRIVATE_VALUE', 'x'.repeat(64)],
            ['relative PRIVATE_VALUE', 'a program'],
            ['"/opt/My App" PRIVATE_VALUE', 'a program'],
            ['', 'a program']]) {
        for (const [suffix, target] of [['the super user', 'administrator'],
                ['user bob', 'bob'], ['user b\u200bo\u202eb', 'b o b'],
                ['user ' + 'x'.repeat(100), 'x'.repeat(64)]]) {
            const message = "Authentication is needed to run `" + command + "' as " + suffix;
            checkPrompt(execAction, message, pidOnly,
                'Enter an administrator password to run ' + name + ' as ' + target + '.');
        }
    }
    for (const message of ["Other text `/opt/tool PRIVATE_VALUE' as the super user",
            "Authentication is needed to run `/opt/tool PRIVATE_VALUE' as the super user trailing",
            'PRIVATE_VALUE'])
        checkPrompt(execAction, message, pidOnly,
            'Enter an administrator password to run a program as administrator.');
    checkPrompt('other.action', "Authentication is needed to run `/opt/true' as user bob", pidOnly,
        "Authentication is needed to run `/opt/true' as user bob");
});
regression('B5: absolute detail paths and command keys', () => {
    for (const key of ['command', 'commandline', 'Command_Line', 'cmdline', 'argv', 'arguments']) {
        for (const [program, name] of [[undefined, 'a program'], ['', 'a program'],
                ['PASSWORD=PRIVATE_VALUE', 'a program'], ['relative', 'a program'],
                ['/opt/My App/bin/tool', 'a program'], ['/tmp/nmcli dir/evil', 'a program'],
                ['/opt/tool\n', 'a program'], ['/opt/\u0000tool', 'a program'],
                ['/opt/nm\u200b\u200f\u061ccli', 'nmcli'],
                ['/opt/' + 'x'.repeat(5000), 'x'.repeat(64)], ['/opt/tool', 'tool']]) {
            const details = {[key]: 'PASSWORD=PRIVATE_VALUE /opt/tool', program};
            checkPrompt('other.action', 'PRIVATE_VALUE', details,
                'Enter an administrator password to run ' + name + ' as administrator.');
            assert.equal(rows('other.action', '', details, '', paths).find(row => row.label === key).value, 'Hidden');
        }
    }
});
regression('B4: nine embedded quote/bracket secret forms', () => {
    for (const [input, expected] of [
            ['--password ab"PRIVATE_VALUE', '--password Hidden'],
            ['password=ab]PRIVATE_VALUE', 'password=Hidden'],
            ["password=ab'PRIVATE_VALUE", 'password=Hidden'],
            ['password=ab`PRIVATE_VALUE', 'password=Hidden'],
            ['password=ab}PRIVATE_VALUE', 'password=Hidden'],
            ['--password=ab"PRIVATE_VALUE', '--password=Hidden'],
            ['Cookie: sid=ab"PRIVATE_VALUE', 'Cookie: Hidden'],
            ['Bearer ab"PRIVATE_VALUE', 'Bearer Hidden'],
            ['token=ab]PRIVATE_VALUE', 'token=Hidden']]) {
        assert.equal(redact(input), expected);
        checkPrompt('other.action', input, pidOnly, expected);
        checkPrompt('other.action', "Run `" + input + "' now", pidOnly, "Run `" + expected + "' now");
    }
});
assert.equal(regressionFailures.length, 0, regressionFailures.join('\n'));
for (const message of [
        'Enter an administrator password to change an account.',
        'Enter a password for this account.', 'Passwords are required.',
        'The password must be entered.', 'Your token expires soon.']) {
    assert.equal(redact(message), message);
    assert.equal(headline('other.action', message, {}, paths.libexecDir), message);
    assert.equal(rows('other.action', message, {}, '', paths)[2].value, message);
}
for (const name of ['password', 'passwd', 'passphrase', 'pwd', 'token', 'secret',
        'api-key', 'api_key', 'apikey', 'credential', 'authorization', 'cookie']) {
    for (const separator of ['=', ': ', ' = '])
        for (const secret of ['PRIVATE_VALUE', '"PRIVATE VALUE"', "'PRIVATE VALUE'"])
            assert.equal(redact(name + separator + secret), name + separator + 'Hidden');
    for (const secret of ['to', 'for', 'PRIVATE_VALUE', '"PRIVATE VALUE"', "'PRIVATE VALUE'"])
        assert.equal(redact('--' + name + ' ' + secret), '--' + name + ' Hidden');
}
for (const scheme of ['Bearer', 'Basic'])
    assert.equal(redact(scheme + ' PRIVATE_VALUE'), scheme + ' Hidden');
assert.equal(redact('https://person:PRIVATE_VALUE@example.com'), 'https://Hidden@example.com');
// Keep exact expected displays independent of the implementation, and check every
// secret in both the headline and the Message row where pkexec repeats its argv.
const secretCases = [
    ['-password PRIVATE_VALUE', '-password Hidden'],
    ['-token PRIVATE_VALUE', '-token Hidden'],
    ['PASSWORD="my PRIVATE VALUE"', 'PASSWORD=Hidden'],
    ['Password=PRIVATE_VALUE', 'Password=Hidden'],
    ['Bearer PRIVATE_VALUE', 'Bearer Hidden'],
    ['Basic PRIVATE_VALUE', 'Basic Hidden'],
    ['password=PRIVATE;OTHER,VALUE', 'password=Hidden'],
    ...['new-password', 'db-password', 'access-token', 'auth-token', 'client-secret']
        .map(name => ['--' + name + ' PRIVATE_VALUE', '--' + name + ' Hidden']),
    ['password PRIVATE_VALUE', 'password Hidden'],
    ['token PRIVATE_VALUE', 'token Hidden'],
    ['passphrase "PRIVATE VALUE"', 'passphrase Hidden'],
    ["passphrase 'PRIVATE VALUE'", 'passphrase Hidden'],
    ['nmcli device wifi connect Home password hunter2',
        'nmcli device wifi connect Home password Hidden'],
    ['mysqladmin -u root --new-password PRIVATE_VALUE', 'mysqladmin -u root --new-password Hidden'],
    ['openssl pkcs12 -password pass:PRIVATE_VALUE', 'openssl pkcs12 -password Hidden'],
    ...['DB_PASSWORD', 'MYSQL_PWD', 'access_token', '_PASSWORD', 'X-Api-Key']
        .map(name => [name + '=PRIVATE_VALUE', name + '=Hidden']),
    ['"password": "PRIVATE VALUE"', '"password": Hidden'],
    ['{"password":"PRIVATE_VALUE"}', '{"password":Hidden}'],
    ['Authorization: token ghp_PRIVATE_VALUE', 'Authorization: Hidden'],
    ['Authorization: Bearer PRIVATE_VALUE', 'Authorization: Hidden'],
    ['Authorization: Basic PRIVATE_VALUE', 'Authorization: Hidden'],
    ['Authorization: Digest username="PRIVATE_VALUE", response="PRIVATE_OTHER"', 'Authorization: Hidden'],
    ['"authorization": "token PRIVATE_VALUE"', '"authorization": Hidden'],
    ['cookie=session=PRIVATE_VALUE; other=PRIVATE_OTHER', 'cookie=Hidden'],
    ['Cookie: a=1; b=PRIVATE_VALUE', 'Cookie: Hidden'],
    ['Cookie: a="PRIVATE VALUE"; b="PRIVATE OTHER"', 'Cookie: Hidden'],
    ['"cookie": "a=1; b=PRIVATE_VALUE"', '"cookie": Hidden'],
    ['https://PRIVATE_VALUE@example.com/x', 'https://Hidden@example.com/x'],
    ['http://user:PRIVATE_VALUE@host', 'http://Hidden@host'],
    ['X-Api-Key: PRIVATE_VALUE', 'X-Api-Key: Hidden'],
    ['token=PRIVATE_VALUE&x=1', 'token=Hidden'],
    ['url?access_token=PRIVATE_VALUE&x=1', 'url?access_token=Hidden'],
    ['--password\tPRIVATE_VALUE', '--password Hidden'],
    ['password=\tPRIVATE_VALUE', 'password= Hidden'],
    ['--password="PRIVATE VALUE"', '--password=Hidden'],
    ["--Password 'PRIVATE VALUE'", '--Password Hidden'],
    ['password:PRIVATE_VALUE', 'password:Hidden'],
    ['?token=PRIVATE_VALUE', '?token=Hidden'],
    ['passphrase "PRIVATE \\"VALUE\\" tail"', 'passphrase Hidden']
];
const failures = [];
for (const [input, expected] of secretCases) {
    try {
        assert.equal(redact(input), expected);
        const message = "Authentication is needed to run `" + input + "' as the super user";
        const display = "Enter an administrator password to run example as administrator.";
        const details = {program: '/opt/example', command_line: input, cmdline_short: input};
        assert.equal(headline('org.freedesktop.policykit.exec', message, details, paths.libexecDir), display);
        const displayed = rows('org.freedesktop.policykit.exec', message, details, '', paths);
        assert.equal(displayed[2].value, display);
        assert.equal(displayed.find(row => row.label === 'command_line').value, 'Hidden');
        assert.equal(displayed.find(row => row.label === 'cmdline_short').value, 'Hidden');
        assert(!/PRIVATE|hunter2/.test(JSON.stringify(displayed)));
    } catch (error) {
        failures.push(input + ': ' + error.message);
    }
}
assert.equal(failures.length, 0, failures.join('\n'));
const harmlessPrompt = "Authentication is needed to run `/opt/true' as the super user";
assert.equal(headline('org.freedesktop.policykit.exec', harmlessPrompt, {}, paths.libexecDir),
    'Enter an administrator password to run true as administrator.');
// A command is omitted as a whole, including unrecognized options and the tail
// left by pkexec's shortening. The expected text contains only the executable.
const commandCases = COMMAND_CASES.concat(secretCases.map(item => item[0]));
commandCases.push('--configuration-file /opt/example/config --password ' + 'PRIVATE_VALUE'.repeat(12));
for (const input of commandCases) {
    const command = '/opt/nmcli ' + input;
    const shortened = command.length > 80 ? command.slice(0, 38) + ' ... ' + command.slice(-37) : command;
    const message = "Authentication is needed to run `" + shortened + "' as the super user";
    for (const action of ['org.freedesktop.policykit.exec', 'other.action']) {
        for (const details of [
                {program: '/opt/nmcli', command_line: command, cmdline_short: shortened},
                {command_line: command}, {cmdline_short: shortened},
                {program: command}, {program: '/opt/nmcli', argv: input},
                {program: '/opt/nmcli', arguments: input}]) {
            const name = details.program === '/opt/nmcli'
                || (action === execAction && !Object.hasOwn(details, 'program')) ? 'nmcli' : 'a program';
            const expected = 'Enter an administrator password to run ' + name + ' as administrator.';
            assert.equal(headline(action, message, details, paths.libexecDir), expected, input);
            assert.equal(rows(action, message, details, '', paths)[2].value, expected, input);
        }
    }
}
for (const details of [null, [], {}, {command_line: ''}, {program: '', cmdline_short: ''}]) {
    const expected = 'Enter an administrator password to run a program as administrator.';
    assert.equal(headline('org.freedesktop.policykit.exec', 'PRIVATE_VALUE', details), expected);
    assert.equal(rows('org.freedesktop.policykit.exec', 'PRIVATE_VALUE', details, '')[2].value, expected);
}
for (const command of ['/opt/nmcli PRIVATE_VALUE', '"/opt/nmcli" PRIVATE_VALUE',
        "'/opt/nmcli' PRIVATE_VALUE", '  /opt/nmcli\tPRIVATE_VALUE']) {
    const expected = 'Enter an administrator password to run a program as administrator.';
    assert.equal(headline('other.action', 'PRIVATE_VALUE', {command_line: command}), expected);
}
// Without a command, third-party policy messages retain the existing redaction.
for (const [input, expected] of secretCases) {
    assert.equal(headline('other.action', input, {}), expected);
    assert.equal(rows('other.action', input, {}, '')[2].value, expected);
}
for (const [action, target, words, wording] of [
        ['remove', 'perl-term-read-password', 'perl-term-read-password perl-term-read-password', 'Remove program'],
        ['install', 'extra/haskell-crypto-token', 'extra/haskell-crypto-token ' + 'a'.repeat(64),
            'Install program and update repository packages']]) {
    const helper = paths.libexecDir + '/emaki-programs-' + action;
    const details = {program: helper, command_line: helper + ' ' + words};
    const expected = target + ' — ' + wording;
    const id = 'org.emaki.programs.' + action;
    assert.equal(headline(id, details.command_line, details, paths.libexecDir), expected);
    const displayed = rows(id, details.command_line, details, '', paths);
    assert.equal(displayed[2].value, expected);
    assert.equal(displayed.find(row => row.label === 'command_line').value, details.command_line);
}
// Ambiguous lowercase positional words and application-specific option names
// remain outside this heuristic; they must not silently become claimed coverage.
for (const input of ['password hunter', '-p PRIVATE_VALUE',
        'nmcli con modify Home wifi-sec.psk PRIVATE_VALUE', '--password\\tPRIVATE_VALUE', 'password='])
    assert.equal(redact(input), input);
const helperCases = [
    ['org.emaki.rollback', paths.binDir + '/emaki-rollback'],
    ['org.emaki.channel.change', paths.binDir + '/emaki-update-channel'],
    ['org.emaki.wifi-recovery', paths.libDir + '/emaki-wifi-recover'],
    ['org.emaki.wifi-password', paths.libexecDir + '/emaki-wifi-password'],
    ['org.emaki.charge-limit', paths.libexecDir + '/emaki-charge-limit'],
    ['org.emaki.update.apply', paths.libexecDir + '/emaki-update-apply'],
    ['org.emaki.update.restore', paths.libexecDir + '/emaki-update-restore'],
    ['org.emaki.programs.remove', paths.libexecDir + '/emaki-programs-remove']
];
function value(action, program, field = 'command_line', suppliedPaths = paths) {
    return rows(action, '', {program, [field]: program + ' restore point-42'}, '', suppliedPaths)
        .find(row => row.label === field).value;
}
for (const [action, program] of helperCases) {
    for (const field of ['command_line', 'argv', 'arguments'])
        assert.equal(value(action, program, field), program + ' restore point-42');
    for (const other of [program + '-other', program + '/child', program.replace('/emaki-', '/../emaki-'),
            '/tmp' + program, program + '\u0000', program.replace('/opt/', '/opt//')])
        assert.equal(value(action, other), 'Hidden');
    assert.equal(value('org.freedesktop.policykit.exec', program), 'Hidden');
    assert.equal(value(action, program, 'command_line', null), 'Hidden');
    assert.equal(value(action, program, 'command_line', {...paths, binDir: '/opt/../opt/emaki/bin',
        libDir: '/opt/../opt/emaki/lib/emaki', libexecDir: '/opt/../opt/emaki/libexec/emaki'}), 'Hidden');
    assert.equal(value(action, program, 'command_unclassified'), 'Hidden');
}
assert.equal(value('__proto__', '/tmp/program'), 'Hidden');
const [action, program] = helperCases[6];
const result = rows(action, '', {program,
    command_line: program + ' restore point-42 --password="PRIVATE ONE" --token PRIVATE_TWO https://u:PRIVATE_THREE@example.com',
    argv: '--api-key=PRIVATE_FOUR\u202e --cookie PRIVATE_FIVE', secret: 'PRIVATE_SIX'}, '', paths);
assert(!JSON.stringify(result).includes('PRIVATE'));
assert(!JSON.stringify(result).includes('\u202e'));
assert(result.find(row => row.label === 'command_line').value.includes('restore point-42'));
assert.equal(rows(action, '', {'org.freedesktop.policykit.exec.path': program,
    command_line: 'hidden'}, '', paths).find(row => row.label === 'command_line').value, 'Hidden');
assert.equal(rows(action, '', {program, command_line: 'hidden'}, '').find(
    row => row.label === 'command_line').value, 'Hidden');
console.log('PASS: 38 whole policy texts, pid-only requests, ' + commandCases.length
    + ' command cases, bounded program/user names, nine quote/bracket forms, helper boundaries and rollback policy');
'''
subprocess.run(['node', '-e', script.replace('TRANSLATION', json.dumps(translation)).replace('SOURCE', source).replace('COPY_TEXT', json.dumps(copy_source))
                .replace('POLICY_TEXTS', json.dumps(policy_texts)).replace('PROGRAM_MESSAGES', json.dumps(program_messages)).replace('COMMAND_CASES', json.dumps(command_cases))], check=True)
