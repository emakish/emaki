#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Policy selection and argument display boundaries without a running authority."""
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
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

source = '\n'.join(line for line in (ROOT / 'shell/PolkitDetails.js').read_text().splitlines()
                   if not line.startswith('.'))
script = r'''
const assert = require('node:assert/strict');
const Copy = {redacted: 'Hidden', unavailable: 'Not available',
    requesterUnavailable: 'Unknown program', messageLabel: 'Message', detailsLabel: 'Details from the program'};
SOURCE
const paths = {binDir: '/opt/emaki/bin', libDir: '/opt/emaki/lib/emaki',
    libexecDir: '/opt/emaki/libexec/emaki'};
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
console.log('PASS: fixed helper arguments, redaction, path boundaries, and rollback argv1 policy');
'''
subprocess.run(['node', '-e', script.replace('SOURCE', source)], check=True)
