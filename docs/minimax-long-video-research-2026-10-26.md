# DGX Spark 长视频工作流与模型资源调查

首次调查：2026-10-26；本次重写及服务器盘点：2026-10-27（北京时间）。\
项目：`D:\aigc`；服务器：SSH 别名 `dgx`，主机 `spark-b34f`。\
目标：用现有硬件完成可复现的长视频创作，明确可抄的工作流、已部署与可扩展模型，以及资源保留和回收依据。

## 1. 先给决策：这台机器应该怎么用

**优先复用 Qwen 分镜图 + H3 分段视频 + VoxCPM2 旁白 + Music 3 配乐，按阶段串行运行。先用现有权重验证官方多关键帧和动作控制，再选择 Director 做多镜头编排。暂不为了“长视频”一次下载多套新视频模型。**

这台机器是 **单个 GB10 的 DGX Spark，ARM64、约 121.69 GiB 系统可见统一内存、4 TB NVMe**。内存容量适合运行较大模型，但 CPU、GPU、模型、系统缓存和 `/dev/shm` 共用这份内存。官方 273 GB/s 内存带宽、FP4 稀疏算力规格，不能直接换算成 H3 的生成速度。[R1]

本次实查还改变了几个判断：

- **H3 的 768p 四步 Turbo、INT8 视频 VAE、Fun ControlNet 和姿态检测模型已经下载。** 可以先测试，不需要重复下载。
- **环境名 `comfyui-ltx23` 不代表已经有 LTX 视频权重。** 本次模型目录里没有找到 LTX 权重，但 LTXVideo 插件及其 2.3/2.5 示例目录存在。
- **当前 GPU 忙于 Gaussian Splatting 项目。** 15:21 快照利用率 96%，可用内存 25.59 GiB，swap 已用约 8.84 GiB；不能依据两周前的空闲快照安排大任务。
- 已部署权重之外，还有 **33.96 GiB OpenPI 机器人模型缓存**、约 **19.18 GiB Qwen3.8-27B NVFP4 权重**及安装/编译缓存。它们应登记用途和负责人，不能凭“当前 AIGC 没调用”认定过时。
- 官方 GitHub 自带 **H3 提示词写作 skill 和完整参考文本**。此前报告中的 Ref2VA 示例省略了官方六段结构，本次给出完整模板。

| 优先级 | 实际做法 | 新增权重 | 选择理由 |
| --- | --- | ---: | --- |
| P0 | 现有 Qwen → H3 单镜 → 本地剪辑、统一声音 | 0 | 已有端到端验证，先得到可审核的成片 |
| P0 | 官方 H3 Multiframe、Fun ControlNet | 0 | 关键权重已存在，补足画面锚点和动作控制 |
| P1 | H3 Director 的 fl2v/r2v 基础示例 | 0 | 多段时间轴、公共人物素材、只重做指定段 |
| P1 | Motion Context 的上下文/裁剪节点，先做两段 | 基础 H3 路线可为 0 | 解决同一镜头的运动和声音接缝；完整示例另有实验模型 |
| P2 | 增加一种替代视频模型做同素材对照 | 见第 6 节 | 用质量、耗时和峰值内存决定是否保留 |
| P2 | 现有 Qwen3.8 VLM 写剧本/检查分镜 | 0 | 权重已在；确认服务兼容性后按阶段加载 |

以下“已部署”指文件与环境存在，“已验证”指既有项目记录中的真实推理，“候选”指本次读过资料但未在 GB10 上推理。本次没有安装插件、下载服务器模型、启动 GPU 推理、停止进程或删除资源。

## 2. 硬件、负载和运行边界

### 2.1 2026-10-27 现场快照

| 项目 | 实查结果 | 对工作流的影响 |
| --- | --- | --- |
| 架构/GPU | `aarch64`，单个 NVIDIA GB10 | 不照搬 x86_64、RTX 4090 或 B200 的 wheel/性能结论 |
| 驱动 | 580.142；`nvidia-smi` 标示 CUDA 13.0 | CUDA 标示是驱动能力，Python 包版本另见第 5 节 |
| 系统可见内存 | 130,663,596,032 bytes，即 121.69 GiB | 属于统一内存，没有第二份独立的 128 GB 显存 |
| 14:52 可用内存 | 约 79.90 GiB | 当时已有两个计算进程，GPU 95% |
| 15:21 可用内存 | 25.59 GiB | 后续四个计算进程，GPU 96%，不适合追加 H3 大任务 |
| swap | 16.00 GiB 总量；15:21 已用约 8.84 GiB | 记录任务期间是否持续增长；已有 swap 占用本身不等于正在换页 |
| 根盘/`/home` | 同一 NVMe 文件系统，总约 3.67 TiB；可用约 2.77 TiB | 盘空间目前充足；目录重复和用途遗失仍要管理 |
| `/dev/shm` | 上限 60.84 GiB，15:21 已用约 0.29 GiB | 上限是可增长额度，实际使用会消耗同一份系统内存 |
| GPU 进程定位 | `gaussian-splatting-main`、`2d-gaussian-splatting-main`；Python 位于 conda `gs`/`2dgs` | 当前算力占用来自其他项目，保留其进程与环境 |

原始清单：[dgx-resource-inventory-2026-10-27.json](dgx-resource-inventory-2026-10-27.json)。盘点各项按顺序采集，属于一段时间内的快照，不是原子采样。GPU PID 和瞬时内存可能在采集中变化。

### 2.2 哪种占用该看什么

| 占用 | 查看方法 | 解释 |
| --- | --- | --- |
| 下载规模 | 模型文件 `size_bytes`，HF LFS 元数据 | 第 3、6 节模型表均采用 GiB；1 GiB = 2³⁰ bytes |
| 实际磁盘块 | 每个目录独立运行 `du -sB1` | 稀疏文件、链接、缓存可能使它不同于下载大小 |
| 整机可用内存 | `/proc/meminfo` 的 `MemAvailable`、`free -b` | 应看 available，不能仅用 free 判断是否够用 |
| 进程/推理内存 | PyTorch allocated/reserved、进程 RSS、整机内存变化 | 要连同模型加载、条件编码、采样、VAE 解码一起测 |
| GPU 工作状态 | `nvidia-smi` 利用率和计算进程；任务耗时 | 本机 GPU 总内存栏为 `Not Supported`，进程数值不能当成独立显存池 |
| 换页压力 | `vmstat 1` 的 si/so、swap 增量 | 与延迟和 available 一起判断任务是否挤占系统 |

