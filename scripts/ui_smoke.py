"""Exercise the real local page. --submit explicitly starts one small DGX job."""
import argparse
import json
import pathlib
import sys

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--submit", action="store_true")
args = parser.parse_args()
output = ROOT / "test-results"
output.mkdir(exist_ok=True)
source = output / "test-input.png"
image = Image.new("RGB", (640, 384), "#e6ded0")
draw = ImageDraw.Draw(image)
draw.rectangle((0, 255, 640, 384), fill="#bd9c76")
draw.rounded_rectangle((65, 40, 280, 240), radius=8, fill="#c5d8da", outline="#f5eee4", width=12)
draw.line((173, 46, 173, 233), fill="#f5eee4", width=8)
draw.line((71, 140, 273, 140), fill="#f5eee4", width=8)
draw.ellipse((304, 282, 457, 323), fill="#987856")
for radius in range(73, 0, -1):
    intensity = int(120 + 95 * (1 - radius / 73))
    draw.ellipse((380-radius, 238-radius, 380+radius, 238+radius), fill=(intensity, 58, 43))
draw.ellipse((350, 195, 373, 215), fill="#ed9c77")
draw.polygon([(510, 286), (565, 286), (557, 333), (518, 333)], fill="#647b55")
draw.line((538, 287, 538, 198), fill="#465e39", width=5)
draw.ellipse((535, 215, 577, 235), fill="#637f4b")
draw.ellipse((500, 239, 541, 259), fill="#78955c")
image.save(source)

errors = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="msedge", headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:8765", wait_until="networkidle")
    page.screenshot(path=output / "workbench-desktop.png", full_page=True)
    if args.submit:
        page.locator("#engine").select_option("h3")
        if page.locator("#connect").is_visible():
            page.locator("#connect").click()
        expect(page.locator("#server-state")).to_contain_text("已就绪", timeout=300000)
        page.locator("#images").set_input_files(source)
        page.locator("#prompt").fill("A red ball on a wooden table beside a small green plant. The camera slowly pushes forward. Soft warm daylight from the window, a quiet room, gentle ambient sound. Keep the scene consistent.")
        page.locator("#size").select_option("custom")
        page.locator("#width").fill("320")
        page.locator("#height").fill("192")
        page.locator("#seconds").fill("1")
        page.locator("#steps").fill("2")
        page.locator("#seed").fill("42")
        with page.expect_response(lambda response: response.url.endswith("/api/jobs") and response.request.method == "POST") as submitted:
            page.locator("#generate").click()
        response = submitted.value
        if response.status != 202:
            raise RuntimeError(response.text())
        job = response.json()
        (output / "smoke-job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
        print(json.dumps({"job_id": job["id"], "state": job["state"]}), flush=True)
        page.wait_for_timeout(2500)
        page.screenshot(path=output / "workbench-running.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=output / "workbench-mobile.png", full_page=True)
    if page.evaluate("() => document.documentElement.scrollWidth > innerWidth"):
        errors.append("Mobile layout overflows horizontally")
    browser.close()
if errors:
    print(json.dumps(errors, ensure_ascii=False))
    sys.exit(1)
print("Browser checks passed", flush=True)
