"""Run CPU-only, isolated supervisor lifecycle checks over SSH; no GPU/model loading."""
import base64
import json
import pathlib
import subprocess

root = pathlib.Path(__file__).resolve().parents[1]
driver = '''
import json, os, pathlib, signal, subprocess, sys
source = json.loads(sys.stdin.readline())["source"]
results = []
for case in ("eof", "lease", "hangup"):
    worker = "import json,sys,time,pathlib; c=json.loads(sys.stdin.readline()); pathlib.Path(c['root'],'input','probe').write_text('cleanup check'); time.sleep(60)"
    config = {"python": sys.executable, "service_source": worker, "lease_seconds": 3}
    process = subprocess.Popen([sys.executable, "-u", "-c", source], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        process.stdin.write(json.dumps(config) + "\\n")
        process.stdin.flush()
        first = process.stdout.readline()
        assert first.startswith("AIGC_EVENT "), first
        info = json.loads(first[len("AIGC_EVENT "):])
        if case == "eof":
            process.stdin.close()
        elif case == "hangup":
            process.send_signal(signal.SIGHUP)
        process.wait(timeout=15)
        output = process.stdout.read()
        assert not pathlib.Path(info["root"]).exists(), output
        assert not pathlib.Path('/proc', str(info["pid"])).exists(), output
        assert '"type": "cleaned"' in output, output
        if case == "lease":
            assert '"type": "lease_expired"' in output, output
        results.append({"case": case, "directory_removed": True, "child_exited": True})
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
print(json.dumps(results))
'''
payload = base64.b64encode(driver.encode()) + b"\n" + json.dumps({"source": (root / "aigc/remote_supervisor.py").read_text(encoding="utf-8")}).encode() + b"\n"
result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "dgx",
                         "python3 -u -c 'import base64;exec(base64.b64decode(input()))'"], input=payload, capture_output=True, timeout=60)
print(result.stdout.decode())
if result.returncode:
    raise RuntimeError(result.stderr.decode())
(root / "test-results/remote-cleanup.json").write_bytes(result.stdout)
