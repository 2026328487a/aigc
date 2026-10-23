"""Explicitly generate a default Qwen image through the real browser UI."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "test-results"
BASE = "http://127.0.0.1:8765"
errors = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE, wait_until="networkidle")
    page.locator("#engine").select_option("qwen21")
    expect(page.locator("#upload-fields")).to_be_hidden()
    expect(page.locator("#steps")).to_have_value("25")
    expect(page.locator("#size")).to_have_value("1024,1024")
    if page.locator("#connect").is_visible():
        page.locator("#connect").click()
    expect(page.locator("#generate")).to_be_enabled(timeout=300000)
    page.locator("#prompt").fill(
        "A single red ceramic teapot on a wooden desk beside a small green plant. "
        "Soft morning sunlight from a window, detailed product photography, "
        "warm peaceful atmosphere, clean composition."
    )
    with page.expect_response(lambda response: response.url.endswith("/api/jobs")
                              and response.request.method == "POST") as submitted:
        page.locator("#generate").click()
    response = submitted.value
    assert response.status == 202, response.text()
    job_id = response.json()["id"]
    print(json.dumps({"job_id": job_id, "source": "browser", "size": 1024, "steps": 25}), flush=True)
    page.wait_for_function("() => !document.querySelector('#download').hidden || "
                           "!document.querySelector('#job-error').hidden", timeout=600000)
    expect(page.locator("#job-error")).to_be_hidden()
    expect(page.locator("#result-image")).to_be_visible()
    expect(page.locator("#result-video")).to_be_hidden()
    expect(page.locator("#cleanup-status")).to_contain_text("已清理", timeout=30000)
    assert page.locator("#result-image").evaluate("image => image.complete && image.naturalWidth === 1024")
    page.screenshot(path=OUTPUT / "qwen-default-desktop.png", full_page=True)
    with page.expect_download() as downloading:
        page.locator("#download").click()
    downloaded = OUTPUT / "qwen-browser-download.png"
    downloading.value.save_as(downloaded)
    original = ROOT / "data" / "jobs" / job_id / "result.png"
    assert hashlib.sha256(downloaded.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest()
    page.set_viewport_size({"width": 390, "height": 844})
    assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    page.screenshot(path=OUTPUT / "qwen-default-mobile.png", full_page=True)
    browser.close()
assert not errors, errors
with httpx.Client(base_url=BASE, trust_env=False) as client:
    job = client.get("/api/jobs/" + job_id).json()
    diagnostics = client.get("/api/diagnostics").json()
assert job["state"] == "completed" and job["remote_cleaned"]
assert not diagnostics["remote"]["files"], diagnostics["remote"]["files"]
report = {"job": job, "diagnostics": diagnostics, "browser_errors": errors, "download_matches": True}
(OUTPUT / f"qwen-{job_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"state": job["state"], "elapsed": job["elapsed"], "image": job["image"],
                  "remote_cleaned": job["remote_cleaned"], "browser_checks": "passed"}), flush=True)
