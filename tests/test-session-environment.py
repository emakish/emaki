#!/usr/bin/env python3
"""Exercise the login export import with a fake manager, never the live session."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / 'scripts/niri-emaki-session').read_text()
start = source.index('emaki-session-import-environment\n')
end = source.index('systemctl --user set-environment', start)
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp)
    stub = path / 'systemctl'
    stub.write_text('#!/usr/bin/python3\nimport json, os, sys\n'
                    'assert sys.argv[1:3] == ["--user", "import-environment"]\n'
                    'values={k: os.environ[k] for k in sys.argv[3:]}\n'
                    'assert all(v.encode("utf-8") is not None for v in values.values())\n'
                    'print(json.dumps(values))\n')
    stub.chmod(0o700)
    exports = dict(DEBUGINFOD_URLS='https://fixture.invalid', VSSCRIPT_PATH='/profile/scripts',
                   EMAKI_LOGIN_HANDOFF='fixture-token', QT_TEST='a\nb $`literal`', GTK_TEST='', XCURSOR_THEME='test', EDITOR='test editor')
    (path / 'emaki-session-import-environment').symlink_to(ROOT / 'scripts/emaki-session-import-environment')
    env = dict(exports, BAD_UTF8='invalid\udcff', PATH=tmp, PWD='/ignored', OLDPWD='/ignored', SHLVL='5', TERM='xterm',
               MAIL='/ignored', MOTD_SHOWN='pam', _='/ignored')
    result = subprocess.run(['/bin/bash', '-c', source[start:end]], env=env,
                            text=True, capture_output=True, check=True)
    observed = json.loads(result.stdout)
    assert all(observed.get(k) == v for k, v in exports.items()), observed
    assert not set(observed) & {'_', 'PWD', 'OLDPWD', 'SHLVL', 'TERM', 'MAIL', 'MOTD_SHOWN'}, observed
    assert observed['PATH'] == tmp
    assert 'BAD_UTF8' not in observed
    assert observed['EMAKI_LOGIN_HANDOFF'] == 'fixture-token'
print('PASS explicit import preserves arbitrary exports and excludes shell/tty locals')

# Run the real compositor wrapper with only installed helper/binary paths redirected.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    helper = base / 'prepare.py'
    helper.write_text('import os\nprint(os.environ.get("FIXTURE_PNG", ""))\n')
    wrapper = base / 'session'
    wrapper.write_text(source.replace('/usr/share/emaki/shell/helpers/session-start.py', str(helper)))
    compositor = base / 'niri-emaki'
    compositor.write_text('#!/usr/bin/python3\nimport json,os\nfrom pathlib import Path\n'
                          'Path(os.environ["RESULT"]).write_text(json.dumps({k:v for k,v in os.environ.items() if k.startswith("EMAKI_")}))\n')
    compositor.chmod(0o700)
    logger = base / 'systemd-cat'
    logger.write_text('#!/bin/sh\n/bin/cat >/dev/null\n')
    logger.chmod(0o700)
    for marker, png in (('', ''), ('stale', ''), ('fresh', '/fixture/frame.png')):
        env = dict(PATH=str(base), HOME=str(base), RESULT=str(base / 'result'),
                   EMAKI_LOGIN_HANDOFF=marker, FIXTURE_PNG=png,
                   EMAKI_SESSION_TAKEOVER='stale', EMAKI_STARTUP_COVER='stale')
        subprocess.run(['/bin/bash', str(wrapper), '--compositor'], env=env, check=True, timeout=3)
        actual = json.loads((base / 'result').read_text())
        assert actual.get('EMAKI_SESSION_TAKEOVER') == ('1' if png else None), actual
        assert actual.get('EMAKI_STARTUP_COVER') == (png or None), actual
print('PASS TTY/invalid handoff leaves takeover unset; prepared fresh handoff enables it')
