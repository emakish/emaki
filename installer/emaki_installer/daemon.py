import asyncio
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import struct
import sys

from .arch_backend import load_archinstall, validate_live_plan
from .timezones import set_live_timezone
from .constants import LOG, MAX_FRAME, SOCKET, TARGET, WORK
from .errors import Code, InstallError, require
from .inventory import Inventory
from .protocol import Controller, decode_frame, encode_frame, read_test_mode
from .runtime import Runner, cleanup, safe_log, secure_log
from .restart import ResidentReboot, RestartGuard
from .worker import Worker


def make_worker(api, inventory, broker):
    return Worker(api, inventory, broker.redactor, broker.emit, broker.log,
                  test_mode=broker.test_mode, skip_update=broker.job.skip_update)


def peer_allowed(sock, group_id):
    _pid, uid, gid = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid == 0 or gid == group_id:
        return True
    try:
        user = pwd.getpwuid(uid)
        return group_id in os.getgrouplist(user.pw_name, user.pw_gid)
    except (KeyError, OSError):
        return False


def export_log(dest, runner, redactor, source=LOG, media=Path('/run/media/live')):
    require(isinstance(dest, str), Code.BAD_DEST, 'Choose a log file on removable media.')
    path = Path(dest)
    require(path.is_absolute() and '..' not in path.parts and media in path.parents,
            Code.BAD_DEST, 'Destination must be under /run/media/live/.')
    # Open every directory with O_NOFOLLOW and keep the final descriptor pinned.
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parent.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        result = json.loads(runner.run(['findmnt', '-J', '-T', str(path.parent), '-o', 'TARGET,SOURCE,MAJ:MIN']))
        mounted = result.get('filesystems', [])
        require(len(mounted) == 1 and media in Path(mounted[0]['target']).parents,
                Code.BAD_DEST, 'Destination is not on a mounted removable filesystem.')
        pinned = os.fstat(fd)
        require(mounted[0].get('maj:min') == f'{os.major(pinned.st_dev)}:{os.minor(pinned.st_dev)}',
                Code.BAD_DEST, 'Destination mount changed during export.')
        source_device = mounted[0]['source'].split('[', 1)[0]
        require(source_device.startswith('/dev/'), Code.BAD_DEST, 'Destination must be block media.')
        tree = json.loads(runner.run(['lsblk', '--tree', '-s', '-J', '-o', 'TYPE,RM,TRAN', source_device]))

        def removable(nodes):
            return any(x.get('rm') in (True, 1, '1') or x.get('tran') == 'usb'
                       or removable(x.get('children', [])) for x in nodes)

        require(removable(tree.get('blockdevices', [])), Code.BAD_DEST, 'Destination is not removable/USB media.')
        # O_EXCL deliberately refuses overwriting an existing file or symlink.
        output_fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=fd)
        with os.fdopen(output_fd, 'w') as output, source.open() as log:
            for line in log:
                output.write(redactor.text(line.rstrip('\n')) + '\n')
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise InstallError(Code.BAD_DEST, 'Cannot create the log there; choose a new filename on mounted removable media.') from exc
    finally:
        os.close(fd)


