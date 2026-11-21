"""Build an installable ComfyUI clothing node bundle and API workflow."""
from pathlib import Path
import json
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aigc.clothing_workflow import build_clothing_workflow

INSTALL_README='''# AIGC Clothing · ComfyUI 视频服装替换

本项目 H3 环境已安装。解压到其他 ComfyUI 时，将整个 AIGC-Clothing
目录放入 custom_nodes，重启后拖入 workflows/h3-clothing-change.workflow.json。
上传源视频，在“服装提示词 / Outfit”填写目标衣着，点击运行。

需要支持 MiniMax H3 的 ComfyUI 核心、SDPose 节点，以及 Python 包 av、
opencv-python、numpy、Pillow、torch、ultralytics；ffmpeg/ffprobe 需在 PATH 中。
画布模型加载节点列出了所需 H3 主模型、LightX2V、人物替换 LoRA、
Union 2.0、文本编码器和 VAE。姿态模型为 sdpose_wholebody_fp16.safetensors，
默认蓝色衣物模式还需注册 ultralytics/segm/person_yolov8m-seg.pt。
这些大模型和第三方节点不包含在此 ZIP 中。

Qwen 参考节点默认使用项目 DGX 上的独立 Qwen Image 2.1 环境。
迁移时复制 qwen-config.example.json 为 qwen-config.json，并修改 Python、
ComfyUI 根目录与模型名。模型路径需由该 Qwen 环境的 folder_paths 注册。
也可连接参考节点的 reference_image，直接提供换装参考图。

默认自动遮罩针对单人、蓝色原衣物、简单背景的测试素材。
其他衣物选择 provided clothing mask video，并连接另一 LoadVideo；
白色标衣服、黑色保护其他区域，遮罩与源片帧率、帧数必须一致。
源片需恒定帧率。改变袖长、裙长等覆盖范围需重新设计遮罩。

一个队列任务处理完整视频，保留原尺寸、帧率、帧数和完整原音轨，
输出 MP4 与 validation.json。视频为有损编码，衣物纹理和边缘仍需验收。
停止任务通过 ComfyUI 原生中断处理，节点不向当前服务器递归提交任务。
'''

QWEN_EXAMPLE={
    'python':'/home/jnu/venvs/comfyui-qwen21/bin/python',
    'comfy_root':'/home/jnu/src/ComfyUI-qwen21',
    'unet':'qwen-image-2.1-UC-int8_convrot.safetensors',
    'clip':'qwen3vl_8b_bf16.safetensors',
    'vae':'qwen_image_2.1_vae_bf16.safetensors',
}


def sources():
    return {'__init__.py':ROOT/'scripts/clothing_nodes.py','core.py':ROOT/'aigc/clothing.py',
            'qwen_worker.py':ROOT/'scripts/clothing_qwen_worker.py','pose_export.py':ROOT/'scripts/clothing_helpers.py'}


if __name__=='__main__':
    bundle=ROOT/'data/clothing/comfyui/AIGC-Clothing'
    bundle.mkdir(parents=True,exist_ok=True)
    for name,path in sources().items(): shutil.copy2(path,bundle/name)
    (bundle/'README.md').write_text(INSTALL_README,encoding='utf-8')
    (bundle/'qwen-config.example.json').write_text(json.dumps(QWEN_EXAMPLE,indent=2)+'\n',encoding='utf-8')
    graph=ROOT/'docs/h3-extended/workflows/h3-clothing-change.api.json'
    graph.write_text(json.dumps(build_clothing_workflow(),ensure_ascii=False,indent=2),encoding='utf-8')
    archive=bundle.parent/'AIGC-Clothing.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in sources(): z.write(bundle/name,arcname='AIGC-Clothing/'+name)
        for name in ['README.md','qwen-config.example.json']:
            z.write(bundle/name,arcname='AIGC-Clothing/'+name)
        z.write(graph,arcname='AIGC-Clothing/workflows/'+graph.name)
        native=graph.with_name('h3-clothing-change.workflow.json')
        if native.exists(): z.write(native,arcname='AIGC-Clothing/workflows/'+native.name)
    print(archive)
