# ComfyUI 视频服装替换工作流

已将此前的命令行流程移入 ComfyUI 自定义节点，并保留可修改的原生 H3 模型节点。一个队列任务完成整段视频的预处理、换装参考、分段生成、服装区域合成与原音轨保存。

## 直接使用

1. 打开 [项目工作台](http://127.0.0.1:8766/)，选择 **MiniMax H3 · 视频**。
2. 在 **视频服装替换 · 完整流程** 模板点击“一键打开”。
3. 在左侧 **加载视频** 节点上传源 MP4，在 **服装提示词 / Outfit** 节点写衣物描述。
4. 点击 ComfyUI“运行”。遮罩与参考图会显示预览，完成后保存完整视频和验收 JSON，并由工作台自动归档到本地作品库。

可直接下载或拖入 [原生画布](h3-extended/workflows/h3-clothing-change.workflow.json)。[API 图](h3-extended/workflows/h3-clothing-change.api.json) 提供同一套节点连接，便于程序提交。

例如深绿色套装：

```text
a dark green oversized cotton sweatshirt with loose long sleeves, matching dark green cotton shorts of the original length, matte fabric, no logos or lettering
```

描述颜色、材质、袖长、下装和图案即可。脸、动作、可见皮肤与背景保留约束由节点自动加入。改提示词会重新制作参考和生成衣物，并自动使用独立工作目录，不需要编辑 `STYLES` 或手动换输出目录。

## 工作流内容

| 阶段 | 节点与作用 |
|---|---|
| 输入 | 原生 `LoadVideo`，支持界面上传；`AIGCClothingPrompt` 输出服装描述 |
| 工作帧 | `AIGCClothingPrepare` 按原帧索引解码，保留源视频尺寸、帧率与时间戳 |
| 姿态 | 原生 SDPose 模型加载、关键点提取及姿态绘制 |
| 遮罩 | `AIGCClothingMasks`，衣物修改区与脸、手、腿保护；输出绿色覆盖预览 |
| 参考 | `AIGCClothingReference`，自动调用已部署的独立 Qwen Image 2.1 环境制作参考，随后回贴原脸与背景 |
| 视频 | 原生 Ref2VA、匹配的 LightX2V、人物替换 LoRA、编码器、VAE、Union 2.0 模型加载节点 |
| 全片生成 | `AIGCClothingGenerate` 使用 ComfyUI GraphBuilder 展开每个窗口的原生 H3 条件、姿态控制、采样、解码与重叠衔接 |
| 合成与保存 | 内部 `AIGCClothingComposite` 仅合成衣物区，复制完整原音轨；`AIGCSaveClothingVideo` 保存 MP4、校验信息和视频预览 |

分段与合成在 ComfyUI 执行器内进行，节点不会向当前服务器再次提交任务，也不会调用本地 Windows 命令行流程。Qwen 使用独立进程加载项目现有 Qwen 环境，因为 H3 核心源码没有 Qwen 2.1 节点；进程结束后释放模型。执行中点击 ComfyUI 停止会中断预处理、采样或参考进程。

## 默认参数

- 工作分辨率：长边 1024，按源视频比例对齐 32；测试竖屏为 576×1024。
- Qwen 参考：25 步，seed 42。
- H3：4 步，原视频帧率；动作参考缩至 0.5 倍。
- 分段：124 帧、22 帧重叠；H3 所需补帧由最后一帧补齐，成片裁回实际帧数。
- Union：只控制姿态，强度 0.65；不连接局部重绘 `mask/source_video` 分支。
- 成片：恢复源尺寸、原帧数和帧率，原 AAC 通过独立流复制封装。常规 H.264 编码仍为有损。

## 源素材与遮罩

默认自动遮罩仍针对测试素材：**单人、深蓝衣服、简单背景，首帧能看清服装**。界面化不等于已经完成任意衣物的通用分割。

其他颜色或复杂素材可将遮罩节点 `mode` 改成 `provided clothing mask video`，再连接另一个原生 `LoadVideo` 到其 `mask_video` 输入。遮罩视频必须与原片帧率、帧数相同，白色只标记需要修改的衣服，黑色保护脸、手、腿和背景。

当前要求恒定帧率输入。短袖、无袖、长裙等明显改变覆盖范围的服装，需要重新设计遮罩并允许生成相应身体或背景区域。模型仍可能产生纹理漂移、衣物边缘和快速遮挡瑕疵，需观看成片验收。

## 安装与代码

本次已部署到项目现有 H3 环境，不需要额外安装视频主模型。独立节点安装包位于 `D:\aigc\data\clothing\comfyui\AIGC-Clothing.zip`；其他 ComfyUI 中解压 `AIGC-Clothing` 到 `custom_nodes` 后重启，依赖与模型仍需匹配。

包内有安装说明和 `qwen-config.example.json`。迁移时将示例复制为 `qwen-config.json`，可覆盖独立 Qwen 环境的 `python`、`comfy_root`、`unet`、`clip`、`vae` 路径。默认使用本项目 DGX 的已部署环境。也可在参考节点的 `reference_image` 输入接入现成换装参考图，跳过 Qwen。

来源文件：[自定义节点](../scripts/clothing_nodes.py)、[共享遮罩与合成实现](../aigc/clothing.py)、[Qwen 参考进程](../scripts/clothing_qwen_worker.py)、[API 图构建器](../aigc/clothing_workflow.py)。原脚本复用同一共享实现。

重新打包与部署：

```powershell
.\.venv\Scripts\python.exe scripts/package_clothing_nodes.py
.\.venv\Scripts\python.exe scripts/deploy_clothing.py
```

## 验证

通过真实 Edge 浏览器导入 19 节点画布，序列化为原生工作流后重新打开，核对提示词连接、参考步数 25、seed 42、H3 步数 4 及分段参数，再从原生界面一次排队处理原片。完整任务成功，执行约 21 分 28 秒；这是本次 DGX 单次测量，包含预处理、模型加载和推理。

- 成片：1080×1920、30 fps、374 帧，视频时长 12.466667 秒；独立解码计数与原片一致。
- 四个窗口从第 0、102、204、306 帧开始，最后窗口生成 73 帧、保留实际 68 帧。
- 完整原 AAC 音频包一致，SHA256 为 `c368537e40420712e36891127ea329b18ef3114d8ad242a7d91a8e2dbeb99bda`。
- 服装编辑遮罩之外的编码前像素差为 0；保存与下载的成片 SHA256 一致。
- 抽查首尾、遮挡和分段交界画面，脸与背景保持一致；袖口及衣物边缘仍有局部残留和纹理瑕疵。

本机结果在 `D:\aigc\test-results\clothing-native\full\result.mp4`，独立验收记录为同目录 `review.json`，对照联系表为 `result.qa.jpg`。工作台作品库也已归档成片与原生画布。

73 帧的小参数双窗口测试同样完成。89 项 Python 测试和 9 项工作流连接测试通过，安装包内源文件、画布参数及原生下载接口均已核对。刷新本地服务后，实际从工作台“一键打开”加载原生画布，并通过节点上传按钮选择 `test.mp4`，上传、参数及连线检查通过，浏览器无页面错误。
