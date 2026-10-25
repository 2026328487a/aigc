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

    async def test_media_is_inline_and_metadata_is_an_attachment(self):
        self.record("a" * 32, files=["result.png", "song.wav"], workflows={"workflow-api.json": {}})
        base = f"/api/comfy/jobs/{'a' * 32}/files/"
        inline = await self.client.get(base + "000.png")
        self.assertEqual(inline.headers["content-type"], "image/png")
        self.assertNotIn("attachment", inline.headers.get("content-disposition", ""))
        audio = await self.client.get(base + "001.wav")
        self.assertEqual(audio.headers["content-type"], "audio/wav")
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


class ArchiveNamingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)

    def legacy(self, archive_id, created, folder=None, local_names=("000.png",)):
        directory = self.root / "jobs" / (folder or archive_id)
        directory.mkdir(parents=True)
        record = {"id": archive_id, "prompt_id": "p", "engine": "qwen21", "session_root": "/x", "created": created,
                  "state": "saved", "error": "", "files": [{"filename": "image_00001_.png", "local_name": n} for n in local_names]}
        (directory / "job.json").write_text(json.dumps(record), encoding="utf-8")
        for name in local_names:
            (directory / name).write_bytes(archive_id.encode())
        (directory / "workflow-api.json").write_text("{}", encoding="utf-8")

    def test_legacy_archives_are_renamed_by_time_and_engine(self):
        self.legacy("a" * 32, "2026-10-25T14:37:12.469168+08:00", local_names=("000.png", "001.png"))
        self.legacy("b" * 32, "2026-10-25T14:37:12.900000+08:00")
        native = NativeComfy(Session(), self.root)
        native.load()
        folders = sorted(p.name for p in (self.root / "jobs").iterdir())
        self.assertEqual(folders, ["2026-10-25_14-37-12_qwen21", "2026-10-25_14-37-12_qwen21-2"])
        first = native.records["a" * 32]
        directory = native.directory("a" * 32)
        self.assertEqual([f["local_name"] for f in first["files"]],
                         ["2026-10-25_14-37-12_qwen21_01.png", "2026-10-25_14-37-12_qwen21_02.png"])
        self.assertEqual((directory / first["files"][0]["local_name"]).read_bytes(), b"a" * 32)
        self.assertTrue((directory / "workflow-api.json").is_file())
        saved = json.loads((directory / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["folder"], directory.name)
        reloaded = NativeComfy(Session(), self.root)
        reloaded.load()
        self.assertEqual(reloaded.directory("b" * 32).name, "2026-10-25_14-37-12_qwen21-2")
        self.assertEqual(sorted(p.name for p in (self.root / "jobs").iterdir()), folders)

    def test_partially_migrated_archive_finishes_renaming(self):
        self.legacy("c" * 32, "2026-10-20T08:00:00+08:00", folder="2026-10-20_08-00-00_qwen21")
        native = NativeComfy(Session(), self.root)
        native.load()
        self.assertEqual(native.records["c" * 32]["files"][0]["local_name"], "2026-10-20_08-00-00_qwen21_01.png")
        self.assertTrue((native.directory("c" * 32) / "2026-10-20_08-00-00_qwen21_01.png").is_file())

    def test_unexpected_folder_names_are_ignored(self):
        self.legacy("d" * 32, "2026-10-20T08:00:00+08:00", folder="..evil")
        native = NativeComfy(Session(), self.root)
        native.load()
        self.assertNotIn("d" * 32, native.records)


if __name__ == "__main__":
    unittest.main()
