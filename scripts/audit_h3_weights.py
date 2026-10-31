"""Read-only duplicate audit; never deletes or moves weights."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path, default=Path('test-results/h3-extended/weight-audit.json'))
    args = parser.parse_args()
    previous = json.loads(args.out.read_text(encoding='utf-8')) if args.out.exists() else {}
    cached = {row['path']: row for row in previous.get('files', [])}
    groups = defaultdict(list)
    for path in args.root.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.safetensors', '.pt'}:
            stat = path.stat()
            groups[stat.st_size].append({'path': str(path), 'size_bytes': stat.st_size,
                                        'mtime_ns': stat.st_mtime_ns})
    files = []
    duplicates = []
    for size, candidates in sorted(groups.items()):
        hashes = defaultdict(list)
        for row in candidates:
            old = cached.get(row['path'], {})
            if old.get('size_bytes') == size and old.get('mtime_ns') == row['mtime_ns']:
                row['sha256'] = old.get('sha256')
            if len(candidates) > 1 and not row.get('sha256'):
                h = hashlib.sha256()
                with open(row['path'], 'rb') as source:
                    for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
                        h.update(block)
                row['sha256'] = h.hexdigest()
                print('HASHED', Path(row['path']).name, round(size / 2**30, 3), 'GiB', flush=True)
            if row.get('sha256'):
                hashes[row['sha256']].append(row['path'])
            files.append(row)
        duplicates.extend({'sha256': sha, 'size_bytes': size, 'copies': paths,
                           'duplicate_bytes': size * (len(paths) - 1)}
                          for sha, paths in hashes.items() if len(paths) > 1)
        report = {'root': str(args.root), 'method': 'All unpacked .safetensors/.pt files grouped by size; equal-size candidates compared with full SHA256.',
                  'excludes': ['ZIP contents', 'Other file extensions'], 'files': files,
                  'duplicates': duplicates, 'deletion_performed': False}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('VERIFIED_DUPLICATES', len(duplicates), round(sum(x['duplicate_bytes'] for x in duplicates) / 2**30, 3), 'GiB', flush=True)


if __name__ == '__main__':
    main()
