"""Generate a short original song through the actual music form."""
import hashlib
import json
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765', wait_until='networkidle')
    page.locator('#engine').select_option('music3')
    expect(page.locator('#lyrics-field')).to_be_visible()
    page.locator('#prompt').fill('Warm Mandarin acoustic pop with gentle female vocals, fingerpicked guitar and soft piano, clear lyrics, hopeful mood.')
    page.locator('#lyrics').fill('[Verse]\n晨光落在窗边\n风吹过安静的街\n[Chorus]\n把今天唱成一首歌\n让好心情慢慢经过')
    page.locator('#seconds').fill('20')
    if page.locator('#connect').is_visible():
        page.locator('#connect').click()
    expect(page.locator('#generate')).to_be_enabled(timeout=300000)
    with page.expect_response(lambda r: r.url.endswith('/api/jobs') and r.request.method == 'POST') as submitted:
        page.locator('#generate').click()
    response = submitted.value
    assert response.status == 202, response.text()
    job_id = response.json()['id']
    print(json.dumps({'job_id': job_id, 'source': 'browser'}), flush=True)
    page.wait_for_function("() => !document.querySelector('#job-progress').hidden && document.querySelector('#download').hidden", timeout=30000)
    page.wait_for_function("() => !document.querySelector('#download').hidden || !document.querySelector('#job-error').hidden", timeout=600000)
    expect(page.locator('#job-error')).to_be_hidden()
    expect(page.locator('#audio-result')).to_be_visible()
    expect(page.locator('#cleanup-status')).to_contain_text('已清理', timeout=30000)
    page.wait_for_function('Number.isFinite(document.querySelector("#result-audio").duration)')
    page.locator('#result-audio').evaluate('a=>a.play()')
    page.wait_for_timeout(400)
    assert not page.locator('#result-audio').evaluate('a=>a.paused')
    page.locator('.result-card').screenshot(path=str(ROOT / 'test-results/music-song-result.png'))
    with page.expect_download() as download:
        page.locator('#download').click()
    path = ROOT / 'test-results/music-song-browser.wav'
    download.value.save_as(path)
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256((ROOT / 'data/jobs' / job_id / 'result.wav').read_bytes()).digest()
    page.set_viewport_size({'width':390,'height':844})
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    browser.close()
assert not errors, errors
with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False) as client:
    job = client.get('/api/jobs/' + job_id).json()
    diagnostics = client.get('/api/diagnostics').json()
assert job['remote_cleaned'] and not diagnostics['remote']['files']
report = {'job':job, 'diagnostics':diagnostics, 'browser_errors':errors, 'download_matches':True, 'playback_passed':True}
(ROOT / 'test-results' / f'music-ui-{job_id}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'result':'passed','job_id':job_id,'elapsed':job['elapsed'],'audio':job['audio']}),flush=True)
