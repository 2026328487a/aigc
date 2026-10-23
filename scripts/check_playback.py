import hashlib
import json
import pathlib

from playwright.sync_api import expect, sync_playwright

root = pathlib.Path(__file__).resolve().parents[1]
job = json.loads((root / "test-results/image-result.json").read_text(encoding="utf-8"))
errors = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8765", wait_until="networkidle")
    page.locator(f"[data-job-id='{job['id']}']").click()
    video = page.locator("#result-video")
    expect(video).to_be_visible()
    video.evaluate("async el => {el.muted=true; await el.play();}")
    page.wait_for_timeout(1000)
    media = video.evaluate("el => ({time:el.currentTime,width:el.videoWidth,height:el.videoHeight,error:el.error?.message})")
    assert media["time"] > 0 and media["width"] == 768 and not media.get("error"), media
    video.evaluate("el => el.pause()")
    page.screenshot(path=root / "test-results/workbench-final.png", full_page=True)
    with page.expect_download() as downloaded:
        page.locator("#download").click()
    path = root / "test-results/browser-download.mp4"
    downloaded.value.save_as(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == job["sha256"]
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=root / "test-results/workbench-mobile-final.png", full_page=True)
    assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth")
    browser.close()
assert not errors, errors
result = {"playback": media, "browser_download_sha256_matches": True, "mobile_overflow": False, "page_errors": errors}
(root / "test-results/browser.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
