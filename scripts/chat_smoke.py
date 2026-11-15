"""Real DGX/workbench checks with short test timeouts; restores production 900s.

python scripts/chat_smoke.py
The test uses its own local port/data and never starts media generation.
"""
import argparse
import asyncio
import datetime
import json
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = 'http://127.0.0.1:8767'
UNITS = ('qwen38-api.socket', 'qwen38-api.service', 'qwen38-backend.service')


def remote(code):
    from aigc.session import SSH_OPTIONS
    result = subprocess.run(['ssh', *SSH_OPTIONS, 'dgx', 'python3 -c ' + shlex.quote(code)],
                            capture_output=True, text=True, encoding='utf-8', timeout=95, check=True)
    return json.loads(result.stdout)


def snapshot():
    return remote("import subprocess,json; names=" + repr(UNITS) + "; d={}; "
                  "exec(\"for n in names:\\n raw=subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID'],text=True);d[n]=dict(l.split('=',1) for l in raw.splitlines());d[n].setdefault('MainPID','0')\");"
                  "gpu=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],text=True);"
                  "mem=next(l.strip() for l in open('/proc/meminfo') if l.startswith('MemAvailable:'));"
                  "print(json.dumps({'units':d,'gpu':gpu.strip(),'memory_available':mem}))")


def released(state):
    return all(x['ActiveState'] in ('inactive', 'failed') and x['MainPID'] == '0' for x in state['units'].values()) and 'llama-server' not in state['gpu']


def wait_released(client=None, timeout=55):
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if client:
            client.get('/api/status').raise_for_status()
            # Simulate the native UI's background polling; it must not renew idle.
            client.get('/qwen/v1/models')
            client.get('/qwen/props?autoload=false')
        state = snapshot()
        if released(state):
            return {'seconds': round(time.monotonic()-started, 2), **state}
        time.sleep(1)
    raise AssertionError('Qwen was not fully released: ' + json.dumps(state))


def watch_native_models():
    started, finished = threading.Event(), threading.Event()
    result = {'chunks': 0}
    def watch():
        try:
            with httpx.Client(base_url=BASE,trust_env=False,timeout=90) as client:
                with client.stream('GET','/qwen/models/sse') as response:
                    response.raise_for_status()
                    started.set()
                    for chunk in response.iter_bytes():
                        result['chunks'] += 1
        except httpx.HTTPError as error:
            result['closed_with'] = type(error).__name__
        finally:
            finished.set()
    thread = threading.Thread(target=watch,daemon=True)
    thread.start()
    assert started.wait(10), 'Native model status SSE did not start'
    return finished, result


def serve():
    import uvicorn
    from aigc import app as workbench
    from aigc.chat import ChatSession
    workbench.DATA = ROOT / 'test-results/chat-smoke-data/jobs'
    workbench.native.root = ROOT / 'test-results/chat-smoke-data/comfy'
    workbench.chat = ChatSession(idle_seconds=5, lease_seconds=5)
    server = uvicorn.Server(uvicorn.Config(workbench.app, host='127.0.0.1', port=8767, access_log=False))
    workbench.app.state.shutdown = lambda: setattr(server, 'should_exit', True)
    server.run()


