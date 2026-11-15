"""DGX worker for the authorized Qwen3.8 Q6_K deployment.

Run remotely through dgx_exec.py, or copy this file to the deployment work folder.
Build and download may run independently. This script never deletes models.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request

WORK = Path('/home/jnu/work/qwen38-gguf-20261111')
SOURCE = Path('/home/jnu/src/llama.cpp')
MODEL_ROOT = Path('/home/jnu/models/Qwen3.8-27B-Uncensored-GGUF')
MODEL_FILE = 'Qwen3.8-27B-Uncensored-Q6_K.gguf'
MODEL_REVISION = '45d0fc0ad6cfcf9eeee6008ddaea925536409b18'
MODEL_BYTES = 22430999968
MODEL_SHA256 = 'a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6'
LLAMA_TAG = 'v0.9.0'
LLAMA_COMMIT = 'cd959bae99ff4bed22bef2604f45241c09bbb8c8'
ALIAS = 'qwen38-27b-uncensored-q6-k'
PORT = 18080
IDLE_SLEEP_SECONDS = 900
PRESET_FILE = WORK / 'qwen-idle.ini'
BACKEND_PORT = 18081
UNITS = ('qwen38-api.socket', 'qwen38-api.service', 'qwen38-backend.service')
UNIT_DIR = Path.home() / '.config/systemd/user'
UNIT_MARKER = '# Managed by qwen38-gguf worker.py\n'


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


def state(stage, **extra):
    report = {'stage': stage, 'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), **extra}
    write_json(WORK / (MODE + '-state.json'), report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


def run(args):
    print('Running: ' + ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def build():
    state('fetching')
    assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain'], text=True).strip(), 'Source has local edits'
    run(['git', '-C', SOURCE, 'fetch', 'origin', 'tag', LLAMA_TAG])
    assert subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', LLAMA_TAG + '^{commit}'], text=True).strip() == LLAMA_COMMIT
    run(['git', '-C', SOURCE, 'merge', '--ff-only', LLAMA_TAG])
    state('configuring', commit=LLAMA_COMMIT)
    run(['cmake', '-S', SOURCE, '-B', SOURCE / 'build', '-DGGML_CUDA=ON',
         '-DCMAKE_CUDA_ARCHITECTURES=121', '-DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc',
         '-DCMAKE_BUILD_TYPE=Release', '-DGGML_CUDA_NCCL=OFF', '-DLLAMA_BUILD_TESTS=OFF'])
    state('compiling', commit=LLAMA_COMMIT)
    run(['cmake', '--build', SOURCE / 'build', '--target', 'llama-server', 'llama-cli', '-j', '8'])
    binary = SOURCE / 'build/bin/llama-server'
    version = subprocess.run([str(binary), '--version'], capture_output=True, text=True, check=True)
    help_text = subprocess.run([str(binary), '--help'], capture_output=True, text=True, check=True).stdout
    assert 'draft-mtp' in help_text, 'MTP support is missing'
    state('complete', commit=LLAMA_COMMIT, tag=LLAMA_TAG, version=(version.stdout + version.stderr).strip())


def download():
    os.environ['HF_HUB_DISABLE_PROGRESS_BARS'] = '1'
    os.environ.pop('HF_HUB_DISABLE_XET', None)
    os.environ['HF_XET_HIGH_PERFORMANCE'] = '1'
    proxy = os.environ.get('QWEN_DOWNLOAD_PROXY', 'http://127.0.0.1:7897')
    os.environ['HTTPS_PROXY'] = proxy
    os.environ['HTTP_PROXY'] = proxy
    from huggingface_hub import hf_hub_download
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    state('downloading', expected_bytes=MODEL_BYTES)
    model = Path(hf_hub_download('JonathanColetti/Qwen3.8-27B-Uncensored-GGUF', MODEL_FILE,
                               revision=MODEL_REVISION, local_dir=MODEL_ROOT))
    assert model.stat().st_size == MODEL_BYTES
    state('verifying_sha256', bytes=MODEL_BYTES)
    digest = hashlib.sha256()
    with model.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 2**20), b''):
            digest.update(block)
    assert digest.hexdigest() == MODEL_SHA256, 'Model SHA256 mismatch'
    report = {'repository': 'JonathanColetti/Qwen3.8-27B-Uncensored-GGUF',
              'revision': MODEL_REVISION, 'filename': MODEL_FILE, 'path': str(model),
              'bytes': MODEL_BYTES, 'sha256': digest.hexdigest(), 'verified': True}
    write_json(MODEL_ROOT / 'download-verification.json', report)
    state('complete', **report)


def is_our_server(command):
    legacy_model = ALIAS in command and str(MODEL_ROOT / MODEL_FILE) in command
    router = '--models-preset' in command and str(PRESET_FILE) in command
    return str(SOURCE / 'build/bin/llama-server') in command and (legacy_model or router)


def start(idle_seconds=IDLE_SLEEP_SECONDS):
    verified = json.loads((MODEL_ROOT / 'download-verification.json').read_text())
    assert verified['verified'] and verified['sha256'] == MODEL_SHA256
    assert (MODEL_ROOT / MODEL_FILE).stat().st_size == MODEL_BYTES
    built = json.loads((WORK / 'build-state.json').read_text())
    assert built['stage'] == 'complete' and built['commit'] == LLAMA_COMMIT
    process_file = WORK / 'server-process.json'
    if process_file.exists():
        previous = json.loads(process_file.read_text())
        if previous.get('mode') == 'socket-activated':
            assert previous['idle_seconds'] == idle_seconds, 'Stop the service before changing its idle timeout'
            run(['systemctl', '--user', 'start', UNITS[0]])
            state('socket_ready', **previous)
            return
        proc = Path('/proc') / str(previous['pid'])
        if proc.exists():
            command = (proc / 'cmdline').read_bytes().decode(errors='replace')
            assert is_our_server(command), 'Recorded PID belongs to another process'
            raise RuntimeError('Stop the existing router before switching to socket activation')
    for port in (PORT, BACKEND_PORT):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', port))
    # The router starts without weights; the first request loads the model.
    # Native sleep unloads weights and KV cache after the last task is idle.
    PRESET_FILE.write_text(
        f'[{ALIAS}]\n'
        f'model = {MODEL_ROOT / MODEL_FILE}\n'
        'load-on-startup = false\n'
        'ctx-size = 32768\n'
        'parallel = 1\n'
        'n-gpu-layers = 99\n'
        'flash-attn = on\n'
        'threads = 8\n'
        'threads-batch = 12\n'
        'jinja = true\n'
        'no-mmproj-auto = true\n'
        'reasoning = off\n'
        'spec-type = draft-mtp\n'
        'spec-draft-n-max = 1\n'
        f'sleep-idle-seconds = {idle_seconds}\n', encoding='utf-8')
    args = [str(SOURCE / 'build/bin/llama-server'),
            '--host', '127.0.0.1', '--port', str(BACKEND_PORT),
            '--models-preset', str(PRESET_FILE), '--models-max', '1',
            '--models-autoload', '--sleep-idle-seconds', str(idle_seconds)]
    # systemd retains only a listening socket while idle. The native proxy exits
    # only after all connections close; StopWhenUnneeded then reaps the complete
    # backend cgroup, including model children and their CUDA contexts.
    unit_text = {
        UNITS[0]: '[Unit]\nDescription=Qwen API on-demand listener\n\n'
                  f'[Socket]\nListenStream=127.0.0.1:{PORT}\n'
                  f'Service={UNITS[1]}\nNoDelay=true\n',
        UNITS[1]: '[Unit]\nDescription=Qwen API idle-exiting proxy\n'
                  f'Requires={UNITS[0]} {UNITS[2]}\nAfter={UNITS[0]} {UNITS[2]}\n\n'
                  '[Service]\nType=notify\n'
                  f'ExecStart=/usr/lib/systemd/systemd-socket-proxyd --exit-idle-time={idle_seconds}s 127.0.0.1:{BACKEND_PORT}\n',
        UNITS[2]: '[Unit]\nDescription=Qwen on-demand llama.cpp router\n'
                  'StopWhenUnneeded=yes\n\n[Service]\nType=exec\n'
                  f'ExecStart={" ".join(args)}\n'
                  f'ExecStartPost=/usr/bin/python3 {WORK}/worker.py wait-ready\n'
                  'KillMode=control-group\nTimeoutStartSec=45\nTimeoutStopSec=45\n'
                  f'StandardOutput=append:{WORK}/server.log\nStandardError=inherit\n',
    }
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name in unit_text:
        path = UNIT_DIR / name
        assert not path.exists() or path.read_text().startswith(UNIT_MARKER), f'Unmanaged unit exists: {path}'
    for name, contents in unit_text.items():
        (UNIT_DIR / name).write_text(UNIT_MARKER + contents, encoding='utf-8')
    run(['systemd-analyze', '--user', 'verify', *[UNIT_DIR / name for name in UNITS]])
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'start', UNITS[0]])
    report = {'command': args, 'port': PORT, 'backend_port': BACKEND_PORT, 'model_alias': ALIAS,
              'log': str(WORK / 'server.log'), 'mode': 'socket-activated',
              'idle_seconds': idle_seconds, 'preset': str(PRESET_FILE), 'units': list(UNITS)}
    write_json(process_file, report)
    state('socket_ready', **report)


def wait_ready():
    # Start the proxy only after its backend can receive the queued first request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        try:
            with opener.open(f'http://127.0.0.1:{BACKEND_PORT}/health', timeout=2) as response:
                if json.load(response).get('status') == 'ok':
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError('Router failed to become ready')


def stop():
    process_file = WORK / 'server-process.json'
    if not process_file.exists():
        state('stopped')
        return
    previous = json.loads(process_file.read_text())
    if previous.get('mode') == 'socket-activated':
        run(['systemctl', '--user', 'stop', *UNITS])
        process_file.unlink()
        state('stopped')
        return
    proc = Path('/proc') / str(previous['pid'])
    if proc.exists():
        command = (proc / 'cmdline').read_bytes().decode(errors='replace')
        assert is_our_server(command), 'Recorded PID belongs to another process'
        os.kill(previous['pid'], signal.SIGTERM)
        for _ in range(30):
            if not proc.exists():
                break
            time.sleep(1)
        assert not proc.exists(), 'Server has not stopped'
    process_file.unlink()
    state('stopped')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['build', 'download', 'start', 'stop', 'wait-ready'])
    parser.add_argument('--idle-seconds', type=int, default=IDLE_SLEEP_SECONDS,
                        help='Unload idle models and stop the API after no connections for this many seconds (default: 900)')
    arguments = parser.parse_args()
    if arguments.idle_seconds < 1:
        parser.error('--idle-seconds must be positive')
    MODE = arguments.mode
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / (MODE + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if MODE == 'start':
                start(arguments.idle_seconds)
            else:
                {'build': build, 'download': download, 'stop': stop, 'wait-ready': wait_ready}[MODE]()
        except Exception as error:
            state('failed', error=str(error))
            raise
