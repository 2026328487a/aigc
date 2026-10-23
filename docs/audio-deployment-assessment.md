# DGX Spark 音乐生成与粤语声音克隆部署评估

检查日期：2026-10-16。本文保留实施前评估；后续已完成部署和真实音频推理验证，当前结果见 [音频部署记录](audio/deployment.md)。以下“尚未”等描述均指初始评估时的状态。

## 结论

可以沿用“本地网页操作 → SSH 隧道 → DGX 推理 → 结果回传本地”的使用方式。音乐推荐 MiniMax Music 3 的 ComfyUI 原生工作流；普通话／粤语声音克隆第一候选为 VoxCPM2，CosyVoice 3 和 GPT-SoVITS 可作为后续对比候选。

这里的“可以”指硬件容量、已有代码和上游支持提供了明确的部署路径，不表示已经完成生成验证。峰值内存、首次加载时间、生成速度和粤语效果需要安装后实测。

## 实机核查

| 项目 | 结果 |
| --- | --- |
| 架构 | aarch64，DGX Spark，单个 NVIDIA GB10 |
| 内存 | 系统可见 121 GiB，检查时 available 115 GiB；不是 121 GiB 独立显存 |
| GPU | 驱动 580.142，检查时利用率 3%；计算进程列表为空 |
| 存储 | /home 所在文件系统约 3.0 TB 可用 |
| 临时目录 | /dev/shm 容量约 61 GiB |
| 已验证运行时 | Python 3.12.3、PyTorch 2.11.0+cu130、CUDA 13.0、torchaudio 2.11.0 |
| CUDA 探测 | 可用，1 个设备，计算能力 12.1 |
| Qwen 环境其他依赖 | Transformers 5.8.0、NumPy 2.4.4；未安装 soundfile |
| Music 3 代码 | 两套现有 ComfyUI 均包含原生支持；Qwen 环境实际导入 MiniMaxMusic3TextEncode、EmptyMiniMaxMusic3LatentAudio 成功 |
| 音频模型文件 | 在两套 ComfyUI 的 diffusion_models、text_encoders、vae、checkpoints 目录中未找到 Music 3／VoxCPM／CosyVoice／SoVITS 匹配文件；没有扫描整个服务器 |

## 音乐生成：MiniMax Music 3

MiniMax 官方 README 仍描述双 GPU 的 SGLang-Omni 部署，但当前 SGLang-Omni 已提供 single-gpu 配置，默认也会根据可见 GPU 数选择同卡放置。因此不能把“双 GPU”视为模型的绝对要求。

本项目更合适的路线是已有 ComfyUI 原生支持。上游提供 Text to Music (MiniMax Music 3) 蓝图，使用 UNETLoader、CLIPLoader、MiniMaxMusic3TextEncode、KSampler、音频 VAE 解码等节点，可接音频保存节点。本地已有对应模型实现，后续应以这份已安装代码的节点参数为准校验蓝图，避免直接假设最新蓝图与旧版本全部一致。

建议先按上游蓝图选择以下三份文件，避免把所有精度版本都下载：

| 文件 | 字节数 | 十进制 GB |
| --- | ---: | ---: |
| diffusion_models/minimax_music3_dit_fp16.safetensors | 4914197682 | 4.91 |
| text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors | 9196611886 | 9.20 |
| vae/minimax_music3_dav.safetensors | 216696128 | 0.22 |
| 合计 | 14327505696 | 14.33 |

模型仓库：Comfy-Org/MiniMax-Music-3；本次查询 revision：9e364ccd290a401561091fb441ed19cd7de92550。元数据通过 hf-mirror.com 获取，正式下载时应固定版本并校验大小、哈希和 Safetensors 头。文件大小不是运行峰值内存。

本地界面可提供音乐描述、歌词、最长时长、种子，完成后播放／下载 WAV 并保存本地历史。先验证 10–20 秒短片段，再测试 60 秒和更长歌曲；最长时长是生成上限，模型可能提前结束，不承诺精准时长或实时生成。

使用 MiniMax-Music3 Community License，开放权重但有商业展示、收入门槛及其他条款；沿用原模型许可证。

## 语音与粤语声音克隆

### 第一候选：VoxCPM2

