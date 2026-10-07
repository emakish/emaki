#!/usr/bin/env python3
"""Actual helpers, cliphist/GIO, QS UI, fake terminal/wl-copy/MPRIS; private profile/bus."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from app_scope_fixture import install, launches
import reaper
reaper.guard()  # nothing this test starts outlives it
ROOT=Path(__file__).resolve().parent.parent
if '--inside' not in sys.argv:
    root=Path(tempfile.mkdtemp(prefix='lt-',dir=ROOT/'.cache'))
    for d in ('r','c','s','d','cache','tmp','bin'):(root/d).mkdir(mode=0o700)
    cfg=root/'bus.conf';cfg.write_text(f'<busconfig><type>session</type><listen>unix:path={runtime_path(root)}/bus</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QML_DISABLE_DISK_CACHE='1',PYTHONDONTWRITEBYTECODE='1',EMAKI_BIN='',EMAKI_SHELL_NOTIFICATIONS='0',EMAKI_TEST_MPRIS='1',GSETTINGS_BACKEND='memory',XDG_CONFIG_HOME=str(root/'c'),XDG_STATE_HOME=str(root/'s'),XDG_DATA_HOME=str(root/'d'),XDG_DATA_DIRS=str(root/'d'),XDG_CACHE_HOME=str(root/'cache'),XDG_RUNTIME_DIR=str(runtime_path(root)),TMPDIR=str(root/'tmp'),DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(root/'missing'))
    for k in ('DISPLAY','WAYLAND_DISPLAY','NIRI_SOCKET','DBUS_SESSION_BUS_ADDRESS','QT_SCALE_FACTOR','QT_LOGGING_RULES'):env.pop(k,None)
    subprocess.run(['dbus-run-session','--config-file='+str(cfg),'--',sys.executable,'-B',__file__,'--inside',str(root)],env=env,check=True,timeout=90)
    raise SystemExit
root=Path(sys.argv[-1]);assert os.environ['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:path='+str(Path(os.environ['XDG_RUNTIME_DIR']) / 'bus'))
install(root, os.environ)
def run(args,**kw):return subprocess.run(args,check=True,capture_output=True,timeout=7,**kw).stdout
def helper(req):return json.loads(run([sys.executable,'-B',str(ROOT/'shell/helpers/launcher-tools.py')],input=json.dumps(req).encode()))
def wait_for(check,timeout=7):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if check():return
        time.sleep(.04)
    raise AssertionError('wait timeout')
# The copy fake does not contact Wayland. A real terminal is never executed.
copy=root/'bin/copy';copy.write_text('#!/usr/bin/python3\nimport sys\nfrom pathlib import Path\nPath('+repr(str(root/'copied'))+').write_bytes(sys.stdin.buffer.read())\n');copy.chmod(0o700)
term=root/'bin/term';term.write_text('#!/usr/bin/python3\nimport os,sys,json\nfrom pathlib import Path\nPath('+repr(str(root/'terminal.json'))+').write_text(json.dumps([os.getcwd(),sys.argv[1:]]))\n');term.chmod(0o700)
os.environ['EMAKI_WL_COPY']=str(copy);os.environ['EMAKI_TERMINAL']=str(term)
core_profile=root/'core';core_profile.mkdir()
for d in ('config','state','runtime'):(core_profile/d).mkdir(mode=0o700)
os.environ['EMAKI_SETTINGS_PROFILE']=str(core_profile)
os.environ['EMAKI_BIN']=str(ROOT/'.cache/target/debug/emaki')
assert helper({'op':'clip-list'})['entries']==[]
assert not (root/'cache/cliphist').exists()
clip=['cliphist','-config-path','/dev/null','-db-path',str(Path(os.environ['XDG_RUNTIME_DIR'])/'emaki-cliphist.db')]
text=b'PRIVATE_CLIP $(touch not-executed) <b>literal</b>'
run(clip+['store'],input=text)
# Valid tiny PNG, as image clipboard entries must be marked as images.
import base64
png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jxN0AAAAASUVORK5CYII=')
run(clip+['store'],input=png)
items=helper({'op':'clip-list'})['entries'];assert len(items)==2 and any(i['image'] for i in items),items
text_id=next(i['id'] for i in items if not i['image']); image_id=next(i['id'] for i in items if i['image'])
assert helper({'op':'clip-copy','id':image_id})['state']=='copied';assert (root/'copied').read_bytes()==png
assert helper({'op':'clip-copy','id':'1;touch BAD'})['state']=='invalid_clip_id'
apps=root/'d/applications';apps.mkdir()
(apps/'fixture-term.desktop').write_text('[Desktop Entry]\nType=Application\nName=Fixture Terminal\nTerminal=true\nPath='+str(root)+'\nIcon=fixture-icon\nExec=/usr/bin/printf "literal ; $(touch BAD)" %c %i %k %% %U\n')
assert helper({'op':'terminal','id':'fixture-term','terminal':str(term)})['state']=='requested'
wait_for(lambda:(root/'terminal.json').exists())
cwd,args=json.loads((root/'terminal.json').read_text());assert cwd==str(root) and args==['-e','/usr/bin/printf','literal ; $(touch BAD)','Fixture Terminal','--icon','fixture-icon',str(apps/'fixture-term.desktop'),'%' ],args
assert helper({'op':'terminal','id':'fixture-term','terminal':str(root/'absent')})['state']=='terminal_missing'
assert helper({'op':'web','query':'PRIVATE_QUERY'})['state']=='no_handler'
web=root/'web.py';web.write_text('import sys,json\nfrom pathlib import Path\nPath('+repr(str(root/'web.json'))+').write_text(json.dumps(sys.argv[1:]))\n')
(apps/'fixture-web.desktop').write_text('[Desktop Entry]\nType=Application\nName=Fixture Browser\nExec=python3 -B '+str(web)+' %u\nMimeType=x-scheme-handler/https;\n')
(root/'c/mimeapps.list').write_text('[Default Applications]\nx-scheme-handler/https=fixture-web.desktop\n')
assert helper({'op':'web','query':'PRIVATE_QUERY & $(touch BAD)'})['state']=='requested'
wait_for(lambda:(root/'web.json').exists());assert json.loads((root/'web.json').read_text())==['https://duckduckgo.com/?q=PRIVATE_QUERY%20%26%20%24%28touch%20BAD%29']
assert not (root/'BAD').exists()
walls=root/'d/emaki/wallpapers';walls.mkdir(parents=True);wall=walls/'fixture.png';wall.write_bytes(png);(walls/'notes.txt').write_text('PRIVATE_NOTE')
assert helper({'op':'wallpapers'})['entries']==[dict(path=str(wall),name='fixture')]
xkb=helper({'op':'xkb-layouts'})['entries'];assert dict(code='ru',name='Russian') in xkb and len(xkb)>50,len(xkb)
defaults=helper({'op':'defaults'})['entries'];assert [d['label'] for d in defaults]==['Browser','Terminal','Files','Text editor','Image viewer','Video player']
assert defaults[0]['key']=='defaults.browser' and defaults[0]['id']=='fixture-web.desktop' and defaults[0]['choices']==[dict(id='fixture-web.desktop',name='Fixture Browser')],defaults[0]
assert defaults[3]['key'] is None and defaults[3]['choices']==[]
assert helper({'op':'frequent-list'})['counts']=={};assert not (root/'s/emaki').exists()
for _ in range(2):assert helper({'op':'frequent-record','id':'fixture-term'})['state']=='ready'
assert helper({'op':'frequent-list'})['counts']=={'fixture-term':2}
# Real QML: enter copies, delete removes; terminal launch records frequency, web row.
qml=root/'q';shutil.copytree(ROOT/'shell',qml);shutil.copyfile(ROOT/'tests/fixtures/ClockTest.qml',qml/'shell.qml')
def ipc(method,*args):return run(['qs','-p',str(qml),'ipc','call','test',method,*map(str,args)]).decode().strip()
def state():
    try:return json.loads(ipc('status'))
    except subprocess.CalledProcessError:return {}
# The shell owns the recorder only on explicit opt-in; the fake watcher records its argv/pid and waits for SIGTERM.
# It writes a temp file and renames it: a plain write_text left an empty recorder.json that the test
# could read mid-write (flaky CI, 2026-09-28).
paste=root/'bin/paste';paste.write_text('#!/usr/bin/python3\nimport os,sys,time,json\nfrom pathlib import Path\np=Path('+repr(str(root/'recorder.json'))+');t=p.with_name(f"recorder.{os.getpid()}.tmp");t.write_text(json.dumps([os.getpid(),sys.argv[1:]]));os.replace(t,p)\nwhile True: time.sleep(1)\n');paste.chmod(0o700)
os.environ['EMAKI_WL_PASTE']=str(paste);os.environ['EMAKI_SHELL_CLIPBOARD_RECORDER']='1'
def recorder():return json.loads((root/'recorder.json').read_text())
def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
log=(root/'qs.log').open('w');proc=subprocess.Popen(['qs','-p',str(qml),'--no-color'],stdout=log,stderr=subprocess.STDOUT)
player=None
try:
    wait_for(lambda:state().get('media',{}).get('count')==0)
    ipc('launcher');wait_for(lambda:state()['launcher']=='open' and state()['search']['frequent_count']==1)
    # Empty All is Recent (nothing yet); Apps keeps the Frequent row + grid. The launcher opens on Apps.
    ipc('mode','All');wait_for(lambda:state()['search']['recent']['state']=='ready');assert state()['search']['result_count']==0 and state()['search']['recent']['count']==0
    ipc('mode','Apps');wait_for(lambda:state()['search']['result_count']==3)   # Frequent tile + two fixture apps
    ipc('down');assert state()['search']['selected_index']==1
    ipc('up');assert state()['search']['selected_index']==0
    ipc('mode','Clipboard');wait_for(lambda:state()['search']['clipboard']['count']==2)
    # Recorder: started with the shell as `wl-paste --watch cliphist store`; Pause kills it, history stays; Resume restarts.
    wait_for(lambda:(root/'recorder.json').exists() and state()['search']['clipboard']['recorder']=='recording')
    first_pid,argv=recorder();assert argv==['--watch',*clip,'store'],argv
    assert ipc('clipRecording','false')=='true';wait_for(lambda:state()['search']['clipboard']['recorder']=='paused')
    wait_for(lambda:not alive(first_pid));assert state()['search']['clipboard']['count']==2
    assert ipc('clipRecording','true')=='true';wait_for(lambda:state()['search']['clipboard']['recorder']=='recording' and recorder()[0]!=first_pid)
    assert alive(recorder()[0])
    ipc('query','PRIVATE_CLIP');wait_for(lambda:state()['search']['result_count']==1)
    assert 'PRIVATE_' not in ipc('status')
    ipc('enter');wait_for(lambda:state()['launcher']=='closed');assert (root/'copied').read_bytes()==text
    ipc('launcher');ipc('mode','Clipboard');wait_for(lambda:state()['search']['clipboard']['count']==2)
    ipc('query','PRIVATE_CLIP');ipc('deleteClip');wait_for(lambda:state()['search']['clipboard']['count']==1)
    assert all(i['id']!=text_id for i in helper({'op':'clip-list'})['entries'])
    ipc('mode','Apps');ipc('query','Fixture Terminal');ipc('enter');wait_for(lambda:state()['launcher']=='closed')
    wait_for(lambda:helper({'op':'frequent-list'})['counts'].get('fixture-term',0)>=3)
    ipc('launcher');ipc('mode','All');ipc('query','PRIVATE_WEB');wait_for(lambda:state()['search']['selected_kind']=='web')
    ipc('enter');wait_for(lambda:state()['launcher']=='closed')
    ipc('launcher')
    assert ipc('mode', 'Settings') == 'false'
    ipc('mode', 'Clipboard'); ipc('tab', 'false')
    assert state()['search']['mode'] == 'All'
    ipc('tab', 'true'); assert state()['search']['mode'] == 'Clipboard'
    ipc('mode', 'All'); ipc('query', 'Top bar')
    wait_for(lambda: state()['search']['selected_kind'] == 'web')
    assert ipc('resultKinds') == 'web'
    assert 'settings' not in state()['search'] and 'page' not in state()['search']
    # The retained core still owns dock keys when an isolated profile is configured.
    toml = lambda: (core_profile/'config/emaki/settings.toml').read_text()
    run(['qs','-p',str(qml),'ipc','call','dock','autoHide','false'])
    wait_for(lambda: not state()['dock']['auto_hide'])
    assert 'auto_hide = false' in toml()
    run(['qs','-p',str(qml),'ipc','call','dock','autoHide','true'])
    wait_for(lambda: state()['dock']['auto_hide'])
    wait_for(lambda: not json.loads(ipc('settingsStatus'))['busy'])
    run([os.environ['EMAKI_BIN'], 'settings', 'set', 'bar.autohide', 'true', '--profile-root', str(core_profile), '--json'], env=dict(os.environ, XDG_CONFIG_HOME=str(core_profile/'config'), XDG_STATE_HOME=str(core_profile/'state'), XDG_RUNTIME_DIR=str(core_profile/'runtime')))
    # Stale saved settings-page history must not produce a launcher result.
    assert helper(dict(op='recent-record',kind='page',ref='bar'))['state'] == 'ready'
    ipc('close'); ipc('launcher'); ipc('mode','All'); ipc('query','')
    wait_for(lambda: state()['search']['recent']['count'] == 3)
    assert state()['search']['recent']['groups'] == ['Apps']
    assert ipc('resultKinds') == 'app'
    ipc('enter'); wait_for(lambda: state()['launcher'] == 'closed')
    assert 'PRIVATE_' not in ipc('status') and str(root) not in ipc('status')
    ipc('close')
    player=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/fixtures/mpris-player.py'),str(root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    wait_for(lambda:state()['media']['count']==1 and state()['media']['playing'])
    assert 'PRIVATE_' not in ipc('status')
    assert ipc('media','toggle')=='true';wait_for(lambda:not state()['media']['playing'])
    assert ipc('media','next')=='true';assert ipc('media','previous')=='true'
    wait_for(lambda:(root/'media-actions').exists() and 'Previous' in (root/'media-actions').read_text())
    assert (root/'media-actions').read_text().splitlines()==['Pause','Next','Previous']
    player.terminate();player.wait(timeout=3);wait_for(lambda:state()['media']['count']==0)
    assert ipc('media','next')=='false'
finally:
    if player and player.poll() is None:player.terminate();player.wait(timeout=3)
    proc.terminate();proc.wait(timeout=3);log.close()
# The shell takes its recorder down with it: no orphan watcher after exit.
last_pid=recorder()[0]
try:wait_for(lambda:not alive(last_pid),3)
finally:
    if alive(last_pid):os.kill(last_pid,9)
# A fresh shell reads bar.* from the core at start (no Settings page opened).
os.environ['EMAKI_SHELL_CLIPBOARD_RECORDER']='0'
log=(root/'qs2.log').open('w');proc=subprocess.Popen(['qs','-p',str(qml),'--no-color'],stdout=log,stderr=subprocess.STDOUT)
try:
    wait_for(lambda:state().get('bar_policy',{}).get('auto_hide') is True)
    assert state()['search']['clipboard']['recorder']=='external'
finally:
    proc.terminate();proc.wait(timeout=3);log.close()
assert helper({'op':'clip-clear'})['state']=='deleted'
assert helper({'op':'clip-list'})['entries']==[]
content=(root/'qs.log').read_text();assert not any(s in content for s in ['PRIVATE_','WARN','ERROR','TypeError','ReferenceError']),content
assert launches(root, ['fixture-term', 'fixture-web', 'wl-copy']).count('fixture-term') >= 3
print('PASS: cliphist text/image/copy/delete/private UI; owned recorder pause/resume/no orphan; GIO web/terminal field codes; persistent frequent; launcher settings removed; retained dock core keys, bar.* applied at start; real QS MPRIS against private player;',root.relative_to(ROOT))
