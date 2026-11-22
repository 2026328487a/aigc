# HMNSFW 加速 LoRA 组合对比

2026-11-22 新增三份加速组合，保留已有的 18 步基准版。四份都采用同一个 H3 FL2VA 主模型、首帧图片入口、默认提示词与随机种子，供用户手动测试。

在工作台选择 **MiniMax H3 · 视频**，可以找到下表四个选项。点击 **一键打开** 加载原生画布；**下载模板** 获取带完整连线和布局的 JSON 文件。

| 工作台选项 | 原生画布 | 采样器 / 步数 | 视频 / 音频 Shift |
|---|---|---|---|
| HMNSFW LoRA · 首帧视频 | [18 步基准版](workflows/h3-hmnsfw-i2v.workflow.json) | Euler / 18 | 12 / 3，基座默认 |
| HMNSFW + Turbo · 8 步 | [Turbo 8 步组合](workflows/h3-hmnsfw-turbo8.workflow.json) | res_multistep / 8 | 12 / 3 |
| HMNSFW + LightX2V · 4 步 | [LightX2V 4 步组合](workflows/h3-hmnsfw-lightx4.workflow.json) | res_multistep / 4 | 12 / 3 |
| HMNSFW + Turbo 768p · 4 步 | [Turbo 768p 4 步组合](workflows/h3-hmnsfw-turbo4-768p.workflow.json) | res_multistep / 4 | **6 / 3** |

对应的 `.api.json` 文件也在同一目录，供程序化使用。

## 相同的测试条件

- 首帧图片预设文件名：`reference.png`，使用前上传自己的图片。
- HMNSFW LoRA 强度：**0.7**；三份加速组合的加速 LoRA 强度均为 **1.0**。
- 随机种子：**42，固定**。
- 默认尺寸：**最长边 512，另一边按输入图片比例计算**；124 帧，24 fps，约 5.17 秒。
- 主模型：`minimax_h3_fl2va_pruned_int8_convrot.safetensors`。
- 文本编码器、FP16 视频 VAE、FP32 音频 VAE 与基准版一致。
- 调度器：Simple，denoise 1.0；输出为 H.264 MP4，CRF 20，包含生成音轨。

三份组合采用“主模型 → 加速 LoRA → HMNSFW LoRA → H3 音视频 Shift → 采样”的连接。最终设置好 Shift 的模型同时连接到引导器与调度器。

**768p 指这份加速权重的训练规格。** 为方便初次快速对比，它的工作流也默认使用最长边 512。正式比较 768p 画质时，将四份工作流的尺寸节点统一切换到 **手动宽高 1344×768**，保持图片、提示词、种子和帧数相同；竖图可以统一用 768×1344。图片会等比缩放并补边到画布。低分辨率的结果不能代表这份权重在训练分辨率下的表现。

2026-11-23 四份模板均加入 **等比尺寸 · 自动连接 H3** 节点。上传图片后选择“最长边”（如 512、768、1024）或“缩放倍数”（如 0.5、2），自动输出图片与宽高，不需要在 H3 节点重复填写。尺寸预览列出原图、缩放后和实际生成画布；宽高通过少量边缘填充对齐到 32 的倍数。更换图片后预览自动更新。详见 [尺寸设置说明](hmnsfw-lora.md)。

## 使用的已安装权重

| 方案 | 加速 LoRA 文件名 |
|---|---|
| Turbo 8 步 | `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` |
| LightX2V 4 步压缩版 | `minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors` |
| Turbo 768p 4 步 | `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` |

所有文件都已在 DGX，沿用现有模型路径配置。Turbo / LightX2V 标签沿用已安装文件的命名，本次对比的是不同版本与压缩形式的 H3 加速权重。视频 Shift 6/12 和音频 Shift 3 根据 [H3 Turbo 上游规格](https://github.com/ModelTC/Minimax-H3-Turbo#model-specs) 配置，使用已安装的 `MiniMaxH3SigmaShift` 节点保留 H3 音视频采样逻辑。

## 手动测试建议

1. 给四份工作流上传同一张首帧图片，并填写同一段目标提示词。
2. 保持 HMNSFW 强度 0.7、随机种子 42、相同尺寸与帧数，先分别运行一遍。
3. 对比耗时、人物保持、动作连贯、细节与音轨。比较耗时时依次运行，并保持其他 GPU 负载相近。
4. 选出更合适的加速版本后，再调整内容 LoRA 强度；这样便于判断变化来自哪个参数。

组合后的画面及声音效果由用户实测。三份原生画布现均为 19 个节点、24 条连线。源码中的 [生成脚本](../../scripts/prepare_hmnsfw_acceleration.py) 可以从基准版重新生成这四份 API 图和原生画布。

2026-11-22 的四份工作流均通过 DGX 节点的 `execution.validate_prompt` CPU 校验。HMNSFW 的 210 对张量与三份加速 LoRA 各自的 208 对张量全部匹配 FL2VA 基座目标层，校验期间 CUDA 未初始化。2026-11-23 的等比尺寸更新已通过本地尺寸计算、画布连线、四个工作台入口与原生画布下载检查；DGX 上的新尺寸节点验证使用 [CPU 校验脚本](../../scripts/validate_h3_image_size.py)。
