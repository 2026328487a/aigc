"""Receive only the approved public model artifact over authenticated LAN HTTP."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from worker import MODEL_BYTES, MODEL_FILE, MODEL_REVISION, MODEL_ROOT, MODEL_SHA256, WORK, write_json

SESSION = json.loads((WORK / 'transfer-session.json').read_text())
TOKEN = SESSION['token']
CHUNK_BYTES = 8 * 2**20
PARTS = MODEL_ROOT / '.http-parts'
STATE_LOCK = threading.Lock()
COMPLETED = {}


class Server(ThreadingHTTPServer):
    request_queue_size = 128


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path == '/health':
            self.reply(200, {'status': 'ok'})
        elif self.path == '/parts' and hmac.compare_digest(self.headers.get('X-Transfer-Token', ''), TOKEN):
            self.reply(200, {'completed': COMPLETED})
        else:
            self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get('X-Transfer-Token', ''), TOKEN):
            self.reply(403, {'error': 'Unauthorized'})
            return
        length = int(self.headers.get('Content-Length', '0'))
        if self.path.startswith('/part/'):
            index = int(self.path.rsplit('/', 1)[1])
            size = min(CHUNK_BYTES, MODEL_BYTES - index * CHUNK_BYTES)
            expected = self.headers.get('X-Chunk-SHA256', '')
            if index < 0 or size <= 0 or length != size or len(expected) != 64:
                self.reply(400, {'error': 'Invalid segment'})
                return
            PARTS.mkdir(parents=True, exist_ok=True)
            assert not PARTS.is_symlink() and PARTS.resolve().parent == MODEL_ROOT.resolve()
            temporary = PARTS / (str(index) + '.' + uuid.uuid4().hex + '.tmp')
            try:
                data = self.rfile.read(size)
                assert len(data) == size and hashlib.sha256(data).hexdigest() == expected
                temporary.write_bytes(data)
                os.replace(temporary, PARTS / f'{index:05d}.part')
                with STATE_LOCK:
                    COMPLETED[str(index)] = {'bytes': size, 'sha256': expected}
                    write_json(WORK / 'transfer-state.json', {'stage': 'receiving',
                               'received_bytes': sum(item['bytes'] for item in COMPLETED.values()),
                               'expected_bytes': MODEL_BYTES})
                self.reply(200, {'index': index, 'sha256': expected})
            except Exception as error:
                temporary.unlink(missing_ok=True)
                self.reply(500, {'error': str(error)})
            return
        if self.path == '/finalize' and length == 0:
            temporary = MODEL_ROOT / (MODEL_FILE + '.http-assembling')
            try:
                count = (MODEL_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
                assert len(COMPLETED) == count, 'Segments are missing'
                digest = hashlib.sha256()
                write_json(WORK / 'transfer-state.json', {'stage': 'assembling_and_verifying'})
                with temporary.open('xb') as output:
                    for index in range(count):
                        data = (PARTS / f'{index:05d}.part').read_bytes()
                        output.write(data)
                        digest.update(data)
                assert temporary.stat().st_size == MODEL_BYTES and digest.hexdigest() == MODEL_SHA256
                final = MODEL_ROOT / MODEL_FILE
                os.replace(temporary, final)
                report = {'repository': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF',
                          'revision': MODEL_REVISION, 'filename': MODEL_FILE, 'path': str(final),
                          'bytes': MODEL_BYTES, 'sha256': digest.hexdigest(), 'verified': True}
                write_json(MODEL_ROOT / 'download-verification.json', report)
                write_json(WORK / 'download-state.json', {'stage': 'complete', **report})
                write_json(WORK / 'transfer-state.json', {'stage': 'complete', **report})
                assert PARTS.resolve().parent == MODEL_ROOT.resolve() and not PARTS.is_symlink()
                shutil.rmtree(PARTS)
                self.reply(200, report)
            except Exception as error:
                temporary.unlink(missing_ok=True)
                self.reply(500, {'error': str(error)})
            return
        if self.path == '/probe' and 0 < length <= 8 * 2**20:
            started = time.monotonic()
            received = len(self.rfile.read(length))
            self.reply(200, {'received_bytes': received, 'seconds': time.monotonic() - started})
            return
        if self.path != '/upload' or length != MODEL_BYTES:
            self.reply(400, {'error': 'Unexpected artifact or length'})
            return
        MODEL_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = MODEL_ROOT / (MODEL_FILE + '.upload-' + hashlib.sha256(TOKEN.encode()).hexdigest()[:12])
        assert temporary.resolve().parent == MODEL_ROOT.resolve() and not temporary.is_symlink()
        digest = hashlib.sha256()
        received = 0
        started = time.monotonic()
        self.connection.settimeout(120)
        try:
            with temporary.open('xb') as output:
                while received < MODEL_BYTES:
                    block = self.rfile.read(min(8 * 2**20, MODEL_BYTES - received))
                    if not block:
                        raise IOError('Transfer ended early')
                    output.write(block)
                    digest.update(block)
                    received += len(block)
                    write_json(WORK / 'transfer-state.json', {'stage': 'receiving', 'received_bytes': received,
                               'expected_bytes': MODEL_BYTES, 'elapsed_seconds': time.monotonic() - started})
            assert received == MODEL_BYTES and digest.hexdigest() == MODEL_SHA256, 'SHA256 mismatch'
            final = MODEL_ROOT / MODEL_FILE
            os.replace(temporary, final)
            report = {'repository': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF',
                      'revision': MODEL_REVISION, 'filename': MODEL_FILE, 'path': str(final),
                      'bytes': MODEL_BYTES, 'sha256': digest.hexdigest(), 'verified': True,
                      'transfer_seconds': round(time.monotonic() - started, 3)}
            write_json(MODEL_ROOT / 'download-verification.json', report)
            write_json(WORK / 'download-state.json', {'stage': 'complete', **report})
            write_json(WORK / 'transfer-state.json', {'stage': 'complete', **report})
            self.reply(200, report)
        except Exception as error:
            if temporary.exists():
                temporary.unlink()
            write_json(WORK / 'transfer-state.json', {'stage': 'failed', 'received_bytes': received,
                       'error': str(error)})
            self.reply(500, {'error': str(error)})


if __name__ == '__main__':
    server = Server(('0.0.0.0', 0), Handler)
    write_json(WORK / 'transfer-server.json', {'pid': os.getpid(), 'port': server.server_port})
    server.serve_forever()
