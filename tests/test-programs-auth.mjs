// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Run the shipped denial rule without a system bus or installed host policies.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const template = fs.readFileSync(new URL('../update-manager/00-emaki-programs.rules', import.meta.url), 'utf8');
for (const prefix of ['/opt/emaki/libexec', '/nix/store/example-emaki/libexec/emaki']) {
    let rule;
    vm.runInNewContext(template.replaceAll('@EMAKI_LIBEXECDIR@', prefix), {
        polkit: {Result: {NO: 'no'}, addRule: callback => { rule = callback; }},
    });
    function decision(operation, command, program = `${prefix}/emaki-programs-${operation}`) {
        const details = {command_line: command, program};
        return rule({id: `org.emaki.programs.${operation}`, lookup: key => details[key]}, {});
    }
    for (const operation of ['install', 'remove']) {
        const helper = `${prefix}/emaki-programs-${operation}`;
        const target = operation === 'install' ? 'extra/example-program' : 'example-program';
        const token = operation === 'install' ? 'a'.repeat(64) : 'example-library,example-program';
        const command = `${helper} ${target} ${token}`;
        assert.equal(decision(operation, command), undefined, 'valid requests retain auth_admin');
        assert.equal(decision(operation, command, '/tmp/other-helper'), 'no');
        assert.equal(decision(operation, null), 'no');
        for (const invalid of [
            `${helper} ${target}`, `${command} extra`, `${helper}  ${target} ${token}`,
            `${helper} --run ${target} ${token}`, `/tmp/other-helper ${target} ${token}`,
            `${helper} ${target} ${'a'.repeat(9000)}`, `${helper} ${'x'.repeat(513)} ${token}`,
        ]) assert.equal(decision(operation, invalid), 'no', invalid);
        for (const unsafe of ['\n', '\r', '\t', '\0', '\u202e', '\u2066', '\u200b', '<', '"', '$']) {
            assert.equal(decision(operation, `${helper} ${target}${unsafe} ${token}`), 'no');
            assert.equal(decision(operation, `${command}${unsafe}`), 'no');
        }
        const policyName = operation === 'install' ? 'org.emaki.programs.install.policy' : 'org.emaki.programs.policy';
        const policy = fs.readFileSync(new URL(`../update-manager/${policyName}`, import.meta.url), 'utf8')
            .replaceAll('@EMAKI_LIBEXECDIR@', prefix);
        assert.ok(policy.includes(`<annotate key="org.freedesktop.policykit.exec.path">${helper}</annotate>`));
        assert.ok(policy.includes('<allow_active>auth_admin</allow_active>'));
        assert.ok(!policy.includes('auth_admin_keep'));
        const message = policy.match(/<message>([^<]+)<\/message>/)[1];
        assert.ok(message.includes('$(command_line)'));
        assert.ok(!message.includes('$(cmdline_short)'));
        const longTarget = (operation === 'install' ? 'extra/' : '') + 'p'.repeat(120);
        const longCommand = `${helper} ${longTarget} ${token}`;
        assert.ok(longCommand.length > 80);
        assert.equal(decision(operation, longCommand), undefined);
        // Mirrors polkit's literal property expansion of the actual MESSAGE.
        const expanded = message.replace(/\$\(([^)]+)\)/g, (_, key) => ({command_line: longCommand})[key]);
        assert.ok(expanded.includes(longTarget), 'the whole target reaches MESSAGE');
    }
    const flatpakHelper = `${prefix}/emaki-programs-flatpak`;
    const flatpakPolicy = fs.readFileSync(new URL('../update-manager/org.emaki.programs.flatpak.policy', import.meta.url), 'utf8')
        .replaceAll('@EMAKI_LIBEXECDIR@', prefix);
    for (const operation of ['install', 'remove']) {
        const appRef = 'app/org.example.Program/x86_64/stable';
        const token = 'a'.repeat(64);
        const command = `${flatpakHelper} ${operation} flathub ${appRef} ${token}`;
        function flatpakDecision(value, program = flatpakHelper) {
            return decision(`flatpak.${operation}`, value, program);
        }
        assert.equal(flatpakDecision(command), undefined, 'system requests retain auth_admin');
        assert.equal(flatpakDecision(command, '/tmp/other-helper'), 'no');
        assert.equal(flatpakDecision(null), 'no');
        for (const invalid of [
            command + ' extra', command.replace(operation, operation === 'install' ? 'remove' : 'install'),
            command.replace(' flathub ', ' --user '), command.replace(' flathub ', ' user/flathub '),
            command.replace(' flathub ', '  flathub '), command.replace(appRef, 'runtime/org.example.Program/x86_64/stable'),
            command.replace(appRef, 'app/org.example.Program/x86_64/stable;true'),
            command.replace(appRef, 'app/' + 'x'.repeat(513) + '/x86_64/stable'),
            command.replace(token, 'a'.repeat(63)), command.replace(token, 'a'.repeat(65)),
            command.replace(' flathub ', ' ' + 'x'.repeat(129) + ' '),
        ]) assert.equal(flatpakDecision(invalid), 'no', invalid);
        for (const unsafe of ['\n', '\r', '\t', '\0', '\u202e', '\u2066', '\u200b', '<', '"', '$']) {
            assert.equal(flatpakDecision(command.replace(' flathub ', ` flathub${unsafe} `)), 'no');
            assert.equal(flatpakDecision(command.replace(appRef, appRef + unsafe)), 'no');
            assert.equal(flatpakDecision(command + unsafe), 'no');
        }
        const action = flatpakPolicy.match(new RegExp(`<action id="org\\.emaki\\.programs\\.flatpak\\.${operation}">([\\s\\S]*?)</action>`))[1];
        assert.ok(action.includes(`<annotate key="org.freedesktop.policykit.exec.path">${flatpakHelper}</annotate>`));
        assert.ok(action.includes(`<annotate key="org.freedesktop.policykit.exec.argv1">${operation}</annotate>`));
        assert.ok(action.includes('<allow_any>no</allow_any>'));
        assert.ok(action.includes('<allow_inactive>no</allow_inactive>'));
        assert.ok(action.includes('<allow_active>auth_admin</allow_active>'));
        assert.ok(!action.includes('auth_admin_keep'));
        assert.ok(action.includes('$(command_line)'));
    }
    const receipt = '20261012T120000123456Z-' + 'b'.repeat(32);
    const undoRef = 'app/org.example.Program/x86_64/stable';
    const undoCommand = `${flatpakHelper} undo flathub ${undoRef} ${receipt} ${'a'.repeat(64)}`;
    assert.equal(decision('flatpak.undo', undoCommand, flatpakHelper), undefined, 'Flatpak undo retains auth_admin');
    for (const invalid of [
        undoCommand.replace(' undo ', ' remove '), undoCommand.replace(receipt, '../' + receipt),
        undoCommand.replace(` ${receipt}`, ''), undoCommand + ' extra', undoCommand.replace(receipt, receipt + '\n'),
    ]) assert.equal(decision('flatpak.undo', invalid, flatpakHelper), 'no', invalid);
    const undoAction = flatpakPolicy.match(/<action id="org\.emaki\.programs\.flatpak\.undo">([\s\S]*?)<\/action>/)[1];
    assert.ok(undoAction.includes('<annotate key="org.freedesktop.policykit.exec.argv1">undo</annotate>'));
    assert.ok(undoAction.includes('<allow_active>auth_admin</allow_active>') && !undoAction.includes('auth_admin_keep'));
    // AUR builds and native undo: one package (and receipt) and a reviewed digest only.
    for (const [operation, middle] of [['aur', 'example-program'], ['undo', `${receipt} example-program`]]) {
        const helper = `${prefix}/emaki-programs-${operation}`;
        const command = `${helper} ${middle} ${'a'.repeat(64)}`;
        assert.equal(decision(operation, command), undefined, `${operation} retains auth_admin`);
        assert.equal(decision(operation, command, '/tmp/other-helper'), 'no');
        assert.equal(decision(operation, null), 'no');
        for (const invalid of [
            `${helper} --run ${middle} ${'a'.repeat(64)}`, `${helper} --build example-program ${'c'.repeat(40)}`,
            `${command} extra`, `${helper}  ${middle} ${'a'.repeat(64)}`, `${helper} ${middle}`,
            command.replace('example-program', 'Example/Program'), command.replace('a'.repeat(64), 'a'.repeat(63)),
            command.replace('example-program', '-example'),
        ]) assert.equal(decision(operation, invalid), 'no', invalid);
        for (const unsafe of ['\n', '\t', '\0', '‮', '​']) assert.equal(decision(operation, command + unsafe), 'no');
        const policy = fs.readFileSync(new URL(`../update-manager/org.emaki.programs.${operation}.policy`, import.meta.url), 'utf8')
            .replaceAll('@EMAKI_LIBEXECDIR@', prefix);
        assert.ok(policy.includes(`<annotate key="org.freedesktop.policykit.exec.path">${helper}</annotate>`));
        assert.ok(policy.includes('<allow_active>auth_admin</allow_active>') && !policy.includes('auth_admin_keep'));
        assert.ok(policy.includes('$(command_line)'));
    }
    assert.equal(rule({id: 'org.example.other', lookup() { throw Error('unrelated action'); }}, {}), undefined);
}

