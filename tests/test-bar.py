#!/usr/bin/env python3
"""Real QML hover/timers/overview policy; offscreen, no real services or commands."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

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
