"""Run on DGX with Python 3.10+. Download pinned public weights via hf-mirror.com."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path('/home/jnu/src/ComfyUI-qwen21/models')
REVISION = '7959e658915f52274897fb573ffeda49de8dc16b'
REPO = 'abenzerps/Qwen-Image-2.1-Uncensored-Quantized'
FILES = [
    ('qwen-image-2.1-UC-int8_convrot.safetensors', 'diffusion_models', 7256796840, '5bc5a6c007eff1e0d4004344a24c4af966b9d2c83d142e613b0d456ccceb19ae'),
    ('text_encoders/qwen3vl_8b_bf16.safetensors', 'text_encoders', 17534334616, '68bdc82bc1b66851162ae656225e7e2068166b603db19bd5d5a3b90eb12669a9'),
    ('vae/qwen_image_2.1_vae_bf16.safetensors', 'vae', 675509688, 'bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9'),
]


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(item):
    source, category, size, expected = item
    destination = ROOT / category / Path(source).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size == size and checksum(destination) == expected:
            print('VERIFIED existing ' + destination.name, flush=True)
            return
        raise RuntimeError('Existing model differs; refusing to overwrite: ' + str(destination))
    partial = destination.with_suffix('.safetensors.part')
    url = f'https://hf-mirror.com/{REPO}/resolve/{REVISION}/{source}'
    for attempt in range(1, 7):
        print(f'DOWNLOAD {destination.name} attempt {attempt}', flush=True)
        process = subprocess.Popen(['curl', '-4', '--fail', '--location', '--silent', '--show-error',
            '--connect-timeout', '20', '--speed-limit', '1024', '--speed-time', '60',
            '--continue-at', '-', '--output', str(partial), url])
        while process.poll() is None:
            time.sleep(20)
            current = partial.stat().st_size if partial.exists() else 0
            print(f'PROGRESS {destination.name} {current}/{size} ({current/size:.1%})', flush=True)
        if process.returncode == 0 and partial.stat().st_size == size:
            break
        time.sleep(3)
    else:
        raise RuntimeError('Download incomplete; resumable partial kept: ' + str(partial))
    if checksum(partial) != expected:
        raise RuntimeError('SHA256 mismatch: ' + str(partial))
    # Validate the safetensors header before promoting the file.
    with partial.open('rb') as f:
        header_size = int.from_bytes(f.read(8), 'little')
        if not 0 < header_size < 100_000_000:
            raise RuntimeError('Invalid safetensors header')
        header = json.loads(f.read(header_size))
        if not any('data_offsets' in value for key, value in header.items() if key != '__metadata__'):
            raise RuntimeError('No tensors in model')
    partial.rename(destination)
    print('VERIFIED ' + destination.name + ' sha256=' + expected, flush=True)


if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, FILES))
    manifest = {'repository': REPO, 'revision': REVISION, 'files': [
        {'source': source, 'path': str(ROOT / category / Path(source).name), 'size': size, 'sha256': sha}
        for source, category, size, sha in FILES]}
    (ROOT.parent / 'QWEN21-MODELS.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('ALL MODELS VERIFIED', flush=True)
