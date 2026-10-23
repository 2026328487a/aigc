# AIGC 本地工作台

本地网页输入文字、上传图片或参考录音，通过 SSH 调用 DGX 上的 Qwen Image 2.1、MiniMax H3、MiniMax Music 3 或 VoxCPM2，图片、视频、音频和任务历史保存到本机。Qwen 的安装记录见 [图片部署说明](docs/qwen21/deployment.md)，音乐和粤语声音克隆见 [音频部署说明](docs/audio/deployment.md)。

**获取项目与首次配置**

本地运行环境为 Windows、Python 3.13 和 Windows OpenSSH；克隆项目需要 Git。

```powershell
git clone https://github.com/2026328487a/aigc.git
cd aigc
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在本机 OpenSSH 配置中设置 `dgx` 别名及密钥认证，并确认 `ssh dgx` 可以连接。生成任务需要已部署对应模型的 DGX，部署路径及依赖见上述部署说明。SSH 配置和私钥由本机管理，不随仓库同步。

**开始使用**

双击项目目录中的 `start.cmd`，会打开 [本地工作台](http://127.0.0.1:8765)。首次启动时，启动器会在缺少 `.venv` 的情况下自动创建环境并安装依赖。也可以在项目目录执行：

```powershell
.\.venv\Scripts\python.exe -m aigc --open-browser
```

1. 选择生成模型，点击“连接 DGX”，等待“已就绪”。这会启动自己的临时推理实例。
2. Qwen 支持只输入描述的文生图，以及上传参考图的图片编辑；H3 提供图生视频和参考图生视频。
   MiniMax Music 3 输入音乐描述和可选歌词；VoxCPM2 输入待朗读文字，声音克隆模式再上传 3–30 秒参考录音。
3. 点击“开始生成”。页面显示阶段和采样进度，完成后自动下载到本地并提供播放、下载。
4. 不用服务器时点击“断开”；空闲 10 分钟也会自动断开。关闭整个程序可展开“连接诊断与退出”，点击“关闭本地工作台”，或在启动终端按 Ctrl+C。

生成结果右上角的“清空预览”可以随时收起已结束任务的图片或视频，停止视频播放；历史和文件仍然保留。清空后，定时刷新及当前标签页重新加载都不会自动展示旧图；点击一条历史或开始下一次生成会重新展示结果。

本地历史每条记录右侧有“删除”。确认后会删除该任务整个本地目录，包括生成图片／视频、参考图、工作流、参数和未完成下载文件，无法撤销；浏览器另存的副本不受影响。运行中或仍在远端清理的任务不能删除。删除正在预览的记录会同时清空预览。

2026-10-16 已实查远端会话及 ComfyUI 文件目录，未发现工作台任务照片残留。功能说明和检查范围见 [历史与预览说明](docs/history-controls.md)。

关闭浏览器标签页不会取消任务。本地服务继续下载结果，随后按空闲超时回收服务器。再次打开网页仍能查看本地历史，播放历史视频不需要连接 DGX。

**当前功能**

- 图生视频：一张首帧图片，可加一张尾帧图片；使用现有 8 步 Turbo LoRA。
- Qwen 文生图：无需上传图片，默认 1024×1024、25 步，保存 PNG。
- Qwen 图片编辑：1–9 张参考图，输出比例跟随首张参考图，所选尺寸控制大致像素总量。
- 参考图生视频：1–9 张参考图；用 `<Picture 1>`、`<Picture 2>` 在提示词中引用；默认 4 步 Turbo LoRA。
- 音乐生成：输入曲风、乐器、人声描述和可选歌词；默认最长 30 秒、30 步，支持设置到 300 秒上限。长时长尚未压测，建议先试短片段；模型可能提前结束。
- 语音与声音克隆：VoxCPM2 支持普通话、粤语、英语提示和自动语言；参考录音 3–30 秒、最多 32 MB，录音原文选填。单次文本上限 2000 字符，建议长文分段。
- 视频提取参考录音：声音克隆页面展开“从视频提取／裁剪参考录音”，上传视频或长录音（最多 128 MB），选择 3–30 秒，可选裁去首尾静音。提取结果自动用作参考录音，可试听、下载 WAV；提取无需连接 DGX。
- 音频播放、WAV 下载、本地历史和参考录音试听。粤语建议使用粤语文本及粤语参考录音；语言选择通过模型风格提示实现。
- 尺寸、时长、采样步数和随机种子；显示模型对齐后的实际时长。
- 生成进度、取消、H.264/AAC 视频播放和下载、本地历史、逐条删除历史及文件、手动清空结果预览。
- SHA-256、文件大小、图片／视频解码及提示词／工作流元数据检查。
- 单任务运行，避免重复点击提交多个任务；当前只接 DGX，4090 尚未接入。

Qwen 建议使用默认 1024×1024、25 步；512×512 可更快试图。H3 建议先用 768×448、2 秒；帧数必须对齐到 `17k+5`，所以目标 2 秒会得到 56 帧，即约 2.33 秒。时长、分辨率和参考图数量增加会提高计算和内存开销。

**Qwen 实测结果（2026-10-15）**

已完成独立环境部署和三个权重文件校验。通过真实网页生成 1024×1024、25 步图片，全流程约 **25.1 秒**；单参考图编辑 512×512、25 步约 **10.0 秒**。两种流程都完成本地 PNG 校验和远端任务文件清理。桌面／手机预览、浏览器下载、18 项本地测试通过。以上是单次测试；多参考图与非正方形编辑尚未实测。详细参数、选型依据和测试记录见 [Qwen 部署说明](docs/qwen21/deployment.md)。

**H3 实测结果（2026-10-14）**

| 流程 | 参数 | 实际视频 | 总耗时 | 远端任务文件 |
| --- | --- | --- | --- | --- |
| 网页上传与图生视频小样 | 320×192，2 步 | 1.625 秒，含音轨 | 29.1 秒 | 已清理 |
| 默认图生视频 | 768×448，8 步 | 2.333 秒，含音轨 | 49.5 秒 | 已清理 |
| 参考图生视频 | 512×320，4 步，1 张图 | 1.625 秒，含音轨 | 23.3 秒 | 已清理 |
| 取消正在运行的任务 | 提交后约 3 秒取消 | 已取消 | — | 已清理 |

这些是本次单次实测，包含不同的模型缓存状态，不是保证耗时。网页还支持尾帧及多参考图，本次真实推理验证覆盖单首帧与单参考图。

远端未认证的历史、下载、WebSocket、提交、上传和清理请求均返回 401。SSH 输入关闭、心跳超时、挂断信号三种会话终止方式均验证了子进程退出和临时目录删除。12 项本地测试通过，覆盖跨站访问、文件边界、视频检查和取消边界。

**数据在哪里**

```text
D:\aigc\
  data\jobs\<任务ID>\
    reference_1.png      本地参考图（规范化为 PNG，不携带源图元数据）
    workflow.json        本次实际提交的节点图
    job.json             提示词、参数、状态、耗时、输出校验和
    result.mp4           生成视频
    result.png           Qwen 生成图片（与视频按任务二选一）
    reference.wav        声音克隆参考录音（规范化为单声道 24 kHz PCM，不带源元数据）
    result.wav           音乐或语音结果（任务只保存对应的一种结果媒体）
  logs\                  本地程序与远端服务诊断日志
  test-results\          本次验证记录、截图和测试素材
