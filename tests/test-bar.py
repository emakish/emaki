#!/usr/bin/env python3
"""Real QML hover/timers/overview policy; offscreen, no real services or commands."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT=Path(__file__).resolve().parent.parent
root=Path(tempfile.mkdtemp(prefix='bp-',dir=ROOT/'.cache'))
for d in ('r','cache','config','state','data','tmp'): (root/d).mkdir(mode=0o700)
q=root/'q';shutil.copytree(ROOT/'shell',q);shutil.copyfile(ROOT/'tests/fixtures/ClockTest.qml',q/'shell.qml')
env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',QML_DISABLE_DISK_CACHE='1',PYTHONDONTWRITEBYTECODE='1',
         EMAKI_BIN='',EMAKI_SETTINGS_PROFILE='',EMAKI_TEST_SYSTEM='0',EMAKI_TEST_MPRIS='0',EMAKI_SHELL_TRAY='0',EMAKI_SHELL_NOTIFICATIONS='0',
         EMAKI_SHELL_BAR_AUTOHIDE='0',EMAKI_SHELL_OVERVIEW_WORKSPACES='1',
         XDG_RUNTIME_DIR=str(root/'r'),XDG_CACHE_HOME=str(root/'cache'),XDG_CONFIG_HOME=str(root/'config'),XDG_STATE_HOME=str(root/'state'),XDG_DATA_HOME=str(root/'data'),XDG_DATA_DIRS=str(root/'data'),TMPDIR=str(root/'tmp'),
         DBUS_SESSION_BUS_ADDRESS='unix:path='+str(root/'none'),DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(root/'none-system'),NIRI_SOCKET='')
for k in ('DISPLAY','WAYLAND_DISPLAY','QT_SCALE_FACTOR','QT_LOGGING_RULES'):env.pop(k,None)
def ipc(method,*args):
    return subprocess.run(['qs','-p',str(q),'ipc','call','test',method,*map(str,args)],env=env,check=True,capture_output=True,text=True,timeout=3).stdout.strip()
def state():return json.loads(ipc('status'))
def wait(pred,seconds=3):
    last=None;end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:
            last=state()
            if pred(last):return last
        except (subprocess.CalledProcessError,json.JSONDecodeError):pass
        time.sleep(.035)
    raise AssertionError(last)
with (root/'qs.log').open('w') as log:
    p=subprocess.Popen(['qs','-p',str(q),'--no-color'],env=env,stdout=log,stderr=subprocess.STDOUT)
    try:
        s=wait(lambda s:s['niri']['connection']=='connected')
        assert s['bar_policy']['normal_islands'] and s['exclusive_zone']==52
        ipc('environment','true','false');s=wait(lambda s:not s['bar_policy']['normal_islands'])
        assert s['bar_policy']['workspace_island']
        ipc('ovWs','false');assert not state()['bar_policy']['visible']
        ipc('ovWs','true');ipc('environment','false','false')
        # Actual pointer events target production HoverHandler/TipTarget.
        for xy,label,key in [((28,25),'Launcher','Super+D'),((75,25),'Workspaces · scroll to switch',''),((760,25),'Notifications and calendar','Super+N')]:
            ipc('hover',*xy);wait(lambda s:s['bar_policy']['tooltip'])
            assert json.loads(ipc('tipText'))==dict(text=label,key=key)
            ipc('hover',800,400);assert not state()['bar_policy']['tooltip']
        for page,label,key in [('tray','Background apps',''),('wifi','Wi-Fi',''),('bt','Bluetooth',''),('sound','Sound · scroll to change',''),('light','Brightness · scroll to change',''),('power','Battery and power','Super+Escape')]:
            ipc('hoverSystem',page);wait(lambda s:s['bar_policy']['tooltip'])
            assert json.loads(ipc('tipText'))==dict(text=label,key=key)
            ipc('hover',800,400)
        # The system island on glass: seven cells at most (six here, one layout), the hovered
        # one under a drop that stays on the last cell and melts when the pointer leaves.
        ipc('hoverSystem','sound');s=wait(lambda s:s['system_glass']['island']['alpha']>.99)
        assert s['system_glass']['island']['hover_key']=='cell-sound' and s['system_glass']['island']['cells']==6,s['system_glass']
        assert s['system']['x']+s['system']['width']<=1536-10+.5 and s['system']['y']==8 and s['system']['height']==36,s['system']
        ipc('hover',800,400);wait(lambda s:s['system_glass']['island']['alpha']==0)
        # Leaving before 450ms cancels the pending hint, including a stale timer.
        ipc('hover',28,25);ipc('hover',800,400);time.sleep(.5);assert not state()['bar_policy']['tooltip']
        ipc('hover',28,25);wait(lambda s:s['bar_policy']['tooltip']);ipc('click',28,25)
        wait(lambda s:s['launcher']=='open');assert not state()['bar_policy']['tooltip']
        ipc('close');wait(lambda s:s['launcher']=='closed')
        ipc('hover',800,400);ipc('barAuto','true')
        s=wait(lambda s:not s['bar_policy']['visible']);assert s['exclusive_zone']==10
        ipc('hover',5,2);time.sleep(.25);assert not state()['bar_policy']['revealed']
        ipc('hover',100,2);wait(lambda s:s['bar_policy']['revealed'])
        ipc('hover',28,25);time.sleep(.55);assert state()['bar_policy']['visible']
        ipc('hover',800,400);wait(lambda s:not s['bar_policy']['visible'])
        ipc('open');ipc('hover',800,400);time.sleep(.6);assert state()['bar_policy']['visible']
        ipc('close');wait(lambda s:not s['bar_policy']['visible'])
        ipc('environment','true','false');assert state()['bar_policy']['workspace_island']
        ipc('ovWs','false');assert not state()['bar_policy']['visible']
        ipc('environment','false','true');ipc('hover',100,2);time.sleep(.25)
        s=state();assert not s['bar_policy']['edge_enabled'] and not s['bar_policy']['visible']
        ipc('barAuto','false');assert not state()['bar_policy']['visible']
        ipc('launcher');wait(lambda s:s['launcher']=='open');assert state()['bar_policy']['visible']
        ipc('close');wait(lambda s:not s['bar_policy']['visible'])
        ipc('barAuto','true');ipc('unknown');ipc('hover',100,2);time.sleep(.25)
        assert not state()['bar_policy']['edge_enabled']
        ipc('barAuto','false');ipc('ovWs','true');ipc('environment','false','false');wait(lambda s:s['bar_policy']['visible'])
        # Overview: the non-workspace islands and the privacy pill slide/fade out over ~300 ms, not at once.
        ipc('casts','1');s=wait(lambda s:s['privacy']['visible'])
        ipc('environment','true','false');s=wait(lambda s:0<s['bar_policy']['overview_shift']<1)
        s=wait(lambda s:s['bar_policy']['overview_shift']==1);assert s['bar_policy']['visible']
        ipc('casts','1');assert not state()['privacy']['visible'] and state()['privacy']['cast']==1   # publish() rebuilt the model without casts
        ipc('environment','false','false');wait(lambda s:0<s['bar_policy']['overview_shift']<1);wait(lambda s:s['bar_policy']['overview_shift']==0)
        ipc('casts','1');assert state()['privacy']['visible'];ipc('casts','0');wait(lambda s:not s['privacy']['visible'])
        # The shell's own capture for the glass is not screen sharing: no pill, cast count 0.
        ipc('shellCast');time.sleep(.2);s=state();assert not s['privacy']['visible'] and s['privacy']['cast']==0,s['privacy']
        # System panel: a page switch keeps it open; closing morphs back (present while expansion > 0).
        ipc('system','sound');s=wait(lambda s:s['system_panel']=='open' and s['system_expansion']==1)
        ipc('system','tray');s=state();assert s['system_panel']=='open' and s['system_page']=='tray' and s['system_expansion']==1,s
        ipc('close');s=wait(lambda s:s['system_panel']=='closing' and 0<s['system_expansion']<1)
        wait(lambda s:s['system_panel']=='closed' and s['system_expansion']==0)
        # Drawer: the head (36 → 70 px) animates with its own morph on both open and close.
        ipc('open');s=wait(lambda s:s['drawer']=='open' and s['clock_head']==1)
        ipc('close');s=wait(lambda s:s['drawer']=='closing' and 0<s['clock_head']<1)
        wait(lambda s:s['drawer']=='closed' and s['clock_head']==0)
    finally:
        p.terminate()
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:p.kill();p.wait()
text=(root/'qs.log').read_text()
assert not any(x in text for x in ('WARN','ERROR','TypeError','ReferenceError')),text
print('PASS: overview variants, reserve 52→10, edge/hot-corner/fullscreen/unknown, modal hold, pointer tooltips and cancellation:',root)

# The bar's input mask after a fullscreen window hid the bar and gave it back: the real
# shell.qml and Surfaces.qml, the layer-shell windows as FloatingWindows that keep their masks.
def remove_block(source,marker):
    while marker in source:
        begin=source.index(marker);end=source.index('{',begin)+1;depth=1
        while depth:depth+=(source[end]=='{')-(source[end]=='}');end+=1
        source=source[:begin]+source[end:]
    return source
m=Path(tempfile.mkdtemp(prefix='bm-',dir=ROOT/'.cache'))
for d in ('r','cache','config','state','data','tmp'): (m/d).mkdir(mode=0o700)
mq=m/'q';shutil.copytree(ROOT/'shell',mq);shutil.copyfile(ROOT/'tests/fixtures/BarMaskDriver.qml',mq/'BarMaskDriver.qml')
with (mq/'qmldir').open('a') as f:f.write('BarMaskDriver 1.0 BarMaskDriver.qml\n')
(mq/'SystemNative.qml').write_text('import QtQuick\nSystemBackend { startupReady: true }\n')
(mq/'helpers/wallpaper.py').write_text('import json\nprint(json.dumps(dict(state="unavailable", texture="")))\n')
(mq/'helpers/launcher-tools.py').write_text('import json,sys\njson.loads(sys.stdin.readline())\nprint(json.dumps(dict(schema_version=1,state="unavailable")))\n')
surf=remove_block((mq/'Surfaces.qml').read_text(),'        anchors {')
surf=re.sub(r'^        (?:exclusiveZone|exclusionMode|WlrLayershell\.[A-Za-z]+|BackgroundEffect\.blurRegion|implicit(?:Width|Height)):[^\n]*\n','',surf,flags=re.MULTILINE)
surf=surf.replace('PanelWindow {','FloatingWindow {\n        implicitWidth: surfaces.controller.viewportWidth\n        implicitHeight: surfaces.controller.viewportHeight')
surf=surf.replace('surfaces.controller.output !== null','true')
assert surf.count('FloatingWindow {')==3 and surf.count('        mask: ')==3,surf
(mq/'Surfaces.qml').write_text(surf)
entry=(mq/'shell.qml').read_text().replace('headless: root.headless','headless: true')
entry=entry[:entry.rfind('}')]+'    BarMaskDriver {\n        scene: scene\n        surfaces: root.surfaceWindows\n    }\n}\n'
(mq/'check.qml').write_text(entry)
menv=dict(env,EMAKI_SHELL_BORDER='soft',EMAKI_SHELL_HEADLESS='0',EMAKI_SHELL_TEST_WIDTH='1280',EMAKI_SHELL_TEST_HEIGHT='800',
          EMAKI_SESSION_START='',EMAKI_SESSION_SKIP_INTRO='',HOME=str(m),EMAKI_PYTHON='/usr/bin/python3',
          XDG_RUNTIME_DIR=str(m/'r'),XDG_CACHE_HOME=str(m/'cache'),XDG_CONFIG_HOME=str(m/'config'),XDG_STATE_HOME=str(m/'state'),
          XDG_DATA_HOME=str(m/'data'),XDG_DATA_DIRS=str(m/'data'),TMPDIR=str(m/'tmp'))
r=subprocess.run(['qs','-p',str(mq/'check.qml'),'--no-color'],env=menv,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=20)
assert r.returncode==0 and 'BAR_MASK_OK' in r.stdout,r.stdout
assert not any(x in r.stdout for x in ('TypeError','ReferenceError','Binding loop','BAR_MASK_FAILED')),r.stdout
print('PASS: the bar input mask follows the bar back after a covering window:',next(l for l in r.stdout.splitlines() if 'BAR_MASK_OK' in l).split('BAR_MASK_OK ')[1],m)
