"""Native ComfyUI gateway and local archives for a session-owned server."""

import asyncio
from contextlib import suppress
from datetime import datetime
import hashlib
import json
import pathlib
import re
import time
from urllib.parse import quote
import uuid

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import httpx
from starlette.background import BackgroundTask
from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .session import ROOT


DONE = {"saved", "failed", "cancelled", "interrupted"}
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
               ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac", ".m4a": "audio/mp4"}
MEDIA_FOLDERS = {"image": "images", "video": "videos", "audio": "audio"}
ARCHIVE_FILES = {"workflow-api.json", "workflow.json", "history.json", "job.json"}
# Archives are stored as <time>_<engine>; older ones used the random archive ID.
FOLDER = re.compile(r"[0-9a-f]{32}|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_[a-z0-9]+(-\d+)?")
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "host", "authorization",
               "cookie", "origin", "referer"}


def file_descriptors(value):
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str) and value.get("type") in ("output", "temp"):
            yield {"filename": value["filename"], "subfolder": value.get("subfolder", ""), "type": value["type"]}
        else:
            for child in value.values():
                yield from file_descriptors(child)
    elif isinstance(value, list):
        for child in value:
            yield from file_descriptors(child)


def file_key(item):
    return (item["type"], item.get("subfolder", ""), item["filename"])


