#!/usr/bin/env python3
"""Real private reader + GIO opener; isolated XDG, no desktop/Wayland or user writes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from app_scope_fixture import install, launches
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parent.parent
PROFILE = Path(tempfile.mkdtemp(prefix='recent-files-', dir=ROOT / '.cache'))
for part in ('data/applications', 'config', 'state', 'cache', 'runtime', 'tmp', 'files'):
    (PROFILE / part).mkdir(parents=True, mode=0o700)
ENV = dict(os.environ, XDG_DATA_HOME=str(PROFILE / 'data'), XDG_DATA_DIRS=str(PROFILE / 'data'),
           XDG_CONFIG_HOME=str(PROFILE / 'config'), XDG_CONFIG_DIRS=str(PROFILE / 'config'),
           XDG_STATE_HOME=str(PROFILE / 'state'), XDG_CACHE_HOME=str(PROFILE / 'cache'),
           XDG_RUNTIME_DIR=str(PROFILE / 'runtime'), TMPDIR=str(PROFILE / 'tmp'))
for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'DBUS_SYSTEM_BUS_ADDRESS'):
    ENV.pop(key, None)
HELPER = ROOT / 'shell/helpers/recent-files.py'
install(PROFILE, ENV)
SOURCE = PROFILE / 'data/recently-used.xbel'


def call(mode='list', path=None, code=0):
    result = subprocess.run([sys.executable, '-B', str(HELPER), mode],
        input=json.dumps({'path': str(path)}) + '\n' if path is not None else '',
        env=ENV, capture_output=True, text=True, timeout=5)
    assert result.returncode == code, (result.returncode, result.stdout, result.stderr)
    assert not result.stderr, result.stderr
    assert len(result.stdout.splitlines()) == 1, result.stdout
    value = json.loads(result.stdout)
    assert value['schema_version'] == 1
    if mode == 'open' or code:
        assert str(PROFILE) not in result.stdout and 'PRIVATE' not in result.stdout
    return value


def xbel(rows):
    SOURCE.write_text('<xbel version="1.0">' + ''.join(
        f'<bookmark href={quoteattr(uri)} visited={quoteattr(when)}/>' for uri, when in rows) + '</xbel>')


assert call()['state'] == 'absent'
file = PROFILE / 'files/PRIVATE12 $(touch INJECTED); & кавычки \' ".txt'
file.write_text('A harmless fixture.\n')
older = PROFILE / 'files/older.txt'
older.write_text('old\n')
fifo = PROFILE / 'files/pipe'
os.mkfifo(fifo)
rows = [(older.as_uri(), '2025-01-01T00:00:00Z'), (file.as_uri(), '2026-09-23T04:30:00Z'),
        (file.as_uri(), '2026-01-01T00:00:00Z'), ('https://example.invalid/private', ''),
        ('file://remote.invalid' + str(file), ''), ((PROFILE / 'missing.txt').as_uri(), ''),
        ((PROFILE / 'files').as_uri(), ''), (fifo.as_uri(), ''),
        ('file:///bad%00path', ''), ('file:///bad%XZ', ''), (file.as_uri() + '?query', '')]
xbel(rows)
before = SOURCE.read_bytes()
value = call()
assert [entry['path'] for entry in value['entries']] == [str(file), str(older)], value
assert SOURCE.read_bytes() == before
assert call('open', file, code=1)['state'] == 'no_handler'
assert call('open', PROFILE / 'missing.txt', code=1)['state'] == 'file_missing'
assert call('open', fifo, code=1)['state'] == 'not_a_file'

for payload, expected in [('<xbel><bookmark', 'invalid_xbel'),
        ('<!DOCTYPE xbel [<!ENTITY evil SYSTEM "file:///etc/passwd">]><xbel>&evil;</xbel>', 'dtd_not_allowed'),
        ('<wrong/>', 'invalid_xbel'), ('<xbel>' + '<a>' * 30 + '</a>' * 30 + '</xbel>', 'xml_limit'),
        ('<xbel>' + '<bookmark/>' * 2049 + '</xbel>', 'too_many_entries'),
        (' ' * (2 * 1024 * 1024 + 1), 'source_too_large')]:
    SOURCE.write_text(payload)
    assert call(code=1)['state'] == expected
    assert SOURCE.read_text() == payload
SOURCE.unlink()
os.mkfifo(SOURCE)
assert call(code=1)['state'] == 'invalid_source'
SOURCE.unlink()
xbel(rows)
SOURCE.chmod(0)
if os.getuid() != 0:
    assert call(code=1)['state'] == 'access_denied'
SOURCE.chmod(0o600)

# Bound the retained set independently from the number of XML bookmarks.
many = []
for index in range(257):
    item = PROFILE / 'files' / f'bounded-{index}.txt'
    item.write_text('bounded\n')
    many.append((item.as_uri(), '2026-09-23T04:30:00Z'))
xbel(many)
bounded = call()
assert len(bounded['entries']) == 256 and bounded['limited'] is True
xbel(rows)
# A request whose stdin never arrives is still bounded by the real helper.
waiting = subprocess.Popen([sys.executable, '-B', str(HELPER), 'open'], env=ENV,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    assert waiting.wait(timeout=5) == 1
    assert json.loads(waiting.stdout.read())['state'] == 'timeout'
    assert waiting.stderr.read() == ''
finally:
    if waiting.poll() is None:
        waiting.kill()
        waiting.wait(timeout=3)
    waiting.stdin.close()
    waiting.stdout.close()
    waiting.stderr.close()

# Real GIO resolves the isolated default desktop entry, passes a literal path,
# and must not forward child output into the private protocol or shell log.
recorder = PROFILE / 'capture.py'
record = PROFILE / 'opened.json'
recorder.write_text('import json,sys\nfrom pathlib import Path\n'
    f'Path({str(record)!r}).write_text(json.dumps(sys.argv[1:]))\n'
    'print("PRIVATE CHILD STDOUT")\nprint("PRIVATE CHILD STDERR", file=sys.stderr)\n')
desktop = PROFILE / 'data/applications/emaki-file-test.desktop'
desktop.write_text('[Desktop Entry]\nType=Application\nName=File test\n'
    f'Exec={sys.executable} -B {recorder} %f\nTerminal=false\nMimeType=text/plain;application/octet-stream;\n')
(PROFILE / 'config/mimeapps.list').write_text('[Default Applications]\n'
    'text/plain=emaki-file-test.desktop\napplication/octet-stream=emaki-file-test.desktop\n')
data_before = {str(p): p.read_bytes() for p in (PROFILE / 'data').rglob('*') if p.is_file()}
assert call('open', file)['state'] == 'requested'
deadline = time.monotonic() + 3
while not record.exists() and time.monotonic() < deadline:
    time.sleep(.02)
assert json.loads(record.read_text()) == [str(file)]
assert not (PROFILE / 'INJECTED').exists() and not (ROOT / 'INJECTED').exists()
assert data_before == {str(p): p.read_bytes() for p in (PROFILE / 'data').rglob('*') if p.is_file()}
# A missing executable must not masquerade as a successful request.
desktop.write_text(desktop.read_text().replace(f'{sys.executable} -B {recorder}', str(PROFILE / 'not-an-executable')))
assert call('open', file, code=1)['state'] in ('open_failed', 'no_handler')
file.unlink()
assert call('open', file, code=1)['state'] == 'file_missing'
assert [entry['path'] for entry in call()['entries']] == [str(older)]
print(f'Recent files: bounded XBEL, local existing files, no handler/missing file, real GIO literal launch, no writes/leaks OK; {PROFILE.relative_to(ROOT)}')
launches(PROFILE, ['emaki-file-test'])