class Server:
    def __init__(self, controller, group_id):
        self.controller, self.group_id = controller, group_id

    async def client(self, reader, writer):
        if not peer_allowed(writer.get_extra_info('socket'), self.group_id):
            writer.close()
            return
        queue = asyncio.Queue(maxsize=2048)
        loop = asyncio.get_running_loop()
        closed = False
        replaying = False
        replay_buffer = []
        replay_floor = 0
        subscribed = False

        def enqueue(msg):
            if closed:
                return
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                # A slow or dead UI must never block disk cleanup or the worker.
                writer.close()

        def deliver_event(msg):
            if msg['seq'] <= replay_floor:
                return
            if replaying:
                replay_buffer.append(msg)
            else:
                enqueue(msg)

        def listener(msg):
            loop.call_soon_threadsafe(deliver_event, msg)

        async def send():
            try:
                while True:
                    msg = await queue.get()
                    writer.write(encode_frame(msg))
                    await asyncio.wait_for(writer.drain(), 15)
            finally:
                writer.close()

        sender = asyncio.create_task(send())
        hello = False
        try:
            while not writer.is_closing():
                try:
                    frame = await reader.readuntil(b'\n')
                except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                    break
                if len(frame) > MAX_FRAME:
                    break
                msg = decode_frame(frame)
                if not hello and msg['type'] != 'hello':
                    break
                if msg['type'] in ('confirm', 'resume') and not subscribed:
                    with self.controller.lock:
                        self.controller.listeners.add(listener)
                    subscribed = True
                replaying = msg['type'] == 'resume'
                responses = await asyncio.to_thread(self.controller.handle, msg)
                replay_high = 0
                for response in responses:
                    if callable(response):
                        response()
                    else:
                        if replaying:
                            await asyncio.wait_for(queue.put(response), 15)
                        else:
                            enqueue(response)
                        replay_high = max(replay_high, response['seq'])
                if replaying:
                    # Events emitted before the replay's snapshot are represented
                    # by its logs/current state; newer ones follow that snapshot.
                    for event in replay_buffer:
                        if event['seq'] > replay_high:
                            enqueue(event)
                    replay_buffer.clear()
                    replay_floor = replay_high
                    replaying = False
                if not hello:
                    if not responses or responses[0]['type'] != 'hello':
                        break
                    hello = True
        except (InstallError, ConnectionError, ValueError, TimeoutError):
            pass
        finally:
            closed = True
            with self.controller.lock:
                self.controller.listeners.discard(listener)
            sender.cancel()
            try:
                await sender
            except (asyncio.CancelledError, ConnectionError, InstallError, TimeoutError):
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def run(self):
        stopping = asyncio.Event()

        def stop():
            with self.controller.lock:
                self.controller.stopping = True
                if self.controller.busy:
                    self.controller.job.cancelled.set()
            stopping.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop)
        server = await asyncio.start_unix_server(self.client, path=str(SOCKET), limit=MAX_FRAME)
        os.chown(SOCKET, 0, self.group_id)
        os.chmod(SOCKET, 0o660)
        async with server:
            while not stopping.is_set() or self.controller.busy:
                await asyncio.sleep(0.25)
                self.controller.expire()
            # Leaving 'async with' waits for every client connection to close (Python 3.12+);
            # the installer window never closes its own, so SIGTERM hung forever under
            # TimeoutStopSec=infinity. Close the clients first.
            server.close()
            server.close_clients()


def main():
    try:
        require(os.geteuid() == 0, Code.BAD_REQUEST, 'emaki-installerd must run as root on the live ISO.')
        require(Path('/run/archiso/bootmnt').is_dir(), Code.BAD_REQUEST, 'The live ISO boot mount is missing.')
        os.umask(0o022)
        gid = grp.getgrnam('emaki-install').gr_gid
        require(not WORK.is_symlink(), Code.UNSAFE_DISK, 'Unsafe runtime directory.')
        WORK.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chown(WORK, 0, gid)
        os.chmod(WORK, 0o750)
        lock_fd = os.open(WORK / 'daemon.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if SOCKET.exists() or SOCKET.is_symlink():
            require(stat.S_ISSOCK(SOCKET.lstat().st_mode), Code.UNSAFE_DISK, 'Socket path is not a socket.')
            SOCKET.unlink()
        with secure_log(LOG) as log:
            initial = Runner(lambda line: print(line, file=log, flush=True))
            boot_device = os.stat('/run/archiso/bootmnt').st_dev
            medium_size = Path(f'/sys/dev/block/{os.major(boot_device)}:{os.minor(boot_device)}/size')
            restart = RestartGuard(ResidentReboot(initial, medium_size=medium_size),
                                   close_fds=(lock_fd,))
            try:
                api = load_archinstall()  # Exact version assertion before any job.
                cleanup(initial, [TARGET, WORK / 'btrfs-top'], lazy=True)
                controller = None
                inventory = Inventory(initial)

                def factory(broker):
                    return make_worker(api, inventory, broker)

                controller = Controller(inventory, factory, version=api.version,
                                        test_mode=read_test_mode(), log_stream=log,
                                        restart_deadline=lambda: restart.forced_deadline,
                                        check_installation=restart.check_installation,
                                        validate_plan=lambda plan: validate_live_plan(api, plan))
                runner = Runner(controller.log, controller.redactor)
                controller.set_timezone_fn = lambda name: set_live_timezone(name, runner)
                inventory.runner = runner
                inventory.start_timezone_lookup()
                controller.save_log_fn = lambda dest: export_log(dest, runner, controller.redactor)
                controller.prepare_reboot_fn = restart.prepare
                controller.reboot_fn = restart
                asyncio.run(Server(controller, gid).run())
            finally:
                restart.close()
        return 0
    except (InstallError, OSError, KeyError) as exc:
        message = {'type': 'error', 'id': '', 'seq': 1, 'code': getattr(exc, 'code', Code.INTERNAL).value,
                   'phase': 'startup', 'message': str(exc), 'retryable': False, 'log_path': str(LOG)}
        if os.geteuid() == 0:
            try:
                with secure_log(LOG) as log:
                    print(json.dumps(message), file=log)
            except (OSError, InstallError):
                pass
        print(json.dumps(message), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
