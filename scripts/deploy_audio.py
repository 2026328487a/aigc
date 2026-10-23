"""Run via dgx_exec.py, passing docs/audio/models.json as a JSON argument.

Creates isolated audio environments and verifies pinned model downloads.
Never edits the existing Qwen/H3 environments or model directories.
"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

MANIFEST = json.loads(sys.argv[1])
BASE = Path('/home/jnu')
SOURCE = BASE / 'src/ComfyUI-qwen21'
MUSIC = BASE / 'src/ComfyUI-music3'
VOX = BASE / 'src/VoxCPM-aigc'
MODEL = BASE / 'models/aigc/VoxCPM2'


def run(args, **kwargs):
    print('RUN', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def copy_environment(name):
    target = BASE / 'venvs' / name
    if not target.exists():
        shutil.copytree(BASE / 'venvs/comfyui-qwen21', target, symlinks=True)
    print('ENVIRONMENT', target, flush=True)
    return target / 'bin/python'


def checksum(path, git=False):
    h = hashlib.sha1() if git else hashlib.sha256()
    if git:
        h.update(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def download(task):
    model, file, root = task
    relative = file['rfilename']
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = file.get('lfs', {}).get('sha256') or file['blobId']
    git = not file.get('lfs')
    if target.exists():
        if checksum(target, git) == expected:
            print('VERIFIED existing', target.name, flush=True)
            return
        raise RuntimeError('Existing target has different content: ' + str(target))
    partial = target.with_name(target.name + '.part')
    url = f"https://hf-mirror.com/{model['repository']}/resolve/{model['revision']}/{relative}"
    for attempt in range(6):
        print('DOWNLOAD', relative, 'attempt', attempt + 1, flush=True)
        p = subprocess.Popen(['curl', '-4', '--fail', '--location', '--silent', '--show-error',
                              '--connect-timeout', '20', '--speed-limit', '1024', '--speed-time', '60',
                              '--continue-at', '-', '--output', str(partial), url])
        while p.poll() is None:
            time.sleep(15)
            size = partial.stat().st_size if partial.exists() else 0
            print('PROGRESS', target.name, size, '/', file['size'], flush=True)
        if p.returncode == 0 and partial.stat().st_size == file['size']:
            break
    else:
        raise RuntimeError('Incomplete download: ' + str(partial))
    if checksum(partial, git) != expected:
        raise RuntimeError('Checksum mismatch: ' + str(partial))
    if target.suffix == '.safetensors':
        with partial.open('rb') as stream:
            size = int.from_bytes(stream.read(8), 'little')
            if not 0 < size < 100_000_000:
                raise RuntimeError('Invalid safetensors header')
            json.loads(stream.read(size))
    partial.rename(target)
    print('VERIFIED', target.name, expected, flush=True)


def install():
    copy_environment('comfyui-music3')
    python = copy_environment('voxcpm2-aigc')
    revision = MANIFEST['voxcpm_source']['revision']
    if not VOX.exists():
        archive = BASE / 'src' / ('voxcpm-' + revision + '.tar.gz')
        run(['curl', '-4', '-fL', '--retry', '3', '-o', archive,
             'https://codeload.github.com/OpenBMB/VoxCPM/tar.gz/' + revision])
        unpack = BASE / 'src' / ('voxcpm-unpack-' + revision)
        unpack.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(unpack, filter='data')
        roots = list(unpack.iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise RuntimeError('Unexpected source archive')
        roots[0].rename(VOX)
    # Keep the tested Blackwell PyTorch build; install only in the cloned TTS environment.
    build_env = dict(os.environ, SETUPTOOLS_SCM_PRETEND_VERSION_FOR_VOXCPM='0.0.0+g' + revision[:12])
    run([python, '-m', 'pip', 'install', '--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple',
         '--no-deps', '-e', VOX], env=build_env)
    constraints = BASE / 'venvs/voxcpm2-aigc/aigc-constraints.txt'
    constraints.write_text('torch==2.11.0+cu130\ntorchaudio==2.11.0\n')
    run([python, '-m', 'pip', 'install', '--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple',
         '-c', constraints,
         'soundfile', 'librosa', 'einops', 'inflect', 'addict', 'wetext', 'simplejson',
         'sortedcontainers', 'argbind', 'modelscope', 'datasets>=3,<4', 'funasr',
         'gradio>=6,<7', 'spaces', 'torchcodec==0.11.0'])
    print('INSTALL COMPLETE', flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    def ignore_source(directory, names):
        skipped = {'.git', '__pycache__', '.venv'}
        if Path(directory) == SOURCE:
            skipped.update({'models', 'input', 'output', 'user'})
        return set(names) & skipped
    shutil.copytree(SOURCE, MUSIC, symlinks=True, dirs_exist_ok=True, ignore=ignore_source)
    if not (MUSIC / 'main.py').is_file():
        raise RuntimeError('Music source copy is incomplete')
    tasks = []
    for key, root in [('music3', MUSIC / 'models'), ('voxcpm2', MODEL), ('music_license', MUSIC / 'licenses/minimax-music3')]:
        tasks.extend((MANIFEST[key], file, root) for file in MANIFEST[key]['files'])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        install_future = pool.submit(install)
        futures = [pool.submit(download, task) for task in tasks]
        for future in concurrent.futures.as_completed([install_future, *futures]):
            future.result()
    (MODEL / 'AIGC-MANIFEST.json').write_text(json.dumps(MANIFEST, indent=2))
    print('AUDIO DEPLOYMENT FILES VERIFIED', flush=True)
