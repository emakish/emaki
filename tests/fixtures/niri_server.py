"""Bounded local niri 26.04 fixture, shared by CLI/QML integration checks."""
import json
import socketserver
import threading


def window(ident, workspace, focused, app_id='PRIVATE_APP', title='PRIVATE_TITLE',
           tile_pos=None, tile_size=(700., 600.)):
    return dict(id=ident, title=title, app_id=app_id, pid=1234,
                workspace_id=workspace, is_focused=focused, is_floating=False, is_urgent=False,
                focus_timestamp=None, layout=dict(pos_in_scrolling_layout=[1, 1],
                    tile_size=list(tile_size), window_size=[696, 596],
                    tile_pos_in_workspace_view=tile_pos, window_offset_in_tile=[2., 2.]))


def workspace(ident, index, focused, active_window):
    return dict(id=ident, idx=index, name='PRIVATE_WORKSPACE', output='A', is_urgent=False,
                is_active=focused, is_focused=focused, active_window_id=active_window)


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.errors = []
        self.streams = []
        self.logical = None
        self.reset()
        super().__init__(str(path), Handler)

    def reset(self):
        with self.lock:
            self.windows = [window(1, 101, True), window(2, 202, False)]
            self.workspaces = [workspace(101, 1, True, 1), workspace(202, 2, False, 2)]
            self.mode = 'apply'
            self.layouts = dict(names=['English (US)', 'Russian'], current_idx=0)
            self.overview_open = False
            self.actions = []

    def set_mode(self, mode):
        with self.lock:
            self.mode = mode

    def overview(self, opened):
        with self.lock:
            self.overview_open = opened
            self.broadcast({'OverviewOpenedOrClosed': {'is_open': opened}})

    def set_windows(self, windows):
        with self.lock:
            self.windows = list(windows)
            self.broadcast({'WindowsChanged': {'windows': self.windows}})

    def open_window(self, row):
        """One window appears, as niri reports it after the snapshot."""
        with self.lock:
            self.windows.append(row)
            self.broadcast({'WindowOpenedOrChanged': {'window': row}})

    def broadcast(self, event):
        """Caller holds the lock. Pushes one event to every EventStream client."""
        line = (json.dumps(event) + '\n').encode()
        alive = []
        for stream in self.streams:
            try:
                stream.write(line)
                stream.flush()
                alive.append(stream)
            except (OSError, ValueError):
                pass
        self.streams = alive


class Handler(socketserver.StreamRequestHandler):
    def send(self, value):
        self.wfile.write((json.dumps(value) + '\n').encode())
        self.wfile.flush()

    def handle(self):
        self.request.settimeout(3)
        try:
            while not self.server.stop.is_set():
                line = self.rfile.readline()
                if not line:
                    return
                request = json.loads(line)
                with self.server.lock:
                    if request == 'Version':
                        self.send({'Ok': {'Version': '26.04 (test)'}})
                    elif request == 'Outputs':
                        self.send({'Ok': {'Outputs': {'A': dict(name='A', make='Fixture', model='Fixture',
                            serial=None, physical_size=None, modes=[], current_mode=None,
                            is_custom_mode=False, vrr_supported=False, vrr_enabled=False,
                            logical=self.server.logical)}}})
                    elif request == 'Windows':
                        self.send({'Ok': {'Windows': self.server.windows}})
                    elif request == 'Workspaces':
                        self.send({'Ok': {'Workspaces': self.server.workspaces}})
                    elif request == 'KeyboardLayouts':
                        self.send({'Ok': {'KeyboardLayouts': self.server.layouts}})
                    elif request == 'OverviewState':
                        self.send({'Ok': {'OverviewState': {'is_open': self.server.overview_open}}})
                    elif request == 'EventStream':
                        self.send({'Ok': 'Handled'})
                        self.send({'WorkspacesChanged': {'workspaces': self.server.workspaces}})
                        self.send({'WindowsChanged': {'windows': self.server.windows}})
                        self.send({'KeyboardLayoutsChanged': {'keyboard_layouts': self.server.layouts}})
                        self.send({'OverviewOpenedOrClosed': {'is_open': self.server.overview_open}})
                        self.server.streams.append(self.wfile)
                    elif isinstance(request, dict) and 'Action' in request:
                        action = request['Action']
                        self.server.actions.append(action)
                        if self.server.mode == 'reject':
                            self.send({'Err': 'PRIVATE_REJECTION'})
                            continue
                        if self.server.mode == 'apply':
                            self.apply(action)
                        self.send({'Ok': 'Handled'})
                    else:
                        raise AssertionError(f'Unexpected niri request: {request!r}')
                if request == 'EventStream':
                    # Observation supplies membership. Action confirmation reads fresh
                    # Windows/Workspaces on its own connection, as on real niri.
                    self.server.stop.wait()
                    return
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as error:
            self.server.errors.append(repr(error))

    def apply(self, action):
        if 'FocusWindow' in action:
            for value in self.server.windows:
                value['is_focused'] = value['id'] == action['FocusWindow']['id']
        elif 'FocusWorkspace' in action:
            for value in self.server.workspaces:
                value['is_focused'] = value['id'] == action['FocusWorkspace']['reference']['Id']
                value['is_active'] = value['is_focused']
        elif 'MoveWindowToWorkspace' in action:
            for value in self.server.windows:
                if value['id'] == action['MoveWindowToWorkspace']['window_id']:
                    value['workspace_id'] = action['MoveWindowToWorkspace']['reference']['Id']
        elif 'CloseWindow' in action:
            self.server.windows = [value for value in self.server.windows if value['id'] != action['CloseWindow']['id']]
            self.server.broadcast({'WindowClosed': {'id': action['CloseWindow']['id']}})
        elif 'SwitchLayout' in action:
            index = action['SwitchLayout']['layout']['Index']
            self.server.layouts['current_idx'] = index
            self.server.broadcast({'KeyboardLayoutSwitched': {'idx': index}})
        else:
            raise AssertionError(f'Unexpected action: {action!r}')

