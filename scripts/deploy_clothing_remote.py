"""Register existing SDPose checkpoints and install the small clothing export helper."""
import base64
import json
from pathlib import Path
import sys

path = Path('/home/jnu/models/aigc/h3-extended/extra_model_paths.yaml')
data = json.loads(path.read_text())
backup = path.with_name('extra_model_paths.before-clothing-20261115.yaml')
if not backup.exists():
    backup.write_bytes(path.read_bytes())
data['h3_baseline']['checkpoints'] = 'checkpoints'
path.write_text(json.dumps(data, indent=2) + '\n')
folder = Path('/home/jnu/src/ComfyUI-h3-extended/custom_nodes/AIGC-Clothing')
folder.mkdir(exist_ok=True)
payload=json.loads(base64.b64decode(sys.argv[1]).decode('utf-8'))
for name,source in payload.items():
    if Path(name).name!=name or not name.endswith('.py'):
        raise ValueError('Expected a simple Python node package filename')
    target=folder/name
    if target.exists() and target.read_text()!=source:
        backup=folder/(name+'.before-clothing-workflow-20261116')
        if not backup.exists(): backup.write_bytes(target.read_bytes())
    target.write_text(source,encoding='utf-8')
print('CLOTHING_HELPER_READY', str(folder))
