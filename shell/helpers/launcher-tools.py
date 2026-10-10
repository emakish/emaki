#!/usr/bin/env python3
"""Private bounded JSON pipe for launcher operations; no payloads in diagnostics."""
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emaki_paths
import app_scope
import clipboard_store

class Refused(Exception):
    pass

def run(argv, data=None, limit=8*1024*1024, env=None, check=True, timeout=3):
    # Drain stdout with a bound; never log child output or inherit its protocol pipe.
    with subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, env=env) as p:
        sel = selectors.DefaultSelector()
        os.set_blocking(p.stdout.fileno(), False)
        sel.register(p.stdout, selectors.EVENT_READ)
        if data is not None:
            os.set_blocking(p.stdin.fileno(), False)
            sel.register(p.stdin, selectors.EVENT_WRITE)
        output = bytearray(); offset = 0; deadline = time.monotonic()+timeout
        try:
            while sel.get_map():
                if time.monotonic() >= deadline:
                    raise Refused('timeout')
                for key, mask in sel.select(.05):
                    if mask & selectors.EVENT_READ:
                        chunk = os.read(key.fd, min(65536, limit+1-len(output)))
                        if not chunk: sel.unregister(key.fileobj)
                        output.extend(chunk)
                        if len(output)>limit: raise Refused('output_limit')
                    else:
                        if offset < len(data):
                            offset += os.write(key.fd, data[offset:offset+65536])
                        if offset == len(data):
                            sel.unregister(key.fileobj); key.fileobj.close()
            if p.wait(timeout=max(.01, deadline-time.monotonic())) != 0 and check:
                raise Refused('operation_failed')
            return bytes(output)
        finally:
            sel.close()
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()

def clip(request):
    # Missing history is a read-only empty state; cliphist list otherwise creates a DB.
    db = clipboard_store.runtime_db()
    if not db.is_file():
        if request['op']=='clip-list': return dict(state='ready', entries=[])
        if request['op']=='clip-clear': return dict(state='deleted')
        raise Refused('clipboard_missing')
    prefix = [os.environ.get('EMAKI_CLIPHIST','cliphist'), '-config-path', '/dev/null', '-db-path', str(db), '-preview-width', '512']
    op = request['op']
    if op=='clip-clear':
        run(prefix+['wipe']); return dict(state='deleted')
    if op=='clip-list':
        output=run(prefix+['list'],limit=1024*1024).decode('utf-8','replace')
        rows=[]
        for line in output.splitlines()[:750]:
            ident, sep, preview=line.partition('\t')
            if sep and ident.isdigit():
                rows.append(dict(id=ident, preview=preview[:512], image=preview.startswith('[[ binary data')))
        return dict(state='ready', entries=rows)
    ident=str(request.get('id',''))
    if not re.fullmatch(r'[0-9]{1,20}',ident): raise Refused('invalid_clip_id')
    data=(ident+'\t\n').encode()
    if op=='clip-delete': run(prefix+['delete'],data); return dict(state='deleted')
    payload=run(prefix+['decode'],data)
    if not payload: raise Refused('clipboard_missing')
    mime='image/png' if payload.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if payload.startswith(b'\xff\xd8\xff') else None
    args=[os.environ.get('EMAKI_WL_COPY','wl-copy')]
    if mime: args+=['--type',mime]
    if not app_scope.copy(args, payload): raise Refused('copy_failed')
    return dict(state='copied')

def gio():
    import gi
    gi.require_version('Gio','2.0')
    from gi.repository import Gio, GLib
    return Gio, GLib

def desktop_quote(text):
    return '"'+str(text).replace('%','%%').replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'

