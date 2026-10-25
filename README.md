# AIGC · ComfyUI 本地管理器

本地管理器通过 SSH 按需启动 DGX 上的独立 ComfyUI，在本机浏览器使用完整节点界面，自动下载和校验生成结果，空闲 10 分钟或手动断开时回收实例与会话文件。图片、视频和音乐分别复用已部署的 Qwen Image 2.1、MiniMax H3、MiniMax Music 3 环境；VoxCPM2 语音保留已有独立入口，后续再接入 ComfyUI 节点。

Qwen 的安装记录见 [图片部署说明](docs/qwen21/deployment.md)，音乐和粤语声音克隆见 [音频部署说明](docs/audio/deployment.md)。原简化表单及历史仍可在 [原工作台](http://127.0.0.1:8765/workbench) 使用。

**获取项目与首次配置**

本地运行环境为 Windows、Python 3.12 或更新版本和 Windows OpenSSH；克隆项目需要 Git。本次验证使用 Python 3.12.10。

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

1. 在本地管理页选择图片、视频或音乐环境，点击“启动 ComfyUI”，等待“已就绪”。
2. 点击“打开 ComfyUI”。这是服务器自带的完整节点界面，HTTP、上传、预览和 WebSocket 都经过本地代理及 SSH 隧道。
3. 在“从已有工作流开始”点击模板的“一键打开”，页面会在需要时启动对应环境，并把工作流加载到 ComfyUI 画布，不会自动运行。修改提示词和参数；图片编辑及视频模板先在 LoadImage 节点上传参考图，再手动运行。也可以点“下载模板”，把 `.api.json` 拖入画布。
4. 任务完成后，管理器自动保存输出、API 工作流、原生工作流（提交携带时）和任务历史，校验文件大小与 SHA-256。管理页“最近任务”可直接预览最近 8 条结果；“本地作品库”（`/library`）提供完整历史，支持分页、按模型环境筛选和按文件名、日期或任务编号查找，图片、视频和音频可直接查看，断开服务器后仍可浏览。旧版以随机 ID 命名的归档文件夹，会在本地管理器启动时自动改为按时间命名。每条任务可“在 ComfyUI 中打开”，优先恢复原生画布，没有时加载 API 工作流；参考图、录音等输入素材在会话结束后已清理，需要重新上传。
5. 素材和输出在同一会话内保留，以便后续工作流引用。点击“断开并清理”或队列空闲 10 分钟后，停止实例并删除整个远端会话目录。运行、排队或归档中的任务不会触发空闲回收；下载失败会保留会话并重试。
6. 语音点击右上角“语音与声音克隆”，使用既有 VoxCPM2 入口。关闭程序可展开“连接诊断与退出”，点击“关闭本地管理器”，或在启动终端按 Ctrl+C。

原生 ComfyUI 的已归档预览可由本地代理提供；核心 `/history` 返回本地保存的任务记录。工作流及设置的 JSON/CSS 等用户文件备份到本机，启动同一模型环境时恢复。当前备份单文件上限 5 MB、总计 20 MB。

不同模型环境仍按需切换，当前使用各环境已有内置节点。新增模型、第三方节点和语音 ComfyUI 节点后续按需要接入。原生前端的新资产库等功能若依赖服务器持久数据库，暂不承诺完整持久化；本地归档和核心历史是结果保存依据。

以下“清空预览”“删除历史”等表单功能说明对应保留的 `/workbench` 页面。

生成结果右上角的“清空预览”可以随时收起已结束任务的图片或视频，停止视频播放；历史和文件仍然保留。清空后，定时刷新及当前标签页重新加载都不会自动展示旧图；点击一条历史或开始下一次生成会重新展示结果。

本地历史每条记录右侧有“删除”。确认后会删除该任务整个本地目录，包括生成图片／视频、参考图、工作流、参数和未完成下载文件，无法撤销；浏览器另存的副本不受影响。运行中或仍在远端清理的任务不能删除。删除正在预览的记录会同时清空预览。

2026-10-16 已实查远端会话及 ComfyUI 文件目录，未发现工作台任务照片残留。功能说明和检查范围见 [历史与预览说明](docs/history-controls.md)。

关闭浏览器标签页不会取消任务。本地管理器继续下载结果，随后按空闲超时回收服务器。再次打开管理页仍可下载本地归档；重新启动实例后可以打开原生 ComfyUI。

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
- 原表单按单任务运行，ComfyUI 使用原生任务队列；两种入口互相避免干扰。当前只接 DGX，4090 尚未接入。

Qwen 建议使用默认 1024×1024、25 步；512×512 可更快试图。H3 建议先用 768×448、2 秒；帧数必须对齐到 `17k+5`，所以目标 2 秒会得到 56 帧，即约 2.33 秒。时长、分辨率和参考图数量增加会提高计算和内存开销。

**原生 ComfyUI 验证（2026-10-23）**

51 项本地测试通过。通过真实 Edge 浏览器加载原生前端、导入 API 工作流，并由前端提交以下模型小样：

| 环境 | 参数 | 已校验的本地结果 |
| --- | --- | --- |
| Qwen Image 2.1 | 512×512，4 步 | PNG，512×512 |
| MiniMax H3 | 320×192，39 帧，2 步 | H.264 MP4，1.625 秒，含音轨 |
| MiniMax Music 3 | 目标 10 秒，10 步 | WAV，约 9.996 秒，双声道 44.1 kHz |

三种任务均验证了本地文件 SHA-256、断开后的本地预览，以及通过独立 SSH 检查确认远端会话目录已删除。Qwen 重连验证了工作流与设置恢复；390 像素宽的管理页和语音入口检查通过。原 VoxCPM2 接口也重新生成了 2.4 秒语音，完成下载、WAV 校验及清理。原生前端未出现 JavaScript 页面错误；页面切换期间有少量可选样式／设置请求被浏览器取消，保留在测试报告中。

这些是小参数的流程验证，不代表默认参数的生成质量或速度。报告位于本机 `test-results/native-*-generate.json` 与 `test-results/native-media-validation.json`。

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
E:\ai\aigc\
  data\comfy\jobs\<生成时间>_<模型环境>\
                         例如 2026-10-25_14-37-12_qwen21；同一秒的任务追加 -2、-3
    <文件夹名>_01.png/mp4/wav/...
                         自动下载的输出，多个文件依次编号
    job.json             原生 ComfyUI 任务状态及输出校验和
    workflow-api.json    实际提交的 API 节点图
    workflow.json        原生画布工作流（提交携带时）
    history.json         原生任务执行记录
  data\comfy\user\<模型环境>\snapshot.json
                         工作流、设置等用户文件的本地备份
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

每个远端会话使用权限 `0700` 的 `/dev/shm/aigc-<随机标识>`，输入、输出、用户目录、上传暂存和主要运行缓存都指向其中。数据库放内存，关闭 prompt/workflow 媒体元数据，不加载额外自定义节点或云端 API 节点。原表单每个任务下载并校验后清理对应文件和历史；原生 ComfyUI 为支持素材复用，在当前会话内保留远端任务文件，断开或空闲回收时删除整个会话目录。

远端所有接口统一使用一次性会话令牌认证，令牌只保存在进程内存，通过 SSH 标准输入传递。本地网页不接触 SSH 密钥；已有 Windows OpenSSH 配置中的 `dgx` 别名负责连接。

这仍然是“尽量不持久保存任务内容”，不是系统级无痕：DGX 已启用 swap，管理员和共用 `jnu` 账号的人仍可能访问运行数据；系统审计正常保留。强制杀死清理监督进程、内核故障等也可能使清理无法执行。失败时界面会区分已确认清理与未确认状态。最初存在的视频、数据库、模型和系统日志没有被清理。

**开发入口**

| 文件 | 职责 |
| --- | --- |
| `aigc/app.py` | 本地 API、上传、任务状态、下载校验与清理协调 |
| `aigc/session.py` | SSH 生命周期、认证、隧道、心跳和诊断 |
| `aigc/native_comfy.py` | 原生 ComfyUI HTTP／WebSocket 代理、队列检查、本地归档与预览 |
| `aigc/remote_supervisor.py` | 远端临时目录、进程管理、超时回收 |
| `aigc/remote_service.py` | ComfyUI 启动适配、统一鉴权、限定目录内的清理接口 |
| `aigc/remote_tts_service.py` | 独立 TTS 接口、参考录音上传、进程取消及会话文件管理 |
| `aigc/remote_tts_worker.py` | 单任务 VoxCPM2 推理与 WAV 输出 |
| `aigc/workflows.py` | H3 与 Qwen 参数、文生图、编辑和视频工作流 |
| `static/launcher.*` | ComfyUI 启动、环境选择、模板和本地归档入口 |
| `static/archive.js` | 首页与作品库共用的作品卡片、媒体预览和弹窗 |
| `static/library.*` | 本地作品库：分页、筛选、查找 |
| `static/open-workflow.*` | 一键打开：按需启动环境，把模板或历史工作流加载到 ComfyUI 画布 |
| `static/index.html`、`static/app.js` | 保留的简化表单，含独立语音入口 |
| `docs/inspection.md` | 初始环境检查与方案存档 |

本次本地环境为 Python 3.12.10；依赖装在项目 `.venv`，直接依赖版本记录在 `requirements.txt`。修改 Python 后，先关闭本地工作台，再重新运行启动器。暂不使用 uvicorn 的 Windows `--reload` 模式，因为本项目需要异步 SSH 子进程。

```powershell
# 本地单元测试，不连接 GPU
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 浏览器检查需要已安装的 Microsoft Edge
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\ui_smoke.py
# 本地作品库检查：用临时归档在 8766 端口启动，不连接 DGX
.\.venv\Scripts\python.exe -X utf8 scripts\library_ui_smoke.py

# 原生 ComfyUI 检查：连接真实 DGX，运行 CPU 图片保存工作流，验证归档和清理
.\.venv\Scripts\python.exe -X utf8 scripts\native_comfy_smoke.py --engine qwen21
.\.venv\Scripts\python.exe -X utf8 scripts\native_comfy_smoke.py --engine h3
# 音乐环境检查使用合成测试录音与 CPU 音频保存，不运行音乐模型推理
.\.venv\Scripts\python.exe -X utf8 scripts\native_comfy_smoke.py --engine music3

# 原生画布的真实模型推理小样（逐个运行）
.\.venv\Scripts\python.exe -X utf8 scripts\native_comfy_smoke.py --engine qwen21 --generate --restore
.\.venv\Scripts\python.exe -X utf8 scripts\native_comfy_smoke.py --engine h3 --generate
.\.venv\Scripts\python.exe -X utf8 scripts\native_comfy_smoke.py --engine music3 --generate

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
