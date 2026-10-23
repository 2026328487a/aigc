# DGX Spark 音乐与语音部署

部署日期：2026-10-16。本地入口保持为 http://127.0.0.1:8765 。

## 已实现功能

- MiniMax Music 3：音乐描述、可选歌词、生成时长上限、种子、采样步数，生成 WAV。
- VoxCPM2：文字转语音、参考录音声音克隆，普通话／粤语／英语风格提示，可选录音原文和声音风格。
- 浏览器上传和试听参考录音，播放与下载生成音频，本地历史、清空预览、逐条删除。
- 参考录音在本机解码、去除元数据、转为单声道 24 kHz PCM；限制为 3–30 秒、32 MB。
- 音频下载核对文件大小和 SHA-256，并完整读取 WAV 帧检查截断；结果长期保存在本地。
- 远端任务输入输出清理，单任务运行、取消、模型切换、断开及空闲回收。

## 固定模型与运行环境

实机为 ARM64 DGX Spark，单个 GB10，系统可见统一内存 121 GiB；部署前 available 115 GiB。没有额外租用算力或调用付费生成 API。

| 用途 | 源码 | Python 环境 | 模型 |
| --- | --- | --- | --- |
| 音乐 | `/home/jnu/src/ComfyUI-music3` | `/home/jnu/venvs/comfyui-music3` | 源码目录的 `models` |
| 语音 | `/home/jnu/src/VoxCPM-aigc` | `/home/jnu/venvs/voxcpm2-aigc` | `/home/jnu/models/aigc/VoxCPM2` |

音乐源码来自已验证的 Qwen ComfyUI 安装副本，新增模型与运行环境单独保存。现有 H3、Qwen 的源码、模型和依赖没有被升级。语音源码固定为 OpenBMB/VoxCPM 的 `cce58a5b59303c9bd63d12f23afe6a49a4a80c59`。GitHub 源码归档不含版本元数据，安装时通过 setuptools-scm 的官方环境变量指定本地构建版本 `0.0.0+gcce58a5b5930`。

- 公共基础：Python 3.12.3，PyTorch 2.11.0+cu130，CUDA 13.0，torchaudio 2.11.0。
- Transformers 5.8.0，NumPy 2.4.4。
- 语音新增：torchcodec 0.11.0、soundfile 0.14.0、librosa 1.0.0 等。各新增环境的 `AIGC-AUDIO-AUDIT.txt` 保存完整 pip freeze。
- 两个新增环境继承既有 `nvidia-cusparselt-cu13 0.8.0` 的 ARM wheel 平台标签告警，`pip check` 会报告此项。没有修改包元数据；CUDA 推理和结果下载已实际通过。

音乐采用 ComfyUI 原生 MiniMaxMusic3TextEncode、EmptyMiniMaxMusic3LatentAudio、KSampler、VAEDecodeAudioTiled 等节点，以及仅在工作台会话进程内注册的 `AIGCSaveAudioWAV`。没有在其他 ComfyUI 源码中添加插件。

音乐三份权重合计 14,327,505,696 字节，使用 FP16 DiT、INT8 ConvRot 文本编码器和 DAV。Comfy-Org/MiniMax-Music-3 revision 为 `9e364ccd290a401561091fb441ed19cd7de92550`。

VoxCPM2 主要两份权重合计 4,957,031,714 字节，模型 revision 为 `32279effe8c19989596f05d353d1447f51d9e915`。所有模型文件通过 hf-mirror.com 下载，固定提交并核对 LFS SHA-256；小文件核对 Git blob SHA-1；Safetensors 另检查文件头。完整清单见 [models.json](models.json)，服务器也保存 `AIGC-MANIFEST.json`。

Music 3 使用自定义社区许可证，原许可证保存于 `/home/jnu/src/ComfyUI-music3/licenses/minimax-music3/LICENSE`。VoxCPM2 为 Apache-2.0，源码带原 LICENSE。

## 推理与会话

音乐复用 ComfyUI HTTP／WebSocket 任务方式。此 ComfyUI 实现按 44.1 kHz 计算 latent 长度并输出 44.1 kHz 立体声；这与 MiniMax 原仓库 README 的 32 kHz 描述不同，工作台如实显示实际 WAV 采样率。

语音使用会话级 aiohttp 服务。每个任务启动独立 VoxCPM2 子进程，生成结束释放模型；取消会终止该进程并等待退出。选择这一方式是为了保持取消与临时录音清理可靠，因此每次生成耗时包含模型加载。