**模型文件相加不是峰值内存。** 量化权重可能包含 BF16 部分、量化尺度和运行时反量化；激活、参考视频、音频、KV cache、VAE、输出帧还需要内存。CPU offload 在统一内存机器上也不能凭空获得另一份容量。

建议先设一个便于执行的任务准入规则，之后用实测修正：H3 基础短镜头先要求 available ≥70 GiB；Qwen 图像 ≥40 GiB；Music 3 短音乐 ≥25 GiB；VoxCPM2 短语音 ≥12 GiB，并检查是否已有持续 GPU 任务。**这些是试验阶段的保守调度阈值，不是模型官方最低需求，也不保证任意分辨率/时长都能运行。** 15:21 快照应等待现有计算任务结束或协调运行时段。

### 2.3 内存要按阶段使用

```text
剧本/VLM（若使用）→ 导出剧本后释放
Qwen 批量做角色图和分镜 → 下载定稿图后释放
H3 逐镜生成 → 每镜下载、审核、释放不需要的中间张量
VoxCPM2 做旁白 → 结束释放
Music 3 做整片配乐 → 结束释放
本地剪辑/混音/字幕 → 导出成片
```

长视频优先拆成 4–10 秒的有效镜头。先测 864×480 附近的小镜头，再测更大尺寸；不要直接把所有片段以 float tensor 累积到最后统一解码/合成。1280×720、24 fps、float32 RGB 的 10 秒帧张量约 2.47 GiB，一分钟约 14.83 GiB，尚未包括 latent、模型和其它副本。

## 3. 服务器现在有哪些模型

### 3.1 当前工作台主力：建议保留

模型大小来自此次文件 `stat`；目录占用包含少量清单/配置。H3 两个分支共用编码器和 VAE，不会为了每个模式重复计算一套文件。

| 资源 | 现有权重规模 | 能力/入口 | 状态与保留理由 |
| --- | ---: | --- | --- |
| H3 整个模型目录 | 71.22 GiB 磁盘块 | 首尾帧、参考图、音视频、可扩展控制 | 主力；包含下表备选权重，不能整体当缓存删除 |
| H3 当前单分支文件组合 | 41.38 GiB 文件大小 | FL2VA 或 Ref2VA + 当前 Turbo + 编码器 + AV VAE | 是单分支加载所需文件集合，实际峰值内存未测 |
| Qwen Image 2.1 UC | 23.72 GiB | 文生图、参考图编辑、角色/场景/分镜 | 工作台已接；既有文生图与单图编辑验证通过 |
| MiniMax Music 3 | 13.34 GiB | 描述/歌词生成歌曲或配乐 | 已接、10/20 秒测试通过；300 秒尚未压测 |
| VoxCPM2 | 4.62 GiB 主要权重 | TTS、参考声音克隆、中文/粤语等 | 已接、短语音与合成参考录音流程通过 |

各主力模型位置与文件：

| 目录/文件（目录均以 `/home/jnu` 为根） | GiB | 当前引用 |
| --- | ---: | --- |
| `src/ComfyUI/models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 19.530 | H3 首尾帧分支 |
| `src/ComfyUI/models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 19.530 | H3 参考图分支 |
| `src/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 14.610 | 两个 H3 分支共用；不是通用聊天服务 |
| `src/ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors` | 4.850 | 当前工作台视频解码 |
| `src/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors` | 0.564 | 当前工作台音频解码 |
| `src/ComfyUI/models/loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 1.822 | 当前 FL2VA，默认 8 步 |
| `src/ComfyUI/models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` | 1.822 | 当前 Ref2VA，默认 4 步 |
| `src/ComfyUI-qwen21/models/diffusion_models/qwen-image-2.1-UC-int8_convrot.safetensors` | 6.758 | 图像主模型 |
| `src/ComfyUI-qwen21/models/text_encoders/qwen3vl_8b_bf16.safetensors` | 16.330 | 图像文本/视觉条件编码，占该套文件规模的大头 |
| `src/ComfyUI-qwen21/models/vae/qwen_image_2.1_vae_bf16.safetensors` | 0.629 | 图像 VAE |
| `src/ComfyUI-music3/models/diffusion_models/minimax_music3_dit_fp16.safetensors` | 4.577 | 音乐主模型 |
| `src/ComfyUI-music3/models/text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors` | 8.565 | 音乐编码器；与 H3 编码器不同 |
| `src/ComfyUI-music3/models/vae/minimax_music3_dav.safetensors` | 0.202 | 音乐 VAE |
| `models/aigc/VoxCPM2/model.safetensors` | 4.266 | 语音主模型 |
| `models/aigc/VoxCPM2/audiovae.pth` | 0.351 | 语音音频 VAE |

主力来源：H3 为 [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)；Qwen UC 为 [abenzerps 的量化仓库](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized)；音频来源及固定 SHA256 见 [既有模型清单](audio/models.json)。本次 H3 按文件大小和 Safetensors 头核对公开清单，没有重新读取几十 GiB 文件计算完整哈希。

### 3.2 已下载、当前简化工作流未引用：先试用，再决定去留

下列 H3 文件都位于 `/home/jnu/src/ComfyUI/models/`。

