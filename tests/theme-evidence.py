#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture production theme surfaces with isolated fixture services, and decoder cost."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from runtime_fixture import runtime_path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.cache/themes-evidence'
OUT.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('pictures', ROOT / 'tests/theme-pictures.py')
pictures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pictures)
staged = OUT / 'shell'
pictures.stage(ROOT, staged)
for script in (staged / 'manager').glob('*.js'):
    script.write_text(script.read_text().replace('file:@EMAKI_DATADIR@/shell', '..'))
with (staged / 'qmldir').open('a') as module:
    module.write('ShellRender 1.0 ShellRender.qml\nSettingsVisualTest 1.0 SettingsVisualTest.qml\nThemeShots 1.0 ThemeShots.qml\n')
assets = Path(os.environ.get('EMAKI_THEME_ASSETS', ROOT / '.cache/themes-assets'))
styles = json.loads((assets / 'catalog.json').read_text())['styles']
for style in styles:
    for key in ('wallpaper', 'overview_video', 'overview_still', 'thumbnail'):
        style[key] = str(assets / style[key])
    style['available'] = True
for name in ('home', 'config', 'data', 'cache', 'state'):
    (OUT / name).mkdir(exist_ok=True)
video = OUT / 'probe.mp4'
if 'EMAKI_THEME_VIDEO' not in os.environ:
    sys.exit('Set EMAKI_THEME_VIDEO to an overview video file (any short MP4) for the theme page renders.')
probe = Path(os.environ['EMAKI_THEME_VIDEO'])
shutil.copyfile(probe, video)
qml = '''import QtQuick
import Quickshell
ShellRoot {
 Window {
  id: win; visible: true; width: 1536; height: 1460; color: "#f2f0ed"
  QtObject {
   id: catalog; property bool writing: false; property var styles: STYLES
   property var values: ({})
   function value(key) { return values[key]; }
   function row(key) { return {value:values[key],editable:true}; }
   function set(key,id) {
    const style=styles.find(s=>s.id===id);
    values={"appearance.style":id,"appearance.accent":style.accent,"appearance.wallpaper":style.wallpaper,"appearance.overview_video":style.overview_video,"appearance.overview_still":style.overview_still};
    SettingsBridge.values=Object.keys(values).map(k=>({key:k,value:values[k]}));
   }
  }
  Rectangle { id: scene; anchors.fill: parent; color: "#f2f0ed"
   Flickable {
    id: listing; anchors.fill: parent; anchors.margins: 24; contentHeight: page.height; clip: true
    SettingsThemesPage { id: page; width: parent.width; catalog: catalog }
   }
   Loader { id: apps; anchors.fill: parent }
   Loader { id: overview; anchors.fill: parent }
   Text { anchors.bottom: parent.bottom; anchors.right: parent.right; text: "Isolated offscreen fixture • production components"; color: "#555555"; font.pixelSize: 13 }
  }
  function capture(name) { scene.grabToImage(result=>result.saveToFile(OUT+"/"+name+".png")); }
  property int step: 0
  Timer {
   interval: 1800; running:true; repeat:true
   onTriggered: {
    if(win.step===0) {catalog.set("appearance.style","emaki");}
    if(win.step===1) {win.capture("01-catalog-emaki");}
    if(win.step===2) {page.selectStyle(catalog.styles.find(s=>s.id==="07-blue-light"));}
    if(win.step===3) {win.capture("02-catalog-blue");}
    if(win.step===4) {listing.visible=false; apps.setSource("ThemeShots.qml",{shot:"programs-browse",theme:"light",accent:"blue"});}
    if(win.step===6) {win.capture("03-programs-blue");}
    if(win.step===7) {apps.setSource("ThemeShots.qml",{shot:"dock",theme:"light",accent:"blue"});}
    if(win.step===9) {win.capture("04-shell-blue");}
    if(win.step===10) {apps.active=false;overview.setSource("OverviewVideoContent.qml",{videoPath:VIDEO,stillPath:STILL,playing:true});}
    if(win.step===12) {win.capture("05-overview-video");frames.running=true;}
    if(win.step===14) {console.log("MEASURE_OPEN");}
    if(win.step===19) {console.log("MEASURE_OPEN_END",overview.item?.videoReady ?? false);overview.item.playing=false;}
    if(win.step===21) {console.log("MEASURE_CLOSED");}
    if(win.step===26) {console.log("MEASURE_CLOSED_END",!overview.item.decoderLoaded);Qt.quit();}
    win.step++;
   }
  }
  property int frame: 0
  Timer { id:frames; interval:100; repeat:true; running:false; onTriggered:{win.capture("frame-"+String(win.frame).padStart(3,"0"));win.frame++;if(win.frame===30)running=false;} }
 }
}
'''
for token, value in [('STYLES', styles), ('OUT', str(OUT)), ('VIDEO', str(video)), ('STILL', styles[0]['overview_still'])]:
    qml = qml.replace(token, json.dumps(value))