- 官方明确列出粤语，支持参考录音克隆、参考录音加转写文本的克隆，以及语速／情绪控制。
- 2B 模型，48 kHz 输出；代码和权重为 Apache-2.0。
- 主要文件 model.safetensors 为 4580080592 字节，audiovae.pth 为 376951122 字节，合计约 4.96 GB，另有少量配置和 tokenizer。
- 仓库 openbmb/VoxCPM2；本次查询 revision：32279effe8c19989596f05d353d1447f51d9e915。
- 上游声明 Python >=3.10、torch/torchaudio >=2.5。现有基础运行时达到这些要求，但 ARM64 上 torchcodec、文本归一化、音频库等仍需安装和导入验证。
- 采用独立源码和虚拟环境，先关闭可选降噪、时间戳和额外加速，只验证最小推理路径。不能仅凭依赖下限就认为所有最新版本组合兼容。

### 其他候选

| 模型 | 适合用途 | Spark 部署关注点 |
| --- | --- | --- |
| CosyVoice 3 | 粤语、流式合成、参考音色 | 原 requirements 固定 torch 2.3.1、CUDA 12 系列及旧 ONNX Runtime，不能直接覆盖现有 Blackwell 环境；需独立适配 |
| GPT-SoVITS | 固定角色音色、少量录音微调、粤语 | requirements 已区分 ARM 的 CPU ONNX Runtime；仍需验证粤语前端等依赖，Transformers <5 与现有环境不同 |
| MiniMax Speech API | 免部署的语音和音色复刻 | 属于远程 API 方案，不满足本次“模型部署在自己的 DGX”这一选择 |

本地语音界面可提供待朗读文本、普通话／粤语、参考录音上传及试听、可选参考录音转写、生成和下载。具体语言控制应按所选模型实际接口实现，不能假定所有模型都有相同 language 参数。

## 与现有工作台的接入

已有可复用部分：本地 FastAPI、SSH 连接／隧道、一次性令牌、远端会话监督、单任务限制、下载哈希校验、本地任务历史、取消和空闲释放。

需要完成的开发：

1. Music 3 增加独立模型配置、工作流构建和节点／权重检查。
2. TTS 增加独立 Python 推理服务及与本地任务接口的适配；用户仍在同一网页操作，无需直接使用另一个后台 UI。
3. 将当前只允许 H3／Qwen 的模型、模式和图片上传参数扩展为音频任务类型。
4. 增加参考音频校验、音频上传、WAV 下载／解码验证、音频播放控件和历史媒体识别。
5. 扩展目前仅允许 result.mp4／result.png 的下载路径规则，以及远端任务文件清理范围。
6. 保留一次运行一个模型任务的资源策略，切换模型释放工作台自己的上一实例；首次加载时间与实际生成时间分别记录。

模型及独立环境长期保存在 DGX；参考音频、歌词和生成结果使用会话临时目录，完成回传并校验后清理，长期作品与声音素材保存在本地。沿用现有工作台的临时数据边界，不把它描述成服务器层面绝对不落盘。

建议验收顺序：Music 3 短音频 → VoxCPM2 普通话 → 同一份参考录音的粤语克隆 → 网页上传／生成／播放／下载 → 取消与断开回收 → 现有图片、视频流程回归。音质评价分别检查内容准确性、粤语发音和音色相似度。

## 来源

- 本项目 README.md、aigc/session.py、aigc/workflows.py、aigc/app.py。
- [MiniMax Music 3](https://github.com/MiniMax-AI/MiniMax-Music3)
- [ComfyUI Music 3 原生节点](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_music.py)
- [ComfyUI 音乐工作流](https://github.com/Comfy-Org/ComfyUI/blob/master/blueprints/Text%20to%20Music%20%28MiniMax%20Music%203%29.json)
- [Comfy-Org 模型包](https://huggingface.co/Comfy-Org/MiniMax-Music-3)
- [SGLang-Omni Music 3 单／双卡配置](https://github.com/sgl-project/sglang-omni/blob/main/sglang_omni/models/minimax_music3/config.py)
- [VoxCPM2 代码](https://github.com/OpenBMB/VoxCPM)、[模型卡](https://huggingface.co/openbmb/VoxCPM2)
- [CosyVoice 依赖](https://github.com/FunAudioLLM/CosyVoice/blob/main/requirements.txt)
- [GPT-SoVITS 依赖](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/requirements.txt)
