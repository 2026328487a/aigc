"""ComfyUI adapter, executed from memory in a session-owned process."""

import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import sys
import wave


def main():
    config = json.loads(sys.stdin.readline())
    root = pathlib.Path(config["root"]).resolve()
    source = pathlib.Path(config["comfy_dir"]).resolve()
    sys.path.insert(0, str(source))
    sys.argv = [str(source / "main.py"),
        "--listen", "127.0.0.1", "--port", str(config["port"]),
        "--input-directory", str(root / "input"),
        "--output-directory", str(root / "output"),
        "--user-directory", str(root / "user"),
        "--temp-directory", str(root / "scratch"),
        "--database-url", "sqlite:///:memory:",
        "--disable-metadata", "--disable-api-nodes", "--disable-all-custom-nodes",
        "--disable-auto-launch", "--cache-none", "--preview-method", "none",
    ]
    import comfy.options
    comfy.options.enable_args_parsing()
    import cuda_malloc  # Apply the normal entry point's allocator setup before torch.
    import main as comfy_main
    from aiohttp import web

    comfy_main.cuda_malloc = cuda_malloc  # main.py imports this only in its CLI entry point.
    loop, server, start = comfy_main.start_comfyui()
    if config.get("engine") == "music3":
        import nodes

        class SaveAudioWAV:
            @classmethod
            def INPUT_TYPES(cls):
                return {"required": {"audio": ("AUDIO",), "filename_prefix": ("STRING",)}}

            RETURN_TYPES = ()
            FUNCTION = "save"
            OUTPUT_NODE = True
            CATEGORY = "audio"

            def save(self, audio, filename_prefix):
                if not re.fullmatch(r"[0-9a-f]{32}/audio", filename_prefix):
                    raise ValueError("Invalid audio output prefix")
                directory = root / "output" / filename_prefix.split('/')[0]
                directory.mkdir(parents=True, exist_ok=True)
                waveform = audio['waveform'][0].detach().float().cpu()
                if not waveform.isfinite().all():
                    raise ValueError("Model produced invalid audio samples")
                if not 1 <= waveform.shape[0] <= 2:
                    raise ValueError("Expected mono or stereo audio")
                pcm = (waveform.clamp(-1, 1).transpose(0, 1).numpy() * 32767).astype('<i2').tobytes()
                with wave.open(str(directory / 'audio.wav'), 'wb') as output:
                    output.setnchannels(waveform.shape[0])
                    output.setsampwidth(2)
                    output.setframerate(int(audio['sample_rate']))
                    output.writeframes(pcm)
                return {"ui": {"audio": [{"filename": "audio.wav", "subfolder": directory.name, "type": "output"}]}}

        nodes.NODE_CLASS_MAPPINGS['AIGCSaveAudioWAV'] = SaveAudioWAV
    expected_auth = "Bearer " + config["token"]

    @web.middleware
    async def authenticate(request, handler):
        if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), expected_auth.encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    server.app.middlewares.insert(0, authenticate)

    def job_path(area, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise web.HTTPBadRequest(text="Invalid job ID")
        base = root / area
        path = base / job_id
        if path.is_symlink() or path.resolve().parent != base:
            raise web.HTTPBadRequest(text="Invalid job path")
        return path

    async def cleanup(request):
        body = await request.json()
        job_id = body.get("job_id", "")
        paths = [job_path(area, job_id) for area in ("input", "output")]
        running, queued = server.prompt_queue.get_current_queue()
        if running or queued:
            raise web.HTTPConflict(text="Wait for this session's queue to finish")
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
        prompt_id = body.get("prompt_id")
        if prompt_id:
            server.prompt_queue.delete_history_item(prompt_id)
        return web.json_response({"cleaned": all(not p.exists() for p in paths)})

    async def artifact(request):
        """Return an integrity manifest without reading anyone else's output files."""
        body = await request.json()
        directory = job_path("output", body.get("job_id", ""))
        filename = body.get("filename", "")
        if pathlib.Path(filename).name != filename or pathlib.Path(filename).suffix not in (".mp4", ".png", ".wav"):
            raise web.HTTPBadRequest(text="Invalid filename")
        path = directory / filename
        if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
            raise web.HTTPNotFound()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return web.json_response({"size": path.stat().st_size, "sha256": digest.hexdigest()})

    async def session_info(request):
        return web.json_response({
            "root": str(root), "pid": os.getpid(), "authenticated": True,
            "persistent_database": False,
            "files": [str(p.relative_to(root)) for area in ("input", "output", "user", "scratch")
                      for p in (root / area).rglob("*") if p.is_file()],
        })

    server.app.router.add_post("/aigc/cleanup", cleanup)
    server.app.router.add_post("/aigc/artifact", artifact)
    server.app.router.add_get("/aigc/session", session_info)
    try:
        loop.run_until_complete(start())
    finally:
        server.asset_manager.shutdown()


if __name__ == "__main__":
    main()