(staged / 'evidence.qml').write_text(qml)
env = dict(os.environ, HOME=str(OUT / 'home'), XDG_CONFIG_HOME=str(OUT / 'config'), XDG_CONFIG_DIRS=str(OUT / 'config'), XDG_DATA_HOME=str(OUT / 'data'), XDG_DATA_DIRS=str(OUT / 'data'), XDG_CACHE_HOME=str(OUT / 'cache'), XDG_STATE_HOME=str(OUT / 'state'), XDG_RUNTIME_DIR=str(runtime_path(OUT)), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1', QT_FFMPEG_DECODING_HW_DEVICE_TYPES=',', DBUS_SESSION_BUS_ADDRESS='unix:path='+str(OUT/'no-bus'), DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(OUT/'no-system-bus'), QT_QUICK_CONTROLS_STYLE='Basic', EMAKI_BIN='', TEST_STOREFRONT=(ROOT/'update-manager/storefront.json').read_text(), TEST_SHELF=json.dumps(json.loads((ROOT/'update-manager/emaki-shelf.json').read_text()).get('items', [])), TEST_ICON=(ROOT/'art/logo/mark.svg').as_uri(), TEST_SCREENSHOT='', EMAKI_SHELL_TRAY='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_TEST_SYSTEM='0', EMAKI_TEST_MPRIS='0', LP_NUM_THREADS='1')
for key in ('DISPLAY','WAYLAND_DISPLAY','WAYLAND_SOCKET','NIRI_SOCKET','QT_QPA_PLATFORMTHEME'):
    env.pop(key,None)
