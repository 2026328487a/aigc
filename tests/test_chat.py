import asyncio
import gzip
import json
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from aigc import app as workbench
from aigc.chat import ChatSession


class ReplyStream(httpx.AsyncByteStream):
    def __init__(self, content=b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\ndata: [DONE]\n\n', hold=False):
        self.content, self.hold = content, hold
        self.closed = False
        self.started = asyncio.Event()
        self.closing = asyncio.Event()
        self.slow_close = False

    async def __aiter__(self):
        self.started.set()
        yield self.content
        if self.hold:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closing.set()
        if self.slow_close:
            await asyncio.sleep(0.03)
        self.closed = True


class ChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = ChatSession(idle_seconds=5)
        self.chat.state = 'ready'
        self.reply = ReplyStream()
        self.requests = []
        self.response_headers = {'content-type':'text/event-stream'}
        async def handler(request):
            self.requests.append(request)
            return httpx.Response(200, headers=self.response_headers, stream=self.reply)
        self.chat.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://remote', headers={'Connection':'close'})

    async def asyncTearDown(self):
        await self.chat.cancel_active()
        if self.chat.client:
            await self.chat.client.aclose()

    async def begin(self, inference=True):
        ready, stream = self.chat.begin_proxy('POST' if inference else 'GET', '/v1/chat/completions' if inference else '/models/sse',
                                             body=b'{"messages":[],"seed":123,"stream_options":{"include_usage":true}}',
                                             headers={'Content-Type':'application/json'}, inference=inference)
        response = await ready
        return response, stream

    async def test_native_json_and_sse_pass_through_without_reformatting(self):
        response, stream = await self.begin()
        content = b''.join([item async for item in stream])
        self.assertEqual(content, self.reply.content)
        self.assertTrue(self.reply.closed)
        self.assertFalse(self.chat.busy)
        self.assertEqual(self.requests[0].headers['connection'], 'close')
        self.assertEqual(json.loads(self.requests[0].content)['seed'], 123)

    async def test_compressed_native_assets_preserve_raw_bytes(self):
        self.reply.content = gzip.compress(b'native UI bundle')
        self.response_headers = {'content-encoding':'gzip'}
        ready, stream = self.chat.begin_proxy('GET','/_app/bundle.js')
        self.assertEqual((await ready).headers['content-encoding'],'gzip')
        self.assertEqual(b''.join([item async for item in stream]), self.reply.content)

    async def test_browser_disconnect_closes_upstream(self):
        self.reply.hold = True
        _, stream = await self.begin()
        await anext(stream)
        await stream.aclose()
        self.assertTrue(self.reply.closed)
        self.assertFalse(self.chat.busy)

    async def test_repeated_cancellation_cannot_interrupt_upstream_close(self):
        self.reply.hold = self.reply.slow_close = True
        _, stream = await self.begin()
        await anext(stream)
        closing = asyncio.create_task(stream.aclose())
        await self.reply.closing.wait()
        closing.cancel()
        try: await closing
        except asyncio.CancelledError: pass
        await self.chat.cancel_active()
        self.assertTrue(self.reply.closed)
        self.assertFalse(self.chat.busy)

    async def test_cancel_before_headers_releases_the_inference_slot(self):
        async def hold(request): await asyncio.Event().wait()
        await self.chat.client.aclose()
        self.chat.client = httpx.AsyncClient(transport=httpx.MockTransport(hold),base_url='http://remote')
        ready, stream = self.chat.begin_proxy('POST','/v1/chat/completions',inference=True)
        await asyncio.sleep(0)
        ready.cancel()
        await asyncio.sleep(0)
        await self.chat.cancel_active()
        self.assertFalse(self.chat.busy)
        self.assertFalse(self.chat.proxy_tasks)
        await stream.aclose()

    async def test_native_background_sse_and_polling_do_not_renew_idle(self):
        self.reply.hold = True
        self.chat.last_used = time.monotonic()-6
        previous = self.chat.last_used
        _, stream = await self.begin(inference=False)
        await anext(stream)
        for _ in range(5): self.chat.public()
        self.assertFalse(self.chat.busy)
        self.assertEqual(self.chat.last_used, previous)
        with patch.object(self.chat,'stop',new_callable=AsyncMock) as stop:
            await self.chat.maintain()
            stop.assert_awaited_once()
        await stream.aclose()
        self.assertEqual(self.chat.last_used,previous)

    async def test_active_reply_blocks_idle_recycling_and_overlap(self):
        self.reply.hold = True
        _, stream = await self.begin()
        await anext(stream)
        self.chat.last_used = time.monotonic()-6
        with patch.object(self.chat,'stop',new_callable=AsyncMock) as stop:
            await self.chat.maintain()
            stop.assert_not_awaited()
        with self.assertRaises(RuntimeError): self.chat.begin_proxy('POST','/v1/chat/completions',inference=True)
        await stream.aclose()

    async def test_confirmed_release_closes_remaining_ssh_channels(self):
        stdout=asyncio.StreamReader()
        stdout.feed_data(b'AIGC_CHAT {"type":"cleaned","cleaned":true}\n');stdout.feed_eof()
        remote=Mock(stdout=stdout,returncode=None);self.chat.remote=remote
        await self.chat._read()
        remote.terminate.assert_called_once()
        self.assertTrue(self.chat.cleaned)
        self.chat.remote=None

    async def test_native_ui_can_post_same_origin_without_custom_chat_headers(self):
        with patch.object(workbench,'chat',self.chat):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app),base_url='http://127.0.0.1:8766') as client:
                r=await client.post('/qwen/v1/chat/completions',json={'messages':[]},headers={'Origin':'http://127.0.0.1:8766'})
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.content,self.reply.content)
                self.assertIn("frame-ancestors 'self'",r.headers['content-security-policy'])
                r=await client.post('/qwen/models',json={},headers={'Origin':'http://127.0.0.1:8766'})
                self.assertEqual(r.status_code,403)


class ChatApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_reading_chat_page_and_status_does_not_start_service(self):
        with patch.object(workbench.chat, 'connect', new_callable=AsyncMock) as connect:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url='http://127.0.0.1:8766') as client:
                self.assertEqual((await client.get('/chat')).status_code, 200)
                for _ in range(3):
                    self.assertIn('chat', (await client.get('/api/status')).json())
            connect.assert_not_awaited()

    async def test_chat_mutations_require_local_same_origin(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url='http://127.0.0.1:8766') as client:
            self.assertEqual((await client.post('/api/chat/connect')).status_code, 403)
            self.assertEqual((await client.post('/api/chat/connect', headers={'Origin': 'https://evil.example','X-AIGC-Client': 'local'})).status_code, 403)

    async def test_changing_to_media_is_blocked_during_chat_reply(self):
        with patch.object(workbench.chat, 'busy', True):
            with self.assertRaises(workbench.HTTPException) as caught:
                await workbench.connect('qwen21')
            self.assertEqual(caught.exception.status_code, 409)

    async def test_changing_to_chat_is_blocked_during_media_work(self):
        with patch.object(workbench, 'active_job', 'job'):
            with self.assertRaises(workbench.HTTPException) as caught:
                await workbench.prepare_chat()
            self.assertEqual(caught.exception.status_code, 409)
        task = asyncio.create_task(asyncio.sleep(10))
        try:
            with patch.object(workbench.session, 'connect_task', task):
                with self.assertRaises(workbench.HTTPException) as caught:
                    await workbench.prepare_chat()
                self.assertEqual(caught.exception.status_code, 409)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_idle_media_is_released_before_chat_starts(self):
        order = []
        async def media_stop(): order.append('media-stop')
        async def chat_start(): order.append('chat-start')
        with patch.object(workbench, 'active_job', None), patch.object(workbench.session, 'connect_task', None), \
             patch.object(workbench.session, 'state', 'ready'), patch.object(workbench.native, 'busy', AsyncMock(return_value=False)), \
             patch.object(workbench.native, 'before_stop', AsyncMock()), patch.object(workbench.session, 'stop', media_stop), \
             patch.object(workbench.chat, 'connect', chat_start):
            await workbench.prepare_chat()
        self.assertEqual(order, ['media-stop','chat-start'])
