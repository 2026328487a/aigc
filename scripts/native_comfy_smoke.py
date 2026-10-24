"""Verify native UI, a small CPU media workflow, archive, and session cleanup."""

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import time
import wave

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aigc.native_comfy import file_descriptors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["qwen21", "h3", "music3"], default="qwen21")
    parser.add_argument("--generate", action="store_true", help="Run a small real model workflow instead of the CPU save workflow")
    parser.add_argument("--restore", action="store_true", help="Reconnect and verify saved workflow/settings restoration")
    args = parser.parse_args()
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    report = {"engine": args.engine, "model_inference": args.generate, "page_errors": [], "failed_requests": []}
    with httpx.Client(base_url="http://127.0.0.1:8765", headers={"X-AIGC-Client": "local"}, trust_env=False, timeout=60) as client:
        status = client.get("/api/status").json()
        assert not status["active_job"] and not status["comfy"]["pending"], "Another task is active"
        existing = {r["id"] for r in status["comfy"]["jobs"]}
        client.post("/api/connect", params={"engine": args.engine}).raise_for_status()
        try:
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                status = client.get("/api/status").json()["session"]
                if status["state"] == "error":
                    raise RuntimeError(status["error"])
                if status["state"] == "ready" and status["engine"] == args.engine:
                    break
                time.sleep(2)
            else:
                raise TimeoutError("ComfyUI startup timed out")
            info = client.get("/api/diagnostics").json()["remote"]
            report["remote_root"] = info["root"]
            assert client.get("/comfy/").status_code == 200
            if args.engine == "music3":
                media = io.BytesIO()
                with wave.open(media, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(24000)
                    audio.writeframes(b"".join(struct.pack("<h", int(1500 * math.sin(2 * math.pi * 440 * i / 24000))) for i in range(24000)))
                upload = client.post("/comfy/upload/audio", files={"audio": ("native-smoke.wav", media.getvalue(), "audio/wav")})
                # ComfyUI uses the shared upload/image endpoint for media files.
                if upload.status_code in (404, 405):
                    upload = client.post("/comfy/upload/image", files={"image": ("native-smoke.wav", media.getvalue(), "audio/wav")})
                upload.raise_for_status()
                descriptor = upload.json()
                graph = {"1": {"class_type": "LoadAudio", "inputs": {"audio": descriptor["name"]}},
                         "2": {"class_type": "AIGCSaveAudioWAV", "inputs": {"audio": ["1", 0], "filename_prefix": "native-smoke"}}}
            else:
                media = io.BytesIO()
                Image.new("RGB", (64, 64), (78, 130, 112)).save(media, format="PNG")
                upload = client.post("/comfy/upload/image", files={"image": ("native-smoke.png", media.getvalue(), "image/png")})
                upload.raise_for_status()
                graph = {"1": {"class_type": "LoadImage", "inputs": {"image": upload.json()["name"]}},
                         "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": "native-smoke"}}}
            if args.generate:
                template = {"qwen21": "qwen-text", "h3": "h3-image", "music3": "music"}[args.engine]
                graph = client.get("/api/comfy/templates/" + template).json()
                if args.engine == "qwen21":
                    graph["12"]["inputs"]["steps"] = 4
                elif args.engine == "h3":
                    graph["20"]["inputs"]["image"] = upload.json()["name"]
                    graph["7"]["inputs"].update(width=320, height=192, length=39)
                    graph["11"]["inputs"]["steps"] = 2
                else:
                    graph["12"]["inputs"]["steps"] = 10
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda e: report["page_errors"].append(str(e)))
                page.on("requestfailed", lambda request: report["failed_requests"].append({"url": request.url, "error": request.failure}))
                page.goto("http://127.0.0.1:8765/", wait_until="domcontentloaded")
                page.wait_for_selector("#open-comfy:not([hidden])")
                page.screenshot(path=output / f"native-{args.engine}-launcher.png", full_page=True)
                page.goto("http://127.0.0.1:8765/comfy/", wait_until="domcontentloaded")
                page.wait_for_function("window.app && window.app.graph && typeof window.app.loadApiJson === 'function'", timeout=90000)
                page.keyboard.press("Escape")
                report["native_app_loaded"] = True
                page.evaluate("async graph => { await window.app.loadApiJson(graph, 'native-smoke.api.json'); await window.app.queuePrompt(0, 1); }", graph)
                workflow = page.evaluate("window.app.graph.serialize()")
                if args.restore:
                    client.post("/comfy/api/userdata/workflows%2Fnative-state-smoke.json", json=workflow).raise_for_status()
                    client.post("/comfy/api/settings/AIGC.NativeStateSmoke", json="restored").raise_for_status()
                deadline = time.monotonic() + (300 if args.generate else 120)
                while time.monotonic() < deadline:
                    jobs = client.get("/api/status").json()["comfy"]["jobs"]
                    record = next((r for r in jobs if r["id"] not in existing), None)
                    if record and record["state"] in ("saved", "failed", "download_error"):
                        break
                    page.wait_for_timeout(500)
                else:
                    raise TimeoutError("Native workflow did not archive")
                report["job"] = record
                assert record["state"] == "saved", record
                assert record["files"], record
                page.wait_for_timeout(1000)
                page.keyboard.press("Escape")
                page.screenshot(path=output / f"native-{args.engine}-canvas.png", full_page=True)
                report["canvas_nodes"] = page.evaluate("window.app.graph._nodes.map(n => n.type)")
                for file in record["files"]:
                    data = client.get(f"/api/comfy/jobs/{record['id']}/files/{file['local_name']}").content
                    assert hashlib.sha256(data).hexdigest() == file["sha256"]
                report["download_verified"] = True
                disconnected = client.post("/api/disconnect")
                disconnected.raise_for_status()
                report["cleaned"] = disconnected.json()["cleaned"]
                assert report["cleaned"] is True
                history = client.get("/comfy/history").json()
                assert record["prompt_id"] in history
                artifact = next(file_descriptors(history[record["prompt_id"]]["outputs"]))
                assert client.get("/comfy/view", params=artifact).status_code == 200
                report["offline_preview"] = True
                browser.close()
            assert info["root"].startswith("/dev/shm/aigc-")
            check = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "dgx", "test ! -e " + shlex.quote(info["root"]) + " && echo CLEANED"], capture_output=True, timeout=20)
            assert check.returncode == 0 and b"CLEANED" in check.stdout
            report["remote_directory_removed"] = True
            if args.restore:
                client.post("/api/connect", params={"engine": args.engine}).raise_for_status()
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    status = client.get("/api/status").json()["session"]
                    if status["state"] == "error":
                        raise RuntimeError(status["error"])
                    if status["state"] == "ready":
                        break
                    time.sleep(2)
                else:
                    raise TimeoutError("Restore startup timed out")
                assert client.get("/comfy/api/userdata/workflows%2Fnative-state-smoke.json").json() == workflow
                assert client.get("/comfy/api/settings").json()["AIGC.NativeStateSmoke"] == "restored"
                report["workflow_and_settings_restored"] = True
                client.delete("/comfy/api/userdata/workflows%2Fnative-state-smoke.json").raise_for_status()
                settings = client.get("/comfy/api/settings").json()
                settings.pop("AIGC.NativeStateSmoke")
                client.post("/comfy/api/userdata/comfy.settings.json", json=settings).raise_for_status()
                restored_root = client.get("/api/diagnostics").json()["remote"]["root"]
                client.post("/api/disconnect").raise_for_status()
                assert restored_root.startswith("/dev/shm/aigc-")
                check = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "dgx", "test ! -e " + shlex.quote(restored_root)], capture_output=True, timeout=20)
                assert check.returncode == 0
                report["restored_session_directory_removed"] = True
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            suffix = "-generate" if args.generate else ""
            (output / f"native-{args.engine}{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if client.get("/api/status").json()["session"]["state"] == "ready":
                client.post("/api/disconnect").raise_for_status()


if __name__ == "__main__":
    main()
