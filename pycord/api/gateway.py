'''
MIT License

Copyright (c) 2017 verixx / king1600

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

from pycord.api.events import EventHandler
from pycord.utils import get_libname, API
from pycord.utils import from_json
from pycord.utils import json
import websockets
import traceback
import platform
import asyncio
import zlib
import time


class ShardConnection:

    '''Represents a websocket connection to a shard.'''

    DISPATCH        = 0
    HEARTBEAT       = 1
    IDENTIFY        = 2
    PRESENCE        = 3
    VOICE_STATE     = 4
    VOICE_PING      = 5
    RESUME          = 6
    RECONNECT       = 7
    REQUEST_MEMBERS = 8
    INVALID_SESSION = 9
    HELLO           = 10
    HEARTBEAT_ACK   = 11
    GUILD_SYNC      = 12

    def __init__(self, client=None, shard_id=None, shard_max=None):
        self.ws = None
        self.alive = False
        self.sequence = None
        self.client = client
        self.is_resume = False
        self.session_id = None
        self.heartbeat_acked = True
        self.handler = EventHandler(self)
        self.shard_info = [shard_id, shard_max]

    async def close(self, code=1000, reason=''):
        self.alive = False
        if self.ws is not None:
            offline = {
                'game': None,
                'afk': False,
                'since': None,
                'status': 'offline'
            }
            await self.send(self.PRESENCE, offline)
            await self.ws.close(code, reason)

    async def send(self, op=DISPATCH, d=None):
        if self.ws is not None:
            data = json.dumps({'op': op, 'd': d})
            await self.ws.send(data)

    async def ping(self, data=None):
        start = time.time()
        if self.ws is not None:
            await (await self.ws.ping(data))
        return ((time.time() - start) * 1000., data)

    async def heartbeat(self, interval):
        await asyncio.sleep(interval)
        if self.ws is not None:
            if self.heartbeat_acked:
                self.heartbeat_acked = False
                await self.send(self.HEARTBEAT, self.sequence)
                self.client.loop.create_task(self.heartbeat(interval))
            else:
                await self.ws.close(1011, 'zombie connection')

    async def resume(self):
        """ Send resume packet """
        payload = {
            'token': self.client.token,
            'session_id': self.session_id,
            'seq': self.sequence
        }
        await self.send(self.RESUME, payload)

    async def identify(self):
        """ Send identification packet """
        payload = {
            'token': self.client.token,
            'properties': {
                '$os': platform.system(),
                '$browser': get_libname(),
                '$device': get_libname()
            },
            'compress': True,
            'large_threshold': 250,
        }

        if not self.client.is_bot:
            payload['synced_guilds'] = []

        if self.shard_info != (None, None):
            payload['shards'] = self.shard_info

        await self.send(self.IDENTIFY, payload)

    async def read_data(self):
        """ Start reading data from websocket connection """
        if self.ws is None: return

        while True:
            try:
                # unpack data and save sequence number
                data = await self.ws.recv()
                if isinstance(data, bytes):
                    data = zlib.decompress(data, 15, 10490000).decode('utf-8')
                data = from_json(data)

                # handle collected data
                await self.client.emit('raw_data', data)
                await self.handle_data(data)

            # ignore websocket close exception
            except websockets.exceptions.ConnectionClosed:
                break

            # ignore asyncio cancellation exceptions
            except asyncio.CancelledError:
                break

            # handle any exceptions
            except Exception as err:
                traceback.print_exc()
                break

    async def handle_data(self, data):
        """ handle websocket data """
        # extract message info
        self.sequence = data.get('s') or self.sequence
        op, data, event = data.get('op'), data.get('d'), data.get('t')

        # handle reconnect from gateway
        if op == self.RECONNECT:
            await self.ws.close()

        # handle heartbeat ack
        elif op == self.HEARTBEAT_ACK:
            self.heartbeat_acked = True

        # start session
        elif op == self.HELLO:
            interval = (data['heartbeat_interval'] - 100) / 1000.0
            self.client.loop.create_task(self.heartbeat(interval))
            await self.identify()

        # retry connecting if possible
        elif op == self.INVALID_SESSION:
            if data:
                await asyncio.sleep(5.0, loop=self.client.loop)
                await self.ws.close()
            else:
                self.sequence = None
                self.session_id = None
                await self.identify()

        # handle gateway events
        elif op == self.DISPATCH:
            handle = f'handle_{event.lower()}'
            if hasattr(self.handler, handle):
                await getattr(self.handler, handle)(data)

    async def start(self, url):
        """ Start long-term connection with gateway """
        self.alive = True
        url = url[:-1] if url.endswith('/') else url
        url += API.WS_ENDPOINT

        # start connection loop
        while self.alive:
            async with websockets.connect(url) as self.ws:
                await self.read_data()
                

                