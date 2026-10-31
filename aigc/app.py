import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
import hashlib
import io
import json
import pathlib
import re
import shutil
import time
from typing import Literal
import uuid
import wave

import av
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect as websocket_connect

from .session import ROOT, Session
from .workflows import Settings, build_workflow
from .reference_audio import MAX_MEDIA_BYTES, extract_reference
from .native_comfy import MEDIA_TYPES, NativeComfy
from .h3_extended import TEMPLATES as H3_TEMPLATES


DATA = ROOT / "data/jobs"
session = Session()
native = NativeComfy(session)
active_task = None
active_job = None
submission_lock = asyncio.Lock()
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def job_dir(job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(404, "任务不存在")
    directory = DATA / job_id
    if directory.is_symlink() or directory.is_junction() or directory.resolve().parent != DATA.resolve():
        raise HTTPException(404, "任务目录无效")
    return directory


def save_job(job):
    directory = job_dir(job["id"])
    temporary = directory / "job.json.tmp"
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(directory / "job.json")


def read_job(job_id):
    path = job_dir(job_id) / "job.json"
    if not path.is_file():
        raise HTTPException(404, "任务不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_video(path):
    with av.open(str(path)) as container:
        video = container.streams.video[0]
        frame = next(container.decode(video))
        if frame.width < 1 or not container.duration:
            raise ValueError("下载的视频无有效画面或时长")
        if any(key.lower() in ("prompt", "workflow") for key in container.metadata):
            raise ValueError("输出仍包含工作流元数据，请检查远端配置")
        return {"width": video.width, "height": video.height,
                "duration": container.duration / av.time_base,
                "audio": bool(container.streams.audio), "codec": video.codec_context.name}


def validate_image(path):
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("输出不是 PNG 图片")
        image.load()
        if any(key.lower() in ("prompt", "workflow") for key in image.info):
            raise ValueError("输出仍包含工作流元数据，请检查远端配置")
        return {"width": image.width, "height": image.height, "mode": image.mode}


def validate_audio(path):
    with wave.open(str(path), 'rb') as audio:
        channels, rate, samples = audio.getnchannels(), audio.getframerate(), audio.getnframes()
        if channels not in (1, 2) or not 8000 <= rate <= 192000 or samples < rate // 10:
            raise ValueError("音频声道、采样率或时长无效")
        width = audio.getsampwidth()
        count = 0
        while data := audio.readframes(65536):
            count += len(data)
        if count != samples * channels * width:
            raise ValueError("音频文件不完整")
    return {"sample_rate": rate, "channels": channels, "duration": round(samples / rate, 3), "format": "wav"}


def normalize_reference(content):
    """Decode with a duration limit and remove metadata; emit mono 24 kHz PCM."""
    output = io.BytesIO()
    samples = 0
    with av.open(io.BytesIO(content)) as source, wave.open(output, 'wb') as target:
        if not source.streams.audio:
            raise ValueError("文件没有音轨")
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(24000)
        resampler = av.AudioResampler(format='s16', layout='mono', rate=24000)
        for frame in source.decode(audio=0):
            for converted in resampler.resample(frame):
                samples += converted.samples
                if samples > 30 * 24000:
                    raise ValueError("参考录音上限 30 秒，请先截取清晰片段")
                target.writeframes(bytes(converted.planes[0])[:converted.samples * 2])
        for converted in resampler.resample(None):
            samples += converted.samples
            target.writeframes(bytes(converted.planes[0])[:converted.samples * 2])
    if not 3 * 24000 <= samples <= 30 * 24000:
        raise ValueError("参考录音需要 3–30 秒，建议清晰的 5–15 秒人声")
    return output.getvalue()


async def track_progress(job, settings, client_id):
    executing_node = None
    try:
        async with websocket_connect(
            f"ws://127.0.0.1:{session.port}/ws?clientId={client_id}",
            additional_headers={"Authorization": "Bearer " + session.token},
            proxy=None, max_size=8 * 1024 * 1024,
        ) as ws:
            job["ws_connected"] = True
            async for raw in ws:
                if not isinstance(raw, str):
                    continue
                message = json.loads(raw)
                event, data = message.get("type"), message.get("data", {})
                if data.get("prompt_id") not in (None, job.get("prompt_id")):
                    continue
                if event == "progress":
                    maximum = data.get("max", settings.steps)
                    value = data.get("value", 0)
                    music_ar = settings.engine == "music3" and executing_node == "7"
                    label = "生成音乐片段" if music_ar else "采样"
                    lower, span = ((15, 45) if music_ar else (60, 25)) if settings.engine == "music3" else (20, 65)
                    job.update(stage=f"{label} {value} / {maximum}", progress=min(85, lower + int(span * value / max(1, maximum))))
                elif event == "executing" and data.get("node"):
                    node = str(data["node"])
                    executing_node = node
                    if node in ("13", "14", "15", "16"):
                        label = {"qwen21": "解码并保存图片", "music3": "解码并保存音频"}.get(settings.engine, "解码并编码视频")
                        job.update(stage=label, progress=90)
                    elif node == "7":
                        job.update(stage="生成音乐结构" if settings.engine == "music3" else "编码提示词与参考图", progress=15)
                    else:
                        job["stage"] = "加载模型／准备推理" if job["progress"] < 20 else job["stage"]
                save_job(job)
    except Exception as error:
        # History polling remains authoritative if the progress connection drops.
        job["progress_connection"] = str(error)[:200]


def output_file(record, suffix=".mp4"):
    for output in record.get("outputs", {}).values():
        for value in output.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("filename", "").endswith(suffix):
                        return item
    raise RuntimeError(f"任务完成，但 ComfyUI 没有返回 {suffix} 输出")


async def run_job(job, settings):
    global active_job
    progress_task = None
    started = time.monotonic()
    directory = job_dir(job["id"])
    try:
        tts = settings.engine == "voxcpm2"
        job.update(state="running", stage="准备音频任务" if settings.engine in ("music3", "voxcpm2") else "上传参考图", progress=3)
        save_job(job)
        images = []
        for name in job["images"]:
            content = (directory / name).read_bytes()
            uploaded = await session.request("POST", "/upload/image",
                files={"image": (name, content, "image/png")},
                data={"subfolder": job["id"], "type": "input", "overwrite": "true"})
            images.append(f"{uploaded['subfolder']}/{uploaded['name']}")
        if tts and job.get('reference_audio'):
            await session.request('POST', '/upload/audio', params={'job_id': job['id']},
                                  content=(directory / job['reference_audio']).read_bytes(),
                                  headers={'Content-Type': 'audio/wav'})
        graph = {"model": "VoxCPM2", "settings": settings.model_dump()} if tts else build_workflow(settings, images, job["id"])
        (directory / "workflow.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        client_id = uuid.uuid4().hex
        if tts:
            response = await session.request('POST', '/tts/submit', json={'job_id': job['id'], 'settings': settings.model_dump()})
        else:
            progress_task = asyncio.create_task(track_progress(job, settings, client_id))
            for _ in range(30):
                if job.get("ws_connected") or progress_task.done():
                    break
                await asyncio.sleep(0.1)
            response = await session.request("POST", "/prompt", json={"prompt": graph, "client_id": client_id})
        job.update(prompt_id=response["prompt_id"], stage="加载模型／准备推理", progress=8)
        save_job(job)
        deadline = time.monotonic() + 7200
        record = None
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            history = await session.request("GET", "/history/" + job["prompt_id"])
            if job["prompt_id"] in history:
                record = history[job["prompt_id"]]
                break
            if tts:
                update = await session.request('GET', '/tts/progress')
                job.update(stage=update['stage'], progress=update['progress'])
            job["elapsed"] = round(time.monotonic() - started, 1)
            save_job(job)
        if record is None:
            raise RuntimeError("任务超过 2 小时，已结束本次会话")
        if record.get("status", {}).get("status_str") != "success":
            errors = [data for kind, data in record.get("status", {}).get("messages", []) if kind == "execution_error"]
            detail = errors[-1].get("exception_message", "推理失败") if errors else "任务已取消或失败"
            raise RuntimeError(detail)
        suffix = {"qwen21": ".png", "h3": ".mp4", "music3": ".wav", "voxcpm2": ".wav"}[settings.engine]
        result = output_file(record, suffix)
        if result.get("subfolder") != job["id"] or result.get("type") != "output":
            raise RuntimeError("输出路径不属于当前任务")
        manifest = await session.request("POST", "/aigc/artifact", json={"job_id": job["id"], "filename": result["filename"]})
        job.update(stage="下载结果到本地", progress=95)
        save_job(job)
        partial = directory / ("result" + suffix + ".part")
        digest = hashlib.sha256()
        async with session.client.stream("GET", "/view", params={key: result[key] for key in ("filename", "subfolder", "type")}) as download:
            download.raise_for_status()
            with partial.open("wb") as output:
                async for chunk in download.aiter_bytes():
                    output.write(chunk)
                    digest.update(chunk)
        if partial.stat().st_size != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise RuntimeError("下载完整性验证失败")
        media_type, validator = {'.png': ('image', validate_image), '.mp4': ('video', validate_video),
                                 '.wav': ('audio', validate_audio)}[suffix]
        job[media_type] = await asyncio.to_thread(validator, partial)
        job["result_file"] = "result" + suffix
        partial.replace(directory / job["result_file"])
        job.update(sha256=manifest["sha256"], bytes=manifest["size"], state="completed", progress=100, stage="已保存到本地")
    except asyncio.CancelledError:
        job.update(state="cancelled", stage="已取消", error="任务由本地取消")
    except Exception as error:
        job.update(state="failed", stage="生成失败", error=str(error)[:3000])
    finally:
        if progress_task:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task
        job["elapsed"] = round(time.monotonic() - started, 1)
        try:
            if job["state"] != "completed" and job.get("prompt_id") and session.client:
                await session.request("POST", f"/api/jobs/{job['prompt_id']}/cancel")
                for _ in range(10):
                    queue = await session.request("GET", "/queue")
                    if not queue.get("queue_running") and not queue.get("queue_pending"):
                        break
                    await asyncio.sleep(1)
            cleanup = await session.request("POST", "/aigc/cleanup", json={"job_id": job["id"], "prompt_id": job.get("prompt_id")})
            job["remote_cleaned"] = cleanup["cleaned"]
        except Exception as error:
            job["cleanup_note"] = str(error)[:500]
            await session.stop()
            job["remote_cleaned"] = session.cleaned is True
        job["cleanup_finished"] = True
        session.last_used = time.monotonic()
        save_job(job)
        active_job = None


async def maintenance():
    while True:
        await asyncio.sleep(2)
        async with submission_lock:
            if session.state == "ready" and active_job is None:
                if session.remote.returncode is not None or session.tunnel.returncode is not None:
                    native.ended()
                    await session.stop()
                    session.error = "连接已断开，可重新连接 DGX"
                else:
                    try:
                        await native.collect()
                        if await native.busy():
                            session.last_used = time.monotonic()
                        elif time.monotonic() - session.last_used > 600:
                            await native.before_stop()
                            await session.stop()
                    except (RuntimeError, httpx.HTTPError, OSError) as error:
                        session.error = str(error)[:500]


async def cancel_active():
    global active_job
    task, job_id = active_task, active_job
    if task is None or task.done():
        return
    if not task.cancelling():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.shield(task)
    if active_job == job_id:
        # A task cancelled before its first instruction never enters run_job's finally.
        job = read_job(job_id)
        job.update(state="cancelled", stage="提交前已取消", remote_cleaned=True, cleanup_finished=True)
        save_job(job)
        active_job = None


@asynccontextmanager
async def lifespan(app):
    DATA.mkdir(parents=True, exist_ok=True)
    native.load()
    for path in DATA.glob("*/job.json"):
        job = json.loads(path.read_text(encoding="utf-8"))
        if job["state"] not in TERMINAL:
            job.update(state="interrupted", stage="本地服务已重启", error="上次任务中断；远端会话会在 SSH 退出或心跳超时后回收")
            save_job(job)
    task = asyncio.create_task(maintenance())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await cancel_active()
    try:
        await native.before_stop()
    except (RuntimeError, httpx.HTTPError, OSError) as error:
        session.logs.append("退出前归档未完成：" + str(error)[:300])
    native.ended()
    await session.stop()


app = FastAPI(title="AIGC 本地工作台", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_only(request: Request, call_next):
    if request.url.path == "/comfy" or request.url.path.startswith("/comfy/"):
        # Native ComfyUI uses its own same-origin HTTP/WebSocket guard.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            async with submission_lock:
                if active_job:
                    return JSONResponse({"error": "原工作台任务正在运行，请等待完成"}, status_code=409)
                return await call_next(request)
        return await call_next(request)
    origin = request.headers.get("origin")
    expected = f"http://{request.headers.get('host')}"
    if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "仅接受本地同源请求"}, status_code=403)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-aigc-client") != "local":
        return JSONResponse({"detail": "缺少本地请求标识"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.get("/")
async def index():
    return FileResponse(ROOT / "static/launcher.html")


@app.get("/workbench")
async def existing_workbench():
    return FileResponse(ROOT / "static/index.html")


@app.get("/library")
async def library():
    return FileResponse(ROOT / "static/library.html")


@app.get("/open-workflow")
async def open_workflow():
    return FileResponse(ROOT / "static/open-workflow.html")


@app.get("/api/status")
async def status():
    return {"session": session.public(), "active_job": active_job, "comfy": native.public()}


@app.get("/api/diagnostics")
async def diagnostics():
    if session.state != "ready":
        return {"session": session.public(), "remote": None}
    return {"session": session.public(), "remote": await session.request("GET", "/aigc/session")}


@app.post("/api/connect", status_code=202)
async def connect(engine: Literal["h3", "qwen21", "music3", "voxcpm2"] = "h3"):
    async with submission_lock:
        if session.state == "stopping":
            raise HTTPException(409, "正在回收会话，请稍后")
        if active_job:
            raise HTTPException(409, "请先等待当前任务完成或取消，再切换模型")
        if session.engine != engine and session.state in ("ready", "starting"):
            if await native.busy():
                raise HTTPException(409, "ComfyUI 正在运行或归档任务，请先等待完成或断开")
            try:
                await native.before_stop()
            except (RuntimeError, httpx.HTTPError, OSError) as error:
                raise HTTPException(409, str(error))
            await session.stop()
        if session.state not in ("ready", "starting") and (not session.connect_task or session.connect_task.done()):
            session.engine = engine
            session.connect_task = asyncio.create_task(session.connect(engine))
        return {"state": session.state, "engine": engine}


@app.post("/api/disconnect")
async def disconnect():
    async with submission_lock:
        await cancel_active()
        try:
            await native.before_stop()
        except (RuntimeError, httpx.HTTPError, OSError) as error:
            raise HTTPException(409, str(error))
        await session.stop()
        return session.public()


@app.get("/api/jobs")
async def jobs():
    return sorted([json.loads(p.read_text(encoding="utf-8")) for p in DATA.glob("*/job.json")], key=lambda j: j["created"], reverse=True)


@app.post("/api/shutdown")
async def shutdown():
    callback = getattr(app.state, "shutdown", None)
    if callback is None:
        raise HTTPException(409, "请在启动终端按 Ctrl+C 关闭本地服务")
    async with submission_lock:
        await cancel_active()
        try:
            await native.before_stop()
        except (RuntimeError, httpx.HTTPError, OSError) as error:
            raise HTTPException(409, str(error))
        await session.stop()
    return JSONResponse({"stopped": True, "remote_cleaned": session.cleaned}, background=BackgroundTask(callback))


@app.post("/api/reference-audio/extract")
def prepare_reference(media: UploadFile = File(...), start: float = Form(0),
                      end: float = Form(15), trim_silence: bool = Form(False)):
    # This synchronous route runs in the thread pool and never contacts DGX.
    media.file.seek(0, io.SEEK_END)
    size = media.file.tell()
    media.file.seek(0)
    if size > MAX_MEDIA_BYTES:
        raise HTTPException(413, '源视频或录音上限 128 MB，请先截取较短的文件')
    if not size:
        raise HTTPException(422, '文件为空，请重新选择')
    try:
        content, info = extract_reference(media.file, start, end, trim_silence)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except (av.FFmpegError, EOFError, OSError) as error:
        raise HTTPException(422, '无法解码这个文件或读取所选片段，请检查文件是否完整，或换用 MP4、MOV、WAV、MP3') from error
    return Response(content, media_type='audio/wav', headers={
        'Content-Disposition': 'attachment; filename="reference.wav"',
        'X-Audio-Duration': str(info['duration']),
        'X-Trimmed-Start': str(info['trimmed_start']),
        'X-Trimmed-End': str(info['trimmed_end']),
    })


@app.post("/api/jobs", status_code=202)
async def create_job(settings: str = Form(...), images: list[UploadFile] = File(default=[]),
                     reference_audio: UploadFile | None = File(default=None)):
    global active_task, active_job
    async with submission_lock:
        if session.state != "ready":
            raise HTTPException(409, "请先连接 DGX，等待模型服务就绪")
        if native.pending():
            raise HTTPException(409, "ComfyUI 正在运行或归档任务，请先等待完成")
        if active_task and not active_task.done():
            raise HTTPException(409, "已有任务在运行，请等待完成或取消")
        try:
            values = Settings.model_validate_json(settings)
        except ValidationError as error:
            raise HTTPException(422, str(error)) from error
        if values.engine != session.engine:
            raise HTTPException(409, "当前连接的模型不同，请先切换连接")
        minimum, maximum = (0, 0) if values.mode in ("text", "music", "speech", "clone") else (1, 2 if values.mode == "image" else 9)
        if not minimum <= len(images) <= maximum:
            raise HTTPException(422, f"此模式需要 {minimum}–{maximum} 张图片")
        normalized = []
        reference = None
        # Direct Python callers may leave FastAPI's default parameter object intact.
        uploaded_audio = reference_audio if hasattr(reference_audio, 'read') else None
        if values.mode == 'clone' and uploaded_audio is None:
            raise HTTPException(422, "声音克隆需要一段参考录音")
        if uploaded_audio is not None:
            if values.mode != 'clone':
                await uploaded_audio.close()
                raise HTTPException(422, "只有声音克隆模式接受参考录音")
            content = await uploaded_audio.read(32 * 1024 * 1024 + 1)
            await uploaded_audio.close()
            if len(content) > 32 * 1024 * 1024:
                raise HTTPException(413, "参考录音上限 32 MB")
            try:
                reference = await asyncio.to_thread(normalize_reference, content)
            except (ValueError, av.FFmpegError, EOFError) as error:
                raise HTTPException(422, str(error)) from error
        for upload in images:
            content = await upload.read(32 * 1024 * 1024 + 1)
            await upload.close()
            if len(content) > 32 * 1024 * 1024:
                raise HTTPException(413, "每张图片上限 32 MB")
            try:
                with Image.open(io.BytesIO(content)) as original:
                    if original.width * original.height > 25_000_000:
                        raise HTTPException(413, "每张图片上限 2500 万像素")
                    image = ImageOps.exif_transpose(original).convert("RGBA" if values.engine == "qwen21" else "RGB")
                    output = io.BytesIO()
                    image.save(output, format="PNG")
                    normalized.append(output.getvalue())
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
                raise HTTPException(422, "无法读取图片，请使用 PNG、JPEG 或 WebP") from error
        job_id = uuid.uuid4().hex
        directory = job_dir(job_id)
        directory.mkdir(parents=True)
        if reference:
            (directory / 'reference.wav').write_bytes(reference)
        names = []
        for index, content in enumerate(normalized):
            name = f"reference_{index + 1}.png"
            (directory / name).write_bytes(content)
            names.append(name)
        job = {"id": job_id, "created": datetime.now().astimezone().isoformat(),
               "settings": values.model_dump(), "frames": values.frames if values.engine == "h3" else None,
               "effective_seconds": round(values.frames / 24, 3) if values.engine == "h3" else None, "images": names,
               "reference_audio": "reference.wav" if reference else None,
               "state": "queued", "stage": "等待提交", "progress": 0,
               "elapsed": 0, "remote_cleaned": False, "cleanup_finished": False}
        save_job(job)
        active_job = job_id
        active_task = asyncio.create_task(run_job(job, values))
        return job


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return read_job(job_id)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    async with submission_lock:
        directory = job_dir(job_id)
        job = read_job(job_id)
        # A completed result may still be downloading/cleaning up in the worker.
        if active_job == job_id or job["state"] not in TERMINAL:
            raise HTTPException(409, "任务正在运行或清理，请完成或取消后再删除")
        try:
            shutil.rmtree(directory)
        except OSError as error:
            # Keep the record available for retry if Windows has a file open.
            if directory.is_dir():
                save_job(job)
            raise HTTPException(409, "部分文件无法删除，请关闭占用文件的程序后重试") from error
        return {"deleted": job_id}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if active_job != job_id or not active_task or active_task.done():
        raise HTTPException(409, "此任务当前未运行")
    await cancel_active()
    return read_job(job_id)


@app.get("/api/jobs/{job_id}/files/{filename}")
async def job_file(job_id: str, filename: str, download: bool = False):
    if not re.fullmatch(r"result\.(mp4|png|wav)|reference_[1-9]\.png|reference\.wav|workflow\.json|job\.json", filename):
        raise HTTPException(404)
    path = job_dir(job_id) / filename
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename=filename if download else None)


@app.get("/api/comfy/templates/{name}")
async def native_template(name: str, download: bool = True):
    if name in H3_TEMPLATES:
        path = ROOT / "docs/h3-extended/workflows" / (name + ".api.json")
        graph = json.loads(path.read_text(encoding="utf-8"))
        headers = {"Content-Disposition": f'attachment; filename="{name}.api.json"'} if download else {}
        return JSONResponse(graph, headers=headers)
    options = {
        "qwen-text": (Settings(engine="qwen21", mode="text", prompt="A small red boat on a quiet lake, soft morning light", width=512, height=512, steps=25), []),
        "qwen-edit": (Settings(engine="qwen21", mode="edit", prompt="Change the background to a quiet garden", width=512, height=512, steps=25), ["reference.png"]),
        "h3-image": (Settings(engine="h3", mode="image", prompt="The camera slowly moves forward", width=768, height=448), ["reference.png"]),
        "h3-reference": (Settings(engine="h3", mode="reference", prompt="<Picture 1> moves gently in the breeze", width=512, height=320, steps=4), ["reference.png"]),
        "music": (Settings(engine="music3", mode="music", prompt="Gentle acoustic guitar and warm piano, calm instrumental music", seconds=10, steps=30), []),
    }
    if name not in options:
        raise HTTPException(404)
    settings, images = options[name]
    graph = build_workflow(settings, images, uuid.uuid4().hex)
    headers = {"Content-Disposition": f'attachment; filename="{name}.api.json"'} if download else {}
    return JSONResponse(graph, headers=headers)


@app.get("/api/comfy/jobs")
async def native_jobs(page: int = Query(1, ge=1), page_size: int = Query(12, ge=1, le=60),
                      engine: Literal["qwen21", "h3", "music3"] | None = None,
                      q: str = Query("", max_length=200)):
    return native.archives(page, page_size, engine, q)


@app.get("/api/comfy/jobs/{archive_id}/workflow")
async def native_workflow(archive_id: str):
    return native.workflow(archive_id)


@app.get("/api/comfy/jobs/{archive_id}/files/{filename}")
async def native_file(archive_id: str, filename: str):
    path = native.file_path(archive_id, filename)
    if not path.is_file():
        raise HTTPException(404)
    # Media is shown inline; JSON and unknown formats are sent as attachments.
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    return FileResponse(path, media_type=media_type, filename=None if media_type else filename)


app.mount("/comfy", native.gateway, name="comfy")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
