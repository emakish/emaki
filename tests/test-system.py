#!/usr/bin/env python3
"""Right island: production QML/controller, fake devices/CLIs, isolated tray bus."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from app_scope_fixture import install, launches
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
# Every state wait below, and the whole run's limit, is multiplied by EMAKI_TEST_WAIT_SCALE
# (default 1): a slow or loaded machine (CI) raises this one knob, no wait carries its own slack.
SCALE = float(os.environ.get('EMAKI_TEST_WAIT_SCALE') or 1)
if '--inside' not in sys.argv:
    root = Path(tempfile.mkdtemp(prefix='sy-', dir=ROOT/'.cache'))
    for d in ('r','config','state','data','cache','tmp'): (root/d).mkdir(mode=0o700)
    (root/'bus.conf').write_text(f'<busconfig><type>session</type><listen>unix:path={runtime_path(root)}/bus</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1',
               XDG_RUNTIME_DIR=str(runtime_path(root)), XDG_CONFIG_HOME=str(root/'config'), XDG_STATE_HOME=str(root/'state'), XDG_DATA_HOME=str(root/'data'), XDG_DATA_DIRS=str(root/'data'), XDG_CACHE_HOME=str(root/'cache'), TMPDIR=str(root/'tmp'),
               EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_TEST_SYSTEM='1', EMAKI_TEST_MPRIS='0', EMAKI_SETTINGS_PROFILE='', NIRI_SOCKET='', EMAKI_BIN='',
               DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(root/'missing'), PIPEWIRE_REMOTE='emaki-no-pipewire')
    for k in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','QT_SCALE_FACTOR','QT_LOGGING_RULES'): env.pop(k,None)
    # The limit only stops a hung run. Measured on a 16-thread laptop (2026-10-05): 50 s at load
    # 15, 68 s at load 20-22. A large part is ~660 `qs ipc` calls (a process each, 33 ms at load
    # 18), so the time grows with load; 90 s is 1.3x the slowest run.
    subprocess.run(['dbus-run-session','--config-file='+str(root/'bus.conf'),'--',sys.executable,'-B',__file__,'--inside',str(root)],env=env,check=True,timeout=90*SCALE)
    raise SystemExit

root=Path(sys.argv[-1])
install(root, os.environ)
assert os.environ['DBUS_SESSION_BUS_ADDRESS'].startswith('unix:path='+str(Path(os.environ['XDG_RUNTIME_DIR']) / 'bus'))
fixture=root/'cli'
fixture.write_text('''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
r=Path(os.environ['EMAKI_SYSTEM_TEST'])
a=sys.argv[1:]; n=Path(sys.argv[0]).name
with (r/'commands').open('a') as f:f.write(json.dumps([n,a])+'\\n')
if n=='brightnessctl':
 if 'set' in a: (r/'brightness').write_text(a[-1].rstrip('%'))
 else: print('fixture,backlight,70,'+(r/'brightness').read_text()+'%,100')
elif n=='powerprofilesctl':
 if a[0]=='list': print('  power-saver:\\n* balanced:\\n  performance:')
 elif a[0]=='set': (r/'profile').write_text(a[1])
 else: print((r/'profile').read_text())
elif n=='systemctl':
 if a not in [['reboot'],['poweroff'],['suspend'],['hibernate']]: sys.exit(9)
 if a in [['suspend'],['hibernate']] and not (r/'locked').exists():sys.exit(10)
 (r/'session').write_text(a[0])
elif n=='busctl':
 if a != ['--system','--json=short','call','org.freedesktop.login1','/org/freedesktop/login1','org.freedesktop.login1.Manager','CanHibernate']: sys.exit(9)
 value=(r/'hibernate').read_text() if (r/'hibernate').exists() else 'no'
 if value=='error': sys.exit(1)
 print(json.dumps({'type':'s','data':[value]}))
elif n=='niri':
 if a != ['msg','action','quit','--skip-confirmation']: sys.exit(9)
 (r/'session').write_text('logout')
elif n=='emaki-lock':
 import time
 if (r/'lock-fail').exists(): sys.exit(1)
 if a not in [['--wait'],['--confirm']]:sys.exit(9)
 time.sleep(.08)
 (r/'locked').write_text('1')
elif n=='nmcli':
 if a[:3]==['-t','-f','NAME,TYPE,UUID,ACTIVE']:
  up=(r/'vpn-up').exists()
  print('PRIVATE\\\\:VPN:wireguard:11111111-2222-3333-4444-555555555555:'+('yes' if up else 'no'))
  print('PRIVATE_WIFI:802-11-wireless:aaaaaaaa-2222-3333-4444-555555555555:yes')
  print('junk line without fields')
 elif a[:2]==['--wait','25'] and a[2:4]==['connection','up'] and a[4:6]==['uuid','11111111-2222-3333-4444-555555555555']: (r/'vpn-up').write_text('1')
 elif a[:2]==['--wait','25'] and a[2:4]==['connection','down'] and a[4:6]==['uuid','11111111-2222-3333-4444-555555555555']: (r/'vpn-up').unlink(missing_ok=True)
 else: sys.exit(2)
''')
fixture.chmod(0o700)
(root/'brightness').write_text('70'); (root/'profile').write_text('balanced')
for name in ('brightnessctl','powerprofilesctl','systemctl','nmcli','emaki-lock','busctl','niri'): (root/name).symlink_to(fixture)
# Fake wlsunset: records pid/argv and waits for SIGTERM (the guard's stdin pipe or a stop).
(root/'wlsunset').write_text('#!/usr/bin/python3\nimport os,sys,time,json\nfrom pathlib import Path\nPath('+repr(str(root/'wlsunset.json'))+').write_text(json.dumps([os.getpid(),sys.argv[1:]]))\nwhile True: time.sleep(1)\n');(root/'wlsunset').chmod(0o700)
os.environ.update(EMAKI_BUSCTL=str(root/'busctl'), EMAKI_NIRI=str(root/'niri'), EMAKI_SYSTEM_TEST=str(root), EMAKI_BRIGHTNESSCTL=str(root/'brightnessctl'), EMAKI_POWERPROFILESCTL=str(root/'powerprofilesctl'), EMAKI_SYSTEMCTL=str(root/'systemctl'), EMAKI_NMCLI=str(root/'nmcli'), EMAKI_LOCK=str(root/'emaki-lock'), EMAKI_WLSUNSET=str(root/'wlsunset'))
# Portal sign-in opens the default http handler through GIO: a fixture browser, never a real one.
(root/'data/applications').mkdir()
web=root/'web.py';web.write_text('import sys,json\nfrom pathlib import Path\nPath('+repr(str(root/'web.json'))+').write_text(json.dumps(sys.argv[1:]))\n')
(root/'data/applications/fixture-web.desktop').write_text('[Desktop Entry]\nType=Application\nName=Fixture Browser\nExec=python3 -B '+str(web)+' %u\nMimeType=x-scheme-handler/http;\n')
(root/'config/mimeapps.list').write_text('[Default Applications]\nx-scheme-handler/http=fixture-web.desktop\n')

def run(args, **kwargs):return subprocess.run(args,check=True,text=True,capture_output=True,timeout=5,**kwargs).stdout.strip()
def bus():return json.loads(run(['busctl','--user','--json=short','call','org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','GetConnectionUnixProcessID','s','org.kde.StatusNotifierWatcher']))['data'][0]
processes=[]
def start(name,tray,env=None):
    q=root/name; shutil.copytree(ROOT/'shell',q)
    for source,dest in [('ClockTest.qml','shell.qml'),('SystemFixture.qml','SystemFixture.qml')]:shutil.copyfile(ROOT/'tests/fixtures'/source,q/dest)
    log=(root/(name+'.log')).open('w'); p=subprocess.Popen(['qs','-p',str(q),'--no-color'],env=dict(env or os.environ,EMAKI_SHELL_TRAY='1' if tray else '0'),stdout=log,stderr=subprocess.STDOUT)
    processes.append((p,log));return q,p

def ipc(q,method,*args):return run(['qs','-p',str(q),'ipc','call','test',method,*map(str,args)])
def state(q):return json.loads(ipc(q,'status'))
def wait(q,pred,timeout=7):
    last=None; end=time.monotonic()+timeout*SCALE
    while time.monotonic()<end:
        try:
            last=state(q)
            if pred(last):return last
        except (subprocess.CalledProcessError,json.JSONDecodeError):pass
        time.sleep(.07)
    raise AssertionError(('state timeout',last))
def action(q,kind,value,expect='confirmed'):
    ipc(q,'systemAction',kind,json.dumps(value))
    return wait(q,lambda s:s['services']['action']==expect)
def helper(r,env=None):
    return json.loads(run([sys.executable,'-B',str(ROOT/'shell/helpers/system-tools.py')],input=json.dumps(r),env=env))
# Service-level cases: the production SystemService over SystemFixture in an offscreen qs of its
# own, with 2 confirmation ticks (400 ms). Each step is a JS pair [act, condition]: the act runs
# once, the next step waits until the condition holds; note(name, value) records a fact.
SCENARIO='''import QtQuick
import Quickshell
ShellRoot {
    id: root
    SystemFixture { id: fx }
    SystemService { id: svc; backend: fx; confirmationTicks: 2 }
    function device(key: string): var { return fx.devices.find(d => d.key === key); }
    function network(key: string): var { return fx.networks.find(n => n.key === key).ref; }
    function note(name: string, value: var): void { console.info("SCENARIO " + JSON.stringify([name, value])); }
    property int mark: 0
    property var old
    readonly property var steps: [STEPS]
    property int step: 0
    property int waited: 0
    Timer {
        interval: 50; repeat: true; running: true
        onTriggered: {
            if (root.step >= root.steps.length) { root.note("done", true); Qt.quit(); return; }
            if (root.waited === 0) root.steps[root.step][0]();
            if (root.steps[root.step][1]()) { root.step++; root.waited = 0; }
            else if (++root.waited > 200) { root.note("stuck", [root.step, svc.actionState, svc.pendingKind]); Qt.quit(); }
        }
    }
}
'''
# A scenario starts at once and runs beside the rest of the test; the returned function waits
# for it and gives its facts.
def scenario(name,steps):
    d=root/name;shutil.copytree(ROOT/'shell',d);shutil.copyfile(ROOT/'tests/fixtures/SystemFixture.qml',d/'SystemFixture.qml')
    with (d/'qmldir').open('a') as f:f.write('SystemFixture 1.0 SystemFixture.qml\n')
    (d/'shell.qml').write_text(SCENARIO.replace('STEPS',',\n'.join(steps)))
    log=(root/(name+'.log')).open('w');p=subprocess.Popen(['qs','-p',str(d),'--no-color'],env=dict(os.environ,EMAKI_SHELL_TRAY='0'),stdout=log,stderr=subprocess.STDOUT);processes.append((p,log))
    def facts():
        p.wait(timeout=40*SCALE);text=(root/(name+'.log')).read_text()
        facts=dict(json.loads(l.split('SCENARIO ',1)[1]) for l in text.splitlines() if 'SCENARIO ' in l)
        assert facts.get('done') is True,(name,facts,text)
        return facts
    return facts
# Failed launches through the production AppCatalog, one per path: the terminal helper's state
# (no terminal), the launcher's stderr and exit code, a crashed helper. A stand-in for
# EMAKI_PYTHON fails the launcher run (app_scope.py) with a raw stderr line and crashes the
# helper for "fixture-crash"; every other helper request runs the real helper. Nothing starts.
LAUNCH='''import QtQuick
import Quickshell
ShellRoot {
    id: root
    readonly property var ids: ["fixture-term", "fixture-fail", "fixture-crash"]
    property int index: 0
    AppCatalog {
        id: catalog
        onFailed: (id, name, reason) => { console.info("LAUNCH " + JSON.stringify([id, reason])); root.index++; next.restart(); }
    }
    Timer {
        id: next
        interval: 100; running: true
        onTriggered: {
            if (root.index >= root.ids.length) { console.info("LAUNCH " + JSON.stringify(["done", true])); Qt.quit(); return; }
            const entry = DesktopEntries.applications.values.find(e => e.id === root.ids[root.index]);
            if (!entry || !catalog.launch(entry)) restart();
        }
    }
}
'''
def launch_failures():
    d=root/'launch';shutil.copytree(ROOT/'shell',d);(d/'shell.qml').write_text(LAUNCH)
    apps=root/'launch-data/applications';apps.mkdir(parents=True)
    for name,terminal in (('term',True),('fail',False),('crash',True)):
        (apps/f'fixture-{name}.desktop').write_text(f'[Desktop Entry]\nType=Application\nName=Fixture {name}\nExec=/usr/bin/true\nTerminal={str(terminal).lower()}\n')
    python=root/'launch-python';python.write_text('#!'+sys.executable+'''
import json,os,subprocess,sys
if any(a.endswith('app_scope.py') for a in sys.argv[1:]):
    print('fixture-raw-line: no such application',file=sys.stderr);sys.exit(2)
request=sys.stdin.readline()
if json.loads(request).get('id')=='fixture-crash':sys.exit(3)
done=subprocess.run([sys.executable,*sys.argv[1:]],input=request,text=True,capture_output=True)
sys.stdout.write(done.stdout);sys.exit(done.returncode)
''');python.chmod(0o700)
    env=dict(os.environ,EMAKI_PYTHON=str(python),EMAKI_TERMINAL=str(root/'missing'),XDG_DATA_HOME=str(root/'launch-data'),XDG_DATA_DIRS=str(root/'launch-data'),EMAKI_SHELL_TRAY='0')
    log=(root/'launch.log').open('w');p=subprocess.Popen(['qs','-p',str(d),'--no-color'],env=env,stdout=log,stderr=subprocess.STDOUT);processes.append((p,log))
    def reasons():
        p.wait(timeout=40*SCALE);text=(root/'launch.log').read_text()
        got=dict(json.loads(l.split('LAUNCH ',1)[1]) for l in text.splitlines() if 'LAUNCH ' in l)
        assert got.pop('done',None) is True,(got,text)
        return got,text
    return reasons
# Fake NetworkManager on this private bus; only the helper and shell "a" see it as their system bus.
nmlog=(root/'nm-fake.log').open('w')
nm=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/fixtures/nm-fake.py'),str(root)],stdout=nmlog,stderr=subprocess.STDOUT);processes.append((nm,nmlog))
until=time.monotonic()+5*SCALE
while not (root/'nm-ready').exists() and time.monotonic()<until: time.sleep(.05)
assert (root/'nm-ready').exists()
nmenv=dict(os.environ,DBUS_SYSTEM_BUS_ADDRESS=os.environ['DBUS_SESSION_BUS_ADDRESS'])
def no_password_argv(before):
    # Every argv of every spawned fake since `before`: no password, no `wifi connect`.
    new=(root/'commands').read_text()[len(before):]
    return not any(x in new for x in ('fixture-password','PRIVATE_HIDDEN','PRIVATE_OPEN','"connect"'))
try:
    # Service-level scenarios start first and run beside everything else; their facts are checked
    # where the panel cases cover the same ground.
    # A panel join NetworkManager has not finished when the check gives up stays registered; its
    # late failure is neither the result of a join of another network nor of a pairing.
    late_wifi=scenario('late-wifi',['[() => { fx.wifiScan = true; svc.act("wifi-disconnect", {key: "fixture"}); }, () => svc.actionState === "confirmed"]',
        '[() => { fx.deny = true; svc.act("wifi-connect", {key: "fixture", password: ""}); }, () => svc.actionState === "confirmation_timeout"]',
        '[() => { root.note("first join kept", !!fx.wifiAttempts["fixture"]); svc.act("wifi-connect", {key: "fixture/near", password: "fixture-password"}); '
        'fx.wifiFailed("fixture", root.network("fixture"), "auth_timeout", root.network("fixture").activation); }, () => svc.actionState !== "pending"]',
        '[() => { root.note("join of another network", svc.actionState); svc.act("wifi-connect", {key: "fixture", password: ""}); }, () => svc.actionState === "confirmation_timeout"]',
        '[() => { root.note("second join kept", !!fx.wifiAttempts["fixture"]); fx.pairOutcome = "hang"; root.note("pair accepted", svc.act("bt-pair", "fixture-nearby")); root.mark = svc.attempts; '
        'fx.wifiFailed("fixture", root.network("fixture"), "wrong_password", root.network("fixture").activation); }, () => svc.actionState !== "pending" || svc.attempts >= root.mark + 2]',
        '[() => { root.note("pairing after the late failure", [svc.actionState, svc.pendingKind, root.device("fixture-nearby").pairing]); '
        'root.note("password asked again", fx.wifiPasswordKeys.includes("fixture")); root.device("fixture-nearby").ref.cancelPair(); }, () => !svc.pendingCheck]'])
    # A join retried on the same network supersedes the one its check gave up on. A late failure
    # of the earlier activation is not the retry's result: it neither asks for the password again
    # nor forgets a new network. The retry's own failure still is its result.
    retry_wifi=scenario('retry-wifi',['[() => { fx.wifiScan = true; svc.act("wifi-disconnect", {key: "fixture"}); }, () => svc.actionState === "confirmed"]',
        '[() => { fx.deny = true; svc.act("wifi-connect", {key: "fixture", password: ""}); }, () => svc.actionState === "confirmation_timeout"]',
        '[() => { root.old = root.network("fixture").activation; svc.act("wifi-connect", {key: "fixture", password: ""}); '
        'fx.wifiFailed("fixture", root.network("fixture"), "wrong_password", root.old); }, () => svc.actionState !== "pending" || svc.attempts >= 1]',
        '[() => { root.note("retry after a late failure of the earlier join", svc.actionState); root.note("password asked again", fx.wifiPasswordKeys.includes("fixture")); '
        'fx.wifiFailed("fixture", root.network("fixture"), "auth_timeout", root.network("fixture").activation); }, () => svc.actionState !== "pending"]',
        '[() => { root.note("the retry failing itself", svc.actionState); root.network("fixture/near").hang = true; '
        'svc.act("wifi-connect", {key: "fixture/near", password: "fixture-password"}); }, () => svc.actionState === "confirmation_timeout"]',
        '[() => { root.old = root.network("fixture/near").activation; svc.act("wifi-connect", {key: "fixture/near", password: "fixture-password"}); '
        'fx.wifiFailed("fixture/near", root.network("fixture/near"), "wrong_password", root.old); }, () => svc.actionState !== "pending"]',
        '[() => { root.note("retry of a new network after a late failure", svc.actionState); '
        'root.note("new network to be forgotten", !!fx.wifiCleanup["fixture/near"]); }, () => true]'])
    # A connection the device never confirms times out (the panel's own service waits 5 s for
    # it). Cancel takes over the pairing's check only for the device being paired: another
    # device's pairing (started by another program) is not cancelled from under it, and a cancel
    # the backend refuses reports the backend's state, not "busy".
    bluetooth=scenario('bluetooth',['[() => { fx.deny = true; svc.act("bt-connect", "fixture-device"); }, () => svc.actionState !== "pending"]',
        '[() => { root.note("connect not answered", svc.actionState); fx.deny = false; fx.secondNearby = true; fx.pairOutcome = "hang"; root.device("fixture-nearby2").ref.pair(); '
        'svc.act("bt-pair", "fixture-nearby"); }, () => svc.actionState === "pending"]',
        '[() => { root.note("other device", [svc.act("bt-cancel-pair", "fixture-nearby2"), svc.actionState, svc.pendingKind, root.device("fixture-nearby2").pairing]); '
        'root.device("fixture-nearby2").ref.cancelPair(); root.device("fixture-nearby").ref.cancelPair(); }, () => !svc.pendingCheck]',
        '[() => svc.act("bt-pair", "fixture-nearby"), () => svc.actionState === "pending"]',
        '[() => { const paired = root.device("fixture-nearby").ref; fx.forgotten = true; '
        'root.note("device gone", [svc.act("bt-cancel-pair", "fixture-nearby"), svc.actionState, svc.pendingKind]); fx.forgotten = false; paired.cancelPair(); }, () => !svc.pendingCheck]'])
    failed_launches=launch_failures()
    # Invalid requests never reach a command, and no shell syntax gets interpreted.
    assert helper({'op':'session','value':'poweroff'})['state']=='confirmation_required'
    assert helper({'op':'session','value':'suspend'})['state']=='confirmation_required'
    assert not (root/'session').exists()
    # Lock uses capture-preserving confirmation; suspend uses the sleep barrier.
    (root/'lock-fail').write_text('1');assert helper({'op':'lock'})['state']=='lock_failed'
    assert helper({'op':'session','value':'suspend','confirmed':True})['state']=='lock_failed' and not (root/'session').exists()
    (root/'lock-fail').unlink();assert helper({'op':'lock'})['state']=='locked' and (root/'locked').exists();(root/'locked').unlink()
    assert helper({'op':'session','value':'suspend','confirmed':True})['state']=='requested' and (root/'locked').exists() and (root/'session').read_text()=='suspend'
    lock_calls=[a for n,a in map(json.loads,(root/'commands').read_text().splitlines()) if n=='emaki-lock']
    assert lock_calls==[['--confirm'],['--wait'],['--confirm'],['--wait']],lock_calls
    (root/'session').unlink();(root/'locked').unlink()
    for value in ('no', 'na', 'challenge', 'yes'):
        (root/'hibernate').write_text(value)
        assert helper({'op':'hibernate-read'})['available'] is (value == 'yes')
        assert helper({'op':'session','value':'hibernate'})['state'] == 'confirmation_required'
    (root/'lock-fail').write_text('1')
    assert helper({'op':'session','value':'hibernate','confirmed':True})['state'] == 'lock_failed'
    assert not (root/'session').exists()
    (root/'lock-fail').unlink()
    assert helper({'op':'session','value':'hibernate','confirmed':True})['state'] == 'requested'
    assert (root/'session').read_text() == 'hibernate' and (root/'locked').exists()
    (root/'session').unlink(); (root/'locked').unlink()
    (root/'hibernate').write_text('challenge')
    assert helper({'op':'session','value':'hibernate','confirmed':True})['state'] == 'hibernate_unavailable'
    assert not (root/'session').exists()
    assert helper({'op':'session','value':'logout'})['state'] == 'confirmation_required'
    assert helper({'op':'session','value':'logout','confirmed':True})['state'] == 'requested'
    assert (root/'session').read_text() == 'logout'
    (root/'session').unlink()
    (root/'hibernate').write_text('yes')
    assert helper({'op':'brightness-set','value':'40; touch x'})['state']=='invalid_brightness'
    assert helper({'op':'profile-set','value':'bad'})['state']=='invalid_profile'
    assert helper({'op':'brightness-read'},dict(os.environ,EMAKI_BRIGHTNESSCTL=str(root/'missing')))['state']=='helper_missing'
    assert helper({'op':'night-light-check'})=={'schema_version':1,'state':'ready','installed':True}
    assert helper({'op':'night-light-check'},dict(os.environ,EMAKI_WLSUNSET=str(root/'missing')))['installed'] is False
    # The sleep guard's notice flags (scripts/emaki-sleep-guard writes its policy name): each one
    # is taken once; the persistent one only when the shell starts, i.e. after the next login.
    state_flag=root/'state/emaki/sleep-lock-failed';runtime_flag=Path(os.environ['XDG_RUNTIME_DIR'])/'emaki-sleep-lock-failed'
    assert helper({'op':'sleep-lock-flags','session_start':True})=={'schema_version':1,'state':'ready','policies':[]}
    state_flag.parent.mkdir(parents=True,exist_ok=True);state_flag.write_text('end-session\n');runtime_flag.write_text('sleep-relock\n')
    assert helper({'op':'sleep-lock-flags','session_start':False})['policies']==['sleep-relock'] and not runtime_flag.exists() and state_flag.exists()
    assert helper({'op':'sleep-lock-flags','session_start':True})['policies']==['end-session'] and not state_flag.exists()
    runtime_flag.write_text('x'*5000);assert helper({'op':'sleep-lock-flags','session_start':False})['policies']==['unknown'] and not runtime_flag.exists()
    assert helper({'op':'sleep-lock-flags','session_start':'yes'})['state']=='invalid_request'
    assert not list(runtime_flag.parent.glob('*sleep-lock*'))+list(root.glob('state/emaki/*sleep-lock*'))
    # Hidden network: NetworkManager D-Bus AddAndActivateConnection on the fake NM owning the
    # NM name on this private bus (the helper's "system bus" address points here). The password
    # is a property in the message: no process is spawned, no argv carries it.
    assert helper({'op':'wifi-hidden','ssid':'','password':''})['state']=='invalid_ssid'
    assert helper({'op':'wifi-hidden','ssid':'x'*33,'password':''})['state']=='invalid_ssid'
    assert helper({'op':'wifi-hidden','ssid':'PRIVATE_HIDDEN','password':'short'})['state']=='invalid_password'
    assert helper({'op':'wifi-hidden','ssid':'PRIVATE_HIDDEN','password':'fixture-password'})['state']=='nm_not_running'  # DBUS_SYSTEM_BUS_ADDRESS = missing socket
    commands_before=(root/'commands').read_text()
    assert helper({'op':'wifi-hidden','ssid':'PRIVATE_HIDDEN; touch BAD','password':'fixture-password'},nmenv)['state']=='confirmed'
    req=json.loads((root/'nm-request.json').read_text())
    assert req==dict(ssid='PRIVATE_HIDDEN; touch BAD',hidden=True,mode='infrastructure',key_mgmt='wpa-psk',psk='fixture-password',device='/org/freedesktop/NetworkManager/Devices/3',specific='/',id='PRIVATE_HIDDEN; touch BAD',type='802-11-wireless'),req
    assert no_password_argv(commands_before) and not (root/'nm-deleted').exists()
    assert helper({'op':'wifi-hidden','ssid':'PRIVATE_OPEN','password':''},nmenv)['state']=='confirmed'
    req=json.loads((root/'nm-request.json').read_text());assert req['key_mgmt'] is None and req['psk'] is None and req['hidden'] is True
    for mode,expect in (('no_secrets','wrong_password'),('error','activation_failed'),('no_wifi','no_wifi_device')):
        (root/'nm-mode').write_text(mode);assert helper({'op':'wifi-hidden','ssid':'PRIVATE_HIDDEN','password':'fixture-password'},nmenv)['state']==expect,mode
    (root/'nm-mode').unlink()
    # A rejected profile (wrong password) is deleted again, not left in NM with its password.
    assert (root/'nm-deleted').read_text().splitlines()==['/org/freedesktop/NetworkManager/Settings/1']
    assert no_password_argv(commands_before)
    vpn=helper({'op':'vpn-list'});assert vpn['state']=='ready' and vpn['connections']==[dict(name='PRIVATE:VPN',kind='wireguard',uuid='11111111-2222-3333-4444-555555555555',active=False)]
    assert helper({'op':'vpn-set','uuid':'11111111-2222-3333-4444-555555555555; rm','up':True})['state']=='invalid_request'
    assert helper({'op':'vpn-set','uuid':'11111111-2222-3333-4444-555555555555','up':True})['state']=='confirmed' and (root/'vpn-up').exists()
    assert helper({'op':'vpn-set','uuid':'11111111-2222-3333-4444-555555555555','up':False})['state']=='confirmed' and not (root/'vpn-up').exists()
    assert helper({'op':'vpn-list'},dict(os.environ,EMAKI_NMCLI=str(root/'missing')))['state']=='helper_missing'
    # Load the actual native bridge with disconnected services, never the user's buses.
    native=root/'n';shutil.copytree(ROOT/'shell',native)
    (native/'shell.qml').write_text('import QtQuick\nimport Quickshell\nShellRoot { SystemNative {} Timer { interval: 400; running: true; onTriggered: { console.log("NATIVE_LOADED"); Qt.quit(); } } }')
    native_result=subprocess.run(['qs','-p',str(native),'--no-color'],capture_output=True,text=True,timeout=5)
    (root/'native.txt').write_text(native_result.stdout+native_result.stderr)
    assert native_result.returncode == 0 and 'NATIVE_LOADED' in native_result.stdout+native_result.stderr, native_result.stdout+native_result.stderr
    assert not any(t in native_result.stdout+native_result.stderr for t in ('TypeError','ReferenceError','Failed to load configuration','Cannot assign','is not a type'))
    # The native bridge on QS's own NetworkManager backend (fake NM on this private bus): QS lists
    # an unsaved network only while the device's scanner is on, and only then asks NM to scan.
    w=root/'w';shutil.copytree(ROOT/'shell',w);shutil.copyfile(ROOT/'tests/fixtures/NativeWifiTest.qml',w/'shell.qml')
    wlog=(root/'native-wifi.txt').open('w');wp=subprocess.Popen(['qs','-p',str(w),'--no-color'],env=nmenv,stdout=wlog,stderr=subprocess.STDOUT);processes.append((wp,wlog))
    def native_wifi(pred,timeout=10):
        last=None;end=time.monotonic()+timeout*SCALE
        while time.monotonic()<end:
            try:
                last=json.loads(run(['qs','-p',str(w),'ipc','call','test','state']))
                if pred(last):return last
            except (subprocess.CalledProcessError,json.JSONDecodeError):pass
            time.sleep(.07)
        raise AssertionError(('native wifi timeout',last))
    def scans():return (root/'nm-scans').read_text().splitlines() if (root/'nm-scans').exists() else []
    def fake_nm(method):run(['busctl','--user','call','org.freedesktop.NetworkManager','/org/freedesktop/NetworkManager','org.emaki.FakeNM',method])
    saved=['wlan0/PRIVATE_SAVED:known'];near=sorted(saved+['wlan0/PRIVATE_NEAR','wlan0/PRIVATE_CAFE'])
    s=native_wifi(lambda s:s['devices']==[{'name':'wlan0','scanner':False}] and s['networks']==saved);assert s['ready'] and s['enabled'],s
    time.sleep(.5);assert native_wifi(lambda s:True)['networks']==saved and scans()==[]  # no scanner: no scan, nothing unsaved
    run(['qs','-p',str(w),'ipc','call','test','scan','true'])
    native_wifi(lambda s:s['devices']==[{'name':'wlan0','scanner':True}] and s['networks']==near)
    until=time.monotonic()+5*SCALE
    while scans()!=['/org/freedesktop/NetworkManager/Devices/3'] and time.monotonic()<until: time.sleep(.05)
    assert scans()==['/org/freedesktop/NetworkManager/Devices/3'],scans()
    # An adapter that appears while scanning gets its scanner on and lists its unsaved network.
    fake_nm('AddWifi');s=native_wifi(lambda s:{'name':'wlan1','scanner':True} in s['devices'] and 'wlan1/PRIVATE_FAR' in s['networks'])
    until=time.monotonic()+5*SCALE
    while len(scans())<2 and time.monotonic()<until: time.sleep(.05)
    assert scans()==['/org/freedesktop/NetworkManager/Devices/3','/org/freedesktop/NetworkManager/Devices/4'],scans()
    fake_nm('RemoveWifi');native_wifi(lambda s:s['devices']==[{'name':'wlan0','scanner':True}] and s['networks']==near)
    run(['qs','-p',str(w),'ipc','call','test','scan','false'])
    native_wifi(lambda s:s['devices']==[{'name':'wlan0','scanner':False}] and s['networks']==saved)
    # Exercise the real SystemBody -> SystemService -> SystemNative -> QS NM D-Bus path.
    # Secrets are fixture constants inside QML, never command-line arguments or status fields.
    ipc(w,'scan','true');native_wifi(lambda s:len(s['networks'])==3)
    def nm_native():return json.loads((root/'nm-native.json').read_text())
    def field(s,key):return next(f for f in s['fields'] if f['key']==key)
    def failed(key,reason='wrong_password'):
        return native_wifi(lambda s:s['action']==reason and (reason!='wrong_password' or
            (s['selected']==key and field(s,key)['visible'] and field(s,key)['focused'] and s['passwordEmpty'])))
    def joined(key):
        return native_wifi(lambda s:s['action']=='confirmed' and key+':known:connected' in s['networks'] and
            not field(s,key)['visible'] and s['passwordEmpty'])
    def disconnected(key):
        ipc(w,'disconnect',key)
        native_wifi(lambda s:s['action']=='confirmed' and not any(':connected' in n for n in s['networks']))
    new='wlan0/PRIVATE_NEAR';known='wlan0/PRIVATE_SAVED'
    ipc(w,'select',new);native_wifi(lambda s:field(s,new)['visible'] and field(s,new)['focused'])
    ipc(w,'deadline',1)  # NM's failure arrives after the generic confirmation timer expires.
    ipc(w,'submit','false');s=failed(new)
    ipc(w,'deadline',25)
    assert s['message']=='Wrong password. Try again.',s
    native_wifi(lambda s:new in s['networks'])
    data=nm_native();assert list(data['profiles'].values())==['PRIVATE_SAVED'],data
    rejected=data['calls'][0][1];assert data['calls']==[['AddAndActivateConnection',rejected],['Delete',rejected]],data
    # A failed row stays editable when clicked again; correct submission creates one profile.
    ipc(w,'select',new);ipc(w,'submit','true');joined(new)
    data=nm_native();assert sorted(data['profiles'].values())==['PRIVATE_NEAR','PRIVATE_SAVED'],data
    assert len([c for c in data['calls'] if c[0]=='AddAndActivateConnection'])==2,data
    disconnected(new)
    # Saved credentials are stale: row click uses ActivateConnection, correction uses Update
    # on that very profile and ActivateConnection again, with no add or delete.
    before=nm_native();ipc(w,'select',known);s=failed(known)
    assert s['message']=='Wrong password. Try again.' and known+':known' in s['networks'],s
    ipc(w,'select',known);native_wifi(lambda s:field(s,known)['visible'])
    ipc(w,'submit','true');joined(known)
    after=nm_native();saved_path='/org/freedesktop/NetworkManager/Settings/2'
    assert after['profiles']==before['profiles'],after
    assert after['calls'][len(before['calls']):]==[['ActivateConnection',saved_path],['Update',saved_path],['ActivateConnection',saved_path]],after
    disconnected(known)
    # Timeouts, lost APs and other errors keep their messages and all profiles, new or saved.
    for mode,reason,message in [('auth_timeout','auth_timeout','The network did not answer in time. Try again.'),
                                ('network_lost','network_lost','The network disappeared while connecting.'),
                                ('client_failed','network_connection_failed','Couldn’t connect to this network.')]:
        (root/'nm-mode').write_text(mode)
        before=nm_native();ipc(w,'select',known);s=failed(known,reason)
        assert s['message']==message and nm_native()['profiles']==before['profiles'],s
        assert nm_native()['calls'][len(before['calls']):]==[['ActivateConnection',saved_path]]
    # Open unsaved network also persists on a non-password failure; never automatically forget it.
    cafe='wlan0/PRIVATE_CAFE';before=nm_native();ipc(w,'select',cafe);failed(cafe,'network_connection_failed')
    after=nm_native();assert len(after['profiles'])==len(before['profiles'])+1
    assert [c[0] for c in after['calls'][len(before['calls']):]]==['AddAndActivateConnection'],after
    (root/'nm-mode').unlink()
    assert not any(secret in ipc(w,'status') for secret in ('PRIVATE_', 'fixture-password', 'bad-password', 'outdated-password'))
    wp.terminate();wp.wait(timeout=3);wlog.close()
    native_log=(root/'native-wifi.txt').read_text()
    # PipeWire/UPower/BlueZ are absent here and complain; the network backend, the D-Bus property
    # groups (a missing or mistyped NM property in the fake) and QML must not.
    assert not any(t in native_log for t in ('quickshell.network','quickshell.dbus.properties','TypeError','ReferenceError','Failed to load configuration','Cannot assign','is not a type','fixture-password','bad-password','outdated-password')),native_log
    q,p=start('a',False,nmenv)
    s=wait(q,lambda s:s['services']['profiles']=='ready' and s['services']['brightness']=='ready')
    assert s['services']['volume']=='64' and s['services']['battery']==82
    assert s['services']['outputs']==2 and s['services']['inputs']==2 and s['services']['streams']==1 and s['services']['mic_meter'] is False
    assert s['services']['network']=='on' and s['services']['bluetooth']=='on' and s['services']['tray']=='disabled'
    assert s['services']['devices']==2 and s['services']['paired']==1 and s['services']['discovering'] is False
    for page in ('sound','power','wifi','bt','light','kb','tray'):
        ipc(q,'system',page);wait(q,lambda s:s['system_page']==page)
        ipc(q,'esc');wait(q,lambda s:s['system_page']=='closed')
    ipc(q,'system','sound');ipc(q,'launcher');wait(q,lambda s:s['system_page']=='closed' and s['launcher']=='open')
    ipc(q,'system','power');wait(q,lambda s:s['system_page']=='power' and s['launcher']!='open')
    ipc(q,'open');wait(q,lambda s:s['drawer']=='open' and s['system_page']=='closed')
    ipc(q,'system','sound');ipc(q,'environment','true','false');wait(q,lambda s:s['system_page']=='closed')
    ipc(q,'environment','false','false')
    # No OSD for the first reading; a change of a known value shows it and it hides by itself.
    assert not state(q)['osd']['shown']
    assert action(q,'volume',37)['services']['volume']=='37'
    s=state(q);assert s['osd']==dict(shown=True,kind='sound',value=37,muted=False),s['osd']
    assert action(q,'mute',None)['services']['volume']=='Muted'
    assert state(q)['osd']['muted'] is True
    wait(q,lambda s:not s['osd']['shown'])
    # Scrolling over the bar icon steps by 5, unmutes and shows the OSD; light clamps at 5.
    ipc(q,'wheelSystem','sound',120);s=wait(q,lambda s:s['services']['volume']=='42');assert s['osd']==dict(shown=True,kind='sound',value=42,muted=False)
    ipc(q,'wheelSystem','light',-120);s=wait(q,lambda s:s['services']['action']=='confirmed' and (root/'brightness').read_text()=='65')
    assert s['osd']['kind']=='light' and s['osd']['value']==65
    for _ in range(14): ipc(q,'wheelSystem','light',-120)
    wait(q,lambda s:(root/'brightness').read_text()=='5' and s['services']['action']=='confirmed',15)  # 14 helper round trips
    # The open panel for that page shows the value itself, no OSD.
    ipc(q,'system','sound');wait(q,lambda s:s['system_page']=='sound' and not s['osd']['shown'])
    action(q,'volume',50);assert not state(q)['osd']['shown']
    ipc(q,'close');wait(q,lambda s:s['system_page']=='closed')
    # A different output is not a volume change.
    action(q,'output',2);assert not state(q)['osd']['shown'],state(q)['osd']
    action(q,'output',1)
    action(q,'mic',None);action(q,'output',2)
    # Mixer: per-app stream volume, microphone gain, input choice; meter only while the sound page is open.
    action(q,'stream-volume',{'id':9,'percent':20});action(q,'stream-volume',{'id':77,'percent':20},'stream_gone')
    action(q,'mic-volume',70);action(q,'input',4);action(q,'input',3);action(q,'input',55,'input_gone')
    ipc(q,'system','sound');wait(q,lambda s:s['services']['mic_meter'] is True);ipc(q,'close');wait(q,lambda s:s['services']['mic_meter'] is False)
    # Wi-Fi scanner (QS WifiDevice.scannerEnabled) only while the Wi-Fi page is open: QS lists a
    # network that is neither saved nor connected only while its device scans (the fixture does too).
    s=state(q);assert (s['services']['wifi_scanners'],s['services']['networks'])==(0,1),s['services']
    ipc(q,'system','wifi');s=wait(q,lambda s:s['services']['wifi_scanners']==1);assert s['services']['networks']==2
    assert json.loads(ipc(q,'wifiRows'))==['PRIVATE_WIFI · Connected','PRIVATE_NEAR · Secured']
    ipc(q,'system','sound');s=wait(q,lambda s:s['system_page']=='sound' and s['services']['wifi_scanners']==0);assert s['services']['networks']==1
    ipc(q,'system','wifi');wait(q,lambda s:s['services']['wifi_scanners']==1)
    action(q,'wifi-power',None);s=wait(q,lambda s:s['services']['network']=='off' and s['services']['wifi_scanners']==0)
    action(q,'wifi-power',None);s=wait(q,lambda s:s['services']['network']=='on' and s['services']['wifi_scanners']==1)
    # An adapter plugged in while the page is open scans too; its unsaved network is listed.
    ipc(q,'wifiAdapter','true');s=wait(q,lambda s:s['services']['wifi_scanners']==2);assert s['services']['networks']==3
    assert json.loads(ipc(q,'wifiRows'))==['PRIVATE_WIFI · Connected','PRIVATE_NEAR · Secured','PRIVATE_FAR · Open network']
    # A scan reorders the list by signal: the open row keeps its delegate, its password field
    # keeps the keyboard and the typing goes on.
    wait(q,lambda s:s['system_expansion']==1)
    assert ipc(q,'holdNetwork','fixture/near')=='focused';ipc(q,'typeText','abc')
    held=json.loads(ipc(q,'heldNetwork'));assert held=={'kept':True,'focused':True,'order':['PRIVATE_WIFI','PRIVATE_NEAR','PRIVATE_FAR'],'password':'abc'},held
    ipc(q,'farSignal','0.8')
    until=time.monotonic()+3*SCALE
    while json.loads(ipc(q,'heldNetwork'))['order']!=['PRIVATE_WIFI','PRIVATE_FAR','PRIVATE_NEAR'] and time.monotonic()<until: time.sleep(.05)
    ipc(q,'typeText','d');held=json.loads(ipc(q,'heldNetwork'))
    assert held=={'kept':True,'focused':True,'order':['PRIVATE_WIFI','PRIVATE_FAR','PRIVATE_NEAR'],'password':'abcd'},held
    # Closing the panel stops every scanner; unsaved networks leave the list again.
    ipc(q,'close');s=wait(q,lambda s:s['services']['wifi_scanners']==0);assert s['services']['networks']==1
    ipc(q,'wifiAdapter','false');ipc(q,'farSignal','0.3')
    action(q,'wifi-disconnect',{'key':'fixture'});action(q,'wifi-connect',{'key':'fixture'})
    action(q,'wifi-forget',{'key':'fixture'});action(q,'wifi-disconnect',{'key':'fixture'})
    # Neither saved nor connected now: listed only while the Wi-Fi page scans.
    action(q,'wifi-connect',{'key':'fixture','password':'bad-password'},'network_gone')
    ipc(q,'system','wifi');wait(q,lambda s:s['services']['wifi_scanners']==1)
    action(q,'wifi-connect',{'key':'fixture','password':'bad-password'},'wrong_password')
    ipc(q,'systemRow',0);assert ipc(q,'wifiMessage')=='Wrong password. Try again.'
    # A Wi-Fi error is not the result of a later audio action, nor is a failure the panel did not start.
    action(q,'output',2)
    ipc(q,'systemActionStrayWifiFailure','output','2','wrong_password');wait(q,lambda s:s['services']['action']=='confirmed')
    # A late failure of a join the check gave up on (scenario late-wifi, started above).
    late=late_wifi()
    assert late=={'first join kept':True,'join of another network':'confirmed','second join kept':True,'pair accepted':True,
                  'pairing after the late failure':['pending','bt-pair',True],'password asked again':True,'done':True},late
    retry=retry_wifi()
    assert retry=={'retry after a late failure of the earlier join':'pending','password asked again':False,'the retry failing itself':'auth_timeout',
                   'retry of a new network after a late failure':'confirmation_timeout','new network to be forgotten':False,'done':True},retry
    ipc(q,'wifiFail','auth_timeout');action(q,'wifi-connect',{'key':'fixture','password':'bad-password'},'auth_timeout')
    assert ipc(q,'wifiMessage').startswith('The network did not answer');ipc(q,'wifiFail','wrong_password')
    action(q,'wifi-connect',{'key':'fixture','password':'fixture-password'});ipc(q,'close')
    s=wait(q,lambda s:s['services']['wifi_scanners']==0);assert s['services']['networks']==1
    # Captive portal: connectivity from the backend, sign-in through the fixture http handler.
    s=state(q);assert s['services']['connectivity']=='full' and s['services']['vpn']=='ready' and s['services']['vpn_count']==1 and s['services']['vpn_active']==0
    ipc(q,'connectivity','portal');wait(q,lambda s:s['services']['connectivity']=='portal')
    ipc(q,'system','wifi');action(q,'portal',None,'requested')
    until=time.monotonic()+5*SCALE
    while not (root/'web.json').exists() and time.monotonic()<until: time.sleep(.05)
    assert json.loads((root/'web.json').read_text())==['http://nmcheck.gnome.org/']
    ipc(q,'connectivity','full');ipc(q,'close')
    # Hidden network from the panel: helper → fake NM over D-Bus; VPN through the nmcli helper. Never a shell.
    (root/'nm-request.json').unlink();commands_before=(root/'commands').read_text()
    ipc(q,'system','wifi');ipc(q,'hidden','PRIVATE_HIDDEN','fixture-password','true');wait(q,lambda s:s['services']['action']=='confirmed' and (root/'nm-request.json').exists())
    req=json.loads((root/'nm-request.json').read_text());assert (req['ssid'],req['psk'],req['hidden'])==('PRIVATE_HIDDEN','fixture-password',True)
    ipc(q,'hidden','','','true');wait(q,lambda s:s['services']['action']=='invalid_ssid');assert ipc(q,'wifiMessage').startswith('Enter the network name')
    (root/'nm-request.json').unlink();ipc(q,'hidden','PRIVATE_OPEN','ignored','false');wait(q,lambda s:s['services']['action']=='confirmed' and (root/'nm-request.json').exists())
    req=json.loads((root/'nm-request.json').read_text());assert (req['ssid'],req['psk'])==('PRIVATE_OPEN',None)
    (root/'nm-mode').write_text('no_secrets');ipc(q,'hidden','PRIVATE_HIDDEN','fixture-password','true');wait(q,lambda s:s['services']['action']=='wrong_password')
    assert ipc(q,'wifiMessage')=='Wrong password. Try again.';(root/'nm-mode').unlink()
    assert no_password_argv(commands_before)  # the 5 s polling adds lines meanwhile; none may carry the password
    action(q,'vpn',{'uuid':'11111111-2222-3333-4444-555555555555','up':True});assert (root/'vpn-up').exists() and state(q)['services']['vpn_active']==1
    action(q,'vpn',{'uuid':'11111111-2222-3333-4444-555555555555','up':False});assert not (root/'vpn-up').exists()
    ipc(q,'close')
    action(q,'bt-connect','fixture-device');action(q,'bt-disconnect','fixture-device')
    # Discovery, pairing (failure = `pairing` drops without `paired`), cancel, forget; 60 s pairing window.
    assert action(q,'bt-scan',None)['services']['discovering'] is True;action(q,'bt-scan',None)
    action(q,'bt-pair','fixture-device','already_paired')
    ipc(q,'system','bt');ipc(q,'btPairOutcome','fail');ipc(q,'systemRow',1);wait(q,lambda s:s['services']['action']=='pairing_failed')
    assert ipc(q,'btDetail',1).startswith('Couldn’t pair') and state(q)['services']['paired']==1
    # Cancelling a pairing that never ends: not refused as busy, no "Couldn’t pair", the panel is free again.
    ipc(q,'btPairOutcome','hang');ipc(q,'systemRow',1);wait(q,lambda s:s['services']['action']=='pending' and ipc(q,'btDetail',1)=='Pairing…')
    ipc(q,'systemRow',1);wait(q,lambda s:s['services']['action']=='confirmed')
    detail=ipc(q,'btDetail',1);assert not detail.startswith(('Pairing','Couldn’t pair')),detail
    # Connection timeout and cancel targets (scenario bluetooth, started above).
    bt=bluetooth()
    assert bt=={'connect not answered':'confirmation_timeout','other device':[False,'busy','bt-pair',True],'device gone':[False,'device_gone','bt-pair'],'done':True},bt
    action(q,'bt-scan',None);action(q,'bt-scan',None)
    ipc(q,'btPairOutcome','ok');ipc(q,'systemRow',1);wait(q,lambda s:s['services']['action']=='confirmed' and s['services']['paired']==2)
    ipc(q,'close');action(q,'bt-forget','fixture-nearby');assert state(q)['services']['devices']==1
    action(q,'bt-forget','fixture-nearby','device_gone')
    action(q,'brightness',43);assert (root/'brightness').read_text()=='43'
    assert state(q)['osd']['kind']=='light' and state(q)['osd']['value']==43
    # Night Light: wlsunset (fake) held at one temperature; warmth 50 = 4000 K; a change restarts it; state survives a restart.
    def sun():return json.loads((root/'wlsunset.json').read_text())
    def alive(pid):
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
    s=wait(q,lambda s:s['services']['night']['state']=='off');assert s['services']['night']==dict(state='off',warmth=50,temperature=4000)
    assert ipc(q,'night','true')=='true';wait(q,lambda s:s['services']['night']['state']=='on' and (root/'wlsunset.json').exists())
    assert sun()[1]==['-t','4000','-T','4001'];pid1=sun()[0]
    assert ipc(q,'nightWarmth','80')=='true';wait(q,lambda s:s['services']['night']['state']=='on' and (root/'wlsunset.json').exists() and sun()[0]!=pid1)
    assert sun()[1]==['-t','2500','-T','2501'] and not alive(pid1);pid2=sun()[0]
    until=time.monotonic()+3*SCALE
    while not (root/'state/emaki/night-light.json').exists() and time.monotonic()<until: time.sleep(.05)
    assert json.loads((root/'state/emaki/night-light.json').read_text())==dict(version=1,on=True,warmth=80)
    assert ipc(q,'night','false')=='true';wait(q,lambda s:s['services']['night']['state']=='off');time.sleep(.3);assert not alive(pid2)
    (root/'wlsunset.json').unlink();ipc(q,'night','true');wait(q,lambda s:s['services']['night']['state']=='on' and (root/'wlsunset.json').exists());pid3=sun()[0]
    # A new shell with the same state dir starts tinted from the first frame; its death takes wlsunset down (stdin guard).
    until=time.monotonic()+3*SCALE
    while json.loads((root/'state/emaki/night-light.json').read_text())['on'] is not True and time.monotonic()<until: time.sleep(.05)  # the save runs 500 ms after a change
    # The new shell is also the next login: it takes the flag the guard left when it ended the
    # session, and says so once; the running shell q leaves that flag alone.
    state_flag.write_text('end-session\n')
    (root/'wlsunset.json').unlink();d,dp=start('d',False,nmenv);wait(d,lambda s:s['services']['night']['state']=='on' and (root/'wlsunset.json').exists())
    assert sun()[1]==['-t','2500','-T','2501'];pid4=sun()[0];assert pid4!=pid3 and alive(pid3)
    wait(d,lambda s:not state_flag.exists() and json.loads(ipc(d,'notices'))==[['Screen lock','Emaki could not lock the screen before sleep, so it ended the session.']])
    assert json.loads(ipc(q,'notices'))==[]
    dp.kill();dp.wait(timeout=3)
    until=time.monotonic()+3*SCALE
    while alive(pid4) and time.monotonic()<until: time.sleep(.05)
    assert not alive(pid4) and alive(pid3)
    ipc(q,'night','false');wait(q,lambda s:s['services']['night']['state']=='off')
    # Without the binary the switch reports it and does nothing.
    e,ep=start('e',False,dict(nmenv,EMAKI_WLSUNSET=str(root/'missing')));wait(e,lambda s:s['services']['night']['state']=='not_installed')
    assert ipc(e,'night','true')=='false' and state(e)['services']['night']['state']=='not_installed';ep.terminate();ep.wait(timeout=3)
    assert not (root/'wlsunset.json').exists() or not alive(sun()[0])
    action(q,'profile','power-saver');assert (root/'profile').read_text()=='power-saver'
    ipc(q,'system','power');ipc(q,'session','reboot');assert not (root/'session').exists()
    ipc(q,'close');assert state(q)['session_confirmation']==''
    ipc(q,'system','power');ipc(q,'session','reboot');ipc(q,'confirmSession');wait(q,lambda s:s['services']['action']=='requested')
    assert (root/'session').read_text()=='reboot'
    # Real Qt Tab/Return/Space on all six power buttons and the confirmation actions.
    buttons = json.loads(ipc(q, 'powerButtons'))
    assert [b['key'] for b in buttons] == ['act-lock','act-sleep','act-reboot','act-off','act-logout','act-hibernate'], buttons
    assert all(b['x'] >= 0 and b['x'] + b['width'] <= b['panelWidth'] for b in buttons), buttons
    assert ipc(q, 'powerKey', 'act-logout', 'return') == 'act-logout'
    assert state(q)['session_confirmation'] == 'logout' and (root/'session').read_text() == 'reboot'
    ipc(q, 'powerKey', 'confirm-no', 'space')
    assert state(q)['session_confirmation'] == ''
    assert ipc(q, 'powerKey', 'act-logout', 'tab') == 'act-hibernate'
    ipc(q, 'powerKey', 'act-hibernate', 'space')
    assert state(q)['session_confirmation'] == 'hibernate'
    ipc(q, 'powerKey', 'confirm-go', 'return')
    wait(q, lambda s: s['services']['action'] == 'requested' and (root/'session').read_text() == 'hibernate')
    (root/'locked').unlink()
    ipc(q, 'powerKey', 'act-logout', 'return'); ipc(q, 'powerKey', 'confirm-go', 'space')
    wait(q, lambda s: s['services']['action'] == 'requested' and (root/'session').read_text() == 'logout')
    (root/'hibernate').write_text('no')
    ipc(q, 'systemRefresh')
    wait(q, lambda s: len(json.loads(ipc(q, 'powerButtons'))) == 5)
    ipc(q, 'session', 'hibernate'); assert state(q)['session_confirmation'] == ''
    (root/'session').write_text('reboot')
    # Lock and Sleep from the panel: Sleep asks first, then locks, then suspends.
    action(q,'lock',None,'locked');assert (root/'locked').exists();(root/'locked').unlink()
    ipc(q,'session','suspend');assert state(q)['session_confirmation']=='suspend' and (root/'session').read_text()=='reboot'
    ipc(q,'confirmSession');wait(q,lambda s:s['services']['action']=='requested' and (root/'session').read_text()=='suspend');assert (root/'locked').exists()
    ipc(q,'close');ipc(q,'battery',9,'false');wait(q,lambda s:s['notifications']['count']==1)
    ipc(q,'battery',8,'false');time.sleep(.15);assert state(q)['notifications']['count']==1
    ipc(q,'dnd','true');ipc(q,'environment','false','true')
    ipc(q,'battery',5,'false');wait(q,lambda s:s['notifications']['count']==2 and s['notifications']['peek_count']>0)
    assert json.loads(ipc(q,'notices'))[0] == ['Battery critical','5% left — plug in soon.']
    ipc(q,'battery',6,'false');ipc(q,'battery',5,'false');ipc(q,'battery',4,'false')
    assert state(q)['notifications']['count']==2
    ipc(q,'battery',50,'true');ipc(q,'battery',4,'false');wait(q,lambda s:s['notifications']['count']==3)
    ipc(q,'battery',3,'false');assert state(q)['notifications']['count']==3
    ipc(q,'dnd','false');ipc(q,'environment','false','false')
    # A flag the guard writes while the shell runs: one notice at once, then the flag is gone; the
    # text says only what the policy in the flag is known to have done.
    for count,(policy,text) in enumerate({'sleep-relock':'Emaki could not lock the screen before sleep. It tried again after waking.',
                                          'stay-awake':'Emaki could not lock the screen when sleep was requested.',
                                          'sleep':'Emaki could not lock the screen before sleep.',
                                          'unknown-policy':'Emaki could not lock the screen before sleep.'}.items(),4):
        runtime_flag.write_text(policy+'\n');wait(q,lambda s:s['notifications']['count']==count and not runtime_flag.exists())
        assert json.loads(ipc(q,'notices'))[0]==['Screen lock',text],(policy,json.loads(ipc(q,'notices'))[0])
    time.sleep(.3);assert state(q)['notifications']['count']==7 and not state_flag.exists()
    assert not any(x in ipc(q,'status') for x in ('PRIVATE_WIFI','PRIVATE_BT','fixture-password','PRIVATE_HIDDEN','PRIVATE:VPN','1111'))
    # Privacy pill: capture streams (fixture) and screencasts (core model); names stay out of status.
    s=state(q);assert {k:s['privacy'][k] for k in ('mic','cam','cast','visible','open','panel','expansion','rows')}==dict(mic=0,cam=0,cast=0,visible=False,open=False,panel='closed',expansion=0,rows=0)
    ipc(q,'captures',json.dumps(dict(list=[dict(kind='mic',name='PRIVATE_APP'),dict(kind='cam',name='PRIVATE_APP'),dict(kind='',name='PRIVATE_OTHER')])))
    s=wait(q,lambda s:s['privacy']['visible']);assert (s['privacy']['mic'],s['privacy']['cam'],s['privacy']['rows'])==(1,1,2)
    assert s['privacy']['rect']['x']+s['privacy']['rect']['width']+8<=s['system']['x']+.5 and s['privacy']['rect']['y']==8
    ipc(q,'casts',2);s=wait(q,lambda s:s['privacy']['cast']==2);assert s['privacy']['rows']==4
    # A click on the pill grows its panel out of it: one plate from the pill's rectangle to 300 wide,
    # its right edge at the pill's, the pill stepping back once the panel is half open.
    pill=s['privacy']['rect'];right=lambda r:r['x']+r['width']
    assert s['privacy']['panel_rect']==pill,(s['privacy']['panel_rect'],pill)
    ipc(q,'clickPrivacy');s=wait(q,lambda s:s['privacy']['panel']=='open' and 0<s['privacy']['expansion']<1);assert s['launcher']=='closed'
    p=s['privacy']['panel_rect'];assert abs(right(p)-right(pill))<=1 and p['y']==8 and pill['width']<=p['width']<=300,(p,pill)
    s=wait(q,lambda s:s['privacy']['expansion']==1);p=s['privacy']['panel_rect']
    assert (p['y'],p['width'],p['height'])==(8,300,44+4*46+8) and abs(right(p)-right(pill))<=1,(p,pill)
    assert s['privacy']['visible'] and not s['privacy']['pill']['shown'] and s['bar_policy']['visible']
    # The pill's place pressed again shrinks it back into the pill; so do Esc and a press outside.
    ipc(q,'clickPrivacy');s=wait(q,lambda s:s['privacy']['panel']=='closing' and 0<s['privacy']['expansion']<1)
    s=wait(q,lambda s:s['privacy']['panel']=='closed' and s['privacy']['expansion']==0);assert s['privacy']['panel_rect']==pill and s['privacy']['pill']['shown']
    ipc(q,'clickPrivacy');wait(q,lambda s:s['privacy']['expansion']==1);ipc(q,'esc')
    wait(q,lambda s:s['privacy']['panel']=='closing');wait(q,lambda s:s['privacy']['panel']=='closed')
    ipc(q,'clickPrivacy');wait(q,lambda s:s['privacy']['expansion']==1);ipc(q,'click',700,600)
    wait(q,lambda s:s['privacy']['panel']=='closing');s=wait(q,lambda s:s['privacy']['panel']=='closed');assert s['launcher']=='closed' and s['privacy']['panel_rect']==pill
    ipc(q,'clickPrivacy');wait(q,lambda s:s['privacy']['open']);ipc(q,'launcher');wait(q,lambda s:s['launcher']=='open' and not s['privacy']['open']);ipc(q,'close')
    wait(q,lambda s:s['privacy']['panel']=='closed')
    # The last capture ending takes the pill and its open panel away at once.
    ipc(q,'clickPrivacy');wait(q,lambda s:s['privacy']['expansion']==1)
    ipc(q,'captures','{"list":[]}');ipc(q,'casts',0);s=wait(q,lambda s:not s['privacy']['visible']);assert s['privacy']['rows']==0 and s['privacy']['panel']=='closed',s['privacy']
    assert not any(x in ipc(q,'status') for x in ('PRIVATE_APP','PRIVATE_OTHER'))
    # A machine on a cable: the Wi-Fi cell and page say wired, with or without a Wi-Fi adapter.
    def cell():return json.loads(ipc(q,'wifiCell'))
    before=cell();action(q,'wifi-disconnect',{'key':'fixture'})
    offline=cell();assert (offline['icon'],offline['fallback'],offline['sub'])==('network-wireless-offline-symbolic','wifi','Not connected'),offline
    ipc(q,'wired','true');s=cell();assert (s['icon'],s['fallback'])==('network-wired-symbolic','wired') and s['sub'].startswith('Wired · Connected'),s
    ipc(q,'connectivity','limited');assert cell()['icon']=='network-wired-no-route-symbolic';ipc(q,'connectivity','full')
    ipc(q,'wifiAbsent','true');s=cell();assert (s['icon'],s['fallback'])==('network-wired-symbolic','wired') and s['sub'].startswith('Wired · Connected'),s
    ipc(q,'wired','false');s=cell();assert (s['icon'],s['fallback'],s['sub'])==('network-wireless-disabled-symbolic','wifi','No Wi-Fi adapter'),s
    ipc(q,'wifiAbsent','false');assert cell()==offline
    action(q,'wifi-connect',{'key':'fixture'});assert cell()==before,(cell(),before)
    # No internal state code on screen; technical names follow plain words in round brackets.
    # Exercise every state the services, helpers and the
    # core can report (tests/fixtures/ClockTest.qml shownTexts), and one nobody knows.
    states=['idle','pending','busy','disabled','unavailable','confirmed','confirmation_timeout','target_gone','stream_gone','invalid_volume','audio_unavailable',
            'wifi_unavailable_or_blocked','network_gone','password_or_security_unsupported','no_bluetooth_adapter','paired_device_required','bluetooth_off','device_gone',
            'already_paired','unsupported','output_unavailable','input_unavailable','output_gone','input_gone','pairing_failed','wrong_password','auth_timeout','network_lost',
            'network_connection_failed','ready','requested','locked','lock_failed','hibernate_unavailable','confirmation_required','invalid_brightness','invalid_profile',
            'invalid_request','invalid_ssid','invalid_password','no_wifi_device','activation_failed','network_not_found','password_required','helper_missing','operation_failed',
            'timeout','unconfirmed','no_handler','open_failed','output_limit','helper_failed','invalid_response','on','off','starting','checking','registering','active',
            'owned_elsewhere','clipboard_missing','copy_failed','invalid_clip_id','copied','deleted','invalid_query','launch_failed','failed','terminal_missing',
            'not_terminal_app','desktop_missing','connecting','closed','disconnected','incompatible','socket_not_configured','initial_state_timeout','unsupported_version',
            'unsupported_event','request_rejected','core_binary_not_configured','invalid_core_response','id_out_of_js_range','action_helper_stopped',
            'invalid_action_response','window_not_found','layout_not_found','postcondition_observed','error','rejected','fixture_code_x']
    # States whose sentences named a program: NetworkManager, wlsunset, niri, Python GObject.
    states+=['nm_not_running','not_installed','niri_unavailable','file_missing','access_denied','helper_dependency_missing']
    def plain(text):
        for name in ('niri','NetworkManager','PipeWire','wlsunset','GObject'):
            if name in text:
                # The explanation must precede the parenthesized technical name.
                text = re.sub(r'(?<=[.!?a-zA-Z…]) \(' + re.escape(name) + r'\)', '', text)
                if name in text:
                    return False
        return '_' not in text and not re.search(r': [a-z]',text)
    assert plain('Networking isn’t running (NetworkManager).')
    assert plain('Night Light isn’t installed (wlsunset).')
    for invalid in ('NetworkManager isn’t running.', 'Networking isn’t running [NetworkManager].',
                    '(NetworkManager).', 'Night Light isn’t installed [wlsunset].'):
        assert not plain(invalid), invalid
    shown=json.loads(ipc(q,'shownTexts',json.dumps(dict(list=states))));assert len(shown)==len(states)*15,len(shown)
    raw={k:v for k,v in shown.items() if not plain(v)};assert not raw,raw
    bar=json.loads(ipc(q,'barTexts','fixture_code_x'));assert bar and all(map(plain,bar)),bar
    down=json.loads(ipc(q,'downTexts'));assert all(map(plain,down)),down
    # A failed launch's drawer notice (ShellScene "Couldn’t open …") gets a plain sentence; the
    # raw reason goes to the log only.
    got,text=failed_launches()
    assert got=={'fixture-term':'The terminal it needs isn’t installed.','fixture-fail':'The app didn’t start.','fixture-crash':'The app didn’t start.'},got
    assert all(map(plain,got.values())) and all(x in text for x in ('fixture-raw-line','terminal_missing','invalid_response')),text
    ipc(q,'systemAbsent');s=state(q)
    assert s['services']['audio']=='unavailable' and s['services']['networks']==0 and s['services']['battery']==-1
    action(q,'volume',40,'unavailable')
    # Actual QS watcher on a private bus: second instance neither loads singleton nor takes name.
    a,owner=start('b',True);wait(a,lambda s:s['services']['tray']=='active');assert bus()==owner.pid
    traylog=(root/'item.log').open('w')
    item=subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/fixtures/tray-item.py'),str(root)],stdout=traylog,stderr=subprocess.STDOUT);processes.append((item,traylog))
    wait(a,lambda s:s['services']['tray_count']==1)
    ipc(a,'system','tray');ipc(a,'systemRow',0)
    until=time.monotonic()+3*SCALE
    while ipc(a,'trayMenuCount')=='0' and time.monotonic()<until: time.sleep(.05)
    assert ipc(a,'trayMenuCount')=='2'
    ipc(a,'trayMenuFirst')
    until=time.monotonic()+3*SCALE
    while not (root/'tray-activated').exists() and time.monotonic()<until: time.sleep(.05)
    assert (root/'tray-activated').read_text()=='clicked';(root/'tray-activated').unlink()
    # Nested DBusMenu: the submenu entry is a QsMenuHandle for a second QsMenuOpener.
    ipc(a,'trayOpenSub',1)
    until=time.monotonic()+3*SCALE
    while ipc(a,'traySubCount')=='0' and time.monotonic()<until: time.sleep(.05)
    assert ipc(a,'traySubCount')=='1'
    ipc(a,'traySubFirst')
    until=time.monotonic()+3*SCALE
    while not (root/'tray-activated').exists() and time.monotonic()<until: time.sleep(.05)
    assert (root/'tray-activated').read_text()=='clicked-3'
    assert 'PRIVATE_SUB' not in ipc(a,'status')
    assert 'PRIVATE_TRAY' not in ipc(a,'status')
    b,other=start('c',True);wait(b,lambda s:s['services']['tray']=='owned_elsewhere');assert bus()==owner.pid
    owner.terminate();owner.wait(timeout=3);time.sleep(.3)
    assert state(b)['services']['tray']=='owned_elsewhere'
    assert subprocess.run(['busctl','--user','--quiet','status','org.kde.StatusNotifierWatcher'],capture_output=True).returncode != 0
    print('PASS: system readings/panels, sound/network/BT confirmation+timeout, private helper, power confirmation, battery threshold, tray ownership:',root)
finally:
    for p,log in processes:
        if p.poll() is None:p.terminate()
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:p.kill();p.wait()
        log.close()
for file in root.glob('*.log'):
    data=file.read_text()
    assert not any(x in data for x in ('WARN','ERROR','TypeError','ReferenceError','PRIVATE_WIFI','PRIVATE_BT','PRIVATE_TRAY','PRIVATE_HIDDEN','PRIVATE:VPN','PRIVATE_SUB','PRIVATE_APP','PRIVATE_MEDIA','fixture-password')),data
assert launches(root, ['fixture-web']) == ['fixture-web']
