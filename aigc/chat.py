"""Lifecycle and transparent proxy for the existing llama.cpp web UI."""
import asyncio
import base64
from collections import deque
from contextlib import suppress
import json
import socket
import time
import httpx

from .session import ROOT, SSH_OPTIONS

MODEL = 'qwen38-27b-uncensored-q6-k'


class ChatSession:
    def __init__(self, idle_seconds=900, lease_seconds=120, ssh_options=None):
        self.idle_seconds, self.lease_seconds = idle_seconds, lease_seconds
        self.ssh_options = list(SSH_OPTIONS if ssh_options is None else ssh_options)
        self.state, self.error = 'disconnected', ''
        self.cleaned = None
        self.owned = False
        self.busy = False
        self.last_used = time.monotonic()
        self.logs = deque(maxlen=8)
        self.remote = self.client = None
        self.reader = self.heartbeat = self.request_task = None
        self.proxy_tasks = set()
        self.lock = asyncio.Lock()
        self.port = None

    def public(self):
        return {'state': self.state, 'error': self.error, 'busy': self.busy,
                'cleaned': self.cleaned, 'model': MODEL, 'logs': list(self.logs),
                'idle_minutes': self.idle_seconds / 60,
                'idle_remaining_seconds': max(0, round(self.idle_seconds - (time.monotonic() - self.last_used)))
                if self.state == 'ready' and not self.busy else None}

    async def _read(self):
        while line := await self.remote.stdout.readline():
            value = line.decode('utf-8', errors='replace').strip()
            if value.startswith('AIGC_CHAT '):
                item = json.loads(value[len('AIGC_CHAT '):])
                if item['type'] == 'ready':
                    self.state = 'ready'
                elif item['type'] == 'owned':
                    self.owned = True
                elif item['type'] == 'cleaned':
                    self.cleaned = item['cleaned']
                    # OpenSSH may retain forwarding channels after the remote
                    # command exits. Once full cleanup is confirmed, close them.
                    if self.cleaned and self.remote.returncode is None:
                        self.remote.terminate()
                elif item['type'] == 'lease_expired':
                    self.logs.append('SSH 心跳超时，远端正在自动回收聊天服务')
            elif value:
                self.logs.append(value[-500:])

    async def _ping(self):
        while self.remote and self.remote.returncode is None:
            try:
                self.remote.stdin.write(b'ping\n')
                await self.remote.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                return
            await asyncio.sleep(min(10, self.lease_seconds / 3))

    async def connect(self):
        async with self.lock:
            if self.state == 'ready' and self.remote and self.remote.returncode is None:
                return
            if self.remote:
                await self._close()
            if self.owned and self.cleaned is not True:
                if not await self._confirm_release():
                    raise RuntimeError('上次聊天的远端回收尚未确认，请稍后重试或检查 SSH 连接')
            self.state, self.error, self.cleaned = 'starting', '', None
            self.owned = False
            self.logs.clear()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.port = sock.getsockname()[1]
            source = base64.b64encode((ROOT / 'aigc/remote_chat_supervisor.py').read_bytes())
            try:
                # The same SSH process carries the forward and the remote lease.
                self.remote = await asyncio.create_subprocess_exec(
                    'ssh', *self.ssh_options, '-o', 'ExitOnForwardFailure=yes',
                    '-L', f'127.0.0.1:{self.port}:127.0.0.1:18080', 'dgx',
                    "python3 -u -c 'import base64;exec(compile(base64.b64decode(input()), \"<aigc-chat>\", \"exec\"))'",
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                self.reader = asyncio.create_task(self._read())
                self.remote.stdin.write(source + b'\n' + json.dumps({
                    'idle_seconds': self.idle_seconds, 'lease_seconds': self.lease_seconds,
                }).encode() + b'\n')
                await self.remote.stdin.drain()
                async with asyncio.timeout(70):
                    while self.state != 'ready':
                        if self.remote.returncode is not None:
                            raise RuntimeError('Qwen 启动失败：' + '\n'.join(self.logs))
                        await asyncio.sleep(0.1)
                self.heartbeat = asyncio.create_task(self._ping())
                self.client = httpx.AsyncClient(
                    base_url=f'http://127.0.0.1:{self.port}', trust_env=False,
                    timeout=httpx.Timeout(180, connect=10),
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=0),
                    headers={'Connection': 'close'},
                )
                self.last_used = time.monotonic()
            except BaseException as error:
                await self._close()
                self.state, self.error = 'error', str(error) or '聊天启动已取消'
                raise

    def begin_proxy(self, method, endpoint, *, headers=None, body=b'', query=(), inference=False):
        if self.state != 'ready' or self.client is None:
            raise RuntimeError('聊天服务未连接')
        if inference and self.busy:
            raise RuntimeError('模型正在回复')
        queue = asyncio.Queue(maxsize=16)
        ready = asyncio.get_running_loop().create_future()
        if inference:
            self.busy = True
            self.error = ''
            self.last_used = time.monotonic()
        task = asyncio.create_task(self._forward(method, endpoint, headers, body, query, queue, ready))
        self.proxy_tasks.add(task)
        if inference:
            self.request_task = task
        def finished(done):
            self.proxy_tasks.discard(done)
            if inference and self.request_task is done:
                self._release_inference(done)
            if not ready.done():
                ready.set_exception(RuntimeError('聊天请求已停止'))
        task.add_done_callback(finished)
        def cancelled_before_headers(future):
            if future.cancelled() and not task.done() and not task.cancelling():
                task.cancel()
        ready.add_done_callback(cancelled_before_headers)
        return ready, self._proxy_stream(queue, task)

    def _release_inference(self, task):
        if self.request_task is task and self.busy and task.done():
            self.busy = False
            self.last_used = time.monotonic()

    async def _forward(self, method, endpoint, headers, body, query, queue, ready):
        response = None
        try:
            async with asyncio.timeout(600):
                forwarded = self.client.build_request(method, endpoint, headers=headers,
                                                     content=body, params=query)
                response = await self.client.send(forwarded, stream=True)
                if not ready.done():
                    ready.set_result(response)
                async for chunk in response.aiter_raw():
                    await queue.put(chunk)
                await queue.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            else:
                await queue.put(error)
        finally:
            if response:
                await response.aclose()

    async def _proxy_stream(self, queue, task):
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), 0.5)
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)
            self._release_inference(task)

    async def cancel_active(self):
        tasks = list(self.proxy_tasks)
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    async def _close(self):
        if self.heartbeat:
            self.heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat
            self.heartbeat = None
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.remote and self.remote.returncode is None:
            with suppress(BrokenPipeError, ConnectionResetError):
                self.remote.stdin.write(b'stop\n')
                await self.remote.stdin.drain()
            try:
                await asyncio.wait_for(self.remote.wait(), 85)
            except asyncio.TimeoutError:
                self.remote.terminate()
                await self.remote.wait()
        if self.reader:
            await self.reader
            self.reader = None
        self.remote = None

    async def stop(self):
        async with self.lock:
            self.state = 'stopping'
            await self.cancel_active()
            await self._close()
            self.state = 'disconnected'
            if self.owned and self.cleaned is not True and not await self._confirm_release():
                self.error = '聊天连接已关闭，远端回收尚未确认；SSH 心跳超时仍会触发回收'
                self.state = 'error'

    async def _confirm_release(self):
        """Read systemd state without opening an API connection or waking Qwen."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                'ssh', *self.ssh_options, 'dgx',
                'systemctl --user show qwen38-api.socket qwen38-api.service qwen38-backend.service -p ActiveState -p MainPID',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            raw, _ = await asyncio.wait_for(process.communicate(), 20)
            lines = raw.decode().splitlines()
            if process.returncode == 0 and sum(line in ('ActiveState=inactive', 'ActiveState=failed') for line in lines) == 3 and lines.count('MainPID=0') == 2:
                self.cleaned = True
                return True
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            if process and process.returncode is None:
                process.terminate()
                await process.wait()
        return False

    async def maintain(self):
        if self.state == 'ready' and not self.busy:
            if self.remote and self.remote.returncode is not None:
                await self.stop()
            elif time.monotonic() - self.last_used >= self.idle_seconds:
                await self.stop()
