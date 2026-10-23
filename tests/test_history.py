import asyncio
from contextlib import ExitStack
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import httpx

from aigc import app as workbench


class HistoryDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.data = self.root / "jobs"
        self.data.mkdir()
        self.stack.enter_context(patch.object(workbench, "DATA", self.data))
        self.stack.enter_context(patch.object(workbench, "active_job", None))
        self.stack.enter_context(patch.object(workbench, "submission_lock", asyncio.Lock()))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=workbench.app), base_url="http://127.0.0.1:8765",
            headers={"X-AIGC-Client": "local"},
        )
        self.addAsyncCleanup(self.client.aclose)

    def job(self, job_id="a" * 32, state="completed"):
        directory = self.data / job_id
        directory.mkdir()
        job = {"id": job_id, "state": state, "created": "2026-10-16T12:00:00+08:00"}
        (directory / "job.json").write_text(json.dumps(job), encoding="utf-8")
        for name in ("result.png", "result.mp4", "reference_1.png", "workflow.json", "result.png.part"):
            (directory / name).write_bytes(b"test fixture")
        return directory

    async def test_delete_removes_task_files_but_preserves_other_jobs_and_downloads(self):
        directory = self.job()
        other = self.job("b" * 32)
        exported = self.root / "download.png"
        exported.write_bytes(b"separate download")
        response = await self.client.delete("/api/jobs/" + directory.name)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(directory.exists())
        self.assertTrue((other / "result.png").is_file())
        self.assertTrue(exported.is_file())
        self.assertEqual([j["id"] for j in (await self.client.get("/api/jobs")).json()], [other.name])
        for suffix in ("", "/files/result.png", "/files/result.mp4", "/files/reference_1.png"):
            self.assertEqual((await self.client.get("/api/jobs/" + directory.name + suffix)).status_code, 404)
        self.assertEqual((await self.client.delete("/api/jobs/" + directory.name)).status_code, 404)

    async def test_running_and_queued_jobs_are_preserved(self):
        for state, job_id in (("running", "a" * 32), ("queued", "b" * 32)):
            with self.subTest(state=state):
                directory = self.job(job_id, state)
                response = await self.client.delete("/api/jobs/" + job_id)
                self.assertEqual(response.status_code, 409)
                self.assertTrue((directory / "job.json").is_file())

    async def test_completed_but_still_cleaning_job_is_preserved(self):
        directory = self.job()
        with patch.object(workbench, "active_job", directory.name):
            response = await self.client.delete("/api/jobs/" + directory.name)
        self.assertEqual(response.status_code, 409)
        self.assertTrue((directory / "result.png").is_file())

    async def test_terminal_history_can_be_deleted_while_another_job_runs(self):
        running = self.job("f" * 32, "running")
        with patch.object(workbench, "active_job", running.name):
            for index, state in enumerate(sorted(workbench.TERMINAL)):
                directory = self.job(str(index) * 32, state)
                self.assertEqual((await self.client.delete("/api/jobs/" + directory.name)).status_code, 200)
        self.assertTrue((running / "job.json").is_file())

    async def test_deletion_requires_local_origin_and_client_header(self):
        directory = self.job()
        self.client.headers.pop("X-AIGC-Client")
        response = await self.client.delete("/api/jobs/" + directory.name)
        self.assertEqual(response.status_code, 403)
        response = await self.client.delete("/api/jobs/" + directory.name,
                                           headers={"X-AIGC-Client": "local", "Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(directory.is_dir())

    async def test_invalid_id_cannot_delete_files(self):
        directory = self.job()
        for job_id in ("not-a-job", "..%5c..%5coutside", "a" * 33):
            self.assertEqual((await self.client.delete("/api/jobs/" + job_id)).status_code, 404)
        self.assertTrue(directory.is_dir())

    async def test_linked_job_directory_cannot_delete_outside_files(self):
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        link = self.data / ("a" * 32)
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
        try:
            self.assertEqual((await self.client.delete("/api/jobs/" + link.name)).status_code, 404)
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        finally:
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()

    async def test_file_in_use_failure_keeps_record_for_retry(self):
        directory = self.job()

        def partial_failure(path):
            (path / "job.json").unlink()
            raise PermissionError("File in use")

        with patch.object(workbench.shutil, "rmtree", side_effect=partial_failure):
            response = await self.client.delete("/api/jobs/" + directory.name)
        self.assertEqual(response.status_code, 409)
        self.assertEqual((await self.client.get("/api/jobs/" + directory.name)).status_code, 200)
        self.assertEqual((await self.client.delete("/api/jobs/" + directory.name)).status_code, 200)


if __name__ == "__main__":
    unittest.main()
