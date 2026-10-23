"""Explicitly submit one Qwen test job to the running local workbench."""
import argparse
import json
import pathlib
import time

import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--size', type=int, default=512)
parser.add_argument('--steps', type=int, default=4)
parser.add_argument('--image', type=pathlib.Path)
parser.add_argument('--prompt', default='A red ceramic teapot on a wooden desk beside a small green plant. Soft morning sunlight from a window, detailed product photography, warm peaceful atmosphere.')
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
with httpx.Client(base_url='http://127.0.0.1:8765', headers={'X-AIGC-Client': 'local'}, timeout=120, trust_env=False) as client:
    response = client.post('/api/connect', params={'engine': 'qwen21'})
    response.raise_for_status()
    for _ in range(150):
        state = client.get('/api/status').json()['session']
        if state['state'] == 'ready' and state['engine'] == 'qwen21':
            break
        if state['state'] == 'error':
            raise RuntimeError(state)
        time.sleep(2)
    else:
        raise RuntimeError('Timed out starting Qwen service')
    settings = {'engine': 'qwen21', 'mode': 'edit' if args.image else 'text', 'prompt': args.prompt,
                'width': args.size, 'height': args.size, 'steps': args.steps, 'seed': 42}
    uploads = [('images', (args.image.name, args.image.read_bytes(), 'image/png'))] if args.image else None
    response = client.post('/api/jobs', data={'settings': json.dumps(settings)}, files=uploads)
    response.raise_for_status()
    job_id = response.json()['id']
    print(json.dumps({'job_id': job_id, 'settings': settings}), flush=True)
    deadline = time.monotonic() + 1800
    reported = 0
    while time.monotonic() < deadline:
        job = client.get('/api/jobs/' + job_id).json()
        if time.monotonic() - reported > 25:
            print(json.dumps({k: job.get(k) for k in ('id', 'state', 'stage', 'elapsed', 'progress', 'error')}, ensure_ascii=False), flush=True)
            reported = time.monotonic()
        if job['state'] in ('completed', 'failed', 'cancelled') and job.get('cleanup_finished'):
            break
        time.sleep(2)
    else:
        client.post(f'/api/jobs/{job_id}/cancel').raise_for_status()
        raise RuntimeError('Inference timeout; cancelled owned test job')
    report = {'job': job, 'diagnostics': client.get('/api/diagnostics').json()}
    (root / 'test-results' / f'qwen-{job_id}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'state': job['state'], 'elapsed': job['elapsed'], 'image': job.get('image'),
                      'remote_cleaned': job.get('remote_cleaned'), 'error': job.get('error')}, ensure_ascii=False), flush=True)
    if job['state'] != 'completed' or not job.get('remote_cleaned'):
        raise RuntimeError('Qwen smoke check failed')
