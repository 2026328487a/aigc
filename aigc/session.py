import asyncio
import base64
from collections import deque
from contextlib import suppress
import json
import pathlib
import re
import secrets
import socket
import time

import httpx


ROOT = pathlib.Path(__file__).resolve().parents[1]
SSH_OPTIONS = ["-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "StrictHostKeyChecking=yes", "-o", "ServerAliveInterval=10",
               "-o", "ServerAliveCountMax=3"]
PROFILES = {
    "h3": {"python": "/home/jnu/venvs/comfyui-ltx23/bin/python", "comfy_dir": "/home/jnu/src/ComfyUI"},
    "qwen21": {"python": "/home/jnu/venvs/comfyui-qwen21/bin/python", "comfy_dir": "/home/jnu/src/ComfyUI-qwen21"},
    "music3": {"python": "/home/jnu/venvs/comfyui-music3/bin/python", "comfy_dir": "/home/jnu/src/ComfyUI-music3"},
    "voxcpm2": {"python": "/home/jnu/venvs/voxcpm2-aigc/bin/python", "model_dir": "/home/jnu/models/aigc/VoxCPM2"},
}


class Session:
    def __init__(self):
        self.state = "disconnected"
        self.engine = "h3"
        self.error = ""
        self.logs = deque(maxlen=40)
        self.remote = None
        self.tunnel = None
        self.client = None
        self.connect_task = None
        self.readers = []
        self.heartbeat_task = None
        self.info = {}
        self.cleaned = None
        self.auth_verified = False
        self.last_used = time.monotonic()
        self.token = ""
        self.port = None
        self.stop_lock = asyncio.Lock()

    def public(self):
        return {"state": self.state, "error": self.error, "host": "dgx", "engine": self.engine,
                "authenticated": self.auth_verified, "cleaned": self.cleaned,
                "logs": list(self.logs)[-8:], "idle_minutes": 10}

    async def _read_output(self, process, label, log_path):
        with log_path.open("a", encoding="utf-8") as output:
            while line := await process.stdout.readline():
                message = line.decode("utf-8", errors="replace").strip()
                if message.startswith("AIGC_EVENT "):
                    event = json.loads(message[len("AIGC_EVENT "):])
                    if event["type"] == "session":
                        self.info = event
                    elif event["type"] == "cleaned":
                        self.cleaned = not event["exists"]
                elif message:
                    self.logs.append(re.sub(r"\x1b\[[0-9;]*m", "", message)[-1000:])
                output.write(f"[{label}] {message}\n")
                output.flush()

    async def _heartbeat(self):
        while self.remote and self.remote.returncode is None:
            try:
                self.remote.stdin.write(b"ping\n")
                await self.remote.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                break
            await asyncio.sleep(10)

    async def connect(self, engine="h3"):
        if self.state in ("starting", "ready", "stopping"):
            return
        self.state, self.error = "starting", ""
        self.engine = engine
        self.cleaned, self.auth_verified = None, False
        self.logs.clear()
        self.info = {}
        self.token = secrets.token_urlsafe(32)
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"dgx-{time.strftime('%Y%m%d-%H%M%S')}.log"
        source_dir = pathlib.Path(__file__).parent
        config = {
            **PROFILES[engine],
            "token": self.token, "lease_seconds": 120, "engine": engine,
            "service_source": (source_dir / ("remote_tts_service.py" if engine == "voxcpm2" else "remote_service.py")).read_text(encoding="utf-8"),
        }
        if engine == "voxcpm2":
            config["worker_source"] = (source_dir / "remote_tts_worker.py").read_text(encoding="utf-8")
        bootstrap = base64.b64encode((source_dir / "remote_supervisor.py").read_bytes())
        try:
            self.remote = await asyncio.create_subprocess_exec(
                "ssh", *SSH_OPTIONS, "dgx",
                "python3 -u -c 'import base64;exec(compile(base64.b64decode(input()), \"<aigc-session>\", \"exec\"))'",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, limit=1024 * 1024,
            )
            self.readers.append(asyncio.create_task(self._read_output(self.remote, "remote", log_path)))
            self.remote.stdin.write(bootstrap + b"\n" + json.dumps(config).encode() + b"\n")
            await self.remote.stdin.drain()
            # Start heartbeats after the supervisor has consumed its bootstrap/config.
            for _ in range(100):
                if self.info:
                    break
                if self.remote.returncode is not None:
                    raise RuntimeError("SSH 启动失败：" + "\n".join(list(self.logs)[-4:]))
                await asyncio.sleep(0.2)
            if not self.info:
                raise RuntimeError("等待 DGX 会话超时")
            self.heartbeat_task = asyncio.create_task(self._heartbeat())
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self.port = sock.getsockname()[1]
            self.tunnel = await asyncio.create_subprocess_exec(
                "ssh", *SSH_OPTIONS, "-N", "-o", "ExitOnForwardFailure=yes",
                "-L", f"127.0.0.1:{self.port}:127.0.0.1:{self.info['port']}", "dgx",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            self.readers.append(asyncio.create_task(self._read_output(self.tunnel, "tunnel", log_path)))
            self.client = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{self.port}", trust_env=False,
                headers={"Authorization": "Bearer " + self.token},
                timeout=httpx.Timeout(60, connect=5),
            )
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                if self.remote.returncode is not None or self.tunnel.returncode is not None:
                    raise RuntimeError("远端服务已退出：" + "\n".join(list(self.logs)[-5:]))
                try:
                    response = await self.client.get("/system_stats", timeout=5)
                    response.raise_for_status()
                    break
                except httpx.TransportError:
                    await asyncio.sleep(2)
            else:
                raise RuntimeError("模型服务启动超过 4 分钟，请查看本地 logs 目录")
            async with httpx.AsyncClient(trust_env=False, timeout=10) as guest:
                for endpoint in ("/history", "/ws", "/view", "/aigc/session"):
                    response = await guest.get(f"http://127.0.0.1:{self.port}{endpoint}")
                    if response.status_code != 401:
                        raise RuntimeError("远端接口认证检查失败，已停止会话")
            self.auth_verified = True
            self.object_info = await self.request("GET", "/object_info")
            required = {"qwen21": ("TextEncodeQwenImage21", "QwenImage21Cache", "SaveImage"),
                        "h3": ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo", "SaveVideo"),
                        "music3": ("MiniMaxMusic3TextEncode", "EmptyMiniMaxMusic3LatentAudio", "AIGCSaveAudioWAV"),
                        "voxcpm2": ("VoxCPM2",)}[engine]
            missing = [node for node in required if node not in self.object_info]
            if missing:
                raise RuntimeError("ComfyUI 缺少节点：" + ", ".join(missing))
            if engine == "qwen21":
                for loader, field, filename in (
                    ("UNETLoader", "unet_name", "qwen-image-2.1-UC-int8_convrot.safetensors"),
                    ("CLIPLoader", "clip_name", "qwen3vl_8b_bf16.safetensors"),
                    ("VAELoader", "vae_name", "qwen_image_2.1_vae_bf16.safetensors"),
                ):
                    choices = self.object_info[loader]["input"]["required"][field][0]
                    if filename not in choices:
                        raise RuntimeError("Qwen 模型文件尚未就绪：" + filename)
            if engine == "music3":
                for loader, field, filename in (
                    ("UNETLoader", "unet_name", "minimax_music3_dit_fp16.safetensors"),
                    ("CLIPLoader", "clip_name", "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"),
                    ("VAELoader", "vae_name", "minimax_music3_dav.safetensors"),
                ):
                    if filename not in self.object_info[loader]["input"]["required"][field][0]:
                        raise RuntimeError("Music 3 模型文件尚未就绪：" + filename)
            self.last_used = time.monotonic()
            self.state = "ready"
        except asyncio.CancelledError:
            await self._close_resources()
            self.state = "disconnected"
            raise
        except Exception as error:
            self.error = str(error)
            await self._close_resources()
            self.state = "error"

    async def request(self, method, path, **kwargs):
        if self.client is None:
            raise RuntimeError("请先连接 DGX")
        response = await self.client.request(method, path, **kwargs)
        if response.is_error:
            raise RuntimeError(f"模型服务 {response.status_code}: {response.text[:1800]}")
        return response.json() if response.content else {}

    async def _close_resources(self):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat_task
            self.heartbeat_task = None
        if self.remote and self.remote.returncode is None:
            with suppress(BrokenPipeError, ConnectionResetError):
                self.remote.stdin.write(b"stop\n")
                await self.remote.stdin.drain()
            try:
                await asyncio.wait_for(self.remote.wait(), 35)
            except asyncio.TimeoutError:
                self.remote.terminate()
                await self.remote.wait()
        if self.tunnel and self.tunnel.returncode is None:
            self.tunnel.terminate()
            await self.tunnel.wait()
        if self.client:
            await self.client.aclose()
        for task in self.readers:
            with suppress(asyncio.CancelledError):
                await task
        self.readers.clear()
        self.remote = self.tunnel = self.client = None
        self.token = ""

    async def stop(self):
        async with self.stop_lock:
            if self.connect_task and not self.connect_task.done():
                self.connect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.connect_task
            self.state = "stopping"
            await self._close_resources()
            self.auth_verified = False
            self.state = "disconnected"
