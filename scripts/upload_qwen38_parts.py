"""Upload the verified public Q6_K model to the dedicated DGX receiver."""
import concurrent.futures
import hashlib
import http.client
import json
from pathlib import Path
import threading
import time

ROOT = Path('E:/ai/aigc/.cache/qwen38-transfer')
MODEL = ROOT / 'Qwen3.8-27B-Uncensored-Q6_K.gguf'
SIZE = 22430999968
SHA256 = 'a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6'
CHUNK = 8 * 2**20
TOKEN = json.loads((ROOT / 'transfer-session.json').read_text(encoding='utf-8-sig'))['token']
PORT = json.loads((ROOT / 'transfer-server.json').read_text())['port']
LOCK = threading.Lock()
UPLOADED = 0


def state(stage, **extra):
    report = {'stage': stage, **extra}
    temporary = ROOT / 'upload-state.json.tmp'
    temporary.write_text(json.dumps(report))
    temporary.replace(ROOT / 'upload-state.json')
    print(json.dumps(report), flush=True)


def request(method, path, data=b'', headers=None):
    connection = http.client.HTTPConnection('192.168.3.66', PORT, timeout=600, blocksize=8 * 2**20)
    try:
        connection.request(method, path, body=data, headers={'X-Transfer-Token': TOKEN,
                           'Content-Length': str(len(data)), **(headers or {})})
        response = connection.getresponse()
        result = json.loads(response.read())
        assert response.status == 200, result
        return result
    finally:
        connection.close()


def upload(index, existing):
    global UPLOADED
    offset = index * CHUNK
    size = min(CHUNK, SIZE - offset)
    with MODEL.open('rb') as stream:
        stream.seek(offset)
        data = stream.read(size)
    assert len(data) == size
    digest = hashlib.sha256(data).hexdigest()
    previous = existing.get(str(index), {})
    if previous.get('sha256') != digest or previous.get('bytes') != size:
        for attempt in range(8):
            try:
                response = request('POST', '/part/' + str(index), data, {'X-Chunk-SHA256': digest})
                assert response['sha256'] == digest
                break
            except Exception:
                if attempt == 7:
                    raise
                time.sleep(3 + attempt)
    with LOCK:
        UPLOADED += size
        state('uploading', received_bytes=UPLOADED, expected_bytes=SIZE, percent=round(100 * UPLOADED / SIZE, 1))


def main():
    verification = ROOT / 'download-verification.json'
    state('waiting_for_verified_download')
    while not verification.exists():
        time.sleep(5)
    report = json.loads(verification.read_text())
    assert report['verified'] and report['sha256'] == SHA256 and MODEL.stat().st_size == SIZE
    existing = request('GET', '/parts')['completed']
    count = (SIZE + CHUNK - 1) // CHUNK
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(lambda index: upload(index, existing), range(count)))
    state('verifying_on_server')
    verified = request('POST', '/finalize')
    assert verified['verified'] and verified['sha256'] == SHA256
    (ROOT / 'server-download-verification.json').write_text(json.dumps(verified, indent=2))
    state('complete', **verified)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        state('failed', error=str(error))
        raise
