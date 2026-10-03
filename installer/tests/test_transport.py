import asyncio
import os
from pathlib import Path
import tempfile
import unittest
import struct
from unittest.mock import patch

from emaki_installer.constants import MAX_FRAME
from emaki_installer.daemon import Server
from emaki_installer.protocol import Controller, Job, encode_frame
from support import FakeInventory, config


class TransportCases:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emi-')
        self.path = str(Path(self.temp.name) / 'sock')
        self.controller = Controller(FakeInventory(), None)
        self.handler = Server(self.controller, os.getgid())
        self.server = await asyncio.start_unix_server(self.handler.client, path=self.path, limit=MAX_FRAME)
        self.connections = []

    async def asyncTearDown(self):
        for _, writer in self.connections:
            writer.close()
            await writer.wait_closed()
        self.server.close()
        await self.server.wait_closed()
        await asyncio.sleep(0)
        self.temp.cleanup()

    async def connect(self):
        connection = await asyncio.open_unix_connection(self.path, limit=MAX_FRAME)
        self.connections.append(connection)
        return connection

    async def read(self, reader):
        import json
        return json.loads(await asyncio.wait_for(reader.readline(), 2))

    async def send(self, writer, kind, **fields):
        writer.write(encode_frame({'type': kind, 'id': kind + '-test', **fields}))
        await writer.drain()

    async def hello(self):
        reader, writer = await self.connect()
        await self.send(writer, 'hello', proto=1)
        result = await self.read(reader)
        self.assertEqual(result['type'], 'hello')
        return reader, writer

    async def test_fragmented_and_coalesced_frames(self):
        reader, writer = await self.connect()
        first = encode_frame({'type': 'hello', 'id': '1', 'proto': 1})
        writer.write(first[:7])
        await writer.drain()
        writer.write(first[7:] + encode_frame({'type': 'probe', 'id': '2'}))
        await writer.drain()
        hello, inv = await self.read(reader), await self.read(reader)
        self.assertEqual((hello['type'], inv['type']), ('hello', 'inventory'))
        self.assertLess(hello['seq'], inv['seq'])

    async def test_oversize_closes_connection(self):
        reader, writer = await self.hello()
        writer.write(b'x' * (MAX_FRAME + 1) + b'\n')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')

    async def test_first_message_must_be_hello(self):
        reader, writer = await self.connect()
        await self.send(writer, 'probe')
        self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')

    async def test_busy_confirmation_on_real_transport(self):
        reader, writer = await self.hello()
        await self.send(writer, 'plan', config=config())
        ack = await self.read(reader)
        self.controller.job = Job('busy-job')
        await self.send(writer, 'confirm', plan_id=ack['plan_id'], token=ack['token'])
        reply = await self.read(reader)
        self.assertEqual(reply['code'], 'busy')

    async def test_resume_cannot_overtake_replayed_logs(self):
        self.controller.job = Job('job')
        self.controller.emit('state', phase='copy_packages', phase_pct=1, total_pct=5.6, indeterminate=False)
        for i in range(20):
            self.controller.emit('log', line=f'old-{i}')
        original = self.controller.handle

        def during_replay(msg):
            result = original(msg)
            if msg['type'] == 'resume':
                self.controller.emit('log', line='new-during-resume')
            return result

        self.controller.handle = during_replay
        reader, writer = await self.hello()
        await self.send(writer, 'resume', job_id='job', since_seq=0)
        messages = [await self.read(reader) for _ in range(22)]
        logs = [m['line'] for m in messages if m['type'] == 'log']
        self.assertEqual(logs, [f'old-{i}' for i in range(20)] + ['new-during-resume'])
        self.assertEqual([m['seq'] for m in messages], sorted(m['seq'] for m in messages))

    async def test_long_resume_backpressures_instead_of_dropping_logs(self):
        self.controller.job = Job('job')
        self.controller.emit('state', phase='copy_packages', phase_pct=1, total_pct=5.6, indeterminate=False)
        for i in range(2200):
            self.controller.emit('log', line=f'long-{i}')
        reader, writer = await self.hello()
        await self.send(writer, 'resume', job_id='job', since_seq=0)
        messages = [await self.read(reader) for _ in range(2201)]
        self.assertEqual([m['line'] for m in messages if m['type'] == 'log'], [f'long-{i}' for i in range(2200)])


@unittest.skipUnless(os.environ.get('EMAKI_TEST_UNIX_SOCKET') == '1',
                     'Set EMAKI_TEST_UNIX_SOCKET=1 where AF_UNIX bind is permitted')
class SocketTests(TransportCases, unittest.IsolatedAsyncioTestCase):
    pass


class MemoryTransportTests(TransportCases, unittest.IsolatedAsyncioTestCase):
    """Exercise the production framing/replay loop without a socket syscall."""
    async def asyncSetUp(self):
        self.controller = Controller(FakeInventory(), None)
        self.handler = Server(self.controller, os.getgid())
        self.connections, self.tasks = [], []
        # The restricted test sandbox also blocks the event loop's cross-thread
        # socket wakeup. Real socket tests exercise the threaded dispatcher.
        async def local_dispatch(function, *args):
            return function(*args)
        dispatch = patch('emaki_installer.daemon.asyncio.to_thread', local_dispatch)
        dispatch.start()
        self.addCleanup(dispatch.stop)

    async def asyncTearDown(self):
        for _, writer in self.connections:
            writer.close()
        await asyncio.wait_for(asyncio.gather(*self.tasks), 2)

    async def connect(self):
        incoming = asyncio.StreamReader(limit=MAX_FRAME)
        outgoing = asyncio.StreamReader(limit=MAX_FRAME)

        class Peer:
            def getsockopt(self, *args):
                return struct.pack('3i', os.getpid(), os.getuid(), os.getgid())

        class PipeWriter:
            def __init__(self, destination):
                self.destination, self.closed = destination, False

            def write(self, data):
                self.destination.feed_data(data)

            async def drain(self):
                await asyncio.sleep(0)

            def close(self):
                if not self.closed:
                    self.closed = True
                    self.destination.feed_eof()

            async def wait_closed(self):
                return

            def is_closing(self):
                return self.closed

            def get_extra_info(self, key):
                return Peer()

        task = asyncio.create_task(self.handler.client(incoming, PipeWriter(outgoing)))
        connection = (outgoing, PipeWriter(incoming))
        self.connections.append(connection)
        self.tasks.append(task)
        return connection


if __name__ == '__main__':
    unittest.main()