// Direct root-helper entry points must refuse spoofed argv groupings, even if
// their space-joined form could match the display guard. No root work is allowed.
const python = String.raw`
import contextlib
import io
import sys
from unittest.mock import patch
sys.path.insert(0, 'update-manager')
import programs_install as install
import programs_remove as remove
for module, target, token in [(install, 'extra/example-program', 'a' * 64),
                              (remove, 'example-program', 'example-program')]:
    invalid = [[target + ' ' + token], [target, token, 'extra'],
               [target + '\u202e', token], [target, token + '\n']]
    for arguments in invalid:
        with patch.object(module.os, 'geteuid', return_value=0), \
             patch.object(module.os, 'open', side_effect=AssertionError('privileged file access')), \
             patch.object(module.subprocess, 'run', side_effect=AssertionError('privileged command')), \
             contextlib.redirect_stdout(io.StringIO()):
            assert module.main(arguments) == 1, arguments
    with patch.object(module.os, 'geteuid', return_value=1000), \
         patch.object(module.os, 'open', side_effect=AssertionError('unprivileged file access')), \
         patch.object(module.subprocess, 'run', side_effect=AssertionError('unprivileged command')), \
         contextlib.redirect_stdout(io.StringIO()):
        assert module.main([target, token]) == 1
`;
const result = spawnSync(process.env.EMAKI_PYTHON || 'python3', ['-B', '-c', python], {cwd: root, encoding: 'utf8'});
assert.equal(result.status, 0, result.stderr || result.error?.message);
console.log('Program authentication rules, messages and helper refusals passed.');
