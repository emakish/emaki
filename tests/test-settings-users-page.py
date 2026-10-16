#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Users page against a private fixed-command fixture: list, add, edit, remove, refusals.

A fake emaki-settings-users on PATH keeps accounts in a JSON file, records every argument list
and what arrived on stdin, and can answer one request with a refusal or a broken reply. The
page runs offscreen in Quickshell; no account of this computer is read or changed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HEADER = '// Copyright (C) 2026 Artur Yakymenko\n// SPDX-License-Identifier: GPL-3.0-or-later\n'
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
base = Path(os.environ['USERS_FIXTURE'])
state = json.loads((base / 'state.json').read_text())
args = sys.argv[1:]
if args and args[-1] == '--json':
    args.pop()
# The window must never send a password: whatever arrives on stdin is recorded.
import select
secret = os.read(0, 4096).decode() if select.select([0], [], [], 0.2)[0] else ''
terminal = args[0] in ('add', 'set-password') and args[-1] == '--terminal'
if terminal:
    args.pop()
with (base / 'calls.log').open('a') as log:
    log.write(json.dumps({'args': args + (['--terminal'] if terminal else []), 'stdin': secret}) + '\n')
# Replies forced for one request each, in order: [[args, mode]] with mode refuse:REASON or bad.
script = state.get('script', [])
forced = script.pop(0)[1] if script and script[0][0] == args else ''
if os.environ.get('USERS_LIFETIME') and args[0] != 'status':
    time.sleep(2.5)
users = state['users']
def reply(status, code=0, **fields):
    body = {'schema_version': 2 if forced == 'schema' else 1, 'status': status, **fields}
    if status in ('read', 'applied'):
        body['users'] = users
        body['administrators'] = sum(1 for user in users if user['administrator'] and not user['locked'])
    (base / 'state.json').write_text(json.dumps(state))
    print('{broken' if forced == 'bad' else json.dumps(body))
    sys.exit(code)
if forced.startswith('refuse:'):
    reply('rejected', 1, reason=forced[7:], action=args[0])
if args[0] == 'status':
    reply('read')
if args[0] == 'add':
    users.append({'login': args[1], 'name': args[2], 'uid': 1000 + len(users), 'administrator': args[4] == 'yes',
                  'locked': False, 'current': False, 'signed_in': False, 'home': '/home/' + args[1]})
elif args[0] == 'remove':
    users[:] = [user for user in users if user['login'] != args[1]]
elif args[0] == 'set-name':
    next(user for user in users if user['login'] == args[1])['name'] = args[2]
elif args[0] == 'set-administrator':
    next(user for user in users if user['login'] == args[1])['administrator'] = args[2] == 'yes'
elif args[0] == 'picture':
    next(user for user in users if user['current'])['picture'] = args[1] == '--set'
reply('applied', action=args[0])
'''
# The private chooser operation (helpers/launcher-tools.py): answers from chooser.json in order.
CHOOSER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
base = Path(os.environ['USERS_FIXTURE'])
request = json.loads(sys.stdin.readline())
with (base / 'calls.log').open('a') as log:
    log.write(json.dumps({'args': ['chooser', request.get('op')], 'stdin': ''}) + '\n')
states = json.loads((base / 'chooser.json').read_text())
(base / 'chooser.json').write_text(json.dumps(states[1:]))
print(json.dumps({'schema_version': 1, 'state': states[0] if states else 'ready',
                  'path': str(base / 'chosen picture.png')}))
'''
USERS = [
    {'login': 'ada', 'name': 'Ada', 'uid': 1000, 'administrator': True, 'locked': False, 'current': True,
     'signed_in': True, 'home': '/home/ada'},
    {'login': 'bo', 'name': '', 'uid': 1001, 'administrator': False, 'locked': False, 'current': False,
     'signed_in': True, 'home': '/home/bo'},
    {'login': 'cy', 'name': '', 'uid': 1002, 'administrator': False, 'locked': False, 'current': False,
     'signed_in': False, 'home': '', 'available': False},
]
SCRIPT = [[['set-administrator', 'ada', 'no'], 'refuse:last_administrator'],
          [['set-name', 'bo', 'Bo'], 'refuse:authorization_refused'], [['set-name', 'bo', 'Bo'], 'bad'], [['status'], 'schema']]