| 文件 | GiB | 能增加什么 | 管理建议 |
| --- | ---: | --- | --- |
| `loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 1.822 | FL2VA 的 768p 四步加速候选 | 列入对照；要核对对应采样器/shift，不直接替换旧八步参数 |
| `vae/minimax_h3_video_vae_int8_convrot.safetensors` | 2.618 | 更小的视频 VAE；当前官方示例也引用它 | 与 FP16 解码做同 latent 对照；通过后选择默认版本 |
| `model_patches/minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors` | 2.139 | 姿态/深度/边缘等动作或空间控制 | 与官方 Fun ControlNet 工作流配套，优先验证 |
| `checkpoints/sdpose_wholebody_fp16.safetensors` | 1.785 | 人体姿态预处理 | 和 ControlNet/检测器作为同一个功能组登记 |
| `diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors` | 0.115 | 姿态预处理的人体检测依赖 | 不凭单文件“没进表单”删除 |
| `embeddings/minimaxh3_*.safetensors`，10 个 | 合计约 0.010 | 官方预设视觉效果嵌入 | 很小；登记关键词、对应模板，避免误认作大型模型 |

ControlNet + SDPose + 检测器合计 **4.04 GiB**，现有文件已经齐全。当前上游还发布了 Union 2.0；服务器这份文件名是原 Union 版，不是 2.0。两套不要在管理表里混称为“ControlNet 最新版”。[R2]

这些资源只是“不在 `aigc/workflows.py` 的当前主路径中”，不是已经证明从来没人使用。旧的原生工作流或其他项目仍可能引用。

### 3.3 其它用途的模型：需要纳入总账

| 资源/位置 | 规模 | 能力与本次证据 | 管理判断 |
| --- | ---: | --- | --- |
| `/home/jnu/models/Qwen3.8-27B-NVFP4` | 19.18 GiB 三份权重；目录约 19.20 GiB | 本地 README 标示 Qwen3.8-27B 的 ModelOpt NVFP4 VLM；config 含图像/视频结构，基于 Qwen3.5 架构 | 可候选用于剧本、分镜描述和抽帧检查；当前工作台未接，未验证服务可用性/量化质量 |
| `/home/jnu/models/clip/ViT-L-14-336px.pt` | 0.870 GiB | CLIP 图文特征/相似度 | 属于分析资源，不负责生成视频；确认其它项目依赖 |
| `/home/jnu/models/dinov2/dinov2_vitl14_pretrain.pth` | 1.134 GiB | DINOv2 视觉特征 | 可做相似度辅助，不能直接证明人脸身份一致；确认其它项目依赖 |
| `/home/jnu/models/hf-cache/hub/models--timm--vit_base_patch32_clip_224.openai` | 0.564 GiB 主要权重 | 另一种 CLIP；HF snapshot 指向 blob | 与上面 ViT-L 是不同模型；snapshot/blob 是同一实体，不是两份副本 |
| `/home/jnu/models/layout/...PP-DocLayoutV3_safetensors` | 0.124 GiB 主要权重 | 文档版面分析 | 与长视频无直接关系；登记 OCR/文档项目负责人 |
| `/home/jnu/models/ocr/tensorrt-engines/gb10` | 两个 engine 合计约 0.020 GiB | PP-OCRv5 mobile 检测/识别，文件名含 `sm121_fp16` | GB10 的 TensorRT 派生资源；需要保留重建依据 |
| `~/.cache/openpi/.../checkpoints/pi05_droid` | 11.58 GiB 磁盘块 | OpenPI 机器人策略 checkpoint | 高占用、用途不同；确认机器人项目是否仍使用 |
| `~/.cache/openpi/.../checkpoints/pi0_aloha_sim` | 11.19 GiB 磁盘块 | 仿真相关机器人 checkpoint | 同上 |
| `~/.cache/openpi/.../checkpoints/pi0_aloha_towel` | 11.19 GiB 磁盘块 | 机器人操作相关 checkpoint | 同上 |

这里没有把 H3 的 `qwen3vl_32b_minimax_h3_*` 或 Qwen Image 的 `qwen3vl_8b_*` 算成可直接启动的通用聊天模型。它们承担生成模型条件编码，需要相应结构与投影；通用 VLM 是另一个服务角色。

现有 `/home/jnu/models/manifests/models.md` 实查只有 `# Model inventory` 标题，尚未形成有用总账。因此本次新增了 [dgx-model-registry.json](dgx-model-registry.json)，按用途分组记录状态、文件、大小、来源和待验证事项。

**盘点范围：**`/home/jnu/models`、三个 ComfyUI 的 `models`、常见 HF/OpenPI/Torch/Ollama 模型缓存。不是全用户、全挂载点扫描；未枚举训练数据、Gaussian Splatting 场景文件和其他账号目录。范围内按 inode 去重的权重/大 checkpoint 文件约 168.59 GiB；这个数不等于整机全部 AI 资源占用。

## 4. 哪些 GitHub 工作流和提示词可以直接借用

已将 **7 个上游工作流 JSON 和 2 份官方提示词规范**保存到 [本地资料包](dgx-long-video-kit/README.md)。它们保持上游原文，下载来源、固定 revision（能取得时）、内容 SHA256 和模型引用见 [sources.json](dgx-long-video-kit/sources.json)。JSON 已解析并检查引用，但尚未在这台 GB10 导入/推理。

### 4.1 优先试的具体文件

