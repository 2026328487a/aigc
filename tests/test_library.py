import json
import pathlib
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import httpx

from aigc import app as workbench
from aigc.native_comfy import NativeComfy
from aigc.session import Session


class LibraryApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.native = NativeComfy(Session(), root)
        self.native.load()
        self.stack.enter_context(patch.object(workbench, "native", self.native))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=workbench.app), base_url="http://127.0.0.1:8765",
            headers={"X-AIGC-Client": "local"},
        )
        self.addAsyncCleanup(self.client.aclose)

    def record(self, archive_id, engine="qwen21", created="2026-10-25T12:00:00+08:00", files=(), workflows=None):
        record = {"id": archive_id, "prompt_id": "prompt-" + archive_id[:4], "engine": engine, "session_root": "/dev/shm/x",
                  "created": created, "state": "saved", "error": None,
                  "files": [{"filename": name, "local_name": f"{i:03}{pathlib.Path(name).suffix}"} for i, name in enumerate(files)]}
        self.native.save(record)
        self.native.records[archive_id] = record
        directory = self.native.directory(archive_id)
        for file in record["files"]:
            (directory / file["local_name"]).write_bytes(b"media")
        for name, content in (workflows or {}).items():
            (directory / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return record

    async def test_archives_paginate_filter_and_search(self):
        for i in range(5):
            self.record(f"{i:x}" * 32, engine="h3" if i % 2 else "qwen21", created=f"2026-10-2{i}T12:00:00+08:00", files=[f"clip{i}.png"])
        data = (await self.client.get("/api/comfy/jobs", params={"page_size": 2})).json()
        self.assertEqual((data["total"], data["pages"], data["page"]), (5, 3, 1))
        self.assertEqual([j["id"] for j in data["jobs"]], ["4" * 32, "3" * 32])
        last = (await self.client.get("/api/comfy/jobs", params={"page_size": 2, "page": 99})).json()
        self.assertEqual((last["page"], len(last["jobs"])), (3, 1))
        h3 = (await self.client.get("/api/comfy/jobs", params={"engine": "h3"})).json()
        self.assertEqual({j["engine"] for j in h3["jobs"]}, {"h3"})
        self.assertEqual(h3["total"], 2)
        found = (await self.client.get("/api/comfy/jobs", params={"q": "CLIP2"})).json()
        self.assertEqual([j["id"] for j in found["jobs"]], ["2" * 32])
        self.assertNotIn("session_root", found["jobs"][0])
        self.assertEqual((await self.client.get("/api/comfy/jobs", params={"engine": "voxcpm2"})).status_code, 422)

    async def test_workflow_prefers_native_graph_and_skips_invalid_files(self):
        api_graph = {"1": {"class_type": "SaveImage", "inputs": {}}}
        self.record("a" * 32, workflows={"workflow.json": {"nodes": [], "links": []}, "workflow-api.json": api_graph})
        self.record("b" * 32, workflows={"workflow.json": "{broken", "workflow-api.json": api_graph})
        self.record("c" * 32, workflows={"workflow-api.json": {"1": {"inputs": {}}}})
        native = (await self.client.get(f"/api/comfy/jobs/{'a' * 32}/workflow")).json()
        self.assertEqual((native["format"], native["engine"]), ("native", "qwen21"))
        fallback = (await self.client.get(f"/api/comfy/jobs/{'b' * 32}/workflow")).json()
        self.assertEqual((fallback["format"], fallback["workflow"]), ("api", api_graph))
        self.assertEqual((await self.client.get(f"/api/comfy/jobs/{'c' * 32}/workflow")).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/comfy/jobs/{'d' * 32}/workflow")).status_code, 404)
        listed = {j["id"]: j["has_workflow"] for j in (await self.client.get("/api/comfy/jobs")).json()["jobs"]}
        self.assertTrue(listed["a" * 32])

    async def test_media_is_inline_unless_download_requested(self):
        self.record("a" * 32, files=["result.png", "song.wav"], workflows={"workflow-api.json": {}})
        base = f"/api/comfy/jobs/{'a' * 32}/files/"
        inline = await self.client.get(base + "000.png")
        self.assertEqual(inline.headers["content-type"], "image/png")
        self.assertNotIn("attachment", inline.headers.get("content-disposition", ""))
        audio = await self.client.get(base + "001.wav")
        self.assertEqual(audio.headers["content-type"], "audio/wav")
        download = await self.client.get(base + "000.png", params={"download": "true"})
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertIn("attachment", (await self.client.get(base + "workflow-api.json")).headers["content-disposition"])
        self.assertEqual((await self.client.get(base + "..%2Fjob.json")).status_code, 404)

    async def test_template_can_be_loaded_without_attachment(self):
        attached = await self.client.get("/api/comfy/templates/qwen-text")
        self.assertIn("attachment", attached.headers["content-disposition"])
        inline = await self.client.get("/api/comfy/templates/qwen-text", params={"download": "false"})
        self.assertNotIn("content-disposition", inline.headers)
        self.assertTrue(all("class_type" in node for node in inline.json().values()))

    async def test_library_and_open_workflow_pages_are_served(self):
        for path in ("/library", "/open-workflow"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
