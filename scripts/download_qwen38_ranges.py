"""Resumable, hash-verified download of the authorized Qwen Q6_K artifact."""
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import urllib.error
import urllib.request

if os.name == 'nt':
    import msvcrt
    MODEL_ROOT = Path(os.environ['QWEN_MODEL_ROOT'])
    WORK = MODEL_ROOT / 'logs'
    MODEL_BYTES = 22430999968
    MODEL_FILE = 'Qwen3.8-27B-Uncensored-Q6_K.gguf'
    MODEL_REVISION = '45d0fc0ad6cfcf9eeee6008ddaea925536409b18'
    MODEL_SHA256 = 'a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6'

    def write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
else:
    import fcntl
    from worker import MODEL_BYTES, MODEL_FILE, MODEL_REVISION, MODEL_ROOT, MODEL_SHA256, WORK, write_json

URL = ('https://huggingface.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF/resolve/'
       + MODEL_REVISION + '/' + MODEL_FILE)
PROXY = os.environ.get('QWEN_DOWNLOAD_PROXY', 'http://127.0.0.1:7897')
PARTS = MODEL_ROOT / '.range-download'
CHUNK_BYTES = 64 * 2**20
PROGRESS_LOCK = threading.Lock()
URL_LOCK = threading.Lock()
SIGNED_URL = None
COMPLETED_BYTES = 0


def progress(stage, **extra):
    data = {'stage': stage, 'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra}
    write_json(WORK / 'download-state.json', data)
    print(json.dumps(data), flush=True)


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({'https': PROXY, 'http': PROXY}))


def resolve_url(refresh=False):
    global SIGNED_URL
    with URL_LOCK:
        if SIGNED_URL is None or refresh:
            for attempt in range(8):
                try:
                    source = URL if attempt % 2 == 0 else URL.replace('huggingface.co', 'hf-mirror.com')
                    connection = opener() if attempt % 3 else urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    req = urllib.request.Request(source, headers={'Range': 'bytes=0-0', 'User-Agent': 'DGX-Qwen-Deployment'})
                    with connection.open(req, timeout=30) as response:
                        assert response.status == 206
                        assert response.headers.get('Content-Range') == f'bytes 0-0/{MODEL_BYTES}'
                        assert len(response.read()) == 1
                        SIGNED_URL = response.url
                    break
                except Exception as error:
                    if attempt == 7:
                        raise
                    print(json.dumps({'resolve_retry': attempt + 1, 'error': type(error).__name__}), flush=True)
                    time.sleep(2)
        return SIGNED_URL


def fetch_part(index):
    global COMPLETED_BYTES
    start = index * CHUNK_BYTES
    end = min(MODEL_BYTES, start + CHUNK_BYTES) - 1
    size = end - start + 1
    part = PARTS / f'{index:05d}.part'
    for attempt in range(12):
        current = part.stat().st_size if part.exists() else 0
        assert current <= size
        if current == size:
            break
        offset = start + current
        try:
            req = urllib.request.Request(resolve_url(), headers={
                'Range': f'bytes={offset}-{end}', 'User-Agent': 'DGX-Qwen-Deployment'})
            with opener().open(req, timeout=45) as response:
                assert response.status == 206
                assert response.headers.get('Content-Range') == f'bytes {offset}-{end}/{MODEL_BYTES}'
                with part.open('ab') as output:
                    remaining = size - current
                    while remaining:
                        block = response.read(min(2 * 2**20, remaining))
                        if not block:
                            raise IOError('Download segment ended early')
                        output.write(block)
                        remaining -= len(block)
            break
        except Exception as error:
            if attempt == 11:
                raise
            if isinstance(error, urllib.error.HTTPError) and error.code in (401, 403):
                resolve_url(refresh=True)
            print(json.dumps({'part': index, 'attempt': attempt + 1, 'retry': type(error).__name__}), flush=True)
            time.sleep(min(10, 1 + attempt))
    assert part.stat().st_size == size
    with PROGRESS_LOCK:
        COMPLETED_BYTES += size
        progress('downloading', completed_bytes=COMPLETED_BYTES, expected_bytes=MODEL_BYTES,
                 percent=round(100 * COMPLETED_BYTES / MODEL_BYTES, 1))


def main():
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(exist_ok=True)
    assert not PARTS.is_symlink() and PARTS.resolve().parent == MODEL_ROOT.resolve()
    identity = {'revision': MODEL_REVISION, 'filename': MODEL_FILE, 'bytes': MODEL_BYTES, 'sha256': MODEL_SHA256}
    metadata = PARTS / 'identity.json'
    if metadata.exists():
        assert json.loads(metadata.read_text()) == identity
    else:
        write_json(metadata, identity)
    progress('resolving_download', expected_bytes=MODEL_BYTES)
    resolve_url()
    count = (MODEL_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
    with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get('QWEN_DOWNLOAD_WORKERS', '16'))) as pool:
        list(pool.map(fetch_part, range(count)))
    progress('assembling_and_verifying', bytes=MODEL_BYTES)
    temporary = MODEL_ROOT / (MODEL_FILE + '.assembling')
    digest = hashlib.sha256()
    with temporary.open('wb') as output:
        for index in range(count):
            with (PARTS / f'{index:05d}.part').open('rb') as stream:
                for block in iter(lambda: stream.read(8 * 2**20), b''):
                    output.write(block)
                    digest.update(block)
    assert temporary.stat().st_size == MODEL_BYTES
    assert digest.hexdigest() == MODEL_SHA256, 'SHA256 mismatch'
    final = MODEL_ROOT / MODEL_FILE
    os.replace(temporary, final)
    report = {'repository': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF', **identity,
              'path': str(final), 'verified': True}
    write_json(MODEL_ROOT / 'download-verification.json', report)
    assert PARTS.resolve().parent == MODEL_ROOT.resolve() and not PARTS.is_symlink()
    shutil.rmtree(PARTS)
    progress('complete', **report)


if __name__ == '__main__':
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / 'download.lock').open('a') as lock:
        if os.name == 'nt':
            lock.write('0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            main()
        except Exception as error:
            progress('failed', error=type(error).__name__ + ': ' + str(error))
            raise
