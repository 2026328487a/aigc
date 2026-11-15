"""Finish the authorized deployment after the verified download becomes ready."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from worker import WORK, write_json


def state(stage, **extra):
    report = {'stage': stage, 'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra}
    write_json(WORK / 'finalization-state.json', report)
    print(json.dumps(report), flush=True)


def main():
    state('waiting_for_verified_download')
    while True:
        download = WORK / 'download-state.json'
        if download.exists():
            current = json.loads(download.read_text())
            if current.get('stage') == 'complete':
                break
            if current.get('stage') == 'failed':
                pids = json.loads((WORK / 'worker-pids.json').read_text())
                try:
                    os.waitpid(pids['download']['pid'], os.WNOHANG)
                except ChildProcessError:
                    pass
                if not (Path('/proc') / str(pids['download']['pid'])).exists():
                    state('resuming_download_after_network_error')
                    environment = dict(os.environ)
                    environment['QWEN_DOWNLOAD_PROXY'] = os.environ.get('QWEN_DOWNLOAD_PROXY', 'http://127.0.0.1:7897')
                    environment['QWEN_DOWNLOAD_WORKERS'] = os.environ.get('QWEN_DOWNLOAD_WORKERS', '32')
                    with (WORK / 'download.log').open('a') as log:
                        process = subprocess.Popen(['/usr/bin/python3', '-u', str(WORK / 'download_ranges.py')],
                                                   env=environment, stdin=subprocess.DEVNULL, stdout=log,
                                                   stderr=subprocess.STDOUT, start_new_session=True)
                    pids['download']['pid'] = process.pid
                    write_json(WORK / 'worker-pids.json', pids)
        time.sleep(10)
    state('starting_server')
    subprocess.run(['/usr/bin/python3', str(WORK / 'worker.py'), 'start'], check=True)
    state('waiting_for_server_health')
    for _ in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:18080/health', timeout=5) as response:
                if json.load(response).get('status') == 'ok':
                    break
        except Exception:
            pass
        time.sleep(5)
    else:
        raise RuntimeError('Server did not become healthy')
    state('verifying_chinese_and_api')
    subprocess.run(['/usr/bin/python3', str(WORK / 'verify_api.py'),
                    '--output', str(WORK / 'api-verification.json')], check=True)
    verified = json.loads((WORK / 'api-verification.json').read_text())
    assert verified['passed']
    state('complete', api_verification=str(WORK / 'api-verification.json'))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        state('failed', error=str(error))
        raise