def terminal(request):
    Gio, GLib=gio()
    ident=request.get('id','')
    if not isinstance(ident,str) or '/' in ident or len(ident)>512: raise Refused('invalid_desktop_id')
    app=Gio.DesktopAppInfo.new(ident if ident.endswith('.desktop') else ident+'.desktop')
    if app is None: raise Refused('desktop_missing')
    key=GLib.KeyFile(); key.load_from_file(app.get_filename(),GLib.KeyFileFlags.NONE)
    if not key.get_boolean('Desktop Entry','Terminal'): raise Refused('not_terminal_app')
    command=key.get_string('Desktop Entry','Exec')
    executable=request.get('terminal','emaki-terminal')
    if not isinstance(executable,str) or not executable or '\x00' in executable: raise Refused('terminal_missing')
    import shutil
    if not shutil.which(executable): raise Refused('terminal_missing')
    # GIO performs field-code/argv expansion on the original entry, without a shell.
    # The in-memory clone has no filename: preserve %k explicitly, leaving %%k literal.
    command=re.sub(r'%%|%k', lambda m: '%%' if m[0]=='%%' else desktop_quote(app.get_filename()), command)
    # The wrapper resolves defaults.terminal from the managed settings store.
    separator = ' -- ' if Path(executable).name == 'emaki-terminal' else ' -e '
    key.set_string('Desktop Entry','Exec',desktop_quote(executable)+separator+command)
    key.set_boolean('Desktop Entry','Terminal',False)
    key.set_boolean('Desktop Entry','DBusActivatable',False)
    launch=Gio.DesktopAppInfo.new_from_keyfile(key)
    if not launch or not app_scope.gio_launch(launch, keyfile=key, app_id=ident): raise Refused('launch_failed')
    return dict(state='requested')

def frequent(request):
    root=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    if not root.is_absolute(): raise Refused('invalid_state_home')
    folder=root/'emaki'; path=folder/'frequent.json'
    def read():
        if not path.exists(): return {}
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise Refused('invalid_frequent')
            raw=f.read(262145)
        if len(raw)>262144: raise Refused('invalid_frequent')
        value=json.loads(raw)
        if not isinstance(value,dict) or len(value)>2048 or any(not isinstance(k,str) or len(k)>512 or '/' in k or type(v) is not int or not 0<=v<=2147483647 for k,v in value.items()): raise Refused('invalid_frequent')
        return value
    if request['op']=='frequent-list': return dict(state='ready', counts=read())
    ident=request.get('id')
    if not isinstance(ident,str) or not ident or '/' in ident or len(ident)>512: raise Refused('invalid_desktop_id')
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(folder/'frequent.lock',os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Refused('frequent_busy') from None
        counts=read()
        if ident not in counts and len(counts)>=2048: raise Refused('frequent_limit')
        counts[ident]=min(2147483647,counts.get(ident,0)+1)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=folder,prefix='.frequent-',delete=False) as f:
                tmp=f.name; json.dump(counts,f,sort_keys=True); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)
    return dict(state='ready',counts=counts)

RECENT_KINDS=('app','file','page','window','clip')

def recent(request):
    # What was last opened through the launcher, newest first, one entry per (kind, ref):
    # app = desktop id, file = path, page = settings page, window = app id (never a title),
    # clip = cliphist id. $XDG_STATE_HOME/emaki/recent.json, at most 64 entries.
    root=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    if not root.is_absolute(): raise Refused('invalid_state_home')
    folder=root/'emaki'; path=folder/'recent.json'
    def valid(entry):
        return (isinstance(entry,dict) and entry.get('kind') in RECENT_KINDS and isinstance(entry.get('ref'),str)
                and 0<len(entry['ref'])<=4096 and '\n' not in entry['ref'] and type(entry.get('t')) is int and 0<=entry['t']<=2**53)
    def read():
        if not path.exists(): return []
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise Refused('invalid_recent')
            raw=f.read(524289)
        if len(raw)>524288: raise Refused('invalid_recent')
        value=json.loads(raw)
        if not isinstance(value,list) or len(value)>64 or not all(valid(e) for e in value): raise Refused('invalid_recent')
        return value
    if request['op']=='recent-list': return dict(state='ready', entries=read())
    kind=request.get('kind'); ref=request.get('ref')
    if kind not in RECENT_KINDS or not isinstance(ref,str) or not ref or '\n' in ref or len(ref)>4096: raise Refused('invalid_recent_entry')
    if kind in ('app','window','page') and '/' in ref: raise Refused('invalid_recent_entry')
    if kind=='file' and not os.path.isabs(ref): raise Refused('invalid_recent_entry')
    if kind=='clip' and not re.fullmatch(r'[0-9]{1,20}',ref): raise Refused('invalid_recent_entry')
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(folder/'recent.lock',os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise Refused('recent_busy') from None
        try: entries=read()
        except Refused: entries=[]   # a damaged log is replaced, not fatal
        entries=[dict(kind=kind,ref=ref,t=int(time.time()*1000))]+[e for e in entries if not (e['kind']==kind and e['ref']==ref)]
        entries=entries[:64]
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=folder,prefix='.recent-',delete=False) as f:
                tmp=f.name; json.dump(entries,f); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if tmp and os.path.exists(tmp): os.unlink(tmp)
    return dict(state='ready',entries=entries)

