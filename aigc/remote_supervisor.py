"""Sent over SSH stdin; never installed in the shared ComfyUI checkout."""

import json
import os
import pathlib
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


def event(kind, **values):
    print("AIGC_EVENT " + json.dumps({"type": kind, **values}), flush=True)


def terminate(signum, frame):
    raise SystemExit(0)


def main():
    config = json.loads(sys.stdin.readline())
    os.umask(0o077)
    root = pathlib.Path(tempfile.mkdtemp(prefix="aigc-", dir="/dev/shm")).resolve()
    process = None
    try:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, terminate)
        for name in ("input", "output", "user", "scratch", "scratch/temp", "cache"):
            (root / name).mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": str(root / "scratch"),
            "TMP": str(root / "scratch"),
            "TEMP": str(root / "scratch"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "TORCHINDUCTOR_CACHE_DIR": str(root / "cache/inductor"),
            "TRITON_CACHE_DIR": str(root / "cache/triton"),
            "CUDA_CACHE_PATH": str(root / "cache/cuda"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DO_NOT_TRACK": "1",
        })
        process = subprocess.Popen(
            [config["python"], "-B", "-u", "-c", config.pop("service_source")],
            stdin=subprocess.PIPE, cwd=root, env=env, start_new_session=True,
        )
        config.update(root=str(root), port=port)
        process.stdin.write((json.dumps(config) + "\n").encode())
        process.stdin.close()
        event("session", root=str(root), port=port, pid=process.pid)
        last_heartbeat = time.monotonic()
        while process.poll() is None:
            readable, _, _ = select.select([sys.stdin], [], [], 2)
            if readable:
                line = sys.stdin.readline()
                if not line or line.strip() == "stop":
                    break
                if line.strip() == "ping":
                    last_heartbeat = time.monotonic()
            if time.monotonic() - last_heartbeat > config.get("lease_seconds", 120):
                event("lease_expired")
                break
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        if root.parent != pathlib.Path("/dev/shm") or not root.name.startswith("aigc-"):
            raise RuntimeError("Refusing to remove an unexpected session path")
        shutil.rmtree(root)
        event("cleaned", root=str(root), exists=root.exists())


if __name__ == "__main__":
    main()
