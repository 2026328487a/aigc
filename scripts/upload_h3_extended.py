"""Upload only manifest-selected addons with SHA256 verification over SSH."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

REMOTE='/home/jnu/models/aigc/h3-extended'
SSH=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','dgx']

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()

def upload(path,target,digest):
    source='''import hashlib,json,sys
from pathlib import Path
t=Path(TARGET)
base=Path('/home/jnu/models/aigc/h3-extended')
if not t.resolve().is_relative_to(base):raise RuntimeError('Unexpected upload path')
t.parent.mkdir(parents=True,exist_ok=True)
p=t.with_name(t.name+'.part')
h=hashlib.sha256()
with p.open('wb') as f:
 while True:
  b=sys.stdin.buffer.read(8*1024*1024)
  if not b:break
  f.write(b);h.update(b)
if h.hexdigest()!=DIGEST:raise RuntimeError('Upload hash mismatch')
p.replace(t)
print(json.dumps({'path':str(t),'sha256':h.hexdigest(),'bytes':t.stat().st_size}))
'''.replace('TARGET',repr(target)).replace('DIGEST',repr(digest))
    bootstrap="import base64;exec(base64.b64decode("+repr(base64.b64encode(source.encode()).decode())+"))"
    with path.open('rb') as f:
        subprocess.run([*SSH,'python3 -u -c '+shlex.quote(bootstrap)],stdin=f,check=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=Path('test-results/h3-extended'));p.add_argument('--staging-only',action='store_true');a=p.parse_args()
    m=json.loads((a.out/'manifest.json').read_text(encoding='utf-8'))
    for name in ['plugins.tar.gz','manifest.json']:
        path=a.out/name;upload(path,REMOTE+'/staging/'+name,sha(path))
    if a.staging_only:return
    check="import pathlib,json;root=pathlib.Path('/home/jnu/models/aigc/h3-extended/models');print(json.dumps({str(p.relative_to(root)):p.stat().st_size for p in root.rglob('*') if p.is_file()}))"
    current=json.loads(subprocess.check_output([*SSH,'python3 -c '+shlex.quote(check)],text=True))
    for item in m['weights']:
        rel=item['relative_path']
        if current.get(rel)==item['size_bytes']:
            # Verify existing payload before accepting a previous upload.
            probe="import hashlib;h=hashlib.sha256();f=open("+repr(REMOTE+'/models/'+rel)+",'rb');exec('for b in iter(lambda:f.read(8388608),b\"\"):\\n h.update(b)');print(h.hexdigest())"
            if subprocess.check_output([*SSH,'python3 -c '+shlex.quote(probe)],text=True).strip()==item['sha256']:
                print('VERIFIED_EXISTING',rel,flush=True);continue
        print('UPLOADING',rel,flush=True)
        upload(Path(item['source']),REMOTE+'/models/'+rel,item['sha256'])
    print('H3_ADDON_WEIGHTS_READY',flush=True)

if __name__=='__main__':main()
