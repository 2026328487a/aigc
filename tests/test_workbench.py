import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import av
import httpx
from fastapi import HTTPException
from PIL import Image, PngImagePlugin

from aigc import app as workbench
from aigc.workflows import Settings, build_workflow


class LocalApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url="http://127.0.0.1:8765")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_cross_origin_read_is_rejected(self):
        response = await self.client.get("/api/status", headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)

    async def test_cross_origin_mutation_is_rejected(self):
        response = await self.client.post("/api/connect", headers={"Origin": "https://example.com", "X-AIGC-Client": "local"})
        self.assertEqual(response.status_code, 403)

    async def test_mutation_requires_custom_header(self):
        response = await self.client.post("/api/connect")
        self.assertEqual(response.status_code, 403)

    async def test_dns_rebinding_host_is_rejected(self):
        response = await self.client.get("/api/status", headers={"Host": "evil.example:8765"})
        self.assertEqual(response.status_code, 400)

    async def test_normal_local_read_is_allowed(self):
        response = await self.client.get("/api/status", headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("token", response.json()["session"])

    async def test_secret_or_arbitrary_file_cannot_be_downloaded(self):
        response = await self.client.get("/api/jobs/" + "a" * 32 + "/files/.env")
        self.assertEqual(response.status_code, 404)


class MediaTests(unittest.TestCase):
    def write_video(self, path, metadata=False):
        with av.open(str(path), "w", options={"movflags": "use_metadata_tags"}) as output:
            if metadata:
                output.metadata["prompt"] = "This should not be in an exported video."
            stream = output.add_stream("libx264", rate=24)
            stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
            for _ in range(3):
                for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 64), "green"))):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)

    def test_valid_mp4_decodes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.mp4"
            self.write_video(path)
            info = workbench.validate_video(path)
            self.assertEqual((info["width"], info["height"]), (64, 64))
            self.assertGreater(info["duration"], 0)

    def test_prompt_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.mp4"
            self.write_video(path, metadata=True)
            with self.assertRaisesRegex(ValueError, "元数据"):
                workbench.validate_video(path)

    def test_nonvideo_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.mp4"
            path.write_bytes(b"not an mp4")
            with self.assertRaises(av.FFmpegError):
                workbench.validate_video(path)

    def test_job_path_rejects_escape(self):
        with self.assertRaises(HTTPException):
            workbench.job_dir("../output")


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_before_worker_start_is_finalized(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workbench, "DATA", pathlib.Path(directory)):
            job = {"id": "a" * 32, "state": "queued"}
            workbench.job_dir(job["id"]).mkdir()
            workbench.save_job(job)
            task = asyncio.create_task(workbench.run_job(job, Settings(prompt="test")))
            with patch.object(workbench, "active_job", job["id"]), patch.object(workbench, "active_task", task):
                await workbench.cancel_active()
                result = workbench.read_job(job["id"])
                self.assertEqual(result["state"], "cancelled")
                self.assertTrue(result["remote_cleaned"])
                self.assertIsNone(workbench.active_job)

    async def test_repeated_cancel_does_not_interrupt_cleanup(self):
        cleaned = []

        async def worker():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(0.02)
                cleaned.append(True)
                workbench.active_job = None

        task = asyncio.create_task(worker())
        await asyncio.sleep(0)
        with patch.object(workbench, "active_job", "a" * 32), patch.object(workbench, "active_task", task):
            await asyncio.gather(workbench.cancel_active(), workbench.cancel_active())
            self.assertEqual(cleaned, [True])


class QwenTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_job_accepts_no_upload_and_old_video_mode_requires_one(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workbench, "DATA", pathlib.Path(directory)), \
             patch.object(workbench.session, "state", "ready"), patch.object(workbench.session, "engine", "qwen21"), \
             patch.object(workbench, "active_job", None), patch.object(workbench, "active_task", None), \
             patch.object(workbench, "run_job", new_callable=AsyncMock):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url="http://127.0.0.1:8765",
                                         headers={"X-AIGC-Client": "local"}) as client:
                for mode in ("edit", "text"):
                    settings = Settings(engine="qwen21", mode=mode, prompt="A green teapot")
                    response = await client.post("/api/jobs", data={"settings": settings.model_dump_json()})
                    self.assertEqual(response.status_code, 422 if mode == "edit" else 202)
                await workbench.active_task
                with patch.object(workbench.session, "engine", "h3"):
                    response = await client.post("/api/jobs", data={"settings": Settings(prompt="video").model_dump_json()})
                    self.assertEqual(response.status_code, 422)

    async def test_job_cannot_use_a_different_connected_model(self):
        with patch.object(workbench.session, "state", "ready"), patch.object(workbench.session, "engine", "h3"), \
             patch.object(workbench, "active_task", None):
            settings = Settings(engine="qwen21", mode="text", prompt="test")
            with self.assertRaises(HTTPException) as caught:
                await workbench.create_job(settings.model_dump_json(), [])
            self.assertEqual(caught.exception.status_code, 409)

    async def test_connect_does_not_switch_while_job_active(self):
        with patch.object(workbench, "active_job", "a" * 32):
            with self.assertRaises(HTTPException) as caught:
                await workbench.connect("qwen21")
            self.assertEqual(caught.exception.status_code, 409)

    def test_edit_latent_follows_reference_dimensions(self):
        graph = build_workflow(Settings(engine="qwen21", mode="edit", prompt="Make it blue"), ["ref.png"], "a" * 32)
        self.assertEqual(graph["12"]["inputs"]["latent_image"], ["7", 2])
        self.assertEqual(graph["7"]["inputs"]["images.image_1"], ["20", 0])

    def test_png_integrity_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "result.png.part"
            image = Image.new("RGBA", (64, 96), (12, 120, 12, 128))
            image.save(path, format="PNG")
            self.assertEqual(workbench.validate_image(path), {"width": 64, "height": 96, "mode": "RGBA"})
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("prompt", "private")
            image.save(path, format="PNG", pnginfo=metadata)
            with self.assertRaisesRegex(ValueError, "元数据"):
                workbench.validate_image(path)
            path.write_bytes(b"bad PNG")
            with self.assertRaises(OSError):
                workbench.validate_image(path)

    def test_engine_mode_validation(self):
        with self.assertRaises(ValueError):
            Settings(engine="qwen21", mode="reference", prompt="test")
        with self.assertRaises(ValueError):
            Settings(prompt="test", width=2048)


if __name__ == "__main__":
    unittest.main()
