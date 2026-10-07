#!/usr/bin/env python3
"""Production greeter.qml auth -> retained plate -> recorded launch/exit wiring.

Only compositor window plumbing and local metadata/artwork helpers are replaced
in an isolated COPY. Auth, state controller, greeter session/shared surfaces and all
production launch/error handlers remain real. No fixture API ships in the entry.
"""
from runtime_fixture import runtime_path
import importlib.util
import json
import os
from pathlib import Path
import re
import queue
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from PIL import Image, ImageChops
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
spec = importlib.util.spec_from_file_location('greetd_server', ROOT / 'tests/fixtures/greetd_server.py')
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)

STATE_STUB = '''import json, sys, time
from pathlib import Path
request = json.loads(sys.stdin.readline())
with (Path(__file__).parent.parent / "operations.jsonl").open("a") as record:
    record.write(json.dumps({"op": request["op"], "user": request.get("user"), "at": time.monotonic()}) + "\\n")
if request["op"] == "launch":
    time.sleep(RECORD_DELAY)
selection = (request["op"] == "load" and INITIAL_UNRESOLVED) or (request["op"] == "select" and request["user"] == "")
print(json.dumps(dict(version=1, ok=True, user="" if selection else "fixture-user",
    sessionId="" if selection else "SESSION_ID", command=[] if selection else ["/usr/bin/niri-session", "--fixture"],
    wallpaperRoot="" if selection else "/fixture/published/fixture-user", multipleUsers=MULTIPLE_USERS, message="")), flush=True)
'''