def start_server(log):
    child = subprocess.Popen([sys.executable, '-X', 'utf8', __file__, '--serve'], cwd=ROOT,
                             stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    with httpx.Client(base_url=BASE, trust_env=False, timeout=2) as client:
        for _ in range(60):
            try:
                client.get('/api/status').raise_for_status()
                return child
            except httpx.HTTPError:
                if child.poll() is not None:
                    raise RuntimeError('Test server failed; inspect chat-smoke-server.log')
                time.sleep(0.2)
    child.terminate()
    raise RuntimeError('Test server startup timed out')


def request(messages, max_tokens=96):
    from aigc.chat import MODEL
    return {'model': MODEL, 'messages': messages, 'max_tokens': max_tokens, 'temperature': 0, 'stream': True}


def stream(client, messages, max_tokens=96):
    if client.get('/api/status').json()['chat']['state'] != 'ready':
        client.post('/api/chat/connect').raise_for_status()
    started = time.monotonic()
    answer, done, busy = '', False, False
    with client.stream('POST', '/qwen/v1/chat/completions', json=request(messages, max_tokens)) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith('data: '):
                continue
            if line[6:] == '[DONE]':
                done = True
                continue
            event = json.loads(line[6:])
            if event.get('error'):
                raise AssertionError(event)
            for choice in event.get('choices', []):
                answer += choice.get('delta', {}).get('content') or ''
                if not busy:
                    busy = client.get('/api/status').json()['chat']['busy']
    assert done and answer, (done, answer)
    return {'answer': answer, 'done': done, 'busy_observed': busy, 'seconds': round(time.monotonic()-started, 2)}


def interrupt(client, action):
    started = time.monotonic()
    with client.stream('POST', '/qwen/v1/chat/completions', json=request([{'role':'user','content':'详细介绍五种学习编程的方法。'}], 512)) as response:
        response.raise_for_status()
        lines = response.iter_lines()
        for line in lines:
            if line.startswith('data: '):
                event = json.loads(line[6:])
                if any(choice.get('delta', {}).get('content') for choice in event.get('choices', [])):
                    break
                if event.get('error'):
                    raise AssertionError(event)
        else:
            raise AssertionError('No tokens before interruption')
        status = client.get('/api/status').json()['chat']
        assert status['busy'], {'first_token_seconds':round(time.monotonic()-started,2), 'status':status, 'first_event':line}
        if action and action != 'cancel':
            result = client.post('/api/chat/' + action)
            result.raise_for_status()
    for _ in range(30):
        if not client.get('/api/status').json()['chat']['busy']:
            return {'seconds': round(time.monotonic()-started, 2), 'upstream_busy': False}
        time.sleep(0.2)
    raise AssertionError('Interrupted request retained its busy slot')


async def lease_check():
    from aigc.chat import ChatSession
    chat = ChatSession(idle_seconds=5, lease_seconds=2)
    try:
        await chat.connect()
        ready, body = chat.begin_proxy('POST', '/v1/chat/completions',
                                      body=json.dumps(request([{'role':'user','content':'只回复：2'}],4)).encode(),
                                      headers={'Content-Type':'application/json'}, inference=True)
        assert (await ready).status_code == 200
        content = b''.join([item async for item in body])
        assert b'[DONE]' in content
        chat.heartbeat.cancel()
        try:
            await chat.heartbeat
        except asyncio.CancelledError:
            pass
        started = time.monotonic()
        # Leave SSH alive but stop heartbeats: this tests the remote fallback.
        await asyncio.wait_for(chat.remote.wait(), 50)
        await chat.reader
        assert chat.cleaned is True
        assert any('心跳超时' in line for line in chat.logs)
        return {'seconds': round(time.monotonic()-started, 2), 'cleaned': chat.cleaned, 'logs': list(chat.logs)}
    finally:
        await chat.stop()


def restore():
    # All tests have ended; return the installed configuration to 900 seconds,
    # then stop the socket too so the workbench can own the next session.
    return remote("import subprocess,json; w='/home/jnu/work/qwen38-gguf-20261111/worker.py';"
                  "subprocess.run(['python3',w,'stop'],check=True,stdout=subprocess.DEVNULL);"
                  "subprocess.run(['python3',w,'start'],check=True,stdout=subprocess.DEVNULL);"
                  "subprocess.run(['python3',w,'stop'],check=True,stdout=subprocess.DEVNULL);print(json.dumps({'idle_seconds':900,'stopped':True}))")


def main():
    report = {'tested_at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
              'test_idle_seconds': 5, 'checks': {}, 'passed': False}
    child = None
    results = ROOT / 'test-results'
    results.mkdir(exist_ok=True)
    log = (results / 'chat-smoke-server.log').open('w', encoding='utf-8')

    def check(name, result):
        report['checks'][name] = result
        print(name + ': ' + json.dumps(result, ensure_ascii=False), flush=True)

    try:
        before = snapshot()
        assert released(before), 'A Qwen service is already running; do not interrupt it'
        child = start_server(log)
        with httpx.Client(base_url=BASE, trust_env=False, timeout=300, headers={'X-AIGC-Client':'local'}) as client:
            client.get('/chat').raise_for_status()
            for _ in range(3): client.get('/api/status').raise_for_status()
            check('connection_page_does_not_start_qwen', snapshot())
            connected = client.post('/api/chat/connect')
            assert connected.is_success, connected.text
            state = snapshot()
            assert state['units'][UNITS[0]]['ActiveState'] == 'active'
            assert all(state['units'][n]['MainPID'] == '0' for n in UNITS[1:])
            check('connect_does_not_load_weights', state)
            native_page = client.get('/qwen/')
            native_page.raise_for_status()
            assert '__sveltekit_' in native_page.text
            assert client.get('/qwen/v1/models').json()['data'][0]['id'] == 'qwen38-27b-uncensored-q6-k'
            model_events_finished, model_events = watch_native_models()
            answer = stream(client,[{'role':'user','content':'用中文介绍写好视频提示词的三个要点。'}])
            assert answer['seconds'] > 5 and answer['busy_observed']
            check('cold_stream_survives_idle_threshold', answer)
            check('loaded_model', snapshot())
            check('idle_release_while_status_polling', wait_released(client))
            assert model_events_finished.wait(5), 'Native model status SSE kept a proxy connection alive'
            check('native_model_status_sse_closed_at_idle', model_events)
            check('wake_again', stream(client,[{'role':'user','content':'记住暗号是绿色海湾，只回复记住了。'}],16))
            history = [{'role':'user','content':'记住暗号是绿色海湾，只回复记住了。'}, {'role':'assistant','content':'记住了。'}, {'role':'user','content':'暗号是什么？只回复暗号。'}]
            remembered = stream(client, history, 16)
            assert '绿色海湾' in remembered['answer']
            check('multi_turn', remembered)
            check('cancel_reply', interrupt(client,'cancel'))
            check('browser_disconnect', interrupt(client,None))
            check('manual_disconnect_during_reply', interrupt(client,'disconnect'))
            check('manual_release', wait_released())
            client.post('/api/shutdown').raise_for_status()
        child.wait(timeout=15)
        child = start_server(log)
        with httpx.Client(base_url=BASE,trust_env=False,timeout=300,headers={'X-AIGC-Client':'local'}) as client:
            stream(client,[{'role':'user','content':'只回复：测试。'}],8)
            child.terminate()  # Windows TerminateProcess: no graceful application shutdown.
            child.wait(timeout=15)
            child = None
        check('forced_local_exit_releases_loaded_model', wait_released())
        check('remote_heartbeat_lease', asyncio.run(lease_check()))
        check('lease_release', wait_released())
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=15)
        log.close()
        if report['checks']:
            report['restored'] = restore()
            report['final'] = snapshot()
        (results / 'qwen-native-workbench-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('passed=' + str(report['passed']), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve',action='store_true')
    if parser.parse_args().serve:
        serve()
    else:
        main()
