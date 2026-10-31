"""Run one real DGX audio job through the local workbench API."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', choices=['music3', 'voxcpm2'], required=True)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--steps', type=int)
    parser.add_argument('--language', default='zh')
    parser.add_argument('--reference')
    parser.add_argument('--reference-text', default='')
    parser.add_argument('--prompt')
    parser.add_argument('--cancel', action='store_true')
    args = parser.parse_args()
    settings = {'engine': args.engine, 'mode': 'music' if args.engine == 'music3' else ('clone' if args.reference else 'speech'),
                'prompt': args.prompt or ('A gentle instrumental acoustic guitar piece, warm piano, no vocals, calm hopeful mood.'
                                         if args.engine == 'music3' else '你好，欢迎来到本地声音工作台。今天我们一起试试自然的语音生成。'),
                'lyrics': '[Instrumental]', 'seconds': args.seconds, 'steps': args.steps or (30 if args.engine == 'music3' else 10),
                'seed': 42, 'language': args.language, 'reference_text': args.reference_text}
    with httpx.Client(base_url=f'http://127.0.0.1:{args.port}', trust_env=False, timeout=60,
                      headers={'X-AIGC-Client': 'local'}) as client:
        status = client.get('/api/status').json()
        if status['active_job']:
            raise RuntimeError('Another task is running; leave it alone')
        client.post('/api/connect', params={'engine': args.engine}).raise_for_status()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            status = client.get('/api/status').json()['session']
            if status['state'] == 'error':
                raise RuntimeError(status['error'])
            if status['state'] == 'ready' and status['engine'] == args.engine:
                break
            time.sleep(2)
        else:
            raise TimeoutError('Connection startup timed out')
        files = {'reference_audio': ('reference.wav', Path(args.reference).read_bytes(), 'audio/wav')} if args.reference else None
        response = client.post('/api/jobs', data={'settings': json.dumps(settings)}, files=files)
        response.raise_for_status()
        job_id = response.json()['id']
        print(json.dumps({'job_id': job_id, 'engine': args.engine}), flush=True)
        started = time.monotonic()
        previous = None
        cancelled = False
        while time.monotonic() - started < 1200:
            job = client.get('/api/jobs/' + job_id).json()
            if job['stage'] != previous:
                print(json.dumps({k: job.get(k) for k in ['state', 'stage', 'elapsed', 'error']}, ensure_ascii=False), flush=True)
                previous = job['stage']
            if args.cancel and not cancelled and job['state'] == 'running' and job['progress'] >= 25 and time.monotonic() - started > 8:
                client.post('/api/jobs/' + job_id + '/cancel').raise_for_status()
                cancelled = True
            if job['state'] in ('completed', 'failed', 'cancelled') and job.get('cleanup_finished'):
                break
            time.sleep(2)
        diagnostics = client.get('/api/diagnostics').json()
        report = {'job': job, 'diagnostics': diagnostics}
        if job['state'] == 'completed':
            data = client.get(f'/api/jobs/{job_id}/files/result.wav').content
            assert hashlib.sha256(data).hexdigest() == job['sha256']
            report['download_matches'] = True
        (ROOT / 'test-results' / f'audio-{job_id}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        assert job['state'] == ('cancelled' if args.cancel else 'completed'), job
        assert job['remote_cleaned'], job
        if diagnostics['remote']:
            assert not diagnostics['remote']['files'], diagnostics
        print(json.dumps({'result': 'passed', 'job_id': job_id, 'elapsed': job['elapsed'], 'audio': job.get('audio')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
