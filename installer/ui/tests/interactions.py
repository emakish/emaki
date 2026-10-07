#!/usr/bin/env python3
"""Real pointer/key events in the production view; offscreen and isolated.

Runs at every window size in SIZES by default: the short- and tall-window checks in
InteractionTest.qml only run at 640 and 886 pixels high. --size WxH runs one size.
KeyboardTest.qml (the live keyboard behind the password fields), PasteTest.qml (no paste
into the account and disk passwords) and CopyTest.qml (no copy out of them) run once, at 1024x700.
"""
import argparse
import json
import sys
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer.inventory import encrypted_warning, unconfirmed_identity_warning

SIZES = ['960x640', '1024x700', '1280x800', '1366x768', '1536x886']
WIFI_NETWORKS = [dict(ssid=f'Apartment {index:02}', bssid=f'02:00:00:00:00:{index:02x}',
                      device='wlan0', strength=95 - index, security={21: 'OWE', 22: 'OWE-TM', 23: ''}.get(index, 'WPA2'),
                      connected=index == 1, enterprise=index == 2) for index in range(25)]


def size(text):
    if not re.fullmatch(r'[1-9]\d*x[1-9]\d*', text):
        raise argparse.ArgumentTypeError('expected WIDTHxHEIGHT, for example 1024x700')
    return text


def run(width, height, test='InteractionTest', marker='INTERACTION_OK', scale=1):
    with tempfile.TemporaryDirectory(prefix='emaki-interactions-') as temporary, \
            tempfile.TemporaryDirectory(prefix='eir-', dir='/tmp') as runtime:
        root = Path(temporary)
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        entry = (root / f'ui/tests/{test}.qml').read_text().replace('".." as UI', '"." as UI').replace('"FakeNiri.js"', '"tests/FakeNiri.js"')
        entry = entry.replace('"__ENCRYPTED_WARNING__"', json.dumps(encrypted_warning('/dev/vda', 'crypto_LUKS')))
        entry = entry.replace('"__UNIDENTIFIED_WARNING__"', json.dumps(unconfirmed_identity_warning('/dev/vda')))
        entry = entry.replace('"__APFS_WARNING__"', json.dumps(encrypted_warning('/dev/vda', 'apfs')))
        entry = entry.replace('["__ENCRYPTED_CASES__"]', json.dumps([
            dict(path=f'/dev/vd{letter}1', type=kind, uuid=f'{kind}-test',
                 warning=encrypted_warning(f'/dev/vd{letter}1', kind))
            for letter, kind in zip('abcd', ('crypto_LUKS', 'BitLocker', 'cs_fvault2', 'apfs'))
        ]))
        entry = entry.replace('["__WIFI_NETWORKS__"]', json.dumps(WIFI_NETWORKS))
        (root / 'ui/interaction-test.qml').write_text(entry)
        if test == 'InteractionTest':
            # Only the external helper is replaced; production view/controller handle real events.
            (root / 'ui/ui-helper.py').write_text(
                'import json, sys, time\n'
                'from pathlib import Path\n'
                'request = json.loads(sys.stdin.readline())\n'
                f'networks = {WIFI_NETWORKS!r}\n'
                'if request["op"] == "join":\n'
                '    opened = request == dict(op="join", device="wlan0", bssid=request.get("bssid"), password="") and request.get("bssid") in ("02:00:00:00:00:15", "02:00:00:00:00:16", "02:00:00:00:00:17")\n'
                '    secured = request.get("bssid") in ("02:00:00:00:00:00", "02:00:00:00:00:18", "02:00:00:00:01:18") and request.get("password") in ("abc", "xyz") and request.get("device") == "wlan0"\n'
                '    message = "Could not connect. Check the password." if secured or opened else "Unexpected join payload"\n'
                '    print(json.dumps(dict(ok=False, message=message)))\n'
                'else:\n'
                '    counter = Path(__file__).with_name("fixture-scan-count")\n'
                '    count = int(counter.read_text()) + 1 if counter.exists() else 1\n'
                '    counter.write_text(str(count))\n'
                '    for network in networks:\n'
                '        network["strength"] -= count % 7 + 1\n'
                '    networks[24]["bssid"] = "02:00:00:00:%02x:18" % (count % 2)\n'
                '    time.sleep(0.65)\n'
                '    print(json.dumps(dict(ok=True, wired=False, networks=networks, fixtureScan=True, fixtureScanCount=count)))\n')
        for filename in (root / 'ui').glob('*.qml'):
            filename.write_text(filename.read_text().replace('"file:///usr/share/emaki/shell"', '"file://' + str(ROOT / 'shell') + '"'))
        for name in ('cache', 'config', 'state', 'data'):
            (root / name).mkdir(mode=0o700)
        env = dict(os.environ, QT_SCALE_FACTOR=str(scale), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=runtime,
                   XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                   EMAKI_INSTALLER_SOCKET=str(Path(runtime) / 'absent-worker.sock'), EMAKI_INSTALLER_WIDTH=str(width), EMAKI_INSTALLER_HEIGHT=str(height))
        for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(root / 'ui/interaction-test.qml'), '--no-color'], env=env,
                                text=True, capture_output=True, timeout=180)
        log = result.stdout + result.stderr
        name = {'InteractionTest': 'interactions', 'KeyboardTest': 'keyboard', 'PasteTest': 'paste', 'CopyTest': 'copy'}[test]
        output = UI / f'tests/artifacts/{name}-{width}x{height}-scale{scale}.log'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(log.replace(str(ROOT), '<checkout>'))
        assert result.returncode == 0 and marker in log, log
        for diagnostic in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'FAIL!', 'Binding loop', 'Unable to assign'):
            assert diagnostic not in log, log
        if test == 'InteractionTest':
            print(f'PASS real pointer/key events at {width}x{height}: Wi-Fi selection, focus, Enter and failed joins; encrypted warning viewport and focus, map, search, software and all password toggles', flush=True)
        elif test == 'KeyboardTest':
            print(f'PASS live keyboard at {width}x{height}: ' + log.split(marker, 1)[1].splitlines()[0].strip(), flush=True)
        elif test == 'PasteTest':
            print(f'PASS no paste into a password field at {width}x{height}: ' + log.split(marker, 1)[1].splitlines()[0].strip(), flush=True)
        else:
            print(f'PASS no copy out of a password field at {width}x{height}: ' + log.split(marker, 1)[1].splitlines()[0].strip(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', type=size, action='append', help='WIDTHxHEIGHT; repeatable (default: ' + ', '.join(SIZES) + ')')
    parser.add_argument('--keyboard', action='store_true', help='run only KeyboardTest.qml, PasteTest.qml and CopyTest.qml')
    args = parser.parse_args()
    if not args.keyboard:
        for text in args.size or SIZES:
            width, height = map(int, text.split('x'))
            run(width, height)
        if not args.size:
            run(960, 520, scale=1.75)  # Small window inside a 1080p work area at 175%.
            run(1536, 864, scale=1.25)  # 1920×1080 physical pixels at fractional scale.
    run(1024, 700, 'KeyboardTest', 'KEYBOARD_OK')
    run(1024, 700, 'PasteTest', 'PASTE_OK')
    run(1024, 700, 'CopyTest', 'COPY_OK')


if __name__ == '__main__':
    main()
