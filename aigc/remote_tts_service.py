"""Authenticated, session-scoped TTS API, executed from memory on DGX."""
import asyncio
from contextlib import suppress
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import sys


async def serve(config):
    from aiohttp import web
    root = pathlib.Path(config['root']).resolve()
    expected = ('Bearer ' + config['token']).encode()
    records = {}
    state = {'task': None, 'process': None, 'job_id': None, 'stage': '', 'progress': 0}
    for name in ['model.safetensors', 'audiovae.pth', 'config.json', 'tokenizer.json']:
        if not (pathlib.Path(config['model_dir']) / name).is_file():
            raise RuntimeError('Missing VoxCPM2 model file: ' + name)
    # Fail connection early for incompatible installations without loading weights.
    from voxcpm import VoxCPM

    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(request.headers.get('Authorization', '').encode(), expected):
            return web.json_response({'error': 'unauthorized'}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[authenticate], client_max_size=32 * 1024 * 1024)

    def job_path(area, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise web.HTTPBadRequest(text='Invalid job ID')
        base = root / area
        path = base / job_id
        if path.is_symlink() or path.resolve().parent != base:
            raise web.HTTPBadRequest(text='Invalid job path')
        return path

    async def stop_worker():
        proc = state['process']
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def generate(job_id, settings, reference):
        directory = job_path('output', job_id)
        directory.mkdir(parents=True, exist_ok=True)
        error_lines = []
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, '-B', '-u', '-c', config['worker_source'],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, limit=1024 * 1024)
            state['process'] = proc
            data = {'model_dir': config['model_dir'], 'settings': settings,
                    'reference': str(reference) if reference else None, 'output': str(directory / 'audio.wav')}
            proc.stdin.write((json.dumps(data) + '\n').encode())
            await proc.stdin.drain()
            proc.stdin.close()
            while line := await proc.stdout.readline():
                text = line.decode('utf-8', errors='replace').strip()
                if text.startswith('TTS_EVENT '):
                    state.update(json.loads(text[10:]))
                else:
                    error_lines.append(text[-1000:])
                    error_lines[:] = error_lines[-12:]
            code = await proc.wait()
            if code:
                raise RuntimeError('\n'.join(error_lines)[-2500:] or f'Worker exited with {code}')
            records[job_id] = {'status': {'status_str': 'success'}, 'outputs': {'audio': {'audio': [
                {'filename': 'audio.wav', 'subfolder': job_id, 'type': 'output'}]}}}
        except asyncio.CancelledError:
            await stop_worker()
            records[job_id] = {'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': '任务已取消'}]]}}
        except Exception as error:
            await stop_worker()
            records[job_id] = {'status': {'status_str': 'error', 'messages': [['execution_error', {'exception_message': str(error)}]]}}
        finally:
            state.update(process=None, job_id=None)

    async def submit(request):
        if state['task'] and not state['task'].done():
            raise web.HTTPConflict(text='An audio task is already running')
        body = await request.json()
        job_id, settings = body['job_id'], body['settings']
        directory = job_path('input', job_id)
        reference = directory / 'reference.wav' if settings['mode'] == 'clone' else None
        if reference and (not reference.is_file() or reference.is_symlink()):
            raise web.HTTPBadRequest(text='Reference audio is missing')
        state.update(job_id=job_id, progress=5, stage='准备语音模型')
        state['task'] = asyncio.create_task(generate(job_id, settings, reference))
        return web.json_response({'prompt_id': job_id})

    async def upload(request):
        if state['job_id']:
            raise web.HTTPConflict(text='Cannot upload while inference is running')
        directory = job_path('input', request.query.get('job_id', ''))
        content = await request.read()
        if not content.startswith(b'RIFF') or content[8:12] != b'WAVE':
            raise web.HTTPBadRequest(text='Expected normalized WAV audio')
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'reference.wav').write_bytes(content)
        return web.json_response({'uploaded': True})

    async def history(request):
        job_id = request.match_info.get('job_id')
        return web.json_response({job_id: records[job_id]} if job_id in records else (records if not job_id else {}))

    async def progress(request):
        return web.json_response({key: state[key] for key in ['job_id', 'stage', 'progress']})

    async def cancel(request):
        if request.match_info['job_id'] == state['job_id'] and state['task']:
            state['task'].cancel()
            await state['task']
        return web.json_response({'cancelled': True})

    async def queue(request):
        return web.json_response({'queue_running': [state['job_id']] if state['job_id'] else [], 'queue_pending': []})

    async def cleanup(request):
        if state['job_id']:
            raise web.HTTPConflict(text='Wait for the task to stop')
        body = await request.json()
        paths = [job_path(area, body.get('job_id', '')) for area in ['input', 'output']]
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
        records.pop(body.get('prompt_id'), None)
        return web.json_response({'cleaned': all(not p.exists() for p in paths)})

    def output_path(job_id, filename):
        if filename != 'audio.wav':
            raise web.HTTPBadRequest(text='Invalid audio filename')
        directory = job_path('output', job_id)
        path = directory / filename
        if path.is_symlink() or not path.is_file():
            raise web.HTTPNotFound()
        return path

    async def view(request):
        return web.FileResponse(output_path(request.query.get('subfolder', ''), request.query.get('filename', '')))

    async def artifact(request):
        body = await request.json()
        path = output_path(body.get('job_id', ''), body.get('filename', ''))
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        return web.json_response({'size': path.stat().st_size, 'sha256': digest})

    async def info(request):
        return web.json_response({'root': str(root), 'pid': os.getpid(), 'authenticated': True,
                                 'persistent_database': False, 'files': [str(p.relative_to(root))
                                  for area in ['input', 'output', 'user', 'scratch']
                                  for p in (root / area).rglob('*') if p.is_file()]})

    async def health(request):
        return web.json_response({'engine': 'voxcpm2', 'ready': True})

    async def object_info(request):
        return web.json_response({'VoxCPM2': {}})

    async def shutdown(app):
        if state['task'] and not state['task'].done():
            state['task'].cancel()
            await state['task']

    app.add_routes([web.get('/system_stats', health), web.get('/object_info', object_info),
                    web.get('/history', history), web.get('/history/{job_id}', history),
                    web.get('/tts/progress', progress), web.post('/tts/submit', submit),
                    web.post('/upload/audio', upload), web.get('/view', view),
                    web.get('/queue', queue), web.post('/api/jobs/{job_id}/cancel', cancel),
                    web.post('/aigc/cleanup', cleanup), web.post('/aigc/artifact', artifact),
                    web.get('/aigc/session', info)])
    app.on_cleanup.append(shutdown)
    return app


if __name__ == '__main__':
    from aiohttp import web
    config = json.loads(sys.stdin.readline())
    web.run_app(serve(config), host='127.0.0.1', port=config['port'], access_log=None, print=None)
