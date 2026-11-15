"""Run on DGX with a temporary 5-second timeout, then restore 900 seconds."""
import datetime
import gzip
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request

WORK = Path('/home/jnu/work/qwen38-gguf-20261111')
BASE = 'http://127.0.0.1:18080'
ALIAS = 'qwen38-27b-uncensored-q6-k'
UNITS = ('qwen38-api.socket', 'qwen38-api.service', 'qwen38-backend.service')
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def units():
    result = {}
    for name in UNITS:
        raw = subprocess.check_output(['systemctl', '--user', 'show', name,
                                       '-p', 'ActiveState', '-p', 'MainPID'], text=True)
        result[name] = dict(line.split('=', 1) for line in raw.splitlines())
    return result


def gpu():
    return subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory',
                                    '--format=csv,noheader'], text=True).strip()


def get(path):
    with OPENER.open(BASE + path, timeout=30) as response:
        return json.load(response)


def request(payload):
    return urllib.request.Request(BASE + '/v1/chat/completions',
                                  data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                  headers={'Content-Type': 'application/json', 'Connection': 'close'})


def wait_released():
    started = time.monotonic()
    while time.monotonic() - started < 45:
        current = units()
        if all(current[name]['ActiveState'] == 'inactive' and current[name]['MainPID'] == '0'
               for name in UNITS[1:]):
            processes = gpu()
            if '/home/jnu/src/llama.cpp/build/bin/llama-server' not in processes:
                assert current[UNITS[0]]['ActiveState'] == 'active'
                return {'elapsed_seconds': round(time.monotonic() - started, 2),
                        'units': current, 'gpu_processes': processes}
        time.sleep(0.5)
    raise AssertionError('API processes did not fully exit: ' + json.dumps(units()))


def main():
    configured = json.loads((WORK / 'server-process.json').read_text())
    assert configured['mode'] == 'socket-activated' and configured['idle_seconds'] == 5
    report = {'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'test_idle_seconds': 5, 'checks': {}, 'passed': False}
    try:
        report['checks']['initial_zero_processes'] = wait_released()
        started = time.monotonic()
        assert get('/health')['status'] == 'ok'
        model = next(x for x in get('/v1/models')['data'] if x['id'] == ALIAS)
        assert model['status']['value'] == 'unloaded'
        with OPENER.open(urllib.request.Request(BASE, headers={'Accept-Encoding': 'gzip'}), timeout=30) as response:
            body = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            assert response.status == 200 and b'<html' in body.lower()
        report['checks']['cold_api_and_page'] = {'seconds': round(time.monotonic() - started, 2),
                                                'model_status': model['status']['value']}
        report['checks']['api_only_release'] = wait_released()
        started = time.monotonic()
        with OPENER.open(request({'model': ALIAS, 'messages': [{'role': 'user', 'content': '只输出一个整数：1+1等于多少？'}],
                                  'temperature': 0, 'max_tokens': 8}), timeout=300) as response:
            reply = json.load(response)
        content = reply['choices'][0]['message']['content'].strip()
        assert content == '2', reply
        report['checks']['cold_inference'] = {'content': content, 'seconds': round(time.monotonic() - started, 2),
                                               'gpu_processes': gpu()}
        report['checks']['model_and_api_release'] = wait_released()
        started = time.monotonic()
        chunks = []
        done = False
        with OPENER.open(request({'model': ALIAS, 'messages': [{'role': 'user', 'content': '用中文详细介绍学习编程的五个步骤，每个步骤至少两句话。'}],
                                  'temperature': 0, 'max_tokens': 96, 'stream': True}), timeout=300) as response:
            for raw in response:
                line = raw.decode('utf-8').strip()
                if not line.startswith('data: '):
                    continue
                if line[6:] == '[DONE]':
                    done = True
                    break
                event = json.loads(line[6:])
                for choice in event.get('choices', []):
                    text = choice.get('delta', {}).get('content')
                    if text:
                        chunks.append(text)
        duration = time.monotonic() - started
        assert done and re.search(r'[\u4e00-\u9fff]', ''.join(chunks)) and duration > 5
        report['checks']['wake_and_stream_past_idle_timeout'] = {
            'done': done, 'chunks': len(chunks), 'seconds': round(duration, 2), 'content': ''.join(chunks)}
        report['checks']['final_release'] = wait_released()
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (WORK / 'socket-activation-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        subprocess.run(['python3', str(WORK / 'worker.py'), 'stop'], check=True)
        subprocess.run(['python3', str(WORK / 'worker.py'), 'start'], check=True)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
