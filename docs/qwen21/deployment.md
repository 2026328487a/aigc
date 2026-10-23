# Qwen Image 2.1 UC：DGX Spark 部署记录

调研与安装：2026-10-14；恢复中断会话并完成真实推理验证：2026-10-15。

## 选型

采用独立 ComfyUI，加载 **INT8 ConvRot 主模型 + BF16 文本编码器 + BF16 VAE**。本地浏览器负责输入、预览与历史；DGX 只运行会话内的推理服务。

DGX 实际为 NVIDIA GB10 / DGX Spark，约 121 GiB 系统可用统一内存，不是多张 A100 的 DGX。部署前可用内存约 115 GiB。该容量足够先选择 BF16 文本编码器，后续应以实测比较速度与输出质量。

| 方案 | 适配情况与取舍 |
| --- | --- |
| 原生 Safetensors INT8 ConvRot | 官方 ComfyUI 文生图模板采用此量化形式；不需要 GGUF 自定义节点。本次选择。 |
| 原生 NVFP4 | 主模型更小，约 4.2 GB；GB10 支持 Blackwell，但更低位宽不代表这个工作负载必然更快或效果相同，暂未做对比测试。 |
| GGUF Q4_K_M | 主模型约 4.6 GB；仓库推荐的 GGUF 路线需要支持新架构的加载器。本机内存充足，不额外引入插件。 |
| Diffusers | 官方提供 QwenImage21Pipeline；需要另一套近期 Diffusers / Transformers 环境。目前已有 ComfyUI 工作台，先复用接口。 |
| MLX | 面向 Apple Silicon，不适用本次 DGX。 |

社区讨论索引中存在旧加载器不识别架构、INT8 文本编码器加载失败、缓存相关问题。它们是兼容性线索，不是已经复现的结论；本次选用近期原生节点和 BF16 文本编码器降低这部分不确定性。部分讨论正文由于网络或镜像 403 未能获取。仓库的“Uncensored”是发布者命名，本次不对其行为或宣传效果作保证。

## 固定版本和目录

- ComfyUI 官方提交：`e0a32bce84f4603f2b61b16b50f8958e68fd6171`。
- 模型仓库：`abenzerps/Qwen-Image-2.1-Uncensored-Quantized`。
- 模型提交：`7959e658915f52274897fb573ffeda49de8dc16b`。
- 新源码：`/home/jnu/src/ComfyUI-qwen21`。
- 新 Python 环境：`/home/jnu/venvs/comfyui-qwen21`。
- 原 H3 源码和环境仍为 `/home/jnu/src/ComfyUI`、`/home/jnu/venvs/comfyui-ltx23`。
- 新环境从旧环境独立复制后安装新版本所需依赖，不使用共享硬链接；未修改 ComfyUI 核心代码。
- 实际运行版本：Python 3.12.3、PyTorch 2.11.0 / CUDA 13.0、Transformers 5.8.0、comfy-kitchen 0.2.37、comfy-aimdo 0.5.6；完整环境见 `environment.txt`。
- `uv pip check` 对 NVIDIA cuSPARSELt 0.8.0 报平台标签警告：官方 aarch64 wheel 内部使用 `manylinux2014_sbsa` 标签。已确认其共享库实际为 ARM aarch64；未修改包元数据，真实文生图和图片编辑均已成功运行。

| 文件 | 大小（字节） | SHA256 |
| --- | ---: | --- |
| `diffusion_models/qwen-image-2.1-UC-int8_convrot.safetensors` | 7256796840 | `5bc5a6c007eff1e0d4004344a24c4af966b9d2c83d142e613b0d456ccceb19ae` |
| `text_encoders/qwen3vl_8b_bf16.safetensors` | 17534334616 | `68bdc82bc1b66851162ae656225e7e2068166b603db19bd5d5a3b90eb12669a9` |
| `vae/qwen_image_2.1_vae_bf16.safetensors` | 675509688 | `bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9` |

三个文件约 25.47 GB。下载通过用户指定的 hf-mirror.com，固定提交，支持断点续传；完成后核对从原 Hugging Face API 获取的大小、SHA256，并读取 Safetensors 头。脚本见 `scripts/download_qwen21.py`。不需要把所有量化版本都下载一遍。

## 推理流程

文生图：UNETLoader → QwenImage21Cache（auto/default）→ KSampler；CLIPLoader 的类型是 `qwen_image`，TextEncodeQwenImage21 提供条件；EmptyLatentImage 提供画布，VAEDecode / SaveImage 输出 PNG。

默认参数按官方 ComfyUI 模板：1024 × 1024、25 步、CFG 1、Euler、simple scheduler、denoise 1。未安装可选的提示词增强模型；官方模板默认关闭该分支。