```

H3 复用 `/home/jnu/src/ComfyUI`、`/home/jnu/venvs/comfyui-ltx23`；Qwen 使用 `/home/jnu/src/ComfyUI-qwen21`、`/home/jnu/venvs/comfyui-qwen21`。音乐使用独立的 `ComfyUI-music3` 源码与 `comfyui-music3` 环境；语音使用 `VoxCPM-aigc` 源码与 `voxcpm2-aigc` 环境，模型位于 `/home/jnu/models/aigc/VoxCPM2`。会话启动器通过 SSH 标准输入发送。

每个远端会话使用权限 `0700` 的 `/dev/shm/aigc-<随机标识>`，输入、输出、用户目录、上传暂存和主要运行缓存都指向其中。数据库放内存，视频关闭 prompt/workflow 元数据，不加载额外自定义节点或云端 API 节点。每个任务下载并校验后清理对应文件和历史，断开时回收整个实例。

远端所有接口统一使用一次性会话令牌认证，令牌只保存在进程内存，通过 SSH 标准输入传递。本地网页不接触 SSH 密钥；已有 Windows OpenSSH 配置中的 `dgx` 别名负责连接。

这仍然是“尽量不持久保存任务内容”，不是系统级无痕：DGX 已启用 swap，管理员和共用 `jnu` 账号的人仍可能访问运行数据；系统审计正常保留。强制杀死清理监督进程、内核故障等也可能使清理无法执行。失败时界面会区分已确认清理与未确认状态。最初存在的视频、数据库、模型和系统日志没有被清理。

**开发入口**

| 文件 | 职责 |
| --- | --- |
| `aigc/app.py` | 本地 API、上传、任务状态、下载校验与清理协调 |
| `aigc/session.py` | SSH 生命周期、认证、隧道、心跳和诊断 |
| `aigc/remote_supervisor.py` | 远端临时目录、进程管理、超时回收 |
| `aigc/remote_service.py` | ComfyUI 启动适配、统一鉴权、限定目录内的清理接口 |
| `aigc/remote_tts_service.py` | 独立 TTS 接口、参考录音上传、进程取消及会话文件管理 |
| `aigc/remote_tts_worker.py` | 单任务 VoxCPM2 推理与 WAV 输出 |
| `aigc/workflows.py` | H3 与 Qwen 参数、文生图、编辑和视频工作流 |
| `static/` | 简单 HTML、CSS、JavaScript 页面 |
| `docs/inspection.md` | 初始环境检查与方案存档 |

本地环境为 Python 3.13；依赖装在项目 `.venv`，直接依赖版本记录在 `requirements.txt`。修改 Python 后，先关闭本地工作台，再重新运行启动器。暂不使用 uvicorn 的 Windows `--reload` 模式，因为本项目需要异步 SSH 子进程。

```powershell
# 本地单元测试，不连接 GPU
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 浏览器检查需要已安装的 Microsoft Edge
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\ui_smoke.py

