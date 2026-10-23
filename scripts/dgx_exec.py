"""Run a local Python maintenance script over the configured DGX SSH connection."""
import base64
import json
from pathlib import Path
import subprocess
import shlex
import sys

if __name__ == "__main__":
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    payload = {"source": source, "args": sys.argv[2:]}
    bootstrap = "import base64,json,sys;p=json.loads(base64.b64decode(input()));sys.argv=['<dgx-script>']+p['args'];exec(compile(p['source'],'<dgx-script>','exec'))"
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                             "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "dgx",
                             "python3 -u -c " + shlex.quote(bootstrap)],
                            input=base64.b64encode(json.dumps(payload).encode()) + b"\n")
    sys.exit(result.returncode)
