# H3 HMNSFW LoRA 首帧图生视频工作流

另有三份保留相同输入参数的加速 LoRA 组合，供手动对比，见 [加速工作流对比说明](hmnsfw-acceleration-comparison.md)。

2026-11-21 已下载并安装 `HMNSFW-Extreme-Realism.safetensors`，并使用 DGX 当前安装的 ComfyUI 节点完成 CPU 参数与连线校验。没有启动 ComfyUI 服务、加载模型或提交视频生成。图像输入用临时占位文件完成校验，实际使用时上传自己的首帧图。

## 打开与使用

1. 在 AIGC 工作台选择 **MiniMax H3 · 视频**，找到 **HMNSFW LoRA · 首帧视频**。
2. 点击 **一键打开** 加载原生画布；也可以点击 **下载模板**，将 [原生画布工作流](workflows/h3-hmnsfw-i2v.workflow.json) 拖入 ComfyUI。
3. 在 **上传首帧图** 节点上传图片；在 **等比尺寸 · 自动连接 H3** 节点选择最长边、缩放倍数或手动宽高。节点会显示原图、等比缩放后的图片和生成画布的尺寸，自动传给 H3。
4. 在 **设置提示词与帧数 · 尺寸自动读取** 节点填写目标画面和动作描述。
5. 按需调整 LoRA 强度和随机种子，再手动点击运行。

[API 工作流](workflows/h3-hmnsfw-i2v.api.json) 用于程序化加载。它的图片文件名为 `reference.png`，需要在使用前替换成已上传图片的实际文件名。

## 已配置参数

| 参数 | 值 |
|---|---|
| 主模型 | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` |
| LoRA | `HMNSFW-Extreme-Realism.safetensors`，强度 0.7 |
| 文本编码器 | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| 视频 VAE | `minimax_h3_video_vae_fp16.safetensors` |
| 音频 VAE | `minimax_h3_audio_vae_fp32.safetensors` |
| 采样 | Euler，Simple，18 步，denoise 1.0 |
| Shift | 保留 H3 原生 video shift 12、audio shift 3 |
| 尺寸与时长 | 最长边 512，另一边按图片比例自动计算；124 帧，24 fps，约 5.17 秒 |
| 随机种子 | 42，固定 |
| Turbo / LightX2V | 未叠加 |
| 输出 | H.264 MP4，CRF 20，保留生成音轨 |

Shift 12 已由当前 H3 模型加载器设置，因此没有另加会替换 H3 音视频采样配置的通用采样补丁。模型输出同时供视频与音频解码使用。

2026-11-23 加入自动等比尺寸。需要对齐 H3 的 32 像素倍数时，节点在两侧或上下复制少量边缘像素，保留完整画面。手动宽高模式也会先等比缩放到指定画布内，再补边。例如 1080×1920 的图片设置最长边 1024，输出为 576×1024；设置 768 时，图片为 432×768，生成画布为 448×768。放大使用普通图像插值，不增加原图中没有的细节。

最长边及最终画布上限为 1536。程序调用 `build_h3_extended` 时，省略宽高使用自动等比；显式传入宽高切换为手动画布模式。对应 API 图中的尺寸设置在节点 `23`，H3 节点 `7` 通过连线读取结果。

工作台在每次连接 H3 时自动上传最新版辅助节点和尺寸预览脚本，保存在该会话的临时目录并加载。断开后随会话清理，下一次连接重新同步。更新项目后重新启动本地工作台，再打开模板即可使用，无需手动安装额外插件。

## 权重位置与来源

DGX 安装位置：

```text
/home/jnu/models/aigc/h3-extended/models/loras/HMNSFW-Extreme-Realism.safetensors
```

现有 H3 扩展模型路径配置已经包含此目录。工作流和本说明另存于 DGX 的 `/home/jnu/models/aigc/h3-extended/workflows/`，下一次启动 H3 时会识别新的 LoRA。

- 来源：[Sentinel7/h3 对应目录](https://huggingface.co/Sentinel7/h3/tree/9c8390e0b65a07c115c6744739821d7cbd1763a8/2834417/3446189)。
- 固定仓库提交：`9c8390e0b65a07c115c6744739821d7cbd1763a8`。
- 文件大小：620,977,128 字节。
- SHA-256：`55aa39239ea4b408dee601cb0fa49157ec13fe2a54eccbb2fb725b124e43c1b5`。
- 本机下载与 DGX 上传分别完成 SHA-256 校验。

这是针对特定成人内容与动作训练的 LoRA。默认提示词采用普通人物动作，便于后续自行填写目标描述；具体内容响应和画面质量尚未做生成实测。

## 校验记录

- 210 对 LoRA 张量与 DGX FL2VA、Ref2VA 两个基座的目标层尺寸全部匹配。
- 原生画布与 API 图现包含 17 个节点、22 条连线，类型匹配、无循环依赖。
- 2026-11-21 的原版通过 DGX 安装版本的 `execution.validate_prompt` 校验；保存节点为 `16`，节点错误为空。
- 新 LoRA 已出现在当前节点的可选文件列表。
- CPU 校验期间 CUDA 未初始化，未使用 `PromptExecutor` 或生成提交接口。
- 检查时 DGX 存在三个 GPU 训练进程，GPU 利用率为 96%；本次仅下载、上传和 CPU 校验。

2026-11-23 的等比尺寸更新已通过本地几何计算、自动连线、模板下载及启动包检查。DGX 的像素处理与四份工作流 CPU 校验使用 `python scripts/validate_h3_image_size.py`，其报告保存在 `test-results/h3-image-size/backend-validation.json`。该校验不会加载模型或提交视频生成。
