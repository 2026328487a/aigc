import asyncio
import base64
import gzip
import hashlib
import json
import pathlib
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from aigc.native_comfy import NativeComfy
from aigc.session import Session


class RawStream(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = Session()
        self.session.state = "ready"
        self.session.info = {"root": "/dev/shm/aigc-test"}
        self.session.token = "private-token"
        self.requests = []
        self.streams = []
        self.result = b"a real result payload"
        self.corrupt = False
        self.history = {}
        self.queue = {"queue_running": [], "queue_pending": []}

        async def handler(request):
            self.requests.append(request)
            path = request.url.path
            if path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "prompt-test"})
            if path.startswith("/history"):
                return httpx.Response(200, json=self.history)
            if path == "/aigc/native/artifact":
                return httpx.Response(200, json={"size": len(self.result), "sha256": "wrong" if self.corrupt else hashlib.sha256(self.result).hexdigest()})
            if path == "/aigc/native/user-files":
                return httpx.Response(200, json=[{"path": "workflows/example.json", "data": base64.b64encode(b"{}").decode()}])
            if path == "/queue":
                if request.method == "POST":
                    self.queue["queue_pending"] = []
                return httpx.Response(200, json=self.queue)
            if path == "/interrupt":
                self.queue["queue_running"] = []
                return httpx.Response(200, json={})
            content = self.result if path == "/view" else gzip.compress(b"native frontend")
            stream = RawStream(content)
            self.streams.append(stream)
            headers = {"content-length": str(len(content))}
            if path != "/view":
                headers["content-encoding"] = "gzip"
            return httpx.Response(200, headers=headers, stream=stream)

        self.session.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://remote", headers={"Authorization": "Bearer " + self.session.token})
        self.native = NativeComfy(self.session, pathlib.Path(self.temp.name))
        self.native.load()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.native.gateway), base_url="http://127.0.0.1:8765")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.session.client.aclose()
        self.temp.cleanup()

    def completed(self):
        self.native.register("a" * 32, "prompt-test", {"prompt": {"1": {"class_type": "SaveImage", "inputs": {}}}})
        self.history = {"prompt-test": {"status": {"completed": True, "status_str": "success"}, "outputs": {"1": {"images": [{"filename": "image.png", "subfolder": "example", "type": "output"}]}}}}

    async def test_cross_origin_and_unidentified_mutations_are_rejected(self):
        self.assertEqual((await self.client.get("/", headers={"Origin": "https://evil.example"})).status_code, 403)
        self.assertEqual((await self.client.post("/prompt", json={})).status_code, 403)
        self.assertFalse(self.requests)

    async def test_static_stream_preserves_compression_and_injects_auth(self):
        response = await self.client.get("/assets/test.js", headers={"Authorization": "Bearer attacker", "Cookie": "secret=value"})
        self.assertEqual(response.content, b"native frontend")
        self.assertEqual(self.requests[-1].headers["authorization"], "Bearer private-token")
        self.assertNotIn("cookie", self.requests[-1].headers)
        self.assertTrue(self.streams[-1].closed)

    async def test_encoded_userdata_paths_and_query_survive_proxy(self):
        await self.client.get("/api/userdata/workflows%2Ftest.json?overwrite=true")
        self.assertIn(b"workflows%2Ftest.json", self.requests[-1].url.raw_path)
        self.assertEqual(self.requests[-1].url.params["overwrite"], "true")

    async def test_submission_uses_isolated_output_names(self):
        payload = {"prompt": {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI"}}, "2": {"class_type": "AIGCSaveAudioWAV", "inputs": {"filename_prefix": "anything"}}}}
        response = await self.client.post("/prompt", json=payload, headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(response.status_code, 200)
        sent = json.loads(self.requests[-1].content)
        record = next(iter(self.native.records.values()))
        self.assertEqual(sent["prompt"]["1"]["inputs"]["filename_prefix"], record["id"] + "/ComfyUI")
        self.assertEqual(sent["prompt"]["2"]["inputs"]["filename_prefix"], record["id"] + "/audio")
        self.assertTrue(self.native.pending())

    async def test_unsafe_output_prefix_is_rejected_before_forwarding(self):
        response = await self.client.post("/prompt", json={"prompt": {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "../../outside"}}}}, headers={"X-AIGC-Client": "local"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.requests)

    async def test_download_is_verified_and_preview_survives_disconnect(self):
        self.completed()
        await self.native.collect()
        self.assertFalse(self.native.pending())
        record = self.native.records["a" * 32]
        self.assertEqual(record["state"], "saved")
        self.assertEqual((self.native.directory(record["id"]) / "000.png").read_bytes(), self.result)
        self.session.state = "disconnected"
        history = (await self.client.get("/history")).json()
        descriptor = history["prompt-test"]["outputs"]["1"]["images"][0]
        response = await self.client.get("/view", params=descriptor)
        self.assertEqual(response.content, self.result)

    async def test_corrupt_download_blocks_idle_and_stop(self):
        self.completed()
        self.corrupt = True
        await self.native.collect()
        self.assertEqual(self.native.records["a" * 32]["state"], "download_error")
        self.assertTrue(await self.native.busy())
        with self.assertRaisesRegex(RuntimeError, "保存到本机"):
            await self.native.before_stop()
        self.assertFalse((self.native.directory("a" * 32) / "000.png").exists())

    async def test_failed_execution_with_completed_false_is_finalized(self):
        self.completed()
        self.history["prompt-test"]["status"] = {"completed": False, "status_str": "error"}
        self.history["prompt-test"]["outputs"] = {}
        await self.native.collect()
        self.assertEqual(self.native.records["a" * 32]["state"], "failed")
        self.assertFalse(self.native.pending())

    async def test_removed_queue_entry_does_not_block_idle_recycling(self):
        self.completed()
        self.history = {}
        self.native.records["a" * 32]["queued_at"] = time.monotonic() - 15
        await self.native.collect()
        self.assertEqual(self.native.records["a" * 32]["state"], "cancelled")
        self.assertFalse(self.native.pending())

    async def test_running_or_queued_native_tasks_block_idle_recycling(self):
        self.queue["queue_running"] = [[1, "running"]]
        self.assertTrue(await self.native.busy())
        self.queue = {"queue_running": [], "queue_pending": [[2, "queued"]]}
        self.assertTrue(await self.native.busy())
        self.queue["queue_pending"] = []
        self.assertFalse(await self.native.busy())

    async def test_stop_interrupts_own_queue_and_preserves_settings(self):
        self.queue = {"queue_running": [[1, "running"]], "queue_pending": [[2, "queued"]]}
        await self.native.before_stop()
        self.assertFalse(self.queue["queue_running"] or self.queue["queue_pending"])
        snapshot = self.native.root / "user/h3/snapshot.json"
        self.assertEqual(json.loads(snapshot.read_text())[0]["path"], "workflows/example.json")
        self.assertTrue(any(r.url.path == "/interrupt" for r in self.requests))

    async def test_internal_admin_endpoints_and_archive_escape_are_blocked(self):
        self.assertEqual((await self.client.get("/aigc/native/user-files")).status_code, 404)
        with self.assertRaises(Exception):
            self.native.directory("../escape")


class NativeCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_mutation_cannot_interrupt_workbench_job(self):
        from aigc import app as workbench

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url="http://127.0.0.1:8765") as client:
            with patch.object(workbench, "active_job", "a" * 32):
                response = await client.post("/comfy/interrupt", json={}, headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(response.status_code, 409)

    async def test_disconnect_finishes_before_new_native_submission(self):
        from aigc import app as workbench

        entered, release = asyncio.Event(), asyncio.Event()

        async def archive():
            entered.set()
            await release.wait()

        async def stop():
            workbench.session.state = "disconnected"

        with patch.object(workbench, "submission_lock", asyncio.Lock()), \
             patch.object(workbench, "cancel_active", AsyncMock()), \
             patch.object(workbench.native, "before_stop", archive), \
             patch.object(workbench.session, "state", "ready"), \
             patch.object(workbench.session, "stop", stop):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url="http://127.0.0.1:8765") as client:
                disconnect = asyncio.create_task(workbench.disconnect())
                await entered.wait()
                submit = asyncio.create_task(client.post("/comfy/prompt", json={}, headers={"Origin": "http://127.0.0.1:8765"}))
                await asyncio.sleep(0.01)
                self.assertFalse(submit.done())
                release.set()
                await disconnect
                self.assertEqual((await submit).status_code, 503)
