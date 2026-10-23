"""Check video-to-reference extraction in Edge against an isolated local server.

Usage: python scripts/reference_ui_smoke.py path/to/video.mp4
Only the final clone worker is stubbed; extraction, upload, and validation are real.
"""
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave

import av
import httpx
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'test-results'
OUTPUT.mkdir(exist_ok=True)
source = Path(sys.argv[1]).resolve()
errors = []
with tempfile.TemporaryDirectory(prefix='reference-ui-', dir=OUTPUT) as temporary:
    directory = Path(temporary)
    jobs = directory / 'jobs'
    jobs.mkdir()
    silent_video = directory / 'no-audio.mp4'
    with av.open(str(silent_video), 'w') as output:
        stream = output.add_stream('libx264', rate=1)
        stream.width = stream.height = 32
        stream.pix_fmt = 'yuv420p'
        for _ in range(5):
            for packet in stream.encode(av.VideoFrame.from_image(Image.new('RGB', (32, 32), 'green'))):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    server_source = f'''
import pathlib, uvicorn
from aigc import app as workbench
workbench.DATA = pathlib.Path({str(jobs)!r})
workbench.session.state = 'ready'
workbench.session.engine = 'voxcpm2'
async def fake_worker(job, settings):
    job.update(state='completed', stage='Local upload verified', result_file='reference.wav', remote_cleaned=True, cleanup_finished=True)
    workbench.save_job(job)
    workbench.active_job = None
workbench.run_job = fake_worker
uvicorn.run(workbench.app, host='127.0.0.1', port={port}, access_log=False, lifespan='off')
'''
    with (OUTPUT / 'reference-ui-server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', server_source], cwd=ROOT,
                                   stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urllib.request.urlopen(url + '/api/status', timeout=1):
                        break
                except urllib.error.URLError:
                    if time.monotonic() > deadline or process.poll() is not None:
                        raise RuntimeError('Isolated test server did not start')
                    time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url, wait_until='networkidle')
                page.locator('#engine').select_option('voxcpm2')
                page.locator('[data-mode=clone]').click()
                expect(page.locator('#generate')).to_be_disabled()
                page.locator('#reference-tools summary').click()
                page.locator('#reference-media').set_input_files(str(source))
                page.wait_for_function('() => Number.isFinite(document.querySelector("#reference-source").duration)')
                page.locator('#reference-trim').check()
                with page.expect_response('**/api/reference-audio/extract') as extracted:
                    page.locator('#reference-extract').click()
                response = extracted.value
                assert response.status == 200, response.text()[:200]
                expect(page.locator('#reference-download')).to_be_visible()
                expect(page.locator('#generate')).to_be_enabled()
                page.wait_for_function('() => Number.isFinite(document.querySelector("#reference-player").duration)')
                duration = page.locator('#reference-player').evaluate('audio => audio.duration')
                assert 3 <= duration <= 30, duration
                page.locator('#reference-player').evaluate('audio => audio.play()')
                page.wait_for_function('() => document.querySelector("#reference-player").currentTime > 0.1')
                with page.expect_download() as downloading:
                    page.locator('#reference-download').click()
                downloaded = directory / 'download.wav'
                downloading.value.save_as(downloaded)
                with httpx.Client(base_url=url, trust_env=False) as client, source.open('rb') as media:
                    check = client.post('/api/reference-audio/extract', headers={'X-AIGC-Client': 'local'},
                                        data={'start': page.locator('#reference-start').input_value(),
                                              'end': page.locator('#reference-end').input_value(), 'trim_silence': 'true'},
                                        files={'media': (source.name, media)})
                    assert check.status_code == 200
                    expected_data = check.content
                assert downloaded.read_bytes() == expected_data, {
                    'download': (downloaded.stat().st_size, downloaded.read_bytes()[:32].hex()),
                    'response': (len(expected_data), expected_data[:32].hex()),
                }
                with wave.open(io.BytesIO(expected_data)) as audio:
                    assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (24000, 1, 2)
                page.locator('#audio-upload-fields').screenshot(path=str(OUTPUT / 'reference-extraction-desktop.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
                page.locator('#audio-upload-fields').screenshot(path=str(OUTPUT / 'reference-extraction-mobile.png'))
                page.locator('#engine').select_option('qwen21')
                assert page.locator('#reference-player').evaluate('audio => audio.paused')
                assert page.locator('#reference-source').evaluate('video => video.paused')
                expect(page.locator('#reference-start')).to_be_disabled()
                page.locator('#engine').select_option('voxcpm2')
                page.locator('[data-mode=clone]').click()
                page.locator('#prompt').fill('测试参考音频上传。')
                with page.expect_response(lambda r: r.url.endswith('/api/jobs') and r.request.method == 'POST') as submitted:
                    page.locator('#generate').click()
                assert submitted.value.status == 202, submitted.value.text()
                job = submitted.value.json()
                uploaded = (jobs / job['id'] / 'reference.wav').read_bytes()
                assert hashlib.sha256(uploaded).digest() == hashlib.sha256(expected_data).digest()
                # Editing a range invalidates the previously prepared reference.
                page.locator('#reference-end').fill('1')
                expect(page.locator('#reference-download')).to_be_hidden()
                page.locator('#reference-extract').click()
                expect(page.locator('#reference-extract-error')).to_contain_text('3–30')
                expect(page.locator('#generate')).to_be_disabled()
                # A file without audio gets an actionable local error.
                page.locator('#reference-media').set_input_files(str(silent_video))
                page.wait_for_function('() => document.querySelector("#reference-source").duration === 5')
                page.locator('#reference-extract').click()
                expect(page.locator('#reference-extract-error')).to_contain_text('没有音轨')
                # Original audio upload remains usable after an extraction error.
                page.locator('#reference-audio').set_input_files(str(downloaded))
                expect(page.locator('#reference-player')).to_be_visible()
                expect(page.locator('#reference-extract-error')).to_be_hidden()
                expect(page.locator('#reference-source')).to_be_hidden()
                expect(page.locator('#generate')).to_be_enabled()
                browser.close()
            assert not errors, errors
            report = {'result': 'passed', 'duration': duration, 'download_matches': True,
                      'clone_upload_matches': True, 'mobile_overflow': False, 'browser_errors': errors,
                      'remote_generation': 'stubbed; local extraction and upload used real endpoints'}
            (OUTPUT / 'reference-extraction-ui.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report), flush=True)
        finally:
            process.terminate()
            process.wait(timeout=15)
