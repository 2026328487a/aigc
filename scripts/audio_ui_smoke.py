"""Exercise Cantonese voice cloning with a synthetic reference through the real UI."""
import hashlib
import json
from pathlib import Path
import sys

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
errors = []
reference = Path(sys.argv[1])
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765/workbench', wait_until='networkidle')
    page.locator('#engine').select_option('voxcpm2')
    page.locator('[data-mode=clone]').click()
    page.locator('#reference-audio').set_input_files(str(reference))
    expect(page.locator('#reference-player')).to_be_visible()
    page.locator('#reference-text').fill('你好呀，歡迎嚟到本地聲音工作台。今日天氣幾好，我哋一齊試下用廣東話講故事。')
    page.locator('#language').select_option('yue')
    page.locator('#prompt').fill('而家我哋用同一把聲，讀一段新嘅內容。你可以喺電腦上直接播放，亦都可以下載保存。')
    if page.locator('#connect').is_visible():
        page.locator('#connect').click()
    expect(page.locator('#generate')).to_be_enabled(timeout=300000)
    with page.expect_response(lambda response: response.url.endswith('/api/jobs') and response.request.method == 'POST') as submitted:
        page.locator('#generate').click()
    response = submitted.value
    assert response.status == 202, response.text()
    job_id = response.json()['id']
    print(json.dumps({'job_id': job_id, 'source': 'browser'}), flush=True)
    page.wait_for_function("() => !document.querySelector('#job-progress').hidden && document.querySelector('#download').hidden", timeout=30000)
    page.wait_for_function("() => !document.querySelector('#download').hidden || !document.querySelector('#job-error').hidden", timeout=600000)
    expect(page.locator('#job-error')).to_be_hidden()
    expect(page.locator('#audio-result')).to_be_visible()
    expect(page.locator('#result-video')).to_be_hidden()
    expect(page.locator('#cleanup-status')).to_contain_text('已清理', timeout=30000)
    page.wait_for_function('Number.isFinite(document.querySelector("#result-audio").duration)')
    page.locator('#result-audio').evaluate('audio => audio.play()')
    page.wait_for_timeout(500)
    assert not page.locator('#result-audio').evaluate('audio => audio.paused')
    page.locator('.result-card').screenshot(path=str(ROOT / 'test-results/voice-clone-result.png'))
    with page.expect_download() as downloading:
        page.locator('#download').click()
    destination = ROOT / 'test-results/voice-clone-browser.wav'
    downloading.value.save_as(destination)
    assert hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256((ROOT / 'data/jobs' / job_id / 'result.wav').read_bytes()).digest()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.locator('#clear-preview').click()
    assert page.locator('#result-audio').evaluate('a => a.paused && !a.hasAttribute("src")')
    page.reload(wait_until='networkidle')
    expect(page.locator('#job-panel')).to_be_hidden()
    browser.close()
assert not errors, errors
with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False) as client:
    job = client.get('/api/jobs/' + job_id).json()
    diagnostics = client.get('/api/diagnostics').json()
assert job['remote_cleaned'] and not diagnostics['remote']['files'], diagnostics
report = {'job': job, 'diagnostics': diagnostics, 'browser_errors': errors,
          'download_matches': True, 'playback_passed': True, 'reference_source': 'VoxCPM2 synthetic Cantonese sample'}
(ROOT / 'test-results' / f'audio-ui-{job_id}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'result': 'passed', 'job_id': job_id, 'elapsed': job['elapsed'], 'audio': job['audio']}), flush=True)
