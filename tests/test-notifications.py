#!/usr/bin/env python3
"""Production QS NotificationServer, private dbus-run-session; never the desktop bus."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
if '--inside' not in sys.argv:
    profile = Path(tempfile.mkdtemp(prefix='nt-', dir=ROOT / '.cache'))
    for d in ('config','state','data','cache','runtime','tmp'):
        (profile / d).mkdir(mode=0o700)
    config = profile/'dbus.conf'
    config.write_text(f'''<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>session</type><listen>unix:path={runtime_path(profile)}/bus</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>''')
    env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
             QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1', EMAKI_BIN='',
             XDG_CONFIG_HOME=str(profile/'config'), XDG_STATE_HOME=str(profile/'state'),
             XDG_DATA_HOME=str(profile/'data'), XDG_DATA_DIRS=str(profile/'data'),
             XDG_CACHE_HOME=str(profile/'cache'), XDG_RUNTIME_DIR=str(runtime_path(profile)),
             TMPDIR=str(profile/'tmp'), NIRI_SOCKET='', DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(profile/'missing'))
    for key in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','QT_SCALE_FACTOR','QT_LOGGING_RULES'):
        env.pop(key,None)
    subprocess.run(['dbus-run-session','--config-file='+str(config),'--',sys.executable,'-B',__file__,'--inside',str(profile)],env=env,check=True,timeout=65)
    raise SystemExit

profile=Path(sys.argv[-1])
assert os.environ['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:path='+str(Path(os.environ['XDG_RUNTIME_DIR']) / 'bus'))
def run(args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=4).stdout.strip()
def bus(method,args=()):
    return json.loads(run(['busctl','--user','--json=short','call','org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',method,*args]))['data'][0]
def start(name, enabled):
    qml=profile/name
    shutil.copytree(ROOT/'shell',qml)
    shutil.copyfile(ROOT/'tests/fixtures/ClockTest.qml',qml/'shell.qml')
    log=(profile/(name+'.log')).open('w')
    env=dict(os.environ,EMAKI_SHELL_NOTIFICATIONS='1' if enabled else '0')
    p=subprocess.Popen(['qs','-p',str(qml),'--no-color'],env=env,stdout=log,stderr=subprocess.STDOUT)
    return p,qml,log
def ipc(qml,method,*args):
    return run(['qs','-p',str(qml),'ipc','call','test',method,*map(str,args)])
def state(qml):return json.loads(ipc(qml,'status'))
def wait(qml,predicate,timeout=5):
    until=time.monotonic()+timeout
    last=None
    while time.monotonic()<until:
        try:
            last=state(qml)
            if predicate(last):return last
        except (subprocess.CalledProcessError,json.JSONDecodeError):pass
        time.sleep(.06)
    raise AssertionError(('state timeout',last))
def notify(app='Fixture', replacement=0, expire=0, actions=False):
    # GVariant arguments are separate argv, never interpolated into a shell.
    text=run(['gdbus','call','--session','--dest','org.freedesktop.Notifications',
        '--object-path','/org/freedesktop/Notifications','--method','org.freedesktop.Notifications.Notify',
        app,str(replacement),'','PRIVATE_NOTE_SUMMARY <b>literal</b>','PRIVATE_NOTE_BODY',
        "['default','Open','mark','Mark read']" if actions else '[]','{}',str(expire)])
    return int(text.split('uint32 ')[1].split(',')[0].split(')')[0])
processes=[]
try:
    p,q,log=start('disabled',False);processes.append((p,q,log))
    wait(q,lambda s:s['notifications']['server']=='disabled')
    assert not bus('NameHasOwner',['s','org.freedesktop.Notifications'])
    p.terminate();p.wait(timeout=4)
    p,q,log=start('active',True);processes.append((p,q,log))
    wait(q,lambda s:s['notifications']['server']=='active')
    owner=bus('GetNameOwner',['s','org.freedesktop.Notifications'])
    other,oq,olog=start('occupied',True);processes.append((other,oq,olog))
    wait(oq,lambda s:s['notifications']['server']=='owned_elsewhere')
    assert owner==bus('GetNameOwner',['s','org.freedesktop.Notifications'])
    # Real clock pointer input, containment, mutual exclusion, Esc and overview.
    s=state(q);cx=s['clock']['x']+80
    ipc(q,'click',cx,25);wait(q,lambda s:s['drawer']=='open')
    ipc(q,'click',cx,25);wait(q,lambda s:s['drawer']=='closed')
    ipc(q,'open');wait(q,lambda s:s['drawer']=='open' and s['clock_panel']['height']>300);ipc(q,'click',700,180)
    assert state(q)['drawer']=='open'
    assert ipc(q,'calendarCheck')=='true'
    old=state(q)['calendar'];ipc(q,'shift',1);assert state(q)['calendar']!=old
    ipc(q,'click',28,26);wait(q,lambda s:s['launcher']=='open' and s['drawer']=='closed')
    ipc(q,'open');wait(q,lambda s:s['drawer']=='open' and s['launcher']=='closed')
    ipc(q,'esc');wait(q,lambda s:s['drawer']=='closed')
    ipc(q,'open');wait(q,lambda s:s['drawer']=='open');ipc(q,'click',100,700)
    wait(q,lambda s:s['drawer']=='closed')
    ipc(q,'open');ipc(q,'environment','true','false');wait(q,lambda s:s['drawer']=='closed')
    ipc(q,'environment','false','false')
    first=notify(actions=True);wait(q,lambda s:s['notifications']['peek_count']==1)
    assert 'PRIVATE_NOTE' not in ipc(q,'status')
    notify(replacement=first,actions=True)
    assert state(q)['notifications']['count']==1
    # Actions travel through the actual server; nonresident invoke dismisses once.
    monitor=subprocess.Popen(['gdbus','monitor','--session','--dest','org.freedesktop.Notifications'],stdout=subprocess.PIPE,text=True)
    time.sleep(.15)
    ipc(q,'action',first,'mark');wait(q,lambda s:s['notifications']['count']==0)
    time.sleep(.1);monitor.terminate();out=monitor.communicate(timeout=3)[0]
    assert 'ActionInvoked' in out and "'mark'" in out, out
    time.sleep(.45)
    notify()
    wait(q,lambda s:s['notifications']['peek_count']==1 and s['clock_panel']['height']>90)
    # A click on the preview body opens history, even without a default action.
    ipc(q,'click',700,78);wait(q,lambda s:s['drawer']=='open')
    ipc(q,'clear');ipc(q,'close');time.sleep(.45)
    for _ in range(3):notify()
    wait(q,lambda s:s['notifications']['peek_count']==3)
    ipc(q,'open');ipc(q,'expand');assert state(q)['notifications']['expanded_groups']==1
    ipc(q,'collapse');assert state(q)['notifications']['expanded_groups']==0
    ipc(q,'clearGroup');assert state(q)['notifications']['count']==0
    for _ in range(3):notify()
    ipc(q,'ageRows');assert state(q)['notifications']['groups']==3
    ipc(q,'clear')
    ipc(q,'close');time.sleep(.45)
    for gate in ('dnd','fullscreen','overview','unknown'):
        if gate=='dnd':ipc(q,'dnd','true')
        elif gate=='unknown':ipc(q,'unknown')
        else:ipc(q,'environment',str(gate=='overview').lower(),str(gate=='fullscreen').lower())
        before=state(q)['notifications']['count'];notify()
        wait(q,lambda s:s['notifications']['count']==before+1)
        assert state(q)['notifications']['peek_count']==0,gate
        ipc(q,'dnd','false');ipc(q,'environment','false','false')
    ipc(q,'clear');time.sleep(.45)
    # Expiration keeps an inert history copy; CloseNotification removes client data.
    nid=notify(expire=150);time.sleep(1.1)
    assert ipc(q,'action',nid,'default')=='false'
    assert state(q)['notifications']['count']==1
    run(['gdbus','call','--session','--dest','org.freedesktop.Notifications','--object-path','/org/freedesktop/Notifications','--method','org.freedesktop.Notifications.CloseNotification',str(notify())])
    assert state(q)['notifications']['count']==1
    ipc(q,'clear');ipc(q,'close');time.sleep(.45)
    # A shell notice that pushes a still-open notification off the 200-entry list closes it:
    # its sender gets NotificationClosed (reason 2, dismissed), as with the overflow of accept().
    oldest=notify()
    for _ in range(199):notify()
    wait(q,lambda s:s['notifications']['count']==200,timeout=10)
    monitor=subprocess.Popen(['gdbus','monitor','--session','--dest','org.freedesktop.Notifications'],stdout=subprocess.PIPE,text=True)
    time.sleep(.15)
    assert ipc(q,'localNotice')=='200'
    time.sleep(.3);monitor.terminate();out=monitor.communicate(timeout=3)[0]
    assert f'NotificationClosed (uint32 {oldest}, uint32 2)' in out, out
    ipc(q,'clear');ipc(q,'close');time.sleep(.45)
    start_time=time.monotonic()
    while time.monotonic()-start_time<7.6:
        notify('Fixture' if int((time.monotonic()-start_time)*2)%2 else 'Other fixture')
        time.sleep(.35)
    assert state(q)['notifications']['peek_count']>1
    time.sleep(.9)
    assert state(q)['notifications']['peek_count']==0,'burst exceeded 8s'
    # Recovery: the owner dies, the blocked shell rechecks (every 5 s) and takes the name.
    p.terminate();p.wait(timeout=4)
    wait(oq,lambda s:s['notifications']['server']=='active',timeout=12)
    assert bus('GetNameOwner',['s','org.freedesktop.Notifications'])!=owner
    # Client bus owns nothing after the last shell exits; preflight-blocked shell
    # must not contain the QS retrying server singleton.
    other.terminate();other.wait(timeout=4);time.sleep(.2)
    assert not bus('NameHasOwner',['s','org.freedesktop.Notifications'])
    # Files written by a newer shell (kept in /home across a system rollback): not read, not
    # overwritten. Notifications, and Night Light, whose Warmth is stored even while it is off.
    newer=profile/'state/emaki/notifications.json'
    newer.write_text(json.dumps(dict(version=2,dnd=True,entries=[dict(app='Fixture',summary='Newer format',body='',time=1790200000000)],grouping='future')))
    night=profile/'state/emaki/night-light.json'
    night.write_text(json.dumps(dict(version=2,on=True,warmth=80,schedule='future')))
    saved=newer.read_bytes(),night.read_bytes()
    p,q,log=start('newer',False);processes.append((p,q,log))
    s=wait(q,lambda s:s['notifications']['server']=='disabled');assert s['notifications']['count']==0 and not s['notifications']['dnd'],s['notifications']
    assert s['services']['night']['warmth']==50,s['services']['night']
    ipc(q,'dnd','true');ipc(q,'nightWarmth','30');wait(q,lambda s:s['notifications']['dnd'] and s['services']['night']['warmth']==30)
    time.sleep(.9)  # past the 500 ms save timers
    assert (newer.read_bytes(),night.read_bytes())==saved
finally:
    for p,q,log in processes:
        if p.poll() is None:
            p.terminate()
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=3)
        log.close()
for log in profile.glob('*.log'):
    text=log.read_text()
    assert not any(s in text for s in ('PRIVATE_NOTE','WARN','ERROR','TypeError','ReferenceError')),log.name+'\n'+text
print('PASS: isolated DBus; default off, occupied owner intact/no steal, takes the name after the owner dies; actual notifications/actions/replacement/expiry; drawer input/calendar/DND/fullscreen/overview; burst <=8s; logs private;',profile.relative_to(ROOT))
