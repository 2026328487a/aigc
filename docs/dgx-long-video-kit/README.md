# DGX 长视频工作流与提示词资料包

整理日期：2026-10-27。此包保存上游原始 JSON/提示词文本；未安装插件、执行生成或修改服务器环境。来源、固定 revision（可取得时）、内容 SHA256、模型与节点引用见 [sources.json](sources.json)。`source_revision: null` 表示从上游当时的 main/master 下载，使用本地文件的 SHA256 固定内容，不能当作已核验 Git commit。

配套分析：[DGX 工作流与资源调查](../minimax-long-video-research-2026-10-26.md)；[服务器原始盘点](../dgx-resource-inventory-2026-10-27.json)；[模型资源总账](../dgx-model-registry.json)。

## 使用顺序

| 文件 | 用途 | 当前服务器的权重条件 | 使用前处理 |
| --- | --- | --- | --- |
| [video_minimax_h3_i2v.json](video_minimax_h3_i2v.json) | 官方首帧、Turbo、INT8 VAE 基线 | 引用权重均已在 H3 models 目录 | 在工作台 H3 原生入口导入，选自己的首帧，核对节点版本/有效尺寸时长 |
| [video_minimax_h3_multiframe_reference.json](video_minimax_h3_multiframe_reference.json) | 人物参考 + 指定时刻关键帧 | 引用权重均已在 | 重新绑定输入图片；检查 Add Guide、图像槽和 guider 连线 |
| [video_minimax_h3_fun_controlnet_union.json](video_minimax_h3_fun_controlnet_union.json) | 原 Union 控制、SDPose/检测器 | 包括预处理模型在内均已在 | 上传自己的控制素材，核对预处理节点；不是 Union 2.0 工作流 |
| [minimax_h3_director_fl2v.json](minimax_h3_director_fl2v.json) | 多组首尾帧导演台 | 核心权重均已在 | **需要 Director 插件且会话启用它**；上游默认基础 25 步，未接当前 Turbo |
| [minimax_h3_director_r2v.json](minimax_h3_director_r2v.json) | 公共人物参考、素材组、分段任务 | 核心权重均已在 | 同上；Ref2VA 模型，先做两组，不启用可选精修/增强支路 |
| [H3-Motion-Context-v0.7.json](H3-Motion-Context-v0.7.json) | 研究 AV latent 接续、Trim、保存/恢复 | **完整示例另含未部署的 FastH3/Veda 模型** | 需要插件和其它工具节点；提取基础上下文链，再接现有模型，重新核对采样参数；不要原样 Queue |
| [LTX-2.3_T2V_I2V_Single_Stage_Distilled_Full.json](LTX-2.3_T2V_I2V_Single_Stage_Distilled_Full.json) | LTX 替代路线的上游单阶段样例 | **缺 LTX/Gemma/LoRA 权重** | 当前 JSON 选 dev + distilled LoRA + Comfy 单文件 Gemma，需按选定完整模型组合和加载器修改；不能按最小 FP8 预算原样运行 |

这些是 ComfyUI **画布格式**，不是直接向 `/prompt` POST 的 API JSON。输入素材名、模型子目录、上游 UUID 子图及辅助节点需在导入后核对。图里的示例图/视频不会随本包自动提供。

H3 当前实例关闭全部 custom nodes；服务器磁盘上已有 LTXVideo/KJNodes 也不代表本工作台已启用。官方 H3 核心节点已有代码基础，但新模板的全部节点兼容性尚未通过本机 `/object_info` 和推理验收。

## 官方提示词原文

- [h3-base-en.txt](h3-base-en.txt)：T2VA/I2VA/FL2VA/L2VA 对齐说明与三个字段。
- [h3-ref-en.txt](h3-ref-en.txt)：Ref2VA 六段结构、Subject/Picture/Video/Audio 标签、对白和声音规范。

原文来自 [MiniMax 官方 h3-prompt-writing](https://github.com/MiniMax-AI/MiniMax-H3/tree/d21241f0a4b3acbb34c97dae47fa417b7065e438/skills/h3-prompt-writing)，本次作为研究资料保存，没有安装为本地 skill。先确定输入模式再选择规范；素材标签必须对应实际条件输入。配套报告第 4 节提供适合本项目的首帧和参考图示例。

## 推荐首次试验

等待共享 GPU 任务结束、确认 available 内存，再以一名人物、一个动作、短镜头起步。先试官方 I2V/Multiframe/控制版；选定结果后再试 Director 或基础上下文链。每次记录模型文件/插件版本、耗时、峰值内存与失败原因。

上游作者关于速度、一致性和示例时长的描述尚未在此 GB10 复现。当前包验证包括下载成功、JSON 解析、文件引用和内容 SHA256；运行兼容性、人物一致性、接缝及跨会话恢复仍需真实任务验证。
