"""Read-only DGX inventory; run remotely with scripts/dgx_exec.py.

Print JSON to stdout. No model loading, hashing of large files, process command
lines, writes, downloads, service changes, or deletion. Filesystem totals are
allocated bytes and deduplicate hard links within each listed directory.
"""
import collections
import datetime
import json
import os
from pathlib import Path
import platform
import struct
import subprocess


BASE = Path('/home/jnu')
WEIGHT_SUFFIXES = {'.safetensors', '.gguf', '.pth', '.pt', '.ckpt', '.bin', '.onnx', '.engine', '.npz'}


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    return {'returncode': result.returncode, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}


def header_summary(path):
    try:
        with path.open('rb') as stream:
            length = struct.unpack('<Q', stream.read(8))[0]
            if length > 32 * 1024 * 1024:
                return {'error': 'unexpected header length'}
            tensors = json.loads(stream.read(length))
        counts = collections.Counter(v['dtype'] for k, v in tensors.items() if k != '__metadata__')
        return {'tensor_count_by_dtype': dict(counts)}
    except (OSError, ValueError, KeyError, struct.error) as exc:
        return {'error': type(exc).__name__}


def main():
    roots = [BASE / 'models', BASE / 'src/ComfyUI/models',
             BASE / 'src/ComfyUI-qwen21/models', BASE / 'src/ComfyUI-music3/models',
             BASE / '.cache/huggingface/hub', BASE / '.cache/openpi',
             BASE / '.cache/torch/hub/checkpoints', BASE / '.ollama/models']
    records, errors = [], []
    for root in roots:
        if not root.exists():
            continue
        for directory, dirs, names in os.walk(root, followlinks=False, onerror=lambda e: errors.append(str(e))):
            dirs.sort()
            for name in sorted(names):
                path = Path(directory) / name
                try:
                    stat = path.stat()
                    if path.suffix.lower() not in WEIGHT_SUFFIXES and stat.st_size < 64 * 1024 * 1024:
                        continue
                    if not path.is_file():
                        continue
                    record = {'path': str(path), 'resolved_path': str(path.resolve()),
                              'size_bytes': stat.st_size, 'allocated_bytes': stat.st_blocks * 512,
                              'device': stat.st_dev, 'inode': stat.st_ino, 'hardlink_count': stat.st_nlink,
                              'is_symlink': path.is_symlink(),
                              'modified_at_utc': datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).isoformat()}
                    if path.suffix.lower() == '.safetensors':
                        record['safetensors'] = header_summary(path)
                    records.append(record)
                except OSError as exc:
                    errors.append(f'{path}: {exc}')
    directories = list(roots)
    directories.extend(p for p in (BASE / 'venvs').iterdir() if p.is_dir())
    directories.extend(BASE / '.cache' / name for name in ('pip', 'uv', 'vllm', 'flashinfer'))
    usage_records = []
    for directory in directories:
        if not directory.exists():
            continue
        # Separate invocations: du otherwise deduplicates across arguments,
        # attributing shared uv/venv hard links to whichever root comes first.
        usage = command(['du', '-sB1', str(directory)])
        if usage['returncode']:
            errors.append(f'du {directory}: {usage["stderr"]}')
        for line in usage['stdout'].splitlines():
            size, path = line.split('\t', 1)
            usage_records.append({'path': path, 'allocated_bytes': int(size)})
    processes = []
    gpu = command(['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory', '--format=csv,noheader,nounits'])
    for line in gpu['stdout'].splitlines():
        try:
            pid, name, memory = (s.strip() for s in line.split(',', 2))
            root = Path('/proc') / pid
            status = dict(line.split(':', 1) for line in (root / 'status').read_text().splitlines() if ':' in line)
            record = {'pid': int(pid), 'process_name': name, 'nvidia_reported_memory_mib': memory,
                      'uid': status.get('Uid', '').strip(), 'ppid': status.get('PPid', '').strip(),
                      'rss': status.get('VmRSS', '').strip()}
            for label in ('cwd', 'exe'):
                try:
                    record[label] = os.readlink(root / label)
                except OSError:
                    record[label] = 'unavailable'
            processes.append(record)
        except (OSError, ValueError) as exc:
            errors.append(f'process: {exc}')
    environments = []
    version_code = "import importlib.metadata as m,json,sys;names=['torch','torchaudio','transformers','comfyui-frontend-package','comfy-kitchen','vllm'];v={};exec('for n in names:\\n try: v[n]=m.version(n)\\n except m.PackageNotFoundError: pass');print(json.dumps({'python':sys.version.split()[0],'packages':v}))"
    for folder in ('comfyui-ltx23', 'comfyui-qwen21', 'comfyui-music3', 'voxcpm2-aigc', 'vllm'):
        interpreter = BASE / 'venvs' / folder / 'bin/python'
        if interpreter.exists():
            environments.append({'path': str(interpreter.parent.parent), 'versions': command([str(interpreter), '-B', '-c', version_code])})
    sources = []
    for folder in ('ComfyUI', 'ComfyUI-qwen21', 'ComfyUI-music3', 'llama.cpp'):
        path = BASE / 'src' / folder
        sources.append({'path': str(path), 'commit': command(['git', '-C', str(path), 'rev-parse', 'HEAD']),
                        'custom_node_directories': sorted(p.name for p in (path / 'custom_nodes').iterdir() if p.is_dir()) if (path / 'custom_nodes').exists() else []})
    unique = {(r['device'], r['inode']): r for r in records}
    result = {'schema_version': 1, 'collected_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'hostname': platform.node(), 'architecture': platform.machine(),
              'scope': list(map(str, roots)), 'limitations': ['Not an all-users or all-mounts inventory.',
                  'Model status cannot be inferred from modification/access times.',
                  'Distinct inodes with identical contents are not detected; no full-file hashing.',
                  'nvidia-smi memory is not a separate VRAM pool on GB10.'],
              'hardware': {'nvidia_smi': command(['nvidia-smi']), 'memory': Path('/proc/meminfo').read_text(),
                           'disk': command(['df', '-B1', '/', '/home', '/dev/shm']),
                           'os_release': Path('/etc/os-release').read_text()},
              'compute_processes': processes, 'directories': usage_records,
              'model_files': records, 'unique_model_file_count': len(unique),
              'unique_model_file_size_bytes': sum(r['size_bytes'] for r in unique.values()),
              'unique_model_file_allocated_bytes': sum(r['allocated_bytes'] for r in unique.values()),
              'environments': environments, 'sources': sources, 'errors': errors}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