语音首先采用原生 PyTorch 路径，关闭可选降噪、torch.compile 和自动文本归一化，不额外下载这些辅助模型。输出为 48 kHz 单声道 PCM WAV。语言选择转为模型支持的括号风格描述，不是独立的硬性语言解码参数；粤语建议输入自然粤语，并使用粤语参考录音。

填写录音原文时使用 `prompt_wav_path` + `prompt_text`；不填写时使用 `reference_wav_path`。原文应准确对应录音。普通文字转语音不需要参考录音。

所有接口沿用一次性令牌和 SSH 回环隧道。素材、生成结果、任务记录处于会话专属 `/dev/shm/aigc-*`，回传校验后删除本任务输入输出和历史；断开回收服务与会话目录。共享系统的 swap 和管理员访问边界与既有工作台相同。

## 验证记录

下表为一次实测，包含推理、传输、校验和清理，不含连接启动；不是保证耗时。

| 流程 | 结果 | 全流程耗时 | 本地任务 ID |
| --- | --- | ---: | --- |
| 音乐小样，10 秒上限、30 步 | 9.996 秒，44.1 kHz 立体声 | 34.6 秒 | `1b3128e2b4e34f4f8373e8c078ea6799` |
| 网页输入原创歌词，20 秒上限、30 步 | 19.992 秒，播放、下载与清理通过 | 74.9 秒 | `ee26c82bc86648a896fbbd3a11a17f28` |
| 普通话 TTS | 6.4 秒，48 kHz 单声道 | 18.4 秒 | `681841cdde2c42df9ab40ff1a68cad22` |
| 粤语 TTS | 5.76 秒，48 kHz 单声道 | 16.3 秒 | `5ea1f16eaf5b466ba0d36e0093e5ffbb` |
| 网页上传粤语录音与原文，克隆新内容 | 7.52 秒，48 kHz 单声道 | 18.4 秒 | `6b044e579721416a982bd509a331bade` |
| 粤语克隆，不填原文 | 4.96 秒，48 kHz 单声道 | 16.3 秒 | `76cd00f08b1847eebbc88f0c4aa9aebf` |
| 语音生成中取消 | 取消、进程退出和任务文件清理通过 | 10.0 秒时取消 | `79ee280cb8e448d890106b5a41318c37` |
| 音乐采样中取消 | 取消与任务文件清理通过 | 24.1 秒时取消 | `2c26d3fd639e45aea68981bad38567e3` |
| 切回 Qwen：512×512、4 步 | PNG 与清理通过 | 14.3 秒 | `ff951599d55e430483e86af4185f4aaa` |
| 切回 H3：320×192、2 步 | 1.625 秒 H.264 视频，含音轨，清理通过 | 18.4 秒 | `3903028ccb354c7db4981f8e19c6ec1c` |

声音克隆验证使用模型生成的粤语参考音频，没有使用用户或第三方真人录音。已验证声音克隆流程、音频非空、可播放和下载完整性；个人音色相似度及口音自然程度仍需用实际录音试听评估。

31 项本地测试通过，覆盖既有图片／视频／历史行为，以及参考录音规范化、超长／过短／损坏录音拒绝、WAV 截断检测、克隆必须上传录音、普通 TTS 不接受录音等。Node.js 语法检查通过。真实 Edge 检查四种模型表单、390px 布局、参考音频上传／试听、生成音频播放、下载哈希和清空预览后的暂停／刷新行为。

本次主要覆盖短音频。300 秒歌曲、长篇语音、批量并发、全部语言／风格组合尚未压测；工作台保持单任务。详细结果在 `test-results/audio-*.json`、`audio-ui-*.json`、`music-ui-*.json` 中。

验收结束已断开工作台的 DGX 连接；检查的四个音频会话目录均已删除，GPU 计算进程列表为空。本地工作台继续运行，等待选择模型并连接。清理结果见 `test-results/audio-final-cleanup.json`。

## 维护脚本

- `scripts/deploy_audio.py`：独立目录部署、固定权重下载和校验，通过 `scripts/dgx_exec.py` 使用现有 `dgx` SSH 别名运行。
- `scripts/audio_smoke.py`：真实音乐／语音／克隆与取消检查。
- `scripts/audio_ui_smoke.py`：真实网页粤语克隆、下载和播放检查，需要提供合成参考 WAV 路径。
- `scripts/music_ui_smoke.py`：从网页提交原创歌词、生成短音乐并检查播放和下载。

启动仍使用根目录 `start.cmd`；本地 Python 代码变更后需要重启工作台。正常使用不需要登录 DGX 手工启动服务。
