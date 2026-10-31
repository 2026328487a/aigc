# H3 扩展部署记录

部署日期 2026-10-31。目标 `dgx`（`spark-b34f`）、Linux ARM64、NVIDIA GB10、约 121.69 GiB 统一内存。现有 OpenArm 在本机 8765；AIGC 的 `start.cmd` 使用 8766。

| 内容 | 路径／版本 |
|---|---|
| ComfyUI 扩展源码 | `/home/jnu/src/ComfyUI-h3-extended` |
| 固定提交 | `a8686f2b33fc540f137df50c0f0719953830a5e7` |
| Python 环境 | `/home/jnu/venvs/comfyui-h3-extended` |
| 附加模型 | `/home/jnu/models/aigc/h3-extended/models` |
| 路径配置 | `/home/jnu/models/aigc/h3-extended/extra_model_paths.yaml` |
| 部署清单 | `/home/jnu/models/aigc/h3-extended/deployment.json` |
| 共用基础模型 | `/home/jnu/src/ComfyUI/models` |
| Torch／torchaudio | `2.11.0+cu130` |
| Transformers | `5.8.0` |
| comfy-kitchen | `0.2.35` |

旧 `/home/jnu/src/ComfyUI` 和 `comfyui-ltx23` 环境保留。独立环境从旧环境复制后，使用约束文件保持 ARM CUDA、Torch、Transformers 和 comfy-kitchen 版本，再补插件的 CPU 依赖。

下载包中的 Windows Python、DLL、EXE 和已有运行数据没有上传。选取 11 个插件源码包：KJNodes、Easy Use、VideoHelperSuite、Custom Scripts、FaceRefine、MelBandRoFormer、MiniMax Tools、3D latent upscaler、Fictiverse、reservedvram、MiniMaxH3Mod。插件源文件摘要和 14 个附加权重的完整 SHA256 见 [部署清单](deployment-manifest.json)，附加权重合计约 **8.414 GiB**。上传结果逐文件校验。

附加权重包括两个任务匹配的 LightX2V LoRA，人物替换、换头、动漫、动作、LMS、DMAD LoRA，3D latent upscaler、taeh3、MelBandRoFormer、Union 2.0 与两个 YOLO 检测器。DMAD 和预览权重暂作为候选保留。标准主模型、文本编码器、音视频 VAE 继续共用。

下载的同名 INT8 视频 VAE 与 DGX 已有文件不同，本次保留已在 DGX 运行的版本。没有用本地下载副本覆盖。动漫与动作 LoRA 在附加目录使用 ASCII 别名，清单保存原名。

三项兼容处理：

1. MiniMax Tools 的可选 AdvancedAddGuide 引用不存在的 `comfy.audio`。只在此模块缺失时跳过该节点，其他音频、续接、拼接节点继续注册；核心 `MiniMaxH3AddGuide` 可用。
2. 小型 `AIGC-H3-Helpers` 提供去掉潜变量遮罩、RefMod 的会话输出／输入以及录音时长对齐。RefMod 文件可以由作品库归档，不必写入共用模型目录。
3. Linux 模型选择器使用 `/` 路径，修脸 detector 为 `bbox/face_yolov8m.pt`。禁用身份识别时仍保留合法的 `identity_model` 枚举，避免校验失败。

H3 的 profile 明确启用自定义节点；启动仍通过已有 SSH 会话系统，输入、输出和工作目录位于随机会话目录。扩展源代码会缓存插件和模型；部分第三方节点有自己的保存行为。提供的模板关闭 RefMod 自动写入模型目录，不使用原大图的自动写提示词支路。

重复部署命令（维护用途）如下。第一步需要原下载目录，权重上传支持已校验文件跳过；基础源码和环境必须已经存在：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\prepare_h3_extended.py "E:\Quark\dowmload\MiniMax H3"
scp test-results/h3-extended/plugins.tar.gz test-results/h3-extended/manifest.json scripts/h3_helpers.py scripts/h3_reference.js dgx:/home/jnu/models/aigc/h3-extended/staging/
.\.venv\Scripts\python.exe -X utf8 scripts\dgx_exec.py scripts\deploy_h3_extended.py
.\.venv\Scripts\python.exe -X utf8 scripts\upload_h3_extended.py
.\.venv\Scripts\python.exe -X utf8 scripts\dgx_exec.py scripts\patch_h3_extended.py
```

上传插件的暂存目录需存在；首次创建用 `ssh dgx "mkdir -p /home/jnu/models/aigc/h3-extended/staging"`。部署前应结束此工作台的生成任务，重新连接之后才会加载插件变更。不会停止其他项目服务。

启动本机工作台：

```powershell
.\.venv\Scripts\python.exe -m aigc --port 8766 --open-browser
```

复现实测，先在工作台连接 H3，再准备 `test-results/h3-extended/assets` 下的 `driver.png`、`person.png`、`source.mp4`、`reference.wav`。测试脚本经同一代理上传素材、提交、等待归档，检查媒体流和哈希：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\run_h3_experiments.py h3-lightx h3-continuation --width 512 --height 320 --length 124
```

每次实测保存在独立的 `test-results/h3-extended/runs/<时间>`，汇总见 `experiments.json`。测试素材与成片不随 Git 上传。

RTX 软件分发核验记录为 `test-results/h3-extended/nvidia-vfx-platforms.json`：NVIDIA 索引的 0.2.0.0 版本没有 Linux aarch64 wheel。该包没有安装，替代流程为 H3 潜空间放大。