# 历史删除与预览检查：使用独立测试数据，不连接 DGX
.\.venv\Scripts\python.exe -X utf8 scripts\history_ui_smoke.py

# 以下命令会执行真实远端操作
.\.venv\Scripts\python.exe scripts\ui_smoke.py --submit
.\.venv\Scripts\python.exe scripts\api_smoke.py
.\.venv\Scripts\python.exe scripts\api_smoke.py --mode reference --width 512 --height 320 --seconds 1 --steps 4
.\.venv\Scripts\python.exe scripts\api_smoke.py --cancel --width 320 --height 192 --seconds 1
.\.venv\Scripts\python.exe -X utf8 scripts\qwen_smoke.py --size 512 --steps 4
.\.venv\Scripts\python.exe -X utf8 scripts\qwen_ui_smoke.py

# 需 SSH；清理监督器测试使用独立 CPU 测试进程，不加载模型
.\.venv\Scripts\python.exe scripts\check_remote_cleanup.py

# 音频真实推理检查
.\.venv\Scripts\python.exe -X utf8 scripts\audio_smoke.py --engine music3 --seconds 10
.\.venv\Scripts\python.exe -X utf8 scripts\audio_smoke.py --engine voxcpm2 --language yue --prompt "你好呀，今日天氣幾好。"
.\.venv\Scripts\python.exe -X utf8 scripts\music_ui_smoke.py
```

API 小样脚本需要先连接 DGX，并使用网页检查脚本生成的 `test-results/test-input.png`。这些是显式触发的开发检查，正常启动工作台不会自动生成视频。

**Git 管理与同步**

仓库地址：[2026328487a/aigc](https://github.com/2026328487a/aigc)，主分支为 `main`。

开始修改前，在工作区干净时获取远端更新：

```powershell
git pull --ff-only
```

修改完成后，检查差异、运行本地测试，再提交和推送：

```powershell
git status
git diff
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git add .
git diff --cached
git commit -m "描述本次修改"
git push
```

Git 跟踪源码、网页资源、测试、脚本、依赖清单和部署文档。`.gitignore` 排除虚拟环境、缓存、本地 `data/`、`logs/`、`test-results/`、环境配置和本地测试图片；这些文件继续保存在本机。克隆到其他电脑后，需要重新创建 Python 环境并配置 SSH，任务历史和生成媒体不会随 Git 同步。

`.gitattributes` 统一文本换行，Windows 启动脚本使用 CRLF，便于不同电脑间协作。
