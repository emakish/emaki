#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the real panel action and service with an isolated, fixed helper fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
COMMAND = '["pkexec", Platform.libDir + "/emaki-wifi-recover", "restart"]'
QML = '''import QtQuick
import Quickshell
ShellRoot {
    id: root
    SystemBackend {
        id: fx
        networkReady: true; wifiEnabled: true; wifiHardwareEnabled: true
        wifiDevices: [{connected: false}]
    }
    SystemService { id: svc; backend: fx; helpersEnabled: true }
    NiriService { id: compositor; binary: "/usr/bin/true" }
    SystemBody { id: body; service: svc; niri: compositor; page: "wifi"; opened: true; width: 480 }
    function require(ok, message) { if (!ok) throw new Error(message); }
    function find(item) {
        if (item.objectName === "wifiRestartAction") return item;
        for (const child of item.children ?? []) { const found = find(child); if (found) return found; }
        return null;
    }
    property int step: 0
    property int ticks: 0
    Timer {
        interval: 40; repeat: true; running: true
        onTriggered: {
            try {
                root.require(++root.ticks < 200, "scenario timed out");
                root.require(svc.status().wifi_recovery_available === true, "missing capability status");
                root.require(svc.status().wifi_recovery_action === fx.wifiRestartAvailable, "eligibility status");
                root.require(svc.status().wifi_recovery_running === svc.wifiRestartRunning, "running status");
                const button = root.find(body);
                root.require(button !== null, "missing recovery action");
                if (root.step === 0) {
                    root.require(button.label === "Restart Wi-Fi" && button.visible, "empty scan action");
                    fx.networks = [{known: true, signal: 0}];
                    root.require(fx.wifiRestartAvailable, "saved absent network hides recovery");
                    fx.networks = [{connected: true, signal: 0}];
                    root.require(!fx.wifiRestartAvailable && !svc.act("wifi-restart", null), "connected network reset");
                    fx.networks = [{busy: true, signal: 0}];
                    root.require(!fx.wifiRestartAvailable, "joining network reset");
                    fx.networks = [{signal: 42}];
                    root.require(!fx.wifiRestartAvailable, "successful scan reset");
                    fx.networks = [];
                    fx.wifiDevices = [{connected: true}];
                    root.require(!fx.wifiRestartAvailable && !svc.act("wifi-restart", null), "working device reset");
                    fx.wifiDevices = [{connected: false}];
                    fx.wifiHardwareEnabled = false;
                    root.require(!fx.wifiRestartAvailable, "hardware disabled reset");
                    fx.wifiHardwareEnabled = true;
                    fx.wifiEnabled = false;
                    root.require(!fx.wifiRestartAvailable, "radio disabled reset");
                    fx.wifiEnabled = true;
                    button.clicked();
                    root.require(svc.wifiRestartRunning, "button did not start helper");
                    root.require(!svc.act("wifi-restart", null), "duplicate helper launch");
                    root.step++;
                } else if (root.step === 1 && !svc.wifiRestartRunning) {
                    root.require(svc.actionState === "requested", "reset success promised recovery");
                    root.require(body.message === "Requested.", "success status");
                    button.clicked(); root.step++;
                } else if (root.step === 2 && !svc.wifiRestartRunning) {
                    root.require(svc.actionState === "wifi_restart_failed", "backoff result ignored");
                    root.require(body.message === "Wi-Fi could not restart. Try again later.", "failure status");
                    button.clicked(); root.step++;
                } else if (root.step === 3 && !svc.wifiRestartRunning) {
                    root.require(svc.actionState === "wifi_restart_failed", "helper failure ignored");
                    console.info("WIFI_RECOVERY_PASS"); Qt.quit();
                }
            } catch (error) { console.error("WIFI_RECOVERY_FAIL " + error); Qt.quit(); }
        }
    }
}
'''


