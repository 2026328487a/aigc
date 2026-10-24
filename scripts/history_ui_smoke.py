"""Exercise history deletion with an isolated local server and disposable media; no DGX calls."""
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import av
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "test-results"
OUTPUT.mkdir(exist_ok=True)
errors = []


def fixture(data, job_id, engine="qwen21", state="completed", created="2026-10-16T12:00:00+08:00"):
    directory = data / job_id
    directory.mkdir()
    picture = Image.new("RGB", (640, 384), "#eef2e6")
    draw = ImageDraw.Draw(picture)
    draw.ellipse((180, 65, 450, 335), fill="#77976b")
    picture.save(directory / "reference_1.png")
    filename = "result.png" if engine == "qwen21" else "result.mp4"
    if engine == "qwen21":
        picture.save(directory / filename)
    else:
        with av.open(str(directory / filename), "w") as output:
            stream = output.add_stream("libx264", rate=24)
            stream.width, stream.height, stream.pix_fmt = 640, 384, "yuv420p"
            for _ in range(24):
                for packet in stream.encode(av.VideoFrame.from_image(picture)):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
    job = {"id": job_id, "created": created, "state": state,
           "settings": {"engine": engine, "prompt": "测试作品：绿色圆形与浅色背景", "width": 640,
                        "height": 384, "steps": 25, "seed": 42},
           "images": ["reference_1.png"], "stage": "已保存到本地" if state == "completed" else "采样中",
           "progress": 100, "elapsed": 10, "remote_cleaned": state == "completed",
           "cleanup_finished": state == "completed", "result_file": filename, "effective_seconds": 1}
    (directory / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    (directory / "workflow.json").write_text("{}", encoding="utf-8")
    return job


with tempfile.TemporaryDirectory(prefix="history-ui-", dir=OUTPUT) as temporary:
    data = pathlib.Path(temporary) / "jobs"
    data.mkdir()
    assert data.resolve().parent.parent == OUTPUT.resolve()
    picture_id, second_id, video_id, running_id = (letter * 32 for letter in "abcd")
    fixture(data, picture_id, created="2026-10-16T13:00:00+08:00")
    fixture(data, second_id)
    fixture(data, video_id, engine="h3")
    fixture(data, running_id, state="running")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    source = (
        "import pathlib, uvicorn; from aigc import app as workbench; "
        f"workbench.DATA=pathlib.Path({str(data)!r}); "
        f"workbench.active_job={running_id!r}; "
        f"uvicorn.run(workbench.app, host='127.0.0.1', port={port}, access_log=False)"
    )
    # No lifespan: the running fixture must stay running, with no real worker.
    source = source.replace("access_log=False)", "access_log=False, lifespan='off')")
    with (OUTPUT / "history-ui-server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", source], cwd=ROOT,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(url + "/api/status", timeout=1):
                        break
                except urllib.error.URLError:
                    if time.monotonic() > deadline or process.poll() is not None:
                        raise RuntimeError("Isolated test server did not start")
                    time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url + "/workbench", wait_until="networkidle")

                def item(job_id):
                    return page.locator(f'[data-job-id="{job_id}"]')

                def show(job_id):
                    item(job_id).locator(".history-open").click()

                def check_empty():
                    expect(page.locator("#job-panel")).to_be_hidden()
                    expect(page.locator("#empty-result")).to_be_visible()
                    assert page.locator("#result-image").get_attribute("src") is None
                    assert page.locator("#result-video").get_attribute("src") is None
                    assert page.locator("#working-image").get_attribute("src") is None

                expect(item(running_id).locator(".history-delete")).to_be_disabled()
                expect(page.locator("#clear-preview")).to_be_hidden()
                show(picture_id)
                expect(page.locator("#result-image")).to_be_visible()
                page.screenshot(path=OUTPUT / "history-controls-desktop.png", full_page=True)
                page.locator("#clear-preview").click()
                page.wait_for_timeout(2300)
                check_empty()
                page.reload(wait_until="networkidle")
                check_empty()
                assert (data / picture_id / "result.png").exists()
                show(video_id)
                page.locator("#result-video").evaluate("video => video.play()")
                page.locator("#clear-preview").click()
                check_empty()
                page.wait_for_function("() => {const video=document.getElementById('result-video'); return video.paused && video.readyState===0 && video.buffered.length===0;}")

                show(picture_id)
                page.once("dialog", lambda dialog: dialog.dismiss())
                item(picture_id).locator(".history-delete").click()
                expect(item(picture_id)).to_be_visible()
                assert (data / picture_id).is_dir()

                endpoint = f"**/api/jobs/{second_id}"
                page.route(endpoint, lambda route: route.fulfill(status=409, content_type="application/json",
                           body=json.dumps({"detail": "测试：文件正在使用，请稍后重试"})))
                page.once("dialog", lambda dialog: dialog.accept())
                item(second_id).locator(".history-delete").click()
                expect(page.locator("#error")).to_contain_text("文件正在使用")
                expect(item(second_id).locator(".history-delete")).to_be_enabled()
                assert (data / second_id).is_dir()
                page.unroute(endpoint)

                page.once("dialog", lambda dialog: dialog.accept())
                item(second_id).locator(".history-delete").click()
                expect(item(second_id)).to_have_count(0)
                expect(page.locator("#result-image")).to_have_attribute("src", f"/api/jobs/{picture_id}/files/result.png")
                assert not (data / second_id).exists()

                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=OUTPUT / "history-controls-mobile.png", full_page=True)
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                page.once("dialog", lambda dialog: dialog.accept())
                item(picture_id).locator(".history-delete").click()
                expect(item(picture_id)).to_have_count(0)
                page.wait_for_timeout(2300)
                check_empty()
                page.reload(wait_until="networkidle")
                check_empty()
                assert not (data / picture_id).exists()
                show(video_id)
                page.once("dialog", lambda dialog: dialog.accept())
                item(video_id).locator(".history-delete").click()
                expect(item(video_id)).to_have_count(0)
                check_empty()
                assert not (data / video_id).exists()
                assert (data / running_id / "job.json").exists()

                # Emulate a new completed submission to verify preview selection resumes.
                new_id = "e" * 32
                page.route("**/api/status", lambda route: route.fulfill(content_type="application/json", body=json.dumps({
                    "session": {"state": "ready", "engine": "qwen21", "authenticated": True, "logs": []}, "active_job": None})))

                def submit(route):
                    if route.request.method == "POST":
                        job = fixture(data, new_id)
                        route.fulfill(status=202, content_type="application/json", body=json.dumps(job))
                    else:
                        route.continue_()

                page.route("**/api/jobs", submit)
                expect(page.locator("#generate")).to_be_enabled(timeout=5000)
                page.locator("#prompt").fill("绿色圆形测试图片")
                page.locator("#generate").click()
                expect(page.locator("#result-image")).to_have_attribute("src", f"/api/jobs/{new_id}/files/result.png")
                expect(page.locator("#job-panel")).to_be_visible()
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=15)

if errors:
    raise AssertionError(errors)
(OUTPUT / "history-ui-smoke.json").write_text(json.dumps({
    "passed": True, "javascript_errors": errors, "isolated_data": True,
    "checks": ["dismiss and reload", "video unload", "cancel deletion", "delete error and retry",
               "selected and unselected deletion", "running job protected", "mobile layout", "new submission preview"],
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("History browser checks passed (isolated fixtures; no user files deleted)")