def core_limit():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def build(profile, scenario):
    qml = profile / 'qml'
    shutil.copytree(ROOT / 'shell', qml)
    multiple_users = scenario in ('escape-selected-two-users', 'escape-unresolved-users')
    unresolved = scenario == 'escape-unresolved-users'
    (qml / 'helpers/greeter-state.py').write_text(STATE_STUB.replace(
        'MULTIPLE_USERS', str(multiple_users)).replace('INITIAL_UNRESOLVED', str(unresolved)).replace(
            'RECORD_DELAY', '.35' if scenario == 'record-delay' else '0').replace(
                'SESSION_ID', 'niri.desktop' if scenario == 'stock-success' else 'niri-emaki.desktop'))
    (qml / 'helpers/lock-environment.py').write_text('import sys\nsys.stdin.buffer.read()\n')
    (qml / 'helpers/wallpaper.py').write_text('import json\nprint(json.dumps(dict(state="unavailable", texture="")))\n')
    entry = (ROOT / 'shell/greeter.qml').read_text()
    assert entry.count('PanelWindow {') == 1
    entry = entry.replace('import QtQuick\n', 'import QtQuick\nimport QtQuick.Window\nimport QtTest\nimport Quickshell.Io\n')
    entry = entry.replace('PanelWindow {', 'Window {')
    entry = entry.replace('model: Quickshell.screens', 'model: [null]')
    entry = entry.replace('required property ShellScreen modelData',
                          'width: 960\n            height: 640\n            visible: true\n            required property ShellScreen modelData')
    entry = entry.replace('            screen: modelData\n', '')
    entry = entry.replace('panel.screen === Quickshell.screens[0]', 'true')
    entry = entry.replace('id: lockSurface', 'id: fixtureSurface').replace('lockSurface.glassItem', 'fixtureSurface.glassItem')
    entry = entry.replace('id: quitCompositor', 'id: quitCompositor\n        onExited: Qt.quit()')
    entry = entry.replace('["emaki-greeter-run", "--success"]', '["/usr/bin/true"]')
    (profile / 'handoff').mkdir(mode=0o711)
    publisher = (qml / 'helpers/greeter-handoff.py').read_text().replace(
        "Path('/var/lib/emaki-greeter/handoff')", 'Path(' + repr(str(profile / 'handoff')) + ')')
    if scenario == 'prepare-failure':
        entry = entry.replace('root.launching = true;', 'root.launching = true; console.log("PREPARE_FAILURE_AT " + Date.now());')
        publisher = 'raise SystemExit(1)\n'
    (qml / 'helpers/greeter-handoff.py').write_text(publisher)
    entry = entry.replace('screen: panel.screen', 'screen: panel.modelData')
    entry = re.sub(r'            anchors \{\n(?:                [^\n]*\n)+            }\n', '', entry)
    entry = re.sub(r'            (?:exclusiveZone|exclusionMode|WlrLayershell\.[A-Za-z]+):[^\n]*\n', '', entry)
    entry = entry.replace('                LockSurface {\n', '''            TestCase {
                id: keyboard
                when: false
            }
            Component.onCompleted: {
                root.testKeyboard = keyboard;
                root.testSurface = fixtureSurface;
                panel.requestActivate();
            }
            LockSurface {
''')
    marker = entry.rfind('}')
    entry = entry[:marker] + '''
    FileView {
        id: cancelBarrier
        path: Quickshell.shellPath("cancel-now")
        blockLoading: true
        printErrors: false
    }
    property var testKeyboard: null
    property var testSurface: null
    Connections {
        target: lockSession
        function onPhaseChanged(): void {
            console.log("GREETER_ENTRY_PHASE " + lockSession.phase);
        }
        function onHandoffRequested(): void {
            console.log("GREETER_ENTRY_HANDOFF " + JSON.stringify({
                at: Date.now(), phase: lockSession.phase, idleMode: lockSession.idleMode,
                clock: lockSession.clock, introStart: lockSession.handoffIntroStart,
                authenticated: lockSession.authenticated,
                fieldsVisible: root.testSurface.fieldsVisible,
                fieldLife: root.testSurface.life,
                enabled: controller.enabled, ready: controller.fieldReady
            }));
        }
    }
    Connections {
        target: controller
        function onFatalError(): void {
            console.log("GREETER_ENTRY_FATAL_AT " + Date.now());
            console.log("GREETER_ENTRY_FATAL_STATE " + JSON.stringify({
                message: controller.message, kind: controller.messageKind,
                technicalFailures: controller._technicalFailures,
                pendingLength: controller._pendingPassword.length,
                bufferLength: controller.buffer.length
            }));
        }
    }
    Timer {
        interval: 20
        repeat: true
        running: true
        property int submitted: 0
        property bool cancelledCheck: false
        property int escapePresses: 0
        property string escapeBaseline: ""
        function check(ok: bool, label: string): void {
            if (!ok)
                throw new Error(label);
        }
        function screenState(): string {
            return JSON.stringify({
                user: controller.user,
                rememberedUser: memory.user,
                usernameMode: controller.usernameMode,
                wallpaperRoot: memory.wallpaperRoot,
                surfaceWallpaperRoot: root.testSurface.wallpaperRoot,
                visualWallpaperRoot: root.testSurface.visualItem.wallpaperRoot,
                sessionId: memory.sessionId,
                phase: lockSession.phase,
                selectionReady: controller.selectionReady,
                fatal: controller._fatal,
                launching: root.launching
            });
        }
        function checkUnchanged(): void {
            check(screenState() === escapeBaseline, "Escape changed account, wallpaper, phase, readiness or fatal state");
            check(controller.buffer.length === 0 && controller.dotCount === 0, "Escape left input behind");
            check(!controller.checking && !controller.queued && !controller._pendingPassword.length,
                  "Escape started or retained authentication");
            check(!fallbackNotice.running, "Escape scheduled fallback");
        }
        function exerciseEscape(): void {
            if (!escapeBaseline) {
                if (!root.testKeyboard || !root.testSurface || !root.testSurface.visualItem
                        || !controller.fieldReady || !controller.selectionReady)
                    return;
                check(controller.usernameMode === INITIAL_UNRESOLVED, "wrong initial field mode");
                check(controller.user === (INITIAL_UNRESOLVED ? "" : "fixture-user"), "wrong initial account");
                check(memory.multipleUsers === MULTIPLE_USERS, "wrong account-count fixture");
                check(memory.wallpaperRoot === (INITIAL_UNRESOLVED ? "" : "/fixture/published/fixture-user"),
                      "missing initial wallpaper root");
                check(lockSession.phase === "locked", "field ready before locked phase");
                // Exercise the production marker generator without authenticating
                // in this socket-free input scenario; only decoration is logged.
                console.log("GREETER_ENTRY_MARKER " + root.createHandoffMarker());
                escapeBaseline = screenState();
                // Real Qt events pass through the production LockInput handler.
                root.testKeyboard.keyClick(Qt.Key_Escape);
                checkUnchanged();
                root.testKeyboard.keyClick(Qt.Key_A);
                root.testKeyboard.keyClick(Qt.Key_B);
                check(controller.buffer.length === 2 && controller.dotCount === 2, "field did not accept input");
                root.testKeyboard.keyClick(Qt.Key_Escape);
                checkUnchanged();
            }
            checkUnchanged();
            // Repeated presses without release model a held key. Keep them going
            // beyond the old asynchronous selection plus 1.2-second fallback.
            root.testKeyboard.keyPress(Qt.Key_Escape);
            ++escapePresses;
            checkUnchanged();
            if (escapePresses >= 90) {
                root.testKeyboard.keyRelease(Qt.Key_Escape);
                checkUnchanged();
                console.log("GREETER_ENTRY_ESCAPE_DONE " + JSON.stringify({presses: escapePresses, state: JSON.parse(escapeBaseline)}));
                stop();
                Qt.exit(0);
            }
        }
        onTriggered: {
            if (controller.succeeded && (!(["melt", "handoff"].includes(lockSession.phase)) || controller.enabled || controller.fieldReady)) {
                console.error("GREETER_ENTRY_CHECK_FAILED accepted session drained or enabled input");
                stop();
                Qt.exit(2);
                return;
            }
            if (CANCEL_SCENARIO)
                cancelBarrier.reload();
            if (CANCEL_SCENARIO && !cancelledCheck && controller.checking
                    && cancelBarrier.text() === "go") {
                const oldAttempt = controller.attemptId;
                root.testKeyboard.keyClick(Qt.Key_Escape);
                if (controller.checking || controller.succeeded || controller._pendingPassword.length
                        || controller.buffer.length || controller.dotCount || controller.attemptId <= oldAttempt
                        || root.launching || fallbackNotice.running) {
                    console.error("GREETER_ENTRY_CHECK_FAILED active Escape retained or accepted its check");
                    stop();
                    Qt.exit(2);
                    return;
                }
                cancelledCheck = true;
                console.log("GREETER_ENTRY_CANCELLED");
                // Fast typing and Enter during the acknowledged cleanup barrier.
                controller.edit("fixture-éЖ🔒");
                controller.submit();
                if (!controller.queued || controller.buffer.length !== 12 || controller.checking) {
                    console.error("GREETER_ENTRY_CHECK_FAILED Escape lost fast retry input");
                    stop();
                    Qt.exit(2);
                }
            }
            if (ESCAPE_SCENARIO) {
                try {
                    exerciseEscape();
                } catch (error) {
                    console.error("GREETER_ENTRY_CHECK_FAILED " + error.message);
                    stop();
                    Qt.exit(2);
                }
                return;
            }
            if (!controller.fieldReady || !controller.selectionReady || controller._fatal)
                return;
            if (controller.usernameMode)
                return;
            if (submitted === 0 || (submitted < MAX_SUBMISSIONS && controller.messageKind === "technical"
                    && !controller.checking && !controller._recovering && !controller._fatal)) {
                if (controller.edit("fixture-éЖ🔒")) {
                    ++submitted;
                    controller.submit();
                }
            }
        }
    }
}
'''.replace('MAX_SUBMISSIONS', '3' if scenario == 'persistent-errors' else
            '2' if scenario == 'error-retry' else '1').replace(
                'ESCAPE_SCENARIO', 'true' if scenario.startswith('escape-') else 'false').replace(
                'CANCEL_SCENARIO', 'true' if scenario.startswith('cancel-') else 'false').replace(
                'INITIAL_UNRESOLVED', str(unresolved).lower()).replace(
                'MULTIPLE_USERS', str(multiple_users).lower())
    target = qml / 'check-entry.qml'
    target.write_text(entry)
    return target