TEST = HEADER + r'''import QtQuick
import Quickshell
import "settings"
Scope {
 Item { width: 760; SettingsUsersPage { id: page; width: 740 } }
 function find(item, name) {
  if (item.objectName === name) return item;
  for (let child of item.children || []) { const found = find(child, name); if (found) return found; }
  return null;
 }
 function check(ok, message) { if (!ok) { console.error("FAILED", message); Qt.exit(1); } }
 Timer {
  property int step: 0
  interval: 500; repeat: true; running: true
  onTriggered: {
   if (page.pending || page.pictureChooser.busy) return;
   if (step === 0) {
    check(page.received && page.accounts.length === 3, "accounts read");
    check(page.me.login === "ada" && find(page, "settings-users-account").title === "Ada", "your account");
    check(find(page, "settings-users-account").explanation === "Administrator · this computer", "approved account line");
    check(find(page, "settings-users-row-bo").title === "bo", "a person without a full name shows the login");
    check(find(page, "settings-users-row-bo").explanation.indexOf("Signed in") >= 0, "signed in state");
    check(find(page, "settings-users-row-cy").explanation.indexOf("until the computer restarts") >= 0 && !find(page, "settings-users-change-cy").enabled, "an account the service cannot change");
    find(page, "settings-users-change-ada").clicked();
    check(page.editing === "ada" && find(page, "settings-users-edit-name").text === "Ada", "edit own account");
    check(find(page, "settings-users-administrator").unavailable, "the only administrator keeps the role");
    check(!find(page, "settings-users-remove").visible || !find(page, "settings-users-remove").parent.visible, "own account cannot be removed");
    find(page, "settings-users-change-bo").clicked();
    check(page.editing === "bo" && !find(page, "settings-users-remove").enabled, "a signed-in account cannot be removed");
    check(find(page, "settings-users-remove-explanation").text.indexOf("sign out") >= 0, "sign out explanation");
    find(page, "settings-users-done").clicked();
    find(page, "settings-users-add-open").clicked();
    check(page.adding, "add form");
    for (const theme of ["dark", "light"]) {
     SettingsBridge.values = [{key: "appearance.theme", value: theme}];
     check(Appearance.dark === (theme === "dark"), "explicit " + theme + " theme");
     for (const name of ["add-name", "add-login", "edit-name"]) {
      const field = find(page, "settings-users-" + name);
      check(field.background.color.toString() === SettingsTheme.field.toString(), theme + " " + name + " background");
      check(field.color.toString() === SettingsTheme.ink.toString(), theme + " " + name + " text");
      check(field.placeholderTextColor.toString() === SettingsTheme.faint.toString(), theme + " " + name + " placeholder");
      check(field.selectionColor.toString() === SettingsTheme.accent.toString(), theme + " " + name + " selection");
      check(field.selectedTextColor.toString() === SettingsTheme.accentText.toString(), theme + " " + name + " selected text");
     }
    }
    find(page, "settings-users-add-name").text = "Dee Person";
    find(page, "settings-users-add-login").text = "Dee";
    check(!find(page, "settings-users-add-confirm-button").enabled, "invalid login blocks adding");
    find(page, "settings-users-add-login").text = "bo";
    check(!find(page, "settings-users-add-confirm-button").enabled, "a taken login blocks adding");
    find(page, "settings-users-add-login").text = "dee";
    find(page, "settings-users-add-name").text = "Dee: Person";
    check(!find(page, "settings-users-add-confirm-button").enabled, "a colon blocks adding");
    find(page, "settings-users-add-name").text = "Dee Person";
    find(page, "settings-users-add-administrator").checked = true;
    check(find(page, "settings-users-add-confirm-button").enabled, "valid form");
    find(page, "settings-users-add-confirm-button").clicked();
    check(page.message.indexOf("terminal window") >= 0 && !page.problem, "the password is asked in the terminal");
   } else if (step === 1) {
    check(!page.adding && page.accounts.some(user => user.login === "dee" && user.administrator), "account added");
    check(page.message === "", "no message after adding");
    find(page, "settings-users-change-dee").clicked();
    find(page, "settings-users-edit-name").text = "Dee P.";
    find(page, "settings-users-save-name").clicked();
   } else if (step === 2) {
    check(page.editing === "dee" && page.edited.name === "Dee P.", "name changed, editor stays");
    find(page, "settings-users-set-password").clicked();
   } else if (step === 3) {
    check(page.message === "Password changed." && !page.problem, "password notice");
    find(page, "settings-users-administrator").valueRequested(false);
   } else if (step === 4) {
    check(!page.edited.administrator, "administrator role removed");
    find(page, "settings-users-remove").clicked();
    check(find(page, "settings-users-remove-explanation").text.indexOf("/home/dee") >= 0, "home folder named");
    find(page, "settings-users-remove-delete").clicked();
   } else if (step === 5) {
    check(page.editing === "" && !page.accounts.some(user => user.login === "dee"), "account removed");
    page.run(["set-administrator", "ada", "no"]);
   } else if (step === 6) {
    check(page.problem && page.message.indexOf("at least one administrator") >= 0, "refusal shown");
    check(page.accounts.length === 3, "refusal keeps the list");
    page.run(["set-name", "bo", "Bo"]);
   } else if (step === 7) {
    check(page.message.indexOf("authentication was cancelled") >= 0, "cancelled authentication");
    page.run(["set-name", "bo", "Bo"]);
   } else if (step === 8) {
    check(page.message.indexOf("could not be confirmed") >= 0, "broken reply");
    page.refresh();
   } else if (step === 9) {
    check(page.message.indexOf("Accounts are unavailable") >= 0, "unknown schema");
    page.refresh();
   } else if (step === 10) {
    check(page.message === "" && page.accounts.length === 3, "refresh");
    find(page, "settings-users-own-password").clicked();
   } else if (step === 11) {
    if (SettingsUsersService.passwordPending) return;
    check(page.message === "Password changed." && !page.problem, "own password terminal receipt");
    page.run(["add", "dee", "Dee", "--administrator", "no", "--terminal"]);
   } else if (step === 12) {
    find(page, "settings-users-change-dee").clicked();
    find(page, "settings-users-remove").clicked();
    find(page, "settings-users-remove-keep").clicked();
   } else if (step === 13) {
    check(!page.accounts.some(user => user.login === "dee"), "keep-home removal");
    page.run(["set-name", "bo", "--terminal"]);
    check(page.message === "", "a flag-shaped name does not select terminal mode");
   } else if (step === 14) {
    check(page.accounts.find(user => user.login === "bo").name === "--terminal", "literal terminal flag name");
    page.run(["set-name", "bo", "--json"]);
   } else if (step === 15) {
    check(page.accounts.find(user => user.login === "bo").name === "--json", "literal json flag name");
    const picture = find(page, "settings-users-picture");
    check(picture && picture.title === "Picture" && picture.explanation === "Shown in Settings, on the lock screen and on the login screen.", "picture row");
    check(picture.parent === find(page, "settings-users-account").parent && picture.parent === find(page, "settings-users-password").parent, "the picture row is in Your account");
    check(find(page, "settings-users-account").y < picture.y && picture.y < find(page, "settings-users-password").y, "between the account row and Password");
    check(find(page, "settings-users-picture-choose").text === "Choose picture…", "choose button");
    check(!find(page, "settings-users-picture-remove").visible, "nothing to remove without a picture");
    find(page, "settings-users-picture-choose").clicked();
    check(page.pictureChooser.state === "pending", "choosing");
   } else if (step === 16) {
    check(page.me.picture === true && page.message === "" && !page.problem, "picture set");
    check(find(page, "settings-users-picture-remove").visible && find(page, "settings-users-picture-remove").text === "Remove", "remove button");
    check(!find(page, "settings-users-picture-error").visible, "no chooser error");
    find(page, "settings-users-picture-remove").clicked();
   } else if (step === 17) {
    check(page.me.picture === false && !find(page, "settings-users-picture-remove").visible, "picture removed");
    find(page, "settings-users-picture-choose").clicked();
   } else if (step === 18) {
    const error = find(page, "settings-users-picture-error");
    check(error.visible && error.text === "Could not choose a picture. Your picture has not changed.", "chooser failure shown");
    find(page, "settings-users-picture-choose").clicked();
    check(!error.visible, "a new choice clears the failure");
   } else if (step === 19) {
    check(!find(page, "settings-users-picture-error").visible && page.me.picture === false, "a cancelled choice changes nothing");
    console.log("USERS_PAGE_PASS"); Qt.quit();
   }
   step++;
  }
 }
}'''
MISSING = HEADER + r'''import QtQuick
import Quickshell
Scope {
 Item { SettingsUsersPage { id: page; width: 740 } }
 Timer { interval: 900; running: true; onTriggered: {
  if (page.message.indexOf("Accounts are unavailable") >= 0 && !page.received) { console.log("USERS_PAGE_PASS"); Qt.quit(); }
  else { console.error("FAILED missing command", page.message); Qt.exit(1); }
 } }
}'''