图片编辑使用 TextEncodeQwenImage21 返回的 latent，保持首张参考图的比例和对齐关系。界面中的宽高用于控制大致像素面积，实际输出尺寸以结果为准。当前界面允许 1–9 张参考图；具体已验证的数量见实测记录。

## 数据与资源生命周期

- 网页：`http://127.0.0.1:8765`，选择 Qwen Image 2.1 UC 后连接 DGX。
- 切换模型会停止工作台自己的远端实例，启动对应独立环境；运行任务时禁止切换。
- 通过 SSH 隧道访问仅监听远端回环地址的 ComfyUI，所有接口均要求会话随机令牌。
- 上传、输出、临时目录位于会话专属 `/dev/shm/aigc-*`；不使用持久任务数据库，关闭输出提示词／工作流元数据。
- 本地检查文件大小、SHA256、图片解码和元数据后，清理远端本任务输入输出及 ComfyUI 历史。
- 本地永久保存 `data/jobs/<任务ID>/result.png`、参数与工作流；参考图先规范化，去除原始附加元数据。
- SSH 退出、心跳超时或主动断开时回收本会话；空闲 10 分钟自动释放服务和模型内存。
- 模型、软件及安装缓存留在服务器。tmpfs 可能受 swap 影响，且无法防止管理员或同一 Unix 账号访问；这不是“服务器绝对零痕迹”的方案。

## 实测结果（2026-10-15）

三个模型文件均已完成下载并通过固定版本的大小与 SHA256 校验；恢复会话时再次核对了 17.53 GB 文本编码器的哈希。以下耗时包含任务提交、推理、结果传输及本地校验，不包含启动远端服务的时间。

| 流程 | 参数 | 全流程耗时 | 结果 |
| --- | --- | ---: | --- |
| 文生图小样 | 512×512，4 步 | 10.0 秒 | PNG，已清理远端文件 |
| 真实网页文生图 | 1024×1024，25 步 | 25.1 秒 | PNG，浏览器预览／下载通过，已清理远端文件 |
| 单参考图编辑 | 512×512，25 步 | 10.0 秒 | 红色茶壶改为蓝色，背景与构图基本保留，已清理远端文件 |

这些是不同缓存状态下的单次测试，不是保证耗时。4 步仅用于快速检查兼容性，日常使用保持默认 25 步。已验证文生图和一张参考图的编辑；多参考图、非正方形编辑及透明背景生成尚未做真实推理验证。

结果保存于本机：

- 文生图：`data/jobs/8f6a8881a3e84b85b7c1417ff2d06d0a/result.png`。
- 编辑图：`data/jobs/02ef1b8eed334ce9b7fe0e0fb47daf14/result.png`。
- 对应测试报告：`test-results/qwen-<任务ID>.json`，含参数、耗时、SHA256 与远端目录检查。

本地 18 项自动检查通过，包含 H3 现有检查、无上传文生图、编辑上传约束、模型切换限制、PNG 解码及元数据检查。真实 Edge 浏览器默认参数提交、PNG 预览、下载文件哈希、390px 手机布局检查通过，无 JavaScript 错误。可用 `scripts/qwen_ui_smoke.py` 显式重复该出图检查。

新 Qwen 实例上的历史、下载、WebSocket、提交、上传、清理接口均拒绝无令牌访问（401）。每次成功任务后的远端输入／输出／用户／暂存目录为空。切换到 H3 后，已确认原 Qwen 会话目录被删除；H3 原环境关键依赖版本与此前一致。

最终回归检查：

- H3 切换后重新生成 320×192、2 步、1.625 秒带音轨视频，耗时 19.4 秒，下载和远端清理通过。任务 `74bd6f9b39594f0ead481defbc9f19b4`。
- Qwen 在采样第 1 / 25 步取消，任务进入已取消状态，远端文件清理通过。报告：`test-results/qwen-cancel-release.json`。
- 测试结束后主动断开，确认本次 H3 和 Qwen 子进程均退出、各自会话目录均删除。本地工作台继续运行，等待用户连接；模型与独立环境保留，当前无本工作台的远端推理实例。

## 参考资料

- [用户指定模型与量化说明](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized)
- [Qwen 官方基础模型](https://huggingface.co/Qwen/Qwen-Image-2.1)
- [Comfy-Org 原生模型打包](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)
- [官方 ComfyUI 文生图模板](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json)
- [本次固定版本的 Qwen 节点源码](https://github.com/Comfy-Org/ComfyUI/blob/e0a32bce84f4603f2b61b16b50f8958e68fd6171/comfy_extras/nodes_qwen.py)
- [GGUF 新架构加载器](https://github.com/leejet/ComfyUI-GGUF)
- [社区 #59：INT8 编码器加载问题](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized/discussions/59)
- [社区 #31：未知架构问题](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized/discussions/31)
- [社区 #37：缓存相关反馈](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized/discussions/37)