def run(mutant=False, failure=None):
    with tempfile.TemporaryDirectory(prefix='wr-', dir='/tmp') as directory:
        base = Path(directory)
        shell = base/'shell'
        shutil.copytree(ROOT/'shell', shell)
        for name in ('runtime', 'config', 'cache', 'state', 'data'):
            (base/name).mkdir(mode=0o700)
        helper = base/'reset'
        helper.write_text('#!/usr/bin/python3\nimport json,sys,time\nfrom pathlib import Path\np=Path('+repr(str(base/'calls'))+')\nc=p.read_text().splitlines() if p.exists() else []\np.write_text("\\n".join(c+[json.dumps(sys.argv[1:])])+"\\n")\ntime.sleep(.15)\nsys.exit([0,75,1][len(c)])\n')
        helper.chmod(0o700)
        if failure == 'missing':
            helper.unlink()
        elif failure == 'hung':
            helper.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(20)\n')
        service = shell/'SystemService.qml'
        source = service.read_text()
        assert COMMAND in source, 'privileged command changed'
        service.write_text(source.replace(COMMAND, COMMAND.replace('"pkexec"', json.dumps(str(helper)))))
        # Other page probes are harmless fixtures too; no host service is contacted.
        (shell/'helpers/system-tools.py').write_text('import json\nprint(json.dumps({"state":"unavailable"}))\n')
        if mutant:
            backend = shell/'SystemBackend.qml'
            source = backend.read_text()
            assert '!wifiDevices.some(d => d.connected)' in source
            backend.write_text(source.replace('!wifiDevices.some(d => d.connected)', 'true'))
        scenario = QML
        if failure:
            start = scenario.index('                } else if (root.step === 1')
            end = scenario.index('            } catch (error)', start)
            scenario = scenario[:start] + '''                } else if (root.step === 1 && !svc.wifiRestartRunning) {
                    root.require(svc.actionState === "wifi_restart_failed", "failed helper kept pending");
                    console.info("WIFI_RECOVERY_PASS"); Qt.quit();
                }
''' + scenario[end:]
            if failure == 'hung':
                scenario = scenario.replace('                    root.step++;',
                    '                    root.require(svc.act("lock", null), "authorization blocked panel lock");\n'
                    '                    root.step++;', 1)
            source = service.read_text()
            if failure == 'missing':
                source = source.replace('interval: 3000', 'interval: 200')
            else:
                source = source.replace('interval: 90000', 'interval: 400')
            service.write_text(source)
        (shell/'shell.qml').write_text(scenario)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   XDG_RUNTIME_DIR=str(base/'runtime'), XDG_CONFIG_HOME=str(base/'config'), XDG_CACHE_HOME=str(base/'cache'),
                   XDG_STATE_HOME=str(base/'state'), XDG_DATA_HOME=str(base/'data'), XDG_DATA_DIRS=str(base/'data'),
                   EMAKI_UDEVADM='/usr/bin/true', EMAKI_NMCLI='/usr/bin/true', EMAKI_SHELL_TRAY='0',
                   EMAKI_SHELL_NOTIFICATIONS='0', DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(base/'missing'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path='+str(base/'missing-session'),
                   PIPEWIRE_REMOTE='missing', NIRI_SOCKET='', EMAKI_SETTINGS_PROFILE='')
        for key in ('DISPLAY', 'WAYLAND_DISPLAY'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(shell), '--no-color'], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        passed = result.returncode == 0 and 'WIFI_RECOVERY_PASS' in result.stdout
        if mutant:
            assert not passed and 'working device reset' in result.stdout, result.stdout
        else:
            assert passed, result.stdout
            if failure:
                return
            assert [json.loads(line) for line in (base/'calls').read_text().splitlines()] == [
                ['/usr/lib/emaki/emaki-wifi-recover', 'restart']] * 3


if __name__ == '__main__':
    run()
    run(mutant=True)
    run(failure='missing')
    run(failure='hung')
    print('Wi-Fi recovery shell: PASS (panel, helper, outcomes, failed start, deadline, working-device mutant)')