class NativeComfy:
    def __init__(self, session, root=None):
        self.session = session
        self.root = pathlib.Path(root) if root else ROOT / "data/comfy"
        self.records = {}
        self.view_files = {}
        self.lock = asyncio.Lock()
        self.gateway = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self._routes()

    def load(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (self.root / "jobs").glob("*/job.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not re.fullmatch(r"[0-9a-f]{32}", record["id"]) or not FOLDER.fullmatch(path.parent.name):
                    continue
                # The directory on disk is authoritative, so an interrupted rename is still found.
                record["folder"] = path.parent.name
                self.records[record["id"]] = record
                if record["state"] not in DONE:
                    record.update(state="interrupted", error="本地服务已重启，上一会话已结束")
                    self.save(record)
                self.migrate(record)
            except (ValueError, KeyError, TypeError, OSError, HTTPException):
                continue

    def folder_name(self, engine, created):
        base = f"{datetime.fromisoformat(created):%Y-%m-%d_%H-%M-%S}_{engine}"
        used = {r.get("folder") for r in self.records.values()}
        name, index = base, 1
        while name in used or (self.root / "jobs" / name).exists():
            index += 1
            name = f"{base}-{index}"
        return name

    def output_name(self, record, index, suffix):
        return f"{record['folder']}_{index + 1:02d}{suffix}"

    def migrate(self, record):
        """Give legacy archives time-based names and move media into shared folders.

        Files that are open elsewhere stay in place and are retried on the next start."""
        if record["folder"] == record["id"]:
            folder = self.folder_name(record["engine"], record["created"])
            try:
                self.directory(record["id"]).rename(self.root / "jobs" / folder)
            except OSError:
                return
            record["folder"] = folder
            self.save(record)
        directory = self.directory(record["id"])
        for index, file in enumerate(record["files"]):
            name = self.output_name(record, index, pathlib.PurePosixPath(file["local_name"]).suffix)
            source = self.checked_file(directory, file["local_name"])
            destination = self.output_path(record["id"], name)
            try:
                if source != destination and source.is_file():
                    if destination.exists():
                        self.session.logs.append(f"媒体迁移遇到同名文件，保留原文件：{source.name}")
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(destination)
                elif not destination.is_file():
                    continue
            except OSError as error:
                self.session.logs.append(f"媒体迁移暂未完成，启动时将重试：{source.name}：{error}")
                continue
            # Recover a move completed before job.json was updated.
            if file["local_name"] != name:
                file["local_name"] = name
                self.save(record)

    def directory(self, archive_id):
        if not re.fullmatch(r"[0-9a-f]{32}", archive_id):
            raise HTTPException(404)
        record = self.records.get(archive_id)
        folder = (record.get("folder") or archive_id) if record else archive_id
        if not FOLDER.fullmatch(folder):
            raise HTTPException(404)
        path = self.root / "jobs" / folder
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(self.root.resolve()):
            raise HTTPException(404)
        return path

    @staticmethod
    def checked_file(directory, filename):
        if not isinstance(filename, str) or not re.fullmatch(r"[^/\\:\x00]+", filename) or filename in (".", ".."):
            raise HTTPException(404)
        path = directory / filename
        if path.is_symlink() or path.is_junction() or path.resolve().parent != directory.resolve():
            raise HTTPException(404)
        return path

    def output_path(self, archive_id, filename):
        directory = self.directory(archive_id)
        media_type = MEDIA_TYPES.get(pathlib.PurePosixPath(filename).suffix.lower(), "")
        bucket = MEDIA_FOLDERS.get(media_type.split("/")[0])
        if bucket:
            directory = self.root / "media" / bucket
            if (directory.parent.is_symlink() or directory.parent.is_junction()
                    or directory.is_symlink() or directory.is_junction()
                    or not directory.resolve().is_relative_to(self.root.resolve())):
                raise HTTPException(404)
        return self.checked_file(directory, filename)

    def file_path(self, archive_id, filename):
        record = self.records.get(archive_id)
        if not record:
            raise HTTPException(404)
        legacy = self.checked_file(self.directory(archive_id), filename)
        if filename in ARCHIVE_FILES:
            return legacy
        if not any(file["local_name"] == filename for file in record["files"]):
            raise HTTPException(404)
        # Locked files or name conflicts can leave individual outputs in the old layout.
        return legacy if legacy.is_file() else self.output_path(archive_id, filename)

    def save(self, record):
        directory = self.directory(record["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "job.json.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "job.json")

    def current(self):
        remote_root = self.session.info.get("root")
        return [r for r in self.records.values() if r["session_root"] == remote_root]

    def pending(self):
        return any(r["state"] not in DONE for r in self.current())

    def ended(self):
        for record in self.current():
            if record["state"] not in DONE:
                record.update(state="interrupted", error=record.get("error") or "服务器会话已结束")
                self.save(record)

    def public_record(self, record):
        result = {k: record.get(k) for k in ("id", "folder", "prompt_id", "engine", "created", "state", "error", "files")}
        directory = self.directory(record["id"])
        result["has_workflow"] = any((directory / name).is_file() for name in ("workflow.json", "workflow-api.json"))
        return result

    def archives(self, page=1, page_size=12, engine=None, query=""):
        query = query.strip().casefold()
        records = [r for r in self.records.values() if not engine or r.get("engine") == engine]
        if query:
            records = [r for r in records if query in " ".join(
                [r["id"], r.get("folder", ""), r.get("created", ""), r.get("prompt_id", "")]
                + [f.get(k, "") for f in r.get("files", []) for k in ("filename", "local_name")]).casefold()]
        records.sort(key=lambda r: r["created"], reverse=True)
        total = len(records)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return {"jobs": [self.public_record(r) for r in records[start:start + page_size]],
                "total": total, "page": page, "page_size": page_size, "pages": pages}

    def public(self):
        return {"url": "/comfy/", "pending": self.pending(),
                "total": len(self.records),
                "jobs": [self.public_record(r)
                         for r in sorted(self.records.values(), key=lambda r: r["created"], reverse=True)[:30]]}

    def workflow(self, archive_id):
        record = self.records.get(archive_id)
        if not record:
            raise HTTPException(404, "任务不存在")
        directory = self.directory(archive_id)
        for filename, format_name in (("workflow.json", "native"), ("workflow-api.json", "api")):
            path = directory / filename
            if not path.is_file() or path.is_symlink():
                continue
            try:
                graph = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            valid = isinstance(graph, dict) and (
                isinstance(graph.get("nodes"), list) if format_name == "native"
                else bool(graph) and all(isinstance(node, dict) and "class_type" in node for node in graph.values()))
            if valid:
                return {"engine": record["engine"], "format": format_name, "filename": filename, "workflow": graph}
        raise HTTPException(404, "此任务没有可加载的工作流")

    def register(self, archive_id, prompt_id, payload):
        record = {"id": archive_id, "prompt_id": prompt_id, "engine": self.session.engine,
                  "session_root": self.session.info["root"], "created": datetime.now().astimezone().isoformat(),
                  "queued_at": time.monotonic(),
                  "state": "queued", "files": [], "error": ""}
        record["folder"] = self.folder_name(record["engine"], record["created"])
        self.records[archive_id] = record
        self.save(record)
        directory = self.directory(archive_id)
        (directory / "workflow-api.json").write_text(json.dumps(payload["prompt"], ensure_ascii=False, indent=2), encoding="utf-8")
        workflow = payload.get("extra_data", {}).get("extra_pnginfo", {}).get("workflow")
        if workflow:
            (directory / "workflow.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
        self.session.last_used = time.monotonic()

    async def collect(self):
        if self.session.state != "ready" or self.session.engine == "voxcpm2":
            return
        async with self.lock:
            for record in self.current():
                if record["state"] in DONE:
                    continue
                try:
                    history = await self.session.request("GET", "/history/" + quote(record["prompt_id"], safe=""))
                    item = history.get(record["prompt_id"])
                    if not item:
                        if time.monotonic() - record.get("queued_at", time.monotonic()) > 10:
                            queue = await self.session.request("GET", "/queue")
                            active = [entry[1] for key in ("queue_running", "queue_pending") for entry in queue.get(key, [])]
                            if record["prompt_id"] not in active:
                                record.update(state="cancelled", error="任务已从服务器队列中移除")
                                self.save(record)
                        continue
                    status = item.get("status", {})
                    if not status.get("completed") and status.get("status_str") not in ("success", "error"):
                        continue
                    directory = self.directory(record["id"])
                    (directory / "history.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
                    record["state"] = "downloading"
                    self.save(record)
                    saved = []
                    unique = {file_key(f): f for f in file_descriptors(item.get("outputs", {}))}
                    for index, descriptor in enumerate(unique.values()):
                        manifest = await self.session.request("POST", "/aigc/native/artifact", json=descriptor)
                        suffix = pathlib.PurePosixPath(descriptor["filename"]).suffix
                        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
                            suffix = ".bin"
                        name = self.output_name(record, index, suffix)
                        destination = self.output_path(record["id"], name)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        # Partial downloads stay with the task, away from ready-to-copy media.
                        temporary = directory / (name + ".part")
                        digest, size = hashlib.sha256(), 0
                        async with self.session.client.stream("GET", "/view", params=descriptor) as response:
                            response.raise_for_status()
                            with temporary.open("wb") as output:
                                async for chunk in response.aiter_bytes():
                                    output.write(chunk)
                                    digest.update(chunk)
                                    size += len(chunk)
                        if size != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
                            raise RuntimeError("远端结果下载校验失败，保留服务器文件等待重试")
                        temporary.replace(destination)
                        saved.append({**descriptor, "local_name": name, **manifest})
                        self.view_files[(record["session_root"], *file_key(descriptor))] = destination
                    record.update(files=saved, state="saved" if item["status"].get("status_str") == "success" else "failed", error="")
                    self.save(record)
                    self.session.last_used = time.monotonic()
                except (httpx.HTTPError, RuntimeError, ValueError, OSError) as error:
                    record.update(state="download_error", error=str(error)[:500])
                    self.save(record)

    async def snapshot_user(self):
        if self.session.state != "ready" or self.session.engine == "voxcpm2":
            return
        files = await self.session.request("GET", "/aigc/native/user-files")
        folder = self.root / "user" / self.session.engine
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "snapshot.json.tmp"
        temporary.write_text(json.dumps(files), encoding="utf-8")
        temporary.replace(folder / "snapshot.json")

    async def busy(self):
        if self.session.state != "ready" or self.session.engine == "voxcpm2" or self.session.client is None:
            return False
        queue = await self.session.request("GET", "/queue")
        return bool(queue.get("queue_running") or queue.get("queue_pending") or self.pending())

    async def before_stop(self):
        if self.session.state != "ready" or self.session.engine == "voxcpm2":
            return
        await self.collect()
        if any(r["state"] == "download_error" for r in self.current()):
            raise RuntimeError("结果尚未成功保存到本机，请重试归档后再断开")
        await self.snapshot_user()
        queue = await self.session.request("GET", "/queue")
        if queue.get("queue_running") or queue.get("queue_pending"):
            await self.session.request("POST", "/queue", json={"clear": True})
            await self.session.request("POST", "/interrupt", json={})
            for _ in range(30):
                queue = await self.session.request("GET", "/queue")
                if not queue.get("queue_running") and not queue.get("queue_pending"):
                    break
                await asyncio.sleep(1)
        await self.collect()
        if any(r["state"] == "download_error" for r in self.current()):
            raise RuntimeError("结果尚未成功保存到本机，请重试归档后再断开")
        for record in self.current():
            if record["state"] not in DONE:
                record.update(state="cancelled", error="会话已停止")
                self.save(record)

    def cached_view(self, params):
        folder = params.get("subfolder", "")
        if folder.startswith("_local/"):
            archive_id = folder[len("_local/"):]
            record = self.records.get(archive_id)
            if record:
                for file in record["files"]:
                    if file["local_name"] == params.get("filename"):
                        return self.file_path(archive_id, file["local_name"])
            return None
        return self.view_files.get((self.session.info.get("root"), params.get("type", "output"), folder, params.get("filename")))

    def local_history(self):
        result = {}
        for record in self.records.values():
            path = self.directory(record["id"]) / "history.json"
            if record["state"] not in DONE or not path.exists():
                continue
            item = json.loads(path.read_text(encoding="utf-8"))
            mapping = {file_key(f): f for f in record["files"]}
            def replace(value):
                if isinstance(value, dict):
                    if isinstance(value.get("filename"), str) and value.get("type") in ("output", "temp"):
                        cached = mapping.get(file_key(value))
                        if cached:
                            value.update(filename=cached["local_name"], subfolder="_local/" + record["id"], type="output")
                    else:
                        for child in value.values():
                            replace(child)
                elif isinstance(value, list):
                    for child in value:
                        replace(child)
            replace(item.get("outputs", {}))
            result[record["prompt_id"]] = item
        return result

    def _routes(self):
        @self.gateway.middleware("http")
        async def same_origin(request, call_next):
            expected = "http://" + request.headers.get("host", "")
            origin = request.headers.get("origin")
            if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": "仅接受本地同源请求"}, status_code=403)
            if request.method not in ("GET", "HEAD", "OPTIONS") and not (
                origin == expected or request.headers.get("sec-fetch-site") == "same-origin" or request.headers.get("x-aigc-client") == "local"
            ):
                return JSONResponse({"error": "缺少本地请求标识"}, status_code=403)
            return await call_next(request)

        @self.gateway.websocket("/ws")
        async def websocket(websocket: WebSocket):
            expected = "http://" + websocket.headers.get("host", "")
            if websocket.headers.get("origin") != expected or self.session.state != "ready" or self.session.engine == "voxcpm2":
                await websocket.close(code=1008)
                return
            await websocket.accept()
            tasks = []
            try:
                async with websocket_connect(f"ws://127.0.0.1:{self.session.port}/ws?{websocket.url.query}",
                    additional_headers={"Authorization": "Bearer " + self.session.token}, max_size=64 * 1024 * 1024, proxy=None) as remote:
                    async def upstream():
                        while True:
                            message = await websocket.receive()
                            if message["type"] == "websocket.disconnect":
                                break
                            await remote.send(message.get("bytes") if message.get("bytes") is not None else message["text"])
                    async def downstream():
                        async for message in remote:
                            if isinstance(message, bytes):
                                await websocket.send_bytes(message)
                            else:
                                await websocket.send_text(message)
                    tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except (WebSocketDisconnect, WebSocketException, OSError):
                pass
            finally:
                for task in tasks:
                    task.cancel()
                    with suppress(asyncio.CancelledError, WebSocketDisconnect, ConnectionClosed):
                        await task
                with suppress(RuntimeError):
                    await websocket.close()

        @self.gateway.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        async def proxy(path: str, request: Request):
            endpoint = "/" + path
            raw_path = request.scope.get("raw_path", endpoint.encode()).decode("ascii")
            root_path = request.scope.get("root_path", "")
            upstream_path = raw_path[len(root_path):] if raw_path.startswith(root_path) else raw_path
            canonical = endpoint.removeprefix("/api") if endpoint.startswith("/api/") else endpoint
            if canonical.startswith("/aigc/"):
                raise HTTPException(404)
            if canonical == "/view" and request.method in ("GET", "HEAD"):
                cached = self.cached_view(request.query_params)
                if cached and cached.is_file():
                    return FileResponse(cached)
            if self.session.state != "ready" or self.session.engine == "voxcpm2":
                if canonical == "/history" and request.method == "GET":
                    return self.local_history()
                raise HTTPException(503, "请在本地连接页面启动 ComfyUI")
            headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS and not k.lower().startswith("sec-")}
            body = await request.body() if request.method not in ("GET", "HEAD") else None
            archive_id, payload = None, None
            if canonical == "/prompt" and request.method == "POST":
                try:
                    payload = json.loads(body)
                    archive_id = uuid.uuid4().hex
                    for node in payload["prompt"].values():
                        inputs = node.get("inputs", {})
                        prefix = inputs.get("filename_prefix")
                        if node.get("class_type") == "AIGCSaveAudioWAV":
                            inputs["filename_prefix"] = archive_id + "/audio"
                        elif isinstance(prefix, str) and node.get("class_type", "").startswith("Save"):
                            if ".." in pathlib.PurePosixPath(prefix.replace("\\", "/")).parts:
                                raise ValueError("Invalid output prefix")
                            inputs["filename_prefix"] = archive_id + "/" + prefix
                    body = json.dumps(payload).encode()
                    headers.pop("content-length", None)
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise HTTPException(400, "工作流格式无效")
            forwarded = self.session.client.build_request(request.method, upstream_path, params=request.query_params.multi_items(), headers=headers, content=body)
            try:
                response = await self.session.client.send(forwarded, stream=True)
            except httpx.TransportError:
                raise HTTPException(502, "ComfyUI 连接中断")
            if archive_id and response.is_success:
                content = await response.aread()
                await response.aclose()
                reply = json.loads(content)
                if reply.get("prompt_id"):
                    self.register(archive_id, reply["prompt_id"], payload)
                return JSONResponse(reply, status_code=response.status_code)
            if canonical.startswith("/history") and request.method == "GET" and response.is_success:
                history = json.loads(await response.aread())
                await response.aclose()
                local = self.local_history()
                if canonical == "/history":
                    history.update(local)
                    maximum = request.query_params.get("max_items")
                    if maximum and maximum.isdigit():
                        history = dict(list(history.items())[-int(maximum):])
                else:
                    key = canonical[len("/history/"):]
                    if key in local:
                        history[key] = local[key]
                return JSONResponse(history)
            if request.method not in ("GET", "HEAD", "OPTIONS") and response.is_success:
                self.session.last_used = time.monotonic()
                if canonical.startswith(("/userdata", "/v2/userdata", "/settings")):
                    try:
                        await self.snapshot_user()
                    except (RuntimeError, httpx.HTTPError, OSError):
                        self.session.logs.append("ComfyUI 设置备份失败，断开前将重试")
            response_headers = {k: v for k, v in response.headers.items() if k.lower() not in HOP_HEADERS}
            async def stream():
                try:
                    async for chunk in response.aiter_raw():
                        yield chunk
                finally:
                    await response.aclose()
            return StreamingResponse(stream(), status_code=response.status_code, headers=response_headers, background=BackgroundTask(response.aclose))