def run(test, mutation=None, missing=False, lifetime=False):
    with tempfile.TemporaryDirectory(prefix='settings-users-page-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        if mutation:
            path = base / ('shell/SettingsUsersService.qml' if mutation[0].startswith(('result.schema', 'op: ', 'pictureError =')) else 'shell/SettingsUsersPage.qml')
            text = path.read_text()
            assert mutation[0] in text, mutation
            path.write_text(text.replace(mutation[0], mutation[1]))
        (base / 'bin').mkdir()
        (base / 'runtime').mkdir(mode=0o700)
        (base / 'state.json').write_text(json.dumps({'users': USERS, 'script': [] if lifetime else SCRIPT}))
        if not lifetime:
            # The whole Settings window of the lifetime run needs the real private operations.
            (base / 'shell/helpers/launcher-tools.py').write_text(CHOOSER)
            (base / 'chooser.json').write_text(json.dumps(['ready', 'portal_failed', 'cancelled']))
        if not missing:
            fake = base / 'bin/emaki-settings-users'
            fake.write_text(FAKE)
            fake.chmod(0o755)
        terminal = base / 'bin/emaki-terminal'
        terminal.write_text("#!/usr/bin/env python3\nimport json, os, time\nfrom pathlib import Path\nbase = Path(os.environ['USERS_FIXTURE'])\nwith (base / 'calls.log').open('a') as log: log.write(json.dumps({'args': ['own-password'], 'stdin': ''}) + '\\n')\nif os.environ.get('USERS_LIFETIME'): time.sleep(2.5)\nprint(json.dumps({'schema_version': 1, 'status': 'changed', 'message': 'Password changed.'}))\n")
        terminal.chmod(0o755)
        if lifetime:
            # Native windows need a compositor; keep the production service/controller/outputs.
            for name in ('PolkitPromptSurface', 'SudoPromptService'):
                (base / 'shell' / (name + '.qml')).write_text(HEADER +
                    'import QtQuick\nimport Quickshell\nScope { property var controller; property var scene; property var outputs; property bool locked; property bool polkitBusy }\n')
        (base / 'shell/Test.qml').write_text(test)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(base / 'runtime'), HOME=str(base),
                   PATH=str(base / 'bin') + ':' + os.environ['PATH'], USERS_FIXTURE=str(base),
                   USERS_LIFETIME='1' if lifetime else '',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        env.pop('WAYLAND_DISPLAY', None)
        env.pop('DISPLAY', None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/Test.qml'), '--no-color'], stdin=subprocess.DEVNULL,
                                env=env, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        passed = result.returncode == 0 and 'USERS_PAGE_PASS' in output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            passed = passed and error not in output
        calls = [json.loads(line) for line in (base / 'calls.log').read_text().splitlines()] \
            if (base / 'calls.log').exists() else []
        return passed, output, calls


EXPECTED = [['add', 'dee', 'Dee Person', '--administrator', 'yes', '--terminal'], ['set-name', 'dee', 'Dee P.'],
            ['set-password', 'dee', '--terminal'], ['set-administrator', 'dee', 'no'], ['remove', 'dee', '--home', 'delete'],
            ['set-administrator', 'ada', 'no'], ['set-name', 'bo', 'Bo'], ['set-name', 'bo', 'Bo'],
            ['own-password'], ['add', 'dee', 'Dee', '--administrator', 'no', '--terminal'], ['remove', 'dee', '--home', 'keep'],
            ['set-name', 'bo', '--terminal'], ['set-name', 'bo', '--json'],
            ['chooser', 'account-picture-choose'], ['picture', '--set', 'chosen picture.png'], ['picture', '--clear'],
            ['chooser', 'account-picture-choose'], ['chooser', 'account-picture-choose']]


def requests_ok(calls):
    """Exactly the expected changes; the window never sends a password."""
    changes = [call['args'] for call in calls if call['args'][0] != 'status']
    # The chosen path lives in the temporary fixture folder.
    changes = [args[:2] + [Path(args[2]).name] if args[:2] == ['picture', '--set'] else args for args in changes]
    return changes == EXPECTED and all(call['stdin'] == '' for call in calls)


passed, output, calls = run(TEST)
assert passed, output
assert requests_ok(calls), calls
passed, output, _ = run(MISSING, missing=True)
assert passed, output
for mutation in (('unavailable: root.lastAdministrator(root.edited)', 'unavailable: false'),
                 ('enabled: !root.pending && !!root.edited && !root.edited.signed_in', 'enabled: !root.pending && !!root.edited'),
                 ('result.schema_version !== 1 ||', ''),
                 ('root.run(["set-password", root.edited.login, "--terminal"])', 'root.run(["set-password", root.edited.login])'),
                 ('visible: root.me?.picture === true', 'visible: true'),
                 ('op: "account-picture-choose"', 'op: "wallpaper-choose"'),
                 ('pictureError = "";\n        pictureChooser.start(', 'pictureChooser.start(')):
    passed, output, calls = run(TEST, mutation)
    assert not (passed and requests_ok(calls)), 'Surviving mutation: ' + str(mutation)
print('PASS: users page fields in dark and light themes, list, add, name, password, role, removal with home choice, refusals, '
      'broken and missing helper, own picture: choose, remove, chooser failure and cancel; seven mutants killed')

passed, output, calls = run((ROOT / 'tests/fixtures/SettingsUsersLifetime.qml').read_text(), lifetime=True)
assert passed, output
assert [call['args'] for call in calls if call['args'][0] != 'status'] == [
    ['set-name', 'bo', 'Bo'], ['set-administrator', 'bo', 'yes'], ['set-password', 'bo', '--terminal'],
    ['own-password'], ['remove', 'bo', '--home', 'keep']], calls
assert all(call['stdin'] == '' for call in calls), calls
print('PASS: authentication close-all unloads the real Settings Loader; name, role, both password '
      'receipts and keep-home removal survive close/reopen')