def record_overview():
    """Record actual production VideoOutput frames through redirected software GL."""
    render = pictures.RENDER
    render.CACHE = OUT / 'render'
    imports = render.build_plugin()
    frames = OUT / 'recorded-frames'
    frames.mkdir(exist_ok=True)
    for old in frames.glob('frame-*.png'):
        old.unlink()
    options = dict(width=960, height=540, warmup=1000, milliseconds=2000,
                   **{'frames-directory': str(frames), 'frame-interval-ms': 125,
                      'ready-property': 'videoReady', 'stats': str(OUT/'recording.json')})
    (staged/'record.qml').write_text('''import QtQuick
import Quickshell
import EmakiRender
ShellRoot {
 OverviewVideoContent {id:video;width:960;height:540;videoPath:VIDEO;stillPath:STILL;playing:true}
 RenderProbe {id:probe}
 Timer {interval:1;running:true;onTriggered:{probe.render(video,OPTIONS);Qt.quit();}}
}
'''.replace('VIDEO',json.dumps(str(video))).replace('STILL',json.dumps(styles[7]['overview_still'])).replace('OPTIONS',json.dumps(options)))
    record_env=dict(env,QT_QUICK_BACKEND='rhi',EGL_PLATFORM='surfaceless',LIBGL_ALWAYS_SOFTWARE='1',GALLIUM_DRIVER='llvmpipe',QML_IMPORT_PATH=str(imports))
    result=subprocess.run(['nice','-n','19','qs','-p',str(staged/'record.qml'),'--no-color'],env=record_env,capture_output=True,text=True,timeout=40)
    (OUT/'recording.log').write_text(result.stdout+result.stderr)
    assert result.returncode == 0, result.stdout+result.stderr
    stats=json.loads((OUT/'recording.json').read_text())
    assert 'llvmpipe' in stats['renderer'] and len(stats['recording']['frames']) >= 6, stats
    from PIL import Image, ImageChops
    images=sorted(frames.glob('frame-*.png'))
    assert ImageChops.difference(Image.open(images[0]).convert('RGB'),Image.open(images[-1]).convert('RGB')).getbbox(), 'Video frames did not change'
    # PNG readback may be slower than the requested cadence on a busy machine.
    # Preserve actual timestamps instead of speeding those captured frames up.
    captured=stats['recording']['frames']
    timeline=[]
    for index, frame in enumerate(captured):
        end=captured[index+1]['elapsed_ms'] if index+1 < len(captured) else stats['measurement_ms']
        timeline.extend(["file '"+frame['file']+"'", 'duration '+str(max(1,end-frame['elapsed_ms'])/1000)])
    timeline.append("file '"+captured[-1]['file']+"'")
    (frames/'timeline.txt').write_text('\n'.join(timeline)+'\n')
    subprocess.run(['nice','-n','19','ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(frames/'timeline.txt'),'-threads','1','-fps_mode','vfr','-c:v','libx264','-pix_fmt','yuv420p',str(OUT/'overview-recording.mp4')],check=True,timeout=30)
    print('PASS: actual overview video framebuffer recording; '+str(len(images))+' frames',flush=True)

if '--record-only' in sys.argv:
    record_overview()
    raise SystemExit()

command = ['nice','-n','19','qs','-p',str(staged/'evidence.qml'),'--no-color']
process = subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
metrics = {'command':command,'mode':'offscreen software (VideoOutput not rendered); fixture services; decoder software; no compositor or hardware decode proof','video':str(probe),'phases':{}}
starts = {}
lines=[]
def usage():
    fields=Path(f'/proc/{process.pid}/stat').read_text().split()
    status=Path(f'/proc/{process.pid}/status').read_text().splitlines()
    return {'time':time.monotonic(),'ticks':int(fields[13])+int(fields[14]),'rss_kib':int(next(line for line in status if line.startswith('VmRSS:')).split()[1])}
for line in process.stdout:
    lines.append(line)
    (OUT / 'run.log').write_text(''.join(lines))
    for phase in ('OPEN','CLOSED'):
        if 'MEASURE_'+phase+'_END' in line:
            end=usage();start=starts[phase];seconds=end['time']-start['time'];ticks=end['ticks']-start['ticks']
            metrics['phases'][phase.lower()]={'seconds':seconds,'cpu_ticks':ticks,'cpu_percent_one_core':100*ticks/os.sysconf('SC_CLK_TCK')/seconds,'rss_start_kib':start['rss_kib'],'rss_end_kib':end['rss_kib'],'state_log':line.strip()}
        elif 'MEASURE_'+phase in line:
            starts[phase]=usage()
process.wait(timeout=10)
metrics['returncode']=process.returncode
(OUT/'run.log').write_text(''.join(lines))
(OUT/'metrics.json').write_text(json.dumps(metrics,indent=2)+'\n')
print(json.dumps(metrics,indent=2))
if process.returncode:
    raise SystemExit('Evidence failed; see '+str(OUT/'run.log'))
# Software VideoOutput frames are blank; do not present them as a recording.
(OUT/'overview-offscreen.mp4').unlink(missing_ok=True)

# Use the existing redirected render probe for real VideoOutput pixels. The ordinary
# offscreen platform above cannot render VideoOutput with its software scenegraph.
render = pictures.RENDER
render.CACHE = OUT / 'render'
compatible = Path(os.environ.get('EMAKI_RENDER_PLUGIN', ROOT / '.cache/render-shell/plugin'))
if compatible.exists() and not (render.CACHE/'plugin/EmakiRender/librendershell.so').exists():
    shutil.copytree(compatible, render.CACHE / 'plugin', dirs_exist_ok=True)
rhi_qml = '''import QtQuick
import Quickshell
Item {
 id: root; width:1536; height:1000
 property string shot:"catalog"; property string chosen:"emaki"
 readonly property bool ready: shot==="catalog" ? true : shot==="overview" ? (surface.item && surface.item.videoReady) : (surface.item && surface.item.ready)
 readonly property var renderStats: ({shot:shot, chosen:chosen, videoReady:shot==="overview" && surface.item && surface.item.videoReady})
 QtObject {
  id: themeCatalog; property bool writing:false; property var styles: STYLES; property var values: ({})
  function value(key) {return values[key];}
  function row(key) {return {value:values[key],editable:true};}
  function set(key,id) {
   const style=styles.find(s=>s.id===id);
   values={"appearance.style":id,"appearance.accent":style.accent,"appearance.wallpaper":style.wallpaper,"appearance.overview_video":style.overview_video,"appearance.overview_still":style.overview_still};
   SettingsBridge.values=Object.keys(values).map(k=>({key:k,value:values[k]}));
  }
 }
 Rectangle {anchors.fill:parent;color:"#f2f0ed"}
 Loader {id:surface;anchors.fill:parent;sourceComponent:root.shot==="catalog"?listing:root.shot==="overview"?video:other}
 Component {id:listing;Flickable {anchors.fill:parent;anchors.margins:24;contentHeight:page.height;clip:true;SettingsThemesPage{id:page;width:parent.width;catalog:themeCatalog}}}
 Component {id:video;OverviewVideoContent{videoPath:VIDEO;stillPath:STILL;playing:true}}
 Component {id:other;ThemeShots{shot:root.shot;accent:"blue";theme:"light";wallpaper:STILL}}
 Text {anchors.bottom:parent.bottom;anchors.right:parent.right;text:"Isolated fixture • production components • software rendering";color:"#555555";font.pixelSize:13}
 Component.onCompleted: themeCatalog.set("appearance.style",chosen)
}
'''
for token,value in [('STYLES',styles),('VIDEO',str(video)),('STILL',styles[7]['overview_still'])]:
    rhi_qml=rhi_qml.replace(token,json.dumps(value))
(staged/'RhiEvidence.qml').write_text(rhi_qml)
shelf=json.loads((ROOT/'update-manager/emaki-shelf.json').read_text())
if isinstance(shelf,dict):
    shelf=shelf.get('items',shelf.get('programs',[]))
env['TEST_SHELF']=json.dumps(shelf)
for name,shot,chosen,height in [('01-catalog-emaki','catalog','emaki',1460),('02-catalog-blue','catalog','07-blue-light',1460),('03-programs-blue','programs-browse','07-blue-light',1000),('04-shell-blue','system-sound','07-blue-light',1000),('05-overview-video','overview','07-blue-light',864)]:
    result=render.render(staged/'RhiEvidence.qml',png=OUT/(name+'.png'),stats=OUT/(name+'.json'),properties={'shot':shot,'chosen':chosen,'height':height},width=1536,height=height,warmup=2000,milliseconds=150,ready_property='ready',extra_env={k:v for k,v in env.items() if k.startswith(('EMAKI_','TEST_','QT_FFMPEG','LP_NUM'))},shader_dir=Path(os.environ.get('EMAKI_SHADER_DIR', ROOT / '.cache/shell-shaders')))
    (OUT/(name+'.log')).write_text(result)
    print('Rendered',name,flush=True)

(staged/'PlaybackCost.qml').write_text('''import QtQuick
Item {
 width:960;height:540
 OverviewVideoContent {id:video;anchors.fill:parent;videoPath:VIDEO;stillPath:STILL;playing:true}
 property int elapsed:0
 Timer {interval:1000;repeat:true;running:true;onTriggered:{
  parent.elapsed++;
  if(parent.elapsed===4)console.log("MEASURE_OPEN");
  if(parent.elapsed===12){console.log("MEASURE_OPEN_END",video.videoReady);video.playing=false;}
  if(parent.elapsed===14)console.log("MEASURE_CLOSED");
  if(parent.elapsed===22)console.log("MEASURE_CLOSED_END",!video.decoderLoaded);
 }}
}
'''.replace('VIDEO',json.dumps(str(video))).replace('STILL',json.dumps(styles[7]['overview_still'])))
(staged/'cost.qml').write_text('''import QtQuick
import Quickshell
import EmakiRender
ShellRoot {
 Loader {id:fixture;source:"PlaybackCost.qml";onLoaded:begin.start()}
 RenderProbe {id:probe}
 Timer {id:begin;interval:1;onTriggered:{probe.render(fixture.item,{width:960,height:540,warmup:0,milliseconds:24000});Qt.quit();}}
}
''')
cost_env=dict(env,QT_QUICK_BACKEND='rhi',EGL_PLATFORM='surfaceless',LIBGL_ALWAYS_SOFTWARE='1',GALLIUM_DRIVER='llvmpipe',QML_IMPORT_PATH=str(render.CACHE/'plugin'))
command=['nice','-n','19','qs','-p',str(staged/'cost.qml'),'--no-color']
process=subprocess.Popen(command,env=cost_env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
starts={};lines=[]
metrics={'command':command,'mode':'960x540 offscreen surfaceless llvmpipe; software decode and rendering; no compositor','video':str(probe),'phases':{}}
for line in process.stdout:
    lines.append(line)
    (OUT/'render-cost.log').write_text(''.join(lines))
    for phase in ('OPEN','CLOSED'):
        if 'MEASURE_'+phase+'_END' in line:
            end=usage();start=starts[phase];seconds=end['time']-start['time'];ticks=end['ticks']-start['ticks']
            metrics['phases'][phase.lower()]={'seconds':seconds,'cpu_ticks':ticks,'cpu_percent_one_core':100*ticks/os.sysconf('SC_CLK_TCK')/seconds,'rss_start_kib':start['rss_kib'],'rss_end_kib':end['rss_kib'],'state_log':line.strip()}
        elif 'MEASURE_'+phase in line:
            starts[phase]=usage()
process.wait(timeout=10)
metrics['returncode']=process.returncode
(OUT/'render-cost.json').write_text(json.dumps(metrics,indent=2)+'\n')
print(json.dumps(metrics,indent=2))

assert process.returncode == 0 and set(metrics['phases']) == {'open','closed'}, metrics
assert all(phase['state_log'].endswith(' true') for phase in metrics['phases'].values()), metrics