def handle(request):
    op=request['op']
    if op in ('clip-list','clip-copy','clip-delete','clip-clear'): return clip(request)
    if op=='terminal': return terminal(request)
    if op.startswith('frequent-') and op in ('frequent-list','frequent-record'): return frequent(request)
    if op in ('recent-list','recent-record'): return recent(request)
    if op=='settings':
        profile=request.get('profile','')
        if profile and not os.path.isabs(profile): raise Refused('isolated_profile_required')
        action=request.get('action'); args=request.get('args',[])
        if action not in ('list','get','history','undo','set','reset') or not isinstance(args,list) or not all(isinstance(a,str) for a in args): raise Refused('invalid_request')
        env = dict(os.environ)
        command = [request.get('binary') or 'emaki', 'settings', action, *args, '--json']
        if profile:
            env.update(XDG_CONFIG_HOME=profile+'/config', XDG_STATE_HOME=profile+'/state', XDG_RUNTIME_DIR=profile+'/runtime')
            command += ['--profile-root', profile]
        value=json.loads(run(command,env=env,check=False,limit=1024*1024,timeout=30))
        return dict(state=value['reason'],status=value['status'],settings=value.get('settings',[]),history=value.get('history',[]),change_id=value.get('change_id'),session_applied=value['session_applied'])
    if op=='defaults':
        # Roles with a core key list the installed candidates (GIO handlers of the MIME,
        # terminals by the TerminalEmulator category); the others stay read-only.
        Gio,_=gio(); values=[]
        # Candidates come from the entries themselves (MimeType/Categories), so a
        # missing mimeinfo.cache does not hide an installed application.
        shown=[a for a in Gio.AppInfo.get_all() if a.should_show() and isinstance(a,Gio.DesktopAppInfo)]
        def entry(app): return dict(id=app.get_id(),name=app.get_display_name())
        for role,key,mime in [('Browser','defaults.browser','x-scheme-handler/https'),('Terminal','defaults.terminal',None),('Files','defaults.files','inode/directory'),('Text editor',None,'text/plain'),('Image viewer',None,'image/png'),('Video player',None,'video/mp4')]:
            if mime:
                app=Gio.AppInfo.get_default_for_type(mime,False)
                choices=[entry(a) for a in shown if mime in (a.get_supported_types() or [])][:32] if key else []
            else:
                app=None
                choices=[entry(a) for a in shown if 'TerminalEmulator' in (a.get_categories() or '').split(';')][:32]
            values.append(dict(label=role,key=key,value=app.get_display_name() if app else 'Unset',id=app.get_id() if app else None,choices=choices))
        return dict(state='ready',entries=values)
    if op=='xkb-layouts':
        # `! layout` of the installed XKB rules list: code and human name, as the core validates.
        rules=Path(os.environ.get('EMAKI_XKB_RULES') or emaki_paths.XKB_RULES)
        if not rules.is_file(): raise Refused('xkb_rules_missing')
        with open(rules,'rb') as f: raw=f.read(1024*1024+1)
        if len(raw)>1024*1024: raise Refused('xkb_rules_missing')
        section=False; rows=[]
        for line in raw.decode('utf-8','replace').splitlines():
            if line.startswith('!'): section=line.strip()=='! layout'
            elif section and line.split():
                code,_,name=line.strip().partition(' ')
                rows.append(dict(code=code,name=name.strip() or code))
        return dict(state='ready',entries=rows[:512])
    if op=='wallpaper-choose':
        import wallpaper_chooser
        return wallpaper_chooser.choose()
    if op=='wallpapers':
        # Candidate pictures for the page: the packaged/user Emaki wallpaper folders only.
        roots=[Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')]
        roots+=[Path(p) for p in (os.environ.get('XDG_DATA_DIRS') or emaki_paths.XDG_DATA_DIRS_DEFAULT).split(':') if p]
        rows=[]; seen=set()
        shipped=Path(emaki_paths.DATADIR)/'wallpaper/fallback.png'
        if shipped.is_file():
            rows.append(dict(path=str(shipped),name='Emaki landscape'))
            seen.add(shipped.name)
        for root in roots:
            folder=root/'emaki/wallpapers'
            if not folder.is_dir(): continue
            for path in sorted(folder.iterdir()):
                if path.suffix.lower() in ('.png','.jpg','.jpeg','.webp') and path.is_file() and path.name not in seen and len(rows)<64:
                    seen.add(path.name); rows.append(dict(path=str(path),name=path.stem))
        # Resolve only a single static inherited picture shared by every output.
        # Mixed-output and slideshow configurations have no single current tile.
        inherited = ''
        try:
            import tomllib
            config_root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
            with (config_root/'wpaperd/config.toml').open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('config_limit')
            config = tomllib.loads(raw.decode())
            if not all(re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key) and isinstance(value, dict)
                       for key, value in config.items()):
                raise ValueError('output_selection_unsupported')
            default = config.get('default', {})
            selectors = [key for key in config if key not in ('default', 'any')]
            options = [dict(default, **config.get('any', {}))]
            options += [dict(default, **config[key]) for key in selectors]
            paths = {str(Path(option['path']).expanduser()) for option in options
                     if isinstance(option.get('path'), str) and option['path']}
            if len(paths) == 1 and all(option.get('path') for option in options):
                path = Path(paths.pop())
                if path.is_absolute() and path.is_file():
                    inherited = str(path)
        except (OSError, ValueError, UnicodeError):
            pass
        return dict(state='ready',entries=rows,inheritedPath=inherited)
    if op=='web':
        query=request.get('query','')
        if not isinstance(query,str) or not query.strip() or len(query)>4096: raise Refused('invalid_query')
        Gio,_=gio()
        url='https://duckduckgo.com/?q='+quote(query,safe='')
        handler=Gio.AppInfo.get_default_for_uri_scheme('https')
        if not handler: raise Refused('no_handler')
        if not app_scope.gio_launch(handler, uris=[url]): raise Refused('open_failed')
        return dict(state='requested')
    raise Refused('invalid_request')

def main():
    protocol=os.fdopen(os.dup(1),'w')
    with open(os.devnull,'w') as null:
        os.dup2(null.fileno(),1); os.dup2(null.fileno(),2)
    try:
        raw=sys.stdin.buffer.readline(16385)
        if len(raw)>16384: raise Refused('invalid_request')
        value=handle(json.loads(raw))
    except Refused as e: value=dict(state=str(e))
    except FileNotFoundError: value=dict(state='helper_missing')
    except PermissionError: value=dict(state='access_denied')
    except (TimeoutError,subprocess.TimeoutExpired): value=dict(state='timeout')
    except Exception: value=dict(state='operation_failed')
    protocol.write(json.dumps(dict(schema_version=1,**value),ensure_ascii=True)+'\n'); protocol.flush()

if __name__=='__main__': main()
