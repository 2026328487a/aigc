"""Verify unauthenticated access is denied even from the server's own loopback."""
import base64
import json
import pathlib
import re
import subprocess

root = pathlib.Path(__file__).resolve().parents[1]
log = max((root / "logs").glob("dgx-*.log"), key=lambda p: p.stat().st_mtime)
ports = re.findall(r"To see the GUI go to: http://127\.0\.0\.1:(\d+)", log.read_text(encoding="utf-8"))
port = int(ports[-1])
source = '''
import json, sys, urllib.request, urllib.error
port = int(sys.stdin.readline())
results=[]
for method,path in [("GET","/history"),("GET","/view"),("GET","/ws"),("POST","/prompt"),("POST","/upload/image"),("POST","/aigc/cleanup")]:
    request=urllib.request.Request(f"http://127.0.0.1:{port}{path}",method=method,data=b"{}" if method=="POST" else None)
    try:
        response=urllib.request.urlopen(request,timeout=5)
        code=response.status
    except urllib.error.HTTPError as error:
        code=error.code
    assert code==401,(method,path,code)
    results.append({"method":method,"path":path,"status":code})
print(json.dumps(results))
'''
payload = base64.b64encode(source.encode()) + b"\n" + str(port).encode() + b"\n"
result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "dgx", "python3 -c 'import base64;exec(base64.b64decode(input()))'"], input=payload, capture_output=True, timeout=20)
print(result.stdout.decode())
if result.returncode:
    raise RuntimeError(result.stderr.decode())
(root / "test-results/remote-auth.json").write_bytes(result.stdout)
