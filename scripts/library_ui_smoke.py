"""Seed a temporary archive, serve the app on 8766, and drive the library UI without DGX."""
import io, json, math, pathlib, struct, sys, tempfile, threading, time, wave
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import av, uvicorn
from PIL import Image
from playwright.sync_api import sync_playwright
from aigc import app as workbench
from aigc.native_comfy import NativeComfy

out = ROOT / "test-results"; out.mkdir(exist_ok=True)
temp = tempfile.TemporaryDirectory()

def seed(i, engine, name, data):
    aid = f"{i:x}" * 32
    rec = {"id": aid, "prompt_id": f"p{i}", "engine": engine, "session_root": "/x", "created": f"2026-10-{10+i:02}T12:00:00+08:00",
           "state": "saved", "error": None, "files": [{"filename": name, "local_name": "000" + pathlib.Path(name).suffix}]}
    # Seed the legacy layout so load() exercises archive naming and media separation.
    d = pathlib.Path(temp.name) / "jobs" / aid; d.mkdir(parents=True)
    (d / "job.json").write_text(json.dumps(rec)); (d / rec["files"][0]["local_name"]).write_bytes(data)
    (d / "workflow-api.json").write_text(json.dumps({"1": {"class_type": "SaveImage", "inputs": {}}}))

png = io.BytesIO(); Image.new("RGB", (320, 200), (40, 120, 200)).save(png, "PNG")
wav = io.BytesIO()
with wave.open(wav, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * t / 16000))) for t in range(16000 * 10)))
mp4 = io.BytesIO()
with av.open(mp4, "w", format="mp4") as c:
    s = c.add_stream("libx264", rate=10); s.width, s.height, s.pix_fmt = 160, 96, "yuv420p"
    for f in range(30):
        frame = av.VideoFrame.from_image(Image.new("RGB", (160, 96), (f * 8, 60, 90)))
        for p in s.encode(frame): c.mux(p)
    for p in s.encode(): c.mux(p)
for i in range(14):
    kind = i % 3
    seed(i + 1, ["qwen21", "h3", "music3"][kind], [f"img{i}.png", f"clip{i}.mp4", f"song{i}.wav"][kind], [png, mp4, wav][kind].getvalue())

native = NativeComfy(workbench.session, temp.name); native.load()
workbench.native = native
report_folders = sorted(p.name for p in (pathlib.Path(temp.name) / "jobs").iterdir())
report_media = {bucket: len(list((pathlib.Path(temp.name) / "media" / bucket).iterdir())) for bucket in ("images", "videos", "audio")}

server = uvicorn.Server(uvicorn.Config(workbench.app, host="127.0.0.1", port=8766, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started: time.sleep(0.1)
report = {"errors": [], "console": [], "folders": report_folders[:2], "media": report_media}
base = "http://127.0.0.1:8766"
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    page = b.new_page(viewport={"width": 1280, "height": 900})
    page.on("pageerror", lambda e: report["errors"].append(str(e)))
    page.on("console", lambda m: m.type in ("error", "warning") and report["console"].append(m.text))
    page.goto(base + "/"); page.wait_for_selector("#jobs article.job")
    report["launcher_cards"] = page.locator("#jobs article.job").count()
    report["launcher_all_link"] = page.text_content("#all-jobs")
    report["template_cards"] = page.locator(".template-card").count()
    page.screenshot(path=out / "library-launcher.png", full_page=True)
    # playback survives the 2s status refresh
    audio = page.locator("#jobs audio").first
    audio.evaluate("a => { a.muted = true; return a.play(); }")
    page.wait_for_timeout(4500)
    report["launcher_audio_still_playing"] = audio.evaluate("a => a.isConnected && !a.paused && a.currentTime > 3")
    page.goto(base + "/library"); page.wait_for_selector("#library-jobs article.job")
    report["library_status"] = page.text_content("#library-status"); report["page"] = page.text_content("#page-number")
    report["library_cards"] = page.locator("#library-jobs article.job").count()
    report["images_loaded"] = page.eval_on_selector_all("#library-jobs img", "is => Promise.all(is.map(i => (i.loading = 'eager', i.decode().then(() => i.naturalWidth > 0, () => false)))).then(r => [r.length, r.every(Boolean)])")
    report["video_meta"] = page.locator("#library-jobs video").first.evaluate("v => new Promise(r => v.readyState >= 1 ? r(v.duration) : v.addEventListener('loadedmetadata', () => r(v.duration)))")
    page.screenshot(path=out / "library-page1.png", full_page=True)
    page.click("#next"); page.locator('#page-number', has_text='2 /').wait_for()
    report["page2_cards"] = page.locator("#library-jobs article.job").count()
    page.select_option("#filter-engine", "h3"); page.locator('#library-status', has_text='共 5 ').wait_for()
    report["h3_engines"] = page.eval_on_selector_all("#library-jobs .job-title span:first-child", "s => [...new Set(s.map(x => x.textContent.split(' · ')[0]))]")
    page.select_option("#filter-engine", ""); page.fill("#search", "song5"); page.click("button[type=submit]")
    page.locator('#library-status', has_text='共 1 ').wait_for()
    page.click(".preview-open"); page.wait_for_selector("dialog[open] audio")
    report["dialog_caption"] = page.text_content("dialog[open] figcaption")
    report["download_links"] = page.locator("a[download], a[href*='download=true']").count()
    page.screenshot(path=out / "library-dialog.png")
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    report["dialog_closed_and_released"] = page.evaluate("!document.querySelector('dialog').open && !document.querySelector('dialog audio')")
    report["open_href"] = page.get_attribute(".workflow-open", "href")
    m = b.new_page(viewport={"width": 390, "height": 844}); m.goto(base + "/library"); m.wait_for_selector("#library-jobs article.job")
    report["mobile_overflow"] = m.evaluate("document.documentElement.scrollWidth > innerWidth")
    m.screenshot(path=out / "library-mobile.png", full_page=True)
    page.goto(base + "/open-workflow?job=notvalid"); page.wait_for_selector("#workflow-error:not([hidden])")
    report["bad_job_error"] = page.text_content("#workflow-error")
    report["session_state_after"] = workbench.session.state
    b.close()
server.should_exit = True
print(json.dumps(report, ensure_ascii=False, indent=1))
assert not report["errors"] and not report["console"], report
assert report["launcher_cards"] == 8 and report["launcher_audio_still_playing"], report
assert (report["library_cards"], report["page2_cards"], report["h3_engines"]) == (12, 2, ["MiniMax H3"]), report
assert report["images_loaded"][1] and report["video_meta"] > 0 and report["dialog_closed_and_released"], report
assert report["folders"][0] == "2026-10-11_12-00-00_qwen21" and report["download_links"] == 0, report
assert report["media"] == {"images": 5, "videos": 5, "audio": 4}, report
assert not list((pathlib.Path(temp.name) / "jobs").glob("*/*.png")), report
assert report["dialog_caption"] == "结果 1 · 2026-10-16_12-00-00_music3_01.wav", report
assert not report["mobile_overflow"] and report["session_state_after"] == "disconnected", report