def run(scenario, scripted=False):
    with tempfile.TemporaryDirectory(prefix='ge-', dir=CACHE) as work:
        profile = Path(work)
        for name in ('r', 'cache', 'config', 'data', 'state', 'tmp'):
            (profile / name).mkdir(mode=0o700)
        target = build(profile, scenario)
        if scripted:
            qml = profile / 'qml'
            (qml / 'helpers/greeter-auth.py').rename(qml / 'helpers/greeter-auth-real.py')
            shutil.copy(ROOT / 'tests/fixtures/greeter/auth_transport.py', qml / 'helpers/greeter-auth.py')
            (qml / 'fixture-mode').write_text(scenario)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   HOME=str(profile), TMPDIR=str(profile / 'tmp'), NIRI_SOCKET='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'GREETD_SOCK'):
            env.pop(name, None)
        server = None
        if scripted:
            env['GREETD_SOCK'] = 'fixture-only'
        elif scenario != 'missing-socket' and not scenario.startswith('escape-'):
            server = server_module.GreetdServer(runtime_path(profile) / 'g.sock')
            env['GREETD_SOCK'] = str(runtime_path(profile) / 'g.sock')
        log_path = profile / 'qs.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(['qs', '-p', str(target), '--no-color'], env=env,
                                       stdout=log, stderr=subprocess.STDOUT, preexec_fn=core_limit)
        try:
            accepted = None
            if server:
                created = server.request('create_session', timeout=8)
                assert created['username'] == 'fixture-user'
                if scenario == 'visible-prompt':
                    server.prompt('visible', 'Username:')
                    try:
                        cancellation = server.request('cancel_session', timeout=.5)
                        server.success(connection=cancellation['connection'])
                    except queue.Empty:
                        pass

                else:
                    if scenario.startswith('cancel-'):
                        original = server.auth_connection
                        if scenario == 'cancel-before-prompt':
                            (profile / 'qml/cancel-now').write_text('go')
                        if scenario == 'cancel-after-prompt':
                            server.prompt()
                            assert server.request('post_auth_message_response')['response_matches']
                            (profile / 'qml/cancel-now').write_text('go')
                        deadline = time.monotonic() + 3
                        while 'GREETER_ENTRY_CANCELLED' not in log_path.read_text() and time.monotonic() < deadline:
                            time.sleep(.01)
                        assert 'GREETER_ENTRY_CANCELLED' in log_path.read_text()
                        assert original not in server.closed, 'Escape closed a pending reply socket'
                        if scenario == 'cancel-after-prompt':
                            server.error('auth_error', 'late wrong verdict', connection=original)
                        else:
                            server.success(connection=original)
                        cancellation = server.request('cancel_session')
                        assert cancellation['connection'] == original
                        if 'automatic_error' not in cancellation:
                            server.success(connection=original)
                        assert not server.panicked
                        retried = server.request('create_session')
                        assert retried['connection'] != original
                    errors = 3 if scenario == 'persistent-errors' else 1 if scenario == 'error-retry' else 0
                    for error_number in range(errors):
                        server.prompt()
                        assert server.request('post_auth_message_response')['response_matches']
                        server.error('error', 'live configuring session')
                        cancellation = server.request('cancel_session')
                        assert cancellation['connection'] == server.auth_connection and server.configuring is None
                        server.success(connection=cancellation['connection'])
                        if error_number == 2:
                            break
                        retried = server.request('create_session', timeout=5)
                        assert retried['connection'] != cancellation['connection'] and 'automatic_error' not in retried
                    if scenario != 'persistent-errors':
                        server.prompt()
                        assert server.request('post_auth_message_response')['response_matches']
                        accepted = time.monotonic()
                        accepted_wall = time.time()
                        server.success()
                        start = server.request('start_session', timeout=8)
                        assert 0 <= start['received_at'] - accepted < 1.15, 'entry waited for a greeter drain'
                        assert start['cmd'] == ['/usr/bin/niri-session', '--fixture']
                        assert start['env'][0] == 'XDG_SESSION_TYPE=wayland'
                        marker = None
                        if scenario in ('stock-success', 'prepare-failure'):
                            assert start['env'] == ['XDG_SESSION_TYPE=wayland'], 'stock recovery must have no cover marker'
                        else:
                            assert len(start['env']) == 2 and start['env'][0] == 'XDG_SESSION_TYPE=wayland'
                            assert start['env'][1].startswith('EMAKI_LOGIN_HANDOFF=')
                            marker_text = start['env'][1].split('=', 1)[1]
                            marker = json.loads(marker_text)
                            assert set(marker) == {'v', 'token', 'createdMs', 'idleMode', 'clock', 'introStart'}
                            assert marker['v'] == 1 and re.fullmatch('[a-f0-9]{32}', marker['token'])
                            assert marker['idleMode'] in ('lake', 'letters', 'breath', 'wind')
                            assert isinstance(marker['clock'], (int, float)) and 0 <= marker['clock'] < 60
                            assert isinstance(marker['introStart'], (int, float)) and 0 <= marker['introStart'] <= marker['clock']
                            assert accepted_wall * 1000 - 10 <= marker['createdMs'] <= time.time() * 1000
                            assert not any(character.isspace() for character in marker_text), 'handoff marker must be compact'
                        operations = [json.loads(line) for line in (profile / 'qml/operations.jsonl').read_text().splitlines()]
                        assert [item['op'] for item in operations] == ['load', 'launch']
                        assert accepted <= operations[-1]['at'] <= start['received_at']
                        if scenario == 'record-delay':
                            assert start['received_at'] - operations[-1]['at'] >= .3, 'launch bypassed the state helper response'
                        handoff = re.search(r'GREETER_ENTRY_HANDOFF (\{[^\n]*\})', log_path.read_text())
                        assert handoff, 'no accepted handoff signal was emitted'
                        handoff = json.loads(handoff.group(1))
                        assert handoff['phase'] == 'handoff' and handoff['authenticated']
                        assert not handoff['enabled'] and not handoff['ready']
                        assert not handoff['fieldsVisible'] and handoff['fieldLife'] == 0
                        if marker is not None:
                            assert handoff['idleMode'] == marker['idleMode'] and abs(handoff['clock'] - marker['clock']) < .1
                            assert abs(handoff['introStart'] - marker['introStart']) < .001
                        server.success()
            expected = 0 if scenario in ('success', 'stock-success', 'prepare-failure', 'error-retry', 'record-delay', 'cancel-before-prompt', 'cancel-after-prompt') or scenario.startswith('escape-') else 1
            assert process.wait(timeout=8) == expected, log_path.read_text()
            if scripted:
                wire = [json.loads(line) for line in (profile / 'qml/wire.jsonl').read_text().splitlines()]
                starts = [item for item in wire if item['type'] == 'start_session']
                assert len(starts) == 1
                assert starts[0]['cmd'] == ['/usr/bin/niri-session', '--fixture']
                launch_env = starts[0]['env']
                assert launch_env[0] == 'XDG_SESSION_TYPE=wayland'
                if scenario in ('stock-success', 'prepare-failure'):
                    assert launch_env == ['XDG_SESSION_TYPE=wayland']
                else:
                    assert len(launch_env) == 2 and launch_env[1].startswith('EMAKI_LOGIN_HANDOFF=')
                    marker = json.loads(launch_env[1].split('=', 1)[1])
                    assert marker['v'] == 1 and re.fullmatch('[a-f0-9]{32}', marker['token'])
                creates = [item for item in wire if item['type'] == 'create_session']
                if scenario.startswith('cancel-'):
                    assert len(creates) == 2 and creates[0]['pid'] != creates[1]['pid']
                    assert starts[0]['pid'] == creates[1]['pid'], 'cancelled worker launched the session'
                else:
                    assert len(creates) == 1
                accepted = 0  # The metadata-only wire fixture confirmed accepted launch.
            if server:
                assert server.events.empty(), 'unexpected duplicate/cancel request'
            if accepted is None:
                operations = [json.loads(line) for line in (profile / 'qml/operations.jsonl').read_text().splitlines()]
                assert [item['op'] for item in operations] == ['load'], 'Escape must not request a user change'
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            if server:
                server.close()
        output = log_path.read_text()
        if scenario == 'prepare-failure':
            handoff_at = int(re.search(r'PREPARE_FAILURE_AT (\d+)', output).group(1))
            assert time.time()*1000 - handoff_at < 1500, 'prepare failure waited for the two-second capture limit'
        events = re.findall(r'EMAKI_SESSION_COVER (\{[^\n]*\})', output)
        assert all('token' not in json.loads(event) for event in events), 'handoff token entered journal'
        assert not any('GREETER_ENTRY_PHASE ' + phase in output for phase in ('drain', 'finished')), 'greeter ran the lock drain'
        if accepted is not None:
            assert output.count('GREETER_ENTRY_HANDOFF ') == 1, 'duplicate authenticated handoff'
            if scenario not in ('stock-success', 'prepare-failure'):
                full = profile / 'handoff' / (marker['token'] + '.png')
                plate = profile / 'handoff' / (marker['token'] + '-plate.png')
                assert full.exists() and plate.exists(), 'launch preceded saved handoff pictures'
                with Image.open(full) as image, Image.open(plate) as backing:
                    assert image.size == backing.size == (960, 640)
                    difference = ImageChops.difference(image, backing)
                    assert difference.getbbox() is not None, 'wordmark disappeared with controls'
                    assert difference.crop((280, 140, 680, 370)).getbbox() is None, 'password/avatar remained in final frame'
                assert 'GREETER_ENTRY_PHASE melt' in output
                assert 'GREETER_ENTRY_PHASE handoff' in output
        if expected == 1:
            notice = re.search(r'GREETER_ENTRY_FATAL_AT (\d+)', output)
            assert notice and time.time() - int(notice.group(1)) / 1000 >= 1.15, 'fatal error line was never presented'
            fatal = json.loads(re.search(r'GREETER_ENTRY_FATAL_STATE (\{[^\n]*\})', output).group(1))
            assert fatal['message'] == 'Authentication error. Opening another login screen.'
            assert fatal['kind'] == 'technical'
            assert fatal['technicalFailures'] == (3 if scenario == 'persistent-errors' else 0)
            assert fatal['pendingLength'] == fatal['bufferLength'] == 0
        if scenario.startswith('cancel-'):
            assert output.count('GREETER_ENTRY_CANCELLED') == 1 and 'GREETER_ENTRY_FATAL_' not in output
        if scenario.startswith('escape-'):
            assert 'GREETER_ENTRY_FATAL_' not in output, 'Escape triggered a fatal error or fallback'
            result = re.search(r'GREETER_ENTRY_ESCAPE_DONE (\{[^\n]*\})', output)
            assert result, 'entry exited before repeated Escape checks completed: ' + output
            result = json.loads(result.group(1))
            assert result['presses'] == 90
            state = result['state']
            unresolved = scenario == 'escape-unresolved-users'
            assert state['user'] == state['rememberedUser'] == ('' if unresolved else 'fixture-user')
            assert state['usernameMode'] == unresolved
            assert state['wallpaperRoot'] == state['surfaceWallpaperRoot'] == state['visualWallpaperRoot'] == (
                '' if unresolved else '/fixture/published/fixture-user')
            assert state['phase'] == 'locked' and state['selectionReady']
            assert not state['fatal'] and not state['launching']
            generated = re.search(r'GREETER_ENTRY_MARKER (\{[^\n]*\})', output)
            assert generated, 'production marker generator failed'
            generated = json.loads(generated.group(1))
            assert set(generated) == {'v', 'token', 'createdMs', 'idleMode', 'clock', 'introStart'}
            assert generated['v'] == 1 and re.fullmatch('[a-f0-9]{32}', generated['token'])
            assert generated['idleMode'] in ('lake', 'letters', 'breath', 'wind')
            assert 0 <= generated['clock'] < 60 and abs(time.time() * 1000 - generated['createdMs']) < 8000
            assert isinstance(generated['introStart'], (int, float)) and 0 <= generated['introStart'] <= generated['clock']
        assert 'fixture-éЖ' not in output, 'password entered the log'
        assert all(error not in output for error in ('ReferenceError', 'TypeError', 'Binding loop', 'GREETER_ENTRY_CHECK_FAILED')), output
        print('PASS: production greeter entry:', scenario, 'exit', expected)


run('missing-socket')
run('escape-selected-one-user')
run('escape-selected-two-users')
run('escape-unresolved-users')
for scripted_scenario in ('success', 'stock-success', 'prepare-failure', 'cancel-before-prompt', 'cancel-after-prompt'):
    run(scripted_scenario, scripted=True)
no_socket = False
with tempfile.TemporaryDirectory(prefix='gep-', dir=CACHE) as work:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(str(runtime_path(work) / 'g.sock'))
        except PermissionError:
            if os.environ.get('EMAKI_TEST_SANDBOX') != '1':
                raise
            no_socket = True
if no_socket:
    print('BLOCKED: explicit restricted entry subset; framed launch/retry scenarios require AF_UNIX bind')
else:
    for scenario in ('success', 'stock-success', 'record-delay', 'visible-prompt', 'error-retry', 'persistent-errors', 'cancel-before-prompt', 'cancel-after-prompt'):
        run(scenario)