| 工作流/代码 | 抄什么 | 权重是否已有 | 接入条件 |
| --- | --- | --- | --- |
| [官方 I2V](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_i2v.json) | 原生加载、采样、AV 解码；INT8 VAE 对照 | 文件引用均已有 | H3 原生工作流入口；核对新模板辅助节点 |
| [官方 Multiframe Reference](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_multiframe_reference.json) | 人物参考 + 指定时刻的关键帧/帧序列 | 文件引用均已有 | 本地已含 `MiniMaxH3AddGuide`；导入后检查参考图和 guider 连线 |
| [官方 Fun ControlNet Union](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_minimax_h3_fun_controlnet_union.json) | 用动作视频/姿态等约束生成 | 包括 SDPose/检测器在内均已有 | 先用原 Union 版；核对预处理节点和控制条件 |
| [Director fl2v](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/4c6245e518dd8a494f3afc4f3cccd53f22d82d10/example_workflows/minimax_h3_director_fl2v.json) | 多组首尾帧、各镜时长/提示词、单段重做 | 所有引用权重已有 | 插件尚未安装；默认示例是基础 25 步，不是当前 Turbo 八步 |
| [Director r2v](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/blob/4c6245e518dd8a494f3afc4f3cccd53f22d82d10/example_workflows/minimax_h3_director_r2v.json) | 公共人物/声音、每组素材、段间引导 | 所有引用权重已有 | 必须用 Ref2VA；先关闭精修/放大/提示词增强等可选支路 |
| [Motion Context v0.7](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/blob/518c083d11d80fa5920222ad89889924ee4401c9/example_workflows/H3-Motion-Context-v0.7.json) | AV latent 上下文、Save/Load、Trim、接缝检查 | **完整示例并非全部已有** | 示例含 FastH3、Veda、额外预览/工具节点；先移植基础上下文链，不据此下载整套 |
| [LTX-2.3 单阶段](https://github.com/Lightricks/ComfyUI-LTXVideo/blob/master/example_workflows/2.3/LTX-2.3_T2V_I2V_Single_Stage_Distilled_Full.json) | 替代视频模型的单阶段对照 | 无 LTX/Gemma 权重 | 本地有插件示例；第 6 节算完整资源后再决定 |

**Director 是目前最贴近“多镜头导演台”的扩展。** 官方示例核心权重与服务器吻合，先跑基础版就不需要新增大型模型。它将多段任务、参考组、选择运行、缓存、导出整合到 UI；不满意可以只重做一段。但若下一段依赖重做段的结尾，上下文链中的下游也应重新审核。[R4]

**Motion Context 应借节点思想，不能把整个最新示例当成现有模型的一键方案。** 本次 JSON 发现 `fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors`、`minimax_h3_r2va_veda_preview_fp8.safetensors`、FastH3 LoRA，以及 `VedaSparseAttention` 等依赖。插件的基础 H3 上下文能力与这些实验采样支路应分别评估。[R5]

[H3 Relay](https://github.com/akatz-ai/h3-relay) 的审核、恢复、分阶段增强可用于后续工程设计，但其 toolkit 文档要求 ComfyUI 0.37.0+，高于当前 H3 0.36.0；FastH3/VSA 还涉及特定依赖。当前阶段不以它驱动整套环境升级。[R6]

### 4.2 提示词优先抄官方规范

[官方 `h3-prompt-writing`](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing) 是文本与参考文件形式，不需要 MiniMax 在线 API。这里复用它的格式，并未安装该 skill。官方仓库另有广告/动画等创作 skill，但部分依赖 MiniMax Hub 的 canvas/tools；可以借镜头设计，不能直接视为本工作台可执行的本地流水线。[R3]

| 模式 | 本地原文 | 格式 |
| --- | --- | --- |
| 文生/首帧/首尾帧 | [h3-base-en.txt](dgx-long-video-kit/h3-base-en.txt) | 首帧/首尾帧对齐说明（对应模式）+ 三个核心字段 |
| Ref2VA 全参考 | [h3-ref-en.txt](dgx-long-video-kit/h3-ref-en.txt) | `subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music` 六段 |

画面、镜头和声音说明用英文；对白/歌词/画面文字保留原语言。参考标签必须对应实际上传槽位，文本声明不会自动建立图像、视频或音频条件。

**首帧 I2VA 示例，8 秒：**

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action. A medium shot follows
the opening image: an adult woman with shoulder-length black hair and a teal
jacket stands to the right of an old mailbox on a rainy street. She holds a
cream envelope in her right hand. The warm shop light, mailbox position,
costume and face remain consistent with the opening image. The camera slowly
pushes forward over a short distance. During the first two seconds she looks
at the envelope. From two to six seconds she raises it and places it into the
mailbox slot in one smooth movement. In the final two seconds she lowers her
hand and pauses beside the mailbox. There is no cut and she remains silent.

overall_soundscape: Steady light rain, distant traffic, paper rustling and
one quiet click of the mailbox flap.

non_diegetic_music: N/A.
```

这里 `<Picture 1>` 的含义是官方 I2VA 对齐语句中的实际首帧；不要再加入并不存在的第二个人物参考槽。首尾帧模式按 base 原文改成 FL2VA 对齐说明，并描述连续动作如何抵达尾帧。

**Ref2VA 示例，实际接入人物图 1、场景图 2、道具图 3，8 秒：**

```text
subject_definitions:
<Subject 1> is the adult woman in <Picture 1>, preserving her face,
shoulder-length black hair, teal jacket and black trousers.
<Subject 2> is the rainy street corner in <Picture 2>, including the old
mailbox on the left and warm shop lighting.
<Subject 3> is the cream envelope in <Picture 3>.

summary:
[Reference-to-video] An eight-second live-action shot shows <Subject 1>
placing <Subject 3> into the mailbox in <Subject 2>.

retention_analysis:
<Subject 1> retains her appearance and costume throughout the shot while
her arm pose changes. <Subject 2> retains its layout and lighting.
<Subject 3> retains its shape and color until it enters the mailbox slot.

detailed_description:
[Shot 1] A medium shot places <Subject 1> on the right and the mailbox from
<Subject 2> on the left. She holds <Subject 3> in her right hand. The camera
makes a slow, short push-in. During the first two seconds she looks at the
envelope. From two to six seconds she raises it and places it into the slot
in one continuous movement. During the final two seconds she lowers her
hand and looks toward the warm shop light. Her face and clothing remain
visible and consistent. She is silent. There is no cut.

overall_soundscape:
Light rain, distant traffic, a paper rustle and one soft mailbox click.

non_diegetic_music:
N/A.
```

这些是按规范为本项目设计的提示词，未推理验证；不能当作质量保证。控制动作/关键帧时，还需要真实接入 ControlNet 或 Add Guide。保持 seed 有助于对照，不是人物身份锁。

### 4.3 最值得借的工作流思想

独立镜头以固定人物图/分镜图约束，每镜只安排一个主要动作和一个主要运镜。需要连续走路/转身时，再使用 AV latent 上下文：上一段末尾 → 下一段条件 → 生成 → 视频/音频同步 Trim → 拼接。

Director 的段间引导可从 22 帧开始试；Motion Context 也有对应视频窗口及独立音频设置。不同插件的帧/latent 索引约定不能互换。共享尾帧主要固定边界画面，不能同时固定速度和声音。续接时保存成对 AV latent，避免反复解码/再编码；每隔少量片段用新关键帧或切镜重新约束身份和场景。

目前 H3 简化表单最长目标 15 秒，24 fps，按 `17k+5` 对齐：8 秒为 192 帧；10 秒为 243 帧、10.125 秒；12 秒为 294 帧、12.250 秒；13 秒为 328 帧、13.667 秒。做一分钟视频应剪辑到目标时长，而不是靠提高单次时长。源码允许更长序列的参数范围不等于可靠的长叙事能力。

## 5. 环境和安装缓存：与权重一起管理

### 5.1 环境实查

| 功能 | 源码/模型入口 | Python 环境（`/home/jnu/venvs/`） | 目录磁盘块 |
| --- | --- | --- | ---: |
| H3 | `/home/jnu/src/ComfyUI` | `comfyui-ltx23` | 6.02 GiB |
| Qwen Image | `/home/jnu/src/ComfyUI-qwen21` | `comfyui-qwen21` | 6.08 GiB |
| Music 3 | `/home/jnu/src/ComfyUI-music3` | `comfyui-music3` | 6.08 GiB |
| VoxCPM2 | `/home/jnu/models/aigc/VoxCPM2` | `voxcpm2-aigc` | 6.92 GiB |
| 通用 VLM 候选 | Qwen3.8 权重目录 | `vllm` | 9.34 GiB |

H3/Qwen/Music/TTS 环境实查：Python 3.12.3、PyTorch 2.11.0、torchaudio 2.11.0、Transformers 5.8.0。H3 `comfy-kitchen` 0.2.35、前端包 1.52.7；Qwen/Music 为 0.2.37、前端包 1.55.17。vLLM 环境实查 PyTorch 2.13.0、vLLM 0.28.0、Transformers 5.8.0；与生成环境存在版本差异，不能合并依赖或认为模型 README 要求已满足。

H3 Git 提交仍是 `a8686f2b33fc540f137df50c0f0719953830a5e7`，版本 0.36.0（既有检查）。Qwen/Music 目录没有 `.git`，本次不能直接核验其源码提交；固定来源记录见 [Qwen 部署记录](qwen21/deployment.md)、[音频部署记录](audio/deployment.md)。把“历史记录的来源 revision”和“当前可核验 Git HEAD”分开保存。

H3 的 `custom_nodes` 中有 KJNodes 和 LTXVideo；没有本次候选的 Director、Motion Context 或 Relay。**工作台启动时使用 `--disable-all-custom-nodes`**，所以已有目录不等于工作台已启用插件。扩展应固定版本、按环境定向启用需要的节点，首先保留现有可用环境。

### 5.2 缓存和其它环境占用

| 目录 | 本次独立 `du` | 如何处理 |
| --- | ---: | --- |
| `/home/jnu/.cache/uv` | 48.81 GiB | 安装包/解包缓存；可能与环境存在硬链接，删除缓存不等于释放全部标称大小 |
| `/home/jnu/.cache/pip` | 16.19 GiB | 下载/构建缓存；确认 ARM/CUDA 包是否能重新获取，再按工具支持的方式回收 |
| `/home/jnu/.cache/vllm` | 9.20 GiB | 编译等缓存；记录环境版本，清理后可能重新编译并增加首次启动时间 |
| `/home/jnu/.cache/flashinfer` | 0.047 GiB | 小型编译缓存，无需优先处理 |
| `/home/jnu/miniconda3` | 28.05 GiB | 含当前 gs/2dgs 运行环境，属于其它项目依赖 |
| `venvs/gree-layout-hf-gpu` | 5.27 GiB | 版面分析环境；确认负责人和包链接关系 |
| `venvs/gree-ocr-trt` | 3.41 GiB | OCR/TensorRT 环境；同上 |
| `venvs/ppocrv6-transformers` | 约 0 GiB 自身目录块 | 小占用不表示包缺失；需要核对符号链接/共享环境，不能据此判定无用 |

**不能把表中目录规模直接相加成可回收空间。** 每个目录的 `du` 各自去重 hardlink；跨目录可能引用同一 inode。HF 的 snapshot → blob 也是链接关系。本次清单保留 resolved path、device、inode、link count，但没有对不同 inode 的大文件逐一算哈希，不能声称找到了全部内容重复文件。

## 6. 可能扩展的模型：能力、完整体积、GB10 适配

本节下载规模由 2026-10-27 的 HF 文件/LFS 元数据计算；是**选定文件组合**，不是把一个模型仓库全部下载。精确文件名、来源 revision、字节数及 LFS hash（公开可得时）见 [dgx-model-registry.json](dgx-model-registry.json)。运行内存与速度尚无本机新实测。

### 6.1 先优化已用模型，不新增整套生成器

| 候选 | 文件大小/新增磁盘 | 能力变化 | 决策 |
| --- | --- | --- | --- |
| H3 INT8 视频 VAE | 2.62 GiB，**已在，新增 0** | 更小解码器；质量/速度需同 latent 比较 | 优先试，替代 FP16 可减少文件集合约 2.23 GiB |
| H3 768p 四步 Turbo | 1.82 GiB，**已在，新增 0** | 另一种加速/分辨率规格 | 与当前旧八步版本对照，不只改步数 |
| H3 基础采样、不接 Turbo | **新增 0** | 比较参考跟随、动作、手脸与声音 | 基础 20–25 步对照；Turbo 强度 1 下简单加步不等于基础采样 |
| Qwen Image INT8 编码器 | 8.71 GiB 新文件；替代现有 16.33 GiB BF16 | 减少图像整套文件规模约 7.62 GiB | 小样验证加载/编辑质量；旧部署记录有兼容性线索，暂保留 BF16 回退 |
| Qwen Image NVFP4 主模型 | 3.91 GiB 新文件；现有 INT8 为 6.76 GiB | 进一步缩小主模型 | 需确认 GB10 加载器/内核；低位宽不自动保证更快 |
| Qwen NVFP4 主模型 + INT8 编码器 + 现有 VAE | 合计 13.25 GiB；试验新增约 12.62 GiB | 比现有套装小约 10.47 GiB | 两项分别通过对照后再组合；不要一开始一起替换导致无法定位变化 |
| H3 W6A8 pruned 主模型 | 单分支 14.89 GiB 新文件；比现有 19.53 小约 4.64 GiB | 较低位宽量化候选 | 先核对本地 ComfyUI/ARM 内核支持，暂不优先 |
| H3 pruned BF16 主模型 | 单分支 37.46 GiB 新文件 | 更高精度对照候选 | 内存和加载成本更高；没有证据要求替换当前 INT8 |
| H3 Fun ControlNet Union 2.0 INT8 | 4.22 GiB 新文件；现有原版 2.14 GiB | 新控制补丁分支 | 先跑已有原版，只有控制结果存在缺口才评估 2.0 |

上述额外精度文件在通过替代验收之前会与旧版同时占磁盘；表中的“减少”是以后退役旧默认的差值，不是下载试验立即释放的空间。[R2][R7]

### 6.2 替代视频生成器，只挑一套做对照

| 候选最小路线 | 所选权重/编码器/VAE 文件规模 | 能力与适用点 | GB10/管理判断 |
| --- | ---: | --- | --- |
| **Wan2.2 TI2V 5B，Comfy 打包** | **16.90 GiB**：主模型 9.31 + UMT5 FP8 6.27 + VAE 1.31 | 文生/图生视频；作为体积较小的视频候选，声音交给现有 TTS/Music | 小模型基线，值得与 H3 比任务效果；未知是否更快，也未证明长镜头更一致 |
| Wan2.2 I2V 14B，双专家 FP8 | **33.14 GiB**：high/low 各 13.31 + 同 UMT5 6.27 + Wan2.1 VAE 0.24 | 图生视频双专家路线，生态较丰富 | 必须登记两个专家；与 5B 可共享编码器，但 VAE 不是同一个版本 |
| **LTX-2.3 distilled FP8 + Gemma 3 HF 编码器** | **50.20 GiB**：完整视频 checkpoint 27.50 + Gemma 权重 22.70；另有 tokenizer/config | 同步音视频、文生/图生；IC-LoRA 可另扩动作/参考/编辑 | 有现成插件和单阶段示例；没有权重。该组合走兼容的 HF Gemma 加载路线，不能原样套不同 Comfy 单文件编码器 |
| LTX-2.3 distilled BF16 + 同 Gemma | **65.68 GiB**，不含可选上采样 | 更高精度替代视频基线 | 比 FP8 更占内存；不要连 dev/distilled 多版本一起存 |
| **LTX-2.5 distilled INT8 + 专用 INT8 Gemma 4 + AV VAE + 空间上采样器** | **36.98 GiB**：20.03 + 14.32 + 1.37 + 0.34 + 0.93 | 新音视频路线；官方当前代码有 DFR、DubIt、编辑/增强等支路 | 上采样器对应标准两阶段依赖；量化 pack 仍需 Comfy 加载器，不能原样套 BF16 CLI 示例 |
| LTX-2.5 distilled NVFP4，复用上述编码器/VAE/上采样器 | **34.39 GiB** | 更小的 2.5 主模型，文件元数据已确认 | 不能因“Blackwell”直接认定当前 GB10 运行内核支持；先验证原生量化链 |

Wan 文件与完整依赖来源：[Comfy-Org/Wan-2.2](https://huggingface.co/Comfy-Org/Wan-2.2)，官方能力资料：[Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2)。不要混用官方 Diffusers 分片包与 Comfy 单文件包：例如官方 5B 仓库三份主权重合计约 18.63 GiB，另有较大的 BF16 T5/VAE，完整下载比上表的 Comfy 组合大。[R8]

LTX 2.3 官方仓库的 checkpoint 包含 transformer、VAE 和文本投影，另外下载 Gemma。**`gemma-3-12b-it-qat-q4_0-unquantized` 虽有 q4 字样，公开分片仍合计 22.70 GiB，不能按“12B 四位约 6GB”估算。** 本地保存的上游 JSON 选择了 `comfy_gemma_3_12B_it.safetensors`、dev checkpoint 及 distilled LoRA；它不是上表的最小 distilled FP8/HF Gemma 组合，导入前应换成与所选加载器吻合的文件。[R9]

LTX 2.5 的 Gemma 4 带专用投影；H3/Qwen Image/Gemma 3 编码器不能替代。四份核心 INT8 权重合计 36.06 GiB，官方 distilled 快速开始还接了约 0.93 GiB 空间上采样器，上表已计入。使用 distilled 模型的这条路线不要求先下载 8.29 GiB 的 distilled LoRA；DFR 另需 detailing IC-LoRA，预算应单列。官方还提供约 1.35 GiB 的 Conv 视频 VAE，描述为较轻且无需额外解码依赖，可优先核对 GB10 兼容性后选一种 VAE，不同时下载两种。[R10]

**建议顺序：**先证明 H3 工作流缺在哪里。若希望小体积替代，选 Wan 5B；若关注音视频、编辑和新 LTX 管线，选一套 LTX 做独立试验。2.3 有本地已有示例，2.5 有更小量化组合但兼容性变动更大。现有 SSD 能放下多个候选，不意味着值得同时维护它们。

源码读取存在边界：LTX-2.5 HF 文件元数据可读，模型卡正文请求返回 401；本节能力依据已读取的官方 GitHub README，具体 pack 大小依据 HF API。没有把未读到的模型卡正文或作者演示当作 GB10 实测。

### 6.3 哪些扩展暂缓

FastH3/VSA、Veda、Semantic Bridge、Character Swap、复杂放大/插帧等可以针对具体失败镜头评估。现在先保留链接和问题定位，不打包下载全部模型。尤其依赖 FlashAttention、SageAttention、Triton/natten、自定义 CUDA 的路线，要核对 **aarch64 + GB10 sm_121 + 实际 Torch/CUDA 版本**；B200 的专用优化不能直接沿用到 GB10。

在线 MiniMax H3/Max 等是额外服务选择，不属于服务器权重清单。此次主线是用现有服务器做本地工作流，不展开在线 API 枚举。

## 7. 为本项目设计的最小长视频方案

### 7.1 一分钟样片，先验证三个关键镜头

以“一名主角在雨夜投递一封信”为例，控制人物、场景与动作数量。先生成中景人物、走近邮筒、投递三个镜头；通过后补全以下时间轴。每镜允许多生成少量余量，再按剪辑目标裁剪。

| 镜头 | 成片目标 | 画面/动作 | 方法 |
| --- | --- | --- | --- |
| S01 | 0–6 秒 | 街道与主角进入，交代环境 | Qwen 场景分镜 → H3 I2VA |
| S02 | 6–14 秒 | 主角看信封、犹豫 | 人物参考/中景，锁服装与道具 |
| S03 | 14–22 秒 | 手与信封特写 | 独立特写，减少全身复杂动作 |
| S04 | 22–28 秒 | 下定决心，走近邮筒 | FL2VA；必要时动作控制 |
| S05 | 28–38 秒 | 投入信封 | 与 S04 做两段续接实验，或剪辑切换 |
| S06 | 38–46 秒 | 表情放松 | 反应近景 |
| S07 | 46–54 秒 | 走向暖光 | 侧背面人物参考 |
| S08 | 54–60 秒 | 邮筒与街道收尾 | 环境镜头，统一雨声与配乐 |

先批量审核 Qwen 分镜图是否同一个人，再进入昂贵的视频阶段。整片旁白用 VoxCPM2 固定声音分段生成；Music 3 用一条统一配乐；H3 生成的雨声/脚步声选择保留和淡化。长时间持续对白与精确口型留到基本片子通过以后。

### 7.2 本地保存一个故事清单

```json
{
  "story_id": "rain-letter-001",
  "target_seconds": 60,
  "fps": 24,
  "assets": {"character": "assets/hero.png", "scene": "assets/street.png"},
  "shots": [
    {"id": "S04", "model_group": "h3_ref2va", "workflow": "ref2va_turbo4",
     "seed": 1234, "target_seconds": 8, "prompt_file": "prompts/S04.txt",
     "depends_on": null, "selected_job_id": null},
    {"id": "S05", "model_group": "h3_ref2va", "workflow": "motion_context_ref2va",
     "seed": 1235, "target_seconds": 10, "prompt_file": "prompts/S05.txt",
     "depends_on": "S04", "context_state": "state/S04-av-latent",
     "trim_prefix_frames": 22, "selected_job_id": null}
  ]
}
```

这是清单设计草案，不是已经实现的执行接口。两行示例固定使用 Ref2VA；真正接续时还需固定量化、采样器和 AV latent 配置。实际插件还需要记录音频 Trim、模型 fingerprint、尺寸和上下文格式。

每镜记录权重/插件 revision、提示词、输入文件 hash、seed、步数、耗时、峰值内存、审核结果和源任务 ID。模型总账中的 `last_verified_use` 应由这些真实任务更新，不能用文件 mtime/atime 代替。

### 7.3 会话机制要先适配

本项目的临时素材和用户目录位于 `/dev/shm/aigc-*`，切换环境、空闲回收或断开后可能删除。因此插件自己的“恢复缓存”必须回传本机：

| 插件/项目点 | 需保存什么 | 否则会怎样 |
| --- | --- | --- |
| Director | 导演包、认可片段、公共素材、段缓存清单 | 重连后时间轴看似存在但素材/缓存缺失 |
| Motion Context | 成对 AV latent、尺寸、模型版本、上下文/Trim 设置 | 只能重新编码视频或重新生成，无法可靠续接 |
| Relay | 阶段清单及其引用的媒体/状态 | 只恢复 JSON 不能恢复已认可结果 |
| 原生 history 归档 | 将内部缓存显式登记为可下载资源 | 未进入 history 描述符的文件不一定被收集 |
| 当前单任务管理 | 故事任务串行排队、逐镜确认/重做 | 不应让插件内部另开未受管理的并行 GPU 队列 |

当前普通用户设置备份的文件类型/大小限制不覆盖大型 WAV、视频和 latent，不能拿它替代故事归档。优先补“故事编号、镜头编号、复用作品库图片、必要状态下载”，再做一键编排。

## 8. 怎么防止资源过时、被忘记或一直占用

### 8.1 总账字段和状态

按“模型功能组”管理，而非仅列文件名：H3 FL/Ref 分支共享编码器/AV VAE，控制预处理依赖单独成组。模型文件、Python 环境、插件、编译缓存和任务媒体也要分开记，因为回收方式不同。

```text
resource_id / 功能 / 负责人或所属项目
状态：主力、试验、待确认用途、待退役、已移除
模型仓库 + revision + 许可证/来源
文件路径 + size_bytes + 预期 hash + 实际验证方式/日期
依赖的编码器/VAE/LoRA/插件/环境；哪些是共享文件
工作流引用；最后真实任务日期与证据
实测：任务参数、峰值内存、耗时、失败率
保留理由、下一次复核日期、替代版本、回滚方法
```

本次总账将已在当前代码中引用的设为主力，已部署未引用的 H3 扩展设为试验候选，其它项目模型设为待确认用途；未知负责人、最后使用时间、峰值内存都明确留空，没有推测填值。

### 8.2 新资源准入和退役规则

1. 新增前写明要解决的失败现象、现有替代、**完整所选文件组合**、环境/内核条件、试验磁盘额度和负责人。
2. 固定 revision，下载指定文件；分别保留来源预期 hash 和实际核验记录。不要 `snapshot_download` 整个包含全部精度/专家/LoRA 的仓库。
3. 用同一组素材和少量关键任务对照。记录质量、冷/热启动耗时、峰值内存、失败原因；通过后才能升为默认。
4. 试验资源建议 14 天后复核；一般资源每月复核，30/60 天没有真实任务记录时列为“待确认用途”。**时间阈值仅提醒复核，不自动删除。** 长期研究资源可以登记保留理由。
5. 新默认通过后，旧精度/旧模型先从默认工作流退出，检查原生工作流和其它项目引用，再决定保留回退或移除。
6. 共享编码器/VAE 按引用关系保留；HF 缓存按 snapshot/blob 关系处理，确认 hardlink/inode 后计算实际可回收块数。
7. 移除条目仍留来源、版本、验证结果和恢复方法；总账记 `removed_at`。服务退出是否释放内存也要记录，卸载磁盘文件不会结束已运行进程。

### 8.3 本机现阶段的处理建议

| 资源 | 本次建议 | 可立即断言的回收量 |
| --- | --- | --- |
| 主力 H3/Qwen/Music/VoxCPM2 | 保留；补实测和共享依赖记录 | 0 |
| 768p Turbo、INT8 VAE、ControlNet/SDPose | 进入验证名单，先获得实际用途 | 0；未确认无引用 |
| OpenPI 三套 checkpoint | 优先联系所属项目确认保留理由/复核日期 | 33.96 GiB 是目录规模，**未批准/执行回收** |
| Qwen3.8 NVFP4 | 确认 vLLM 能否运行及质量；若用作导演/审核，按阶段加载 | 0；不是忘记就删除 |
| pip/uv 安装缓存 | 确认可重建与链接关系后工具化回收 | 待核验；目录标称不是承诺回收量 |
| vLLM 编译缓存 | 结合实际服务版本与重编译成本复核 | 待核验 |
| gs/2dgs conda 环境/进程 | 当前活跃，保持；纳入共享时段协调 | 0 |

本次没有执行上述删除、停服或卸载。主要变化是把“存在但不知道用途”变成可追踪的资源记录。

### 8.4 可重复盘点

新增 [scripts/audit_dgx_resources.py](../scripts/audit_dgx_resources.py)，通过已有 SSH 执行器运行。它只读文件大小、inode/链接、Safetensors 头、环境包元数据和负载；不加载模型、不计算大型文件全量哈希、不读进程完整命令行、不写服务器文件。

Windows 项目目录中执行（输出保存到本机，避免 Windows PowerShell `>` 的 UTF-16 编码）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -c 'import subprocess,pathlib; r=subprocess.run([".venv/Scripts/python.exe","-X","utf8","scripts/dgx_exec.py","scripts/audit_dgx_resources.py"],capture_output=True); r.check_returncode(); pathlib.Path("docs/dgx-resource-inventory-latest.json").write_bytes(r.stdout)'
```

脚本目前按 `/home/jnu` 和已知目录配置；换服务器/账号时应修改扫描根目录。刷新原始盘点后，比较新增/缺失文件、更新模型总账与实际任务记录。脚本不会替你推断负责人、最后使用时间或删除决策。

## 9. 下一轮只做能回答关键问题的验证

| 实验 | 使用资源 | 要得到的结论 |
| --- | --- | --- |
| E0：空闲时基线 | 系统状态与当前主力环境 | 确认其它任务结束；记录可用内存、GPU 负载、swap 压力 |
| E1：固定人物分镜 | Qwen，4 张图 | 图片阶段的身份/服装/道具一致性；作为所有视频对照的共同输入 |
| E2：H3 采样与解码 | 当前 Turbo、基础不接 Turbo、两个已有 VAE | 同素材测耗时、峰值内存、参考跟随、手脸/声音；确定默认组合 |
| E3：关键帧/动作 | 已有 Multiframe、ControlNet/SDPose | 判断动作和空间失败能否用现有资源解决 |
| E4：导演台/接续 | Director 基础两个镜头；必要时基础 Motion Context | 单段重做、接缝前后 1 秒、AV Trim、重连恢复是否正确 |
| E5：成片 | 6–8 镜、统一旁白/配乐 | 45–60 秒片子的因果、身份、节奏和可恢复性 |
| E6：一个替代模型 | Wan 5B 或一套 LTX，单独环境 | 相同素材的任务效果/耗时/峰值内存是否值得维护新增资源 |

既有项目的单次结果可作可运行证据：Qwen 1024²/25 步约 25.1 秒；H3 320×192/2 步小样约 18–19 秒；Music 3 10/20 秒约 34.6/74.9 秒；短 TTS 约 16–18 秒。缓存、任务尺寸和包含的传输/加载阶段不同，**不能线性推算一分钟 H3 的制作成本**。[本地部署记录]

每组实验先用一个关键输入/seed，出现差异再补少量种子。达到结论后更新默认工作流和资源状态，不因为候选很多就把每套模型长期留在服务器。

## 10. 来源与证据

本次公共资料主要读取官方/社区 GitHub 原文、示例 JSON、HF API 文件元数据和模型卡；网页硬件资料用 defuddle 提取。访问日期：2026-10-27。部分 GitHub tree API 后续触发限流，改用已知 raw 文件；未取得完整内容的页面没有作为事实依据。

| 编号 | 来源 | 本次用途 |
| --- | --- | --- |
| R1 | [NVIDIA DGX Spark Hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) | 统一内存、ARM、带宽与算力规格；具体负载以 SSH 实查为准 |
| R2 | [H3 Comfy 模型仓库](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/e5eb578a89295337b8ff433a035929ce0279e0b6)；[官方模板目录](https://github.com/Comfy-Org/workflow_templates/tree/main/templates) | 文件规模、量化/控制版本、工作流引用 |
| R3 | [MiniMax 官方 H3 prompt skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing) | base/Ref 六段结构、素材标签、可移植范围 |
| R4 | [Director 固定提交](https://github.com/AIMixer/ComfyUI_MiniMaxH3_Director/tree/4c6245e518dd8a494f3afc4f3cccd53f22d82d10) | 多段 UI、示例、默认采样、缓存/导演包 |
| R5 | [Motion Context 固定提交](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context/tree/518c083d11d80fa5920222ad89889924ee4401c9) | 上下文/Trim/状态；示例额外 FastH3/Veda 依赖 |
| R6 | [Relay 固定提交](https://github.com/akatz-ai/h3-relay/tree/e2a7665b6eb8ea37a88c0de2c9c6b69a8f7c9220) | 分阶段审核/恢复、toolkit 版本条件 |
| R7 | [Qwen UC 量化仓库](https://huggingface.co/abenzerps/Qwen-Image-2.1-Uncensored-Quantized/tree/7959e658915f52274897fb573ffeda49de8dc16b)；[Turbo 官方代码](https://github.com/ModelTC/Minimax-H3-Turbo) | 可替代精度文件大小、Turbo 规格 |
| R8 | [Wan Comfy 文件](https://huggingface.co/Comfy-Org/Wan-2.2/tree/ee6f4a40737a995bf5818954cfce6d59443b0f04)；[官方 5B 包](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B)；[Wan GitHub](https://github.com/Wan-Video/Wan2.2) | 5B/14B 所选完整文件组合、模型/包装区别 |
| R9 | [LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3)；[LTX-2.3 FP8](https://huggingface.co/Lightricks/LTX-2.3-fp8)；[Gemma 3 编码器包](https://huggingface.co/google/gemma-3-12b-it-qat-q4_0-unquantized)；[LTXVideo 示例](https://github.com/Lightricks/ComfyUI-LTXVideo) | 2.3 完整大小、文本编码器与工作流要求 |
| R10 | [LTX 官方当前 README](https://github.com/Lightricks/LTX-2)；[LTX-2.5 文件清单](https://huggingface.co/Lightricks/LTX-2.5/tree/49b093a39c19169a8a81721dcc541e07e945232f) | 2.5 管线能力、专用编码器/AV VAE/量化文件；模型卡正文未读到 |

本地依据：`aigc/workflows.py`、`aigc/session.py`、`aigc/remote_service.py`、原生归档代码、[初始检查](inspection.md)、[Qwen 部署](qwen21/deployment.md)、[音频部署](audio/deployment.md)。原网页/JSON 抓取和重写前报告备份位于被 Git 忽略的 `test-results/minimax-research/`；可交接资料、原始盘点、资源总账与工作流包位于 `docs/`。
