"""Install the clothing helper without replacing models or core dependencies."""
import base64
import json
import shlex
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'scripts'))
from package_clothing_nodes import sources
payload={name:path.read_text(encoding='utf-8') for name,path in sources().items()}
source = base64.b64encode(json.dumps(payload).encode('utf-8')).decode('ascii')
remote={'source':(root/'scripts/deploy_clothing_remote.py').read_text(encoding='utf-8'),'args':[source]}
bootstrap="import base64,json,sys;p=json.loads(base64.b64decode(input()));sys.argv=['<clothing-deploy>']+p['args'];exec(compile(p['source'],'<clothing-deploy>','exec'))"
result=subprocess.run(['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
                       '-o','ConnectTimeout=10','dgx','python3 -u -c '+shlex.quote(bootstrap)],
                      input=base64.b64encode(json.dumps(remote).encode('utf-8'))+b'\n',cwd=root)
raise SystemExit(result.returncode)
