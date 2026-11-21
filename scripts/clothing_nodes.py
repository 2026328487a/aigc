"""Native ComfyUI clothing workflow: preparation, reference, windows, composite.

Deployed with core.py and qwen_worker.py inside one custom-node package.
No HTTP requests, local manager, Windows paths or CLI pipeline are required.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import av
import cv2
import numpy as np
from PIL import Image
import torch

import folder_paths
import comfy.model_management as mm
from comfy.utils import ProgressBar
from comfy_execution.graph_utils import GraphBuilder
from . import core

CATEGORY='AIGC/Clothing'
DEFAULT_OUTFIT='an ivory cable-knit sweater with loose long sleeves and matching ivory knit shorts of the original length, matte fabric, no logos or lettering'
DEFAULT_QWEN={
    'python':'/home/jnu/venvs/comfyui-qwen21/bin/python',
    'comfy_root':'/home/jnu/src/ComfyUI-qwen21',
    'unet':'qwen-image-2.1-UC-int8_convrot.safetensors',
    'clip':'qwen3vl_8b_bf16.safetensors','vae':'qwen_image_2.1_vae_bf16.safetensors',
}


def tensor(rgb):
    return torch.from_numpy(np.ascontiguousarray(rgb).astype(np.float32)/255)


def frames_tensor(folder,start,count,width=None,height=None):
    images=[]
    for i in range(start,start+count):
        rgb=np.array(Image.open(Path(folder)/f'{i:05}.png').convert('RGB'))
        if width is not None:
            rgb=cv2.resize(rgb,(width,height),interpolation=cv2.INTER_AREA)
        images.append(tensor(rgb))
    return torch.stack(images)


def interrupted():
    mm.throw_exception_if_processing_interrupted()


def new_stage(context,name):
    root=Path(folder_paths.get_temp_directory())/'aigc-clothing'/f'{name}-{uuid.uuid4().hex}'
    root.mkdir(parents=True)
    old=Path(context['root'])
    for name in ['frames','masks','pose-frames']:
        source=old/name
        if source.exists():
            try: (root/name).symlink_to(source,target_is_directory=True)
            except OSError: shutil.copytree(source,root/name)
    shutil.copy2(old/'source.json',root/'source.json')
    return {**context,'root':str(root)}


class ClothingPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'outfit':('STRING',{'default':DEFAULT_OUTFIT,'multiline':True})}}
    RETURN_TYPES=('STRING',)
    RETURN_NAMES=('outfit',)
    FUNCTION='prompt'
    CATEGORY=CATEGORY
    def prompt(self,outfit):
        if not outfit.strip(): raise ValueError('Enter the outfit colour, fabric, sleeves and lower garment.')
        return (outfit.strip(),)


class ClothingPrepare:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'video':('VIDEO',),'work_long_edge':('INT',{'default':1024,'min':128,'max':1536,'step':32})}}
    RETURN_TYPES=('CLOTHING_CONTEXT','IMAGE')
    RETURN_NAMES=('clip','source_frames')
    FUNCTION='prepare'
    CATEGORY=CATEGORY

    def prepare(self,video,work_long_edge):
        source=video.get_stream_source() if hasattr(video,'get_stream_source') else None
        if not isinstance(source,str) or not Path(source).is_file():
            raise ValueError('Connect a file from the native Load Video node.')
        if hasattr(video,'get_active_trim_window') and video.get_active_trim_window()!=(0.0,0.0):
            raise ValueError('Upload an already trimmed video; this workflow preserves the loaded file timeline.')
        width,height=video.get_dimensions()
        scale=work_long_edge/max(width,height)
        work_width=max(32,round(width*scale/32)*32)
        work_height=max(32,round(height*scale/32)*32)
        root=Path(folder_paths.get_temp_directory())/'aigc-clothing'/f'source-{uuid.uuid4().hex}'
        root.mkdir(parents=True)
        interrupted()
        core.prepare(Path(source),root,work_width,work_height,encode_videos=False,interrupt=interrupted)
        meta=json.loads((root/'source.json').read_text())
        if meta['frames']<5: raise ValueError('H3 needs at least 5 source frames.')
        differences=np.diff(meta['timestamps'])
        if not np.allclose(differences,1/meta['fps'],rtol=.002,atol=.0001):
            raise ValueError('This workflow requires a constant frame rate video to preserve source timing.')
        meta['source_sha256']=hashlib.sha256(Path(source).read_bytes()).hexdigest()
        core.dump(root/'source.json',meta)
        return ({'root':str(root),'meta':meta},frames_tensor(root/'frames',0,meta['frames']))


class ClothingMasks:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'clip':('CLOTHING_CONTEXT',),'keypoints':('POSE_KEYPOINT',),'pose_images':('IMAGE',),
                            'mode':(['blue outfit (tested)','provided clothing mask video'],)},
                'optional':{'mask_video':('VIDEO',)}}
    RETURN_TYPES=('CLOTHING_CONTEXT','IMAGE')
    RETURN_NAMES=('masked_clip','mask_preview')
    FUNCTION='masks'
    CATEGORY=CATEGORY

    def masks(self,clip,keypoints,pose_images,mode,mask_video=None):
        meta=clip['meta']
        if len(keypoints)!=meta['frames'] or len(pose_images)!=meta['frames']:
            raise ValueError('Pose frame count must match the complete source video.')
        context=new_stage(clip,'mask')
        root=Path(context['root'])
        # Each preparation owns its mask files; prompt changes can safely reuse them.
        if (root/'masks').is_symlink():
            (root/'masks').unlink()
        elif (root/'masks').exists():
            # No deletion: keep the copied initial mask as a diagnostic.
            (root/'masks').rename(root/'initial-masks')
        (root/'masks').mkdir()
        core.dump(root/'pose.json',keypoints)
        pose_dir=root/'pose-frames'
        pose_dir.mkdir(exist_ok=True)
        pbar=ProgressBar(meta['frames'])
        for i,img in enumerate(pose_images):
            interrupted()
            Image.fromarray(np.rint(img.cpu().numpy().clip(0,1)*255).astype(np.uint8)).save(pose_dir/f'{i:05}.png')
            pbar.update(1)
        if mode=='provided clothing mask video':
            if mask_video is None: raise ValueError('Connect a white-clothing/black-protected mask video.')
            source=mask_video.get_stream_source()
            with av.open(source) as container:
                stream=container.streams.video[0]
                if abs(float(stream.average_rate)-meta['fps'])>1e-5:
                    raise ValueError('Mask video frame rate must match the source.')
                count=0
                for i,frame in enumerate(container.decode(video=0)):
                    interrupted()
                    mask=frame.to_ndarray(format='gray')
                    mask=cv2.resize(mask,(meta['work_width'],meta['work_height']),interpolation=cv2.INTER_NEAREST)
                    Image.fromarray((mask>127).astype(np.uint8)*255).save(root/'masks'/f'{i:05}.png')
                    count+=1
            if count!=meta['frames']: raise ValueError('Mask video must contain exactly the source frame count.')
        else:
            from ultralytics import YOLO
            models=folder_paths.get_full_path('ultralytics','segm/person_yolov8m-seg.pt')
            if models is None: raise ValueError('Install the existing person_yolov8m-seg.pt segmentation model.')
            os.environ.setdefault('YOLO_AUTOINSTALL','false')
            os.environ.setdefault('YOLO_CONFIG_DIR',str(root/'yolo-config'))
            yolo=YOLO(models)
            encoder=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo',
                '-pix_fmt','gray','-s',f"{meta['work_width']}x{meta['work_height']}",'-r',str(meta['fps']),
                '-i','pipe:0','-c:v','ffv1',str(root/'person-mask.mkv')],stdin=subprocess.PIPE)
            try:
                for i in range(meta['frames']):
                    interrupted()
                    rgb=np.array(Image.open(root/'frames'/f'{i:05}.png').convert('RGB'))
                    result=yolo.predict(source=rgb[...,::-1],imgsz=640,device='cpu',verbose=False)[0]
                    mask=np.zeros(rgb.shape[:2],np.uint8)
                    if result.masks is not None:
                        for label,points in zip(result.boxes.cls.tolist(),result.masks.xy):
                            if int(label)==0: cv2.fillPoly(mask,[points.astype(np.int32)],255)
                    encoder.stdin.write(mask.tobytes())
                encoder.stdin.close()
                if encoder.wait()!=0: raise RuntimeError('Person mask encoder failed.')
            except BaseException:
                encoder.kill();encoder.wait();raise
            core.refine_masks(root,encode_video=False,interrupt=interrupted)
        indices=np.unique(np.linspace(0,meta['frames']-1,min(8,meta['frames'])).astype(int))
        preview=[]
        for i in indices:
            rgb=np.array(Image.open(root/'frames'/f'{i:05}.png').convert('RGB'))
            mask=np.array(Image.open(root/'masks'/f'{i:05}.png'))>127
            rgb[mask]=np.rint(.45*rgb[mask]+.55*np.array([0,220,80])).astype(np.uint8)
            preview.append(tensor(rgb))
        context['mask_mode']=mode
        return (context,torch.stack(preview))


class ClothingReference:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'clip':('CLOTHING_CONTEXT',),'steps':('INT',{'default':25,'min':1,'max':40}),
                            'seed':('INT',{'default':42,'min':0,'max':2**53-1}),
                            'outfit':('STRING',{'forceInput':True})},
                'optional':{'reference_image':('IMAGE',)}}
    RETURN_TYPES=('CLOTHING_CONTEXT','IMAGE')
    RETURN_NAMES=('reference_clip','outfit_reference')
    FUNCTION='reference'
    CATEGORY=CATEGORY

    def reference(self,clip,outfit,steps,seed,reference_image=None):
        context=new_stage(clip,'run')
        root=Path(context['root'])
        ref_dir=root/'references'/'custom'
        ref_dir.mkdir(parents=True)
        output=ref_dir/'result-00.png'
        if reference_image is None:
            config=DEFAULT_QWEN.copy()
            config_path=Path(__file__).with_name('qwen-config.json')
            if config_path.exists(): config.update(json.loads(config_path.read_text()))
            if not Path(config['python']).is_file() or not Path(config['comfy_root']).is_dir():
                raise ValueError('Configure the existing Qwen 2.1 environment in qwen-config.json, or connect a reference image.')
            job={**config,'source_image':str(root/'frames/00000.png'),'output_image':str(output),
                 'steps':steps,'seed':seed,'prompt':
                    'Edit only the clothing of the same person in the input photograph. Replace the current outfit with '+outfit+
                    '. Preserve the original face, hairstyle, expression, pose, proportions, hands, visible skin, legs, camera and background. '
                    'Keep the original clothing coverage and loose sleeve silhouette. Remove old logos, numbers and lettering. '
                    'One realistic photograph, no collage, no additional people.'}
            core.dump(root/'reference-job.json',job)
            mm.unload_all_models();mm.soft_empty_cache()
            log=(root/'reference-worker.log').open('w',encoding='utf-8')
            process=subprocess.Popen([config['python'],'-u',str(Path(__file__).with_name('qwen_worker.py')),
                                      str(root/'reference-job.json')],stdout=log,stderr=subprocess.STDOUT)
            started=time.monotonic()
            try:
                while process.poll() is None:
                    interrupted()
                    if time.monotonic()-started>1800: raise TimeoutError('Qwen reference inference timed out.')
                    time.sleep(.5)
                if process.returncode:
                    raise RuntimeError((root/'reference-worker.log').read_text(errors='replace')[-8000:])
            except BaseException:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait()
                raise
            finally:
                log.close()
        else:
            rgb=np.rint(reference_image[0].cpu().numpy().clip(0,1)*255).astype(np.uint8)
            Image.fromarray(rgb).save(output)
        original=np.array(Image.open(root/'frames/00000.png').convert('RGB'))
        generated=np.array(Image.open(output).convert('RGB'))
        generated=cv2.resize(generated,(original.shape[1],original.shape[0]),interpolation=cv2.INTER_LANCZOS4)
        mask=np.array(Image.open(root/'masks/00000.png'))
        alpha=cv2.GaussianBlur(mask.astype(np.float32)/255,(5,5),0)[...,None]
        reference=np.rint(alpha*generated+(1-alpha)*original).astype(np.uint8)
        Image.fromarray(reference).save(root/'references/custom.png')
        context.update(outfit=outfit,seed=seed,reference_steps=steps)
        return (context,tensor(reference)[None])


class ClothingWindow:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'clip':('CLOTHING_CONTEXT',),'start':('INT',{'default':0,'min':0}),
                            'length':('INT',{'default':124,'min':5}),
                            'reference_scale':('FLOAT',{'default':.5,'min':.25,'max':1})},
                'optional':{'previous':('CLOTHING_SEGMENTS',)}}
    RETURN_TYPES=('IMAGE','IMAGE','IMAGE')
    RETURN_NAMES=('outfit_reference','source_motion','source_pose')
    FUNCTION='window'
    CATEGORY=CATEGORY

    def window(self,clip,start,length,reference_scale,previous=None):
        interrupted()
        root=Path(clip['root']);meta=clip['meta']
        ref=np.array(Image.open(root/'references/custom.png').convert('RGB'))
        if start:
            if previous is None or previous['covered']<=start: raise ValueError('Previous source window is incomplete.')
            original=np.array(Image.open(root/'frames'/f'{start:05}.png').convert('RGB'))
            candidate=np.array(Image.open(root/'results/custom/cloth-frames'/f'{start:05}.png').convert('RGB'))
            candidate=core.ClothingRepair(root,'custom').apply(candidate,start)
            mask=np.array(Image.open(root/'masks'/f'{start:05}.png'))
            alpha=cv2.GaussianBlur(mask.astype(np.float32)/255,(5,5),0)[...,None]
            ref=np.rint(alpha*candidate+(1-alpha)*original).astype(np.uint8)
        count=min(length,meta['frames']-start)
        rw=max(32,round(meta['work_width']*reference_scale/32)*32)
        rh=max(32,round(meta['work_height']*reference_scale/32)*32)
        source=frames_tensor(root/'frames',start,count,rw,rh)
        pose=frames_tensor(root/'pose-frames',start,count)
        # Repeat the last real source/pose frame only for the H3 length grid.
        if count<length:
            source=torch.cat([source,source[-1:].expand(length-count,-1,-1,-1)])
            pose=torch.cat([pose,pose[-1:].expand(length-count,-1,-1,-1)])
        return (tensor(ref)[None],source,pose)


class ClothingCommit:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'clip':('CLOTHING_CONTEXT',),'images':('IMAGE',),'start':('INT',{'default':0}),
                            'valid':('INT',{'default':124}),'model_frames':('INT',{'default':124}),
                            'overlap':('INT',{'default':22})},'optional':{'previous':('CLOTHING_SEGMENTS',)}}
    RETURN_TYPES=('CLOTHING_SEGMENTS',)
    FUNCTION='commit'
    CATEGORY=CATEGORY

    def commit(self,clip,images,start,valid,model_frames,overlap,previous=None):
        interrupted()
        if len(images)!=model_frames: raise RuntimeError('H3 output frame count changed.')
        root=Path(clip['root'])
        decoded=[np.rint(img.cpu().numpy().clip(0,1)*255).astype(np.uint8) for img in images[:valid]]
        core.merge_window(root/'results/custom/cloth-frames',decoded,start,valid,overlap)
        segments=list(previous['segments']) if previous else []
        segments.append({'start':start,'valid_frames':valid,'model_frames':model_frames})
        return ({'covered':start+valid,'segments':segments},)


class ClothingComposite:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'clip':('CLOTHING_CONTEXT',),'segments':('CLOTHING_SEGMENTS',)}}
    RETURN_TYPES=('VIDEO','STRING')
    RETURN_NAMES=('edited_video','validation')
    FUNCTION='composite'
    CATEGORY=CATEGORY

    def composite(self,clip,segments):
        interrupted()
        root=Path(clip['root']);meta=clip['meta']
        if segments['covered']!=meta['frames']: raise ValueError('Not all source video frames were generated.')
        frames=[root/'results/custom/cloth-frames'/f'{i:05}.png' for i in range(meta['frames'])]
        target=core.composite_video(root,'custom',frames,root/'results/custom.mp4',interrupt=interrupted)
        report=json.loads(target.with_suffix('.validation.json').read_text())
        report.update(outfit=clip['outfit'],mask_mode=clip['mask_mode'],segments=segments['segments'])
        from comfy_api.latest import InputImpl
        return (InputImpl.VideoFromFile(str(target)),json.dumps(report,ensure_ascii=False))


class ClothingGenerate:
    @classmethod
    def INPUT_TYPES(cls):
        link={'rawLink':True}
        return {'required':{'clip':('CLOTHING_CONTEXT',),'model':('MODEL',link),'text_encoder':('CLIP',link),
                            'video_vae':('VAE',link),'audio_vae':('VAE',link),'controlnet':('MODEL_PATCH',link),
                            'steps':('INT',{'default':4,'min':1,'max':40}),
                            'chunk_frames':('INT',{'default':124,'min':22,'max':362,'step':17}),
                            'overlap_frames':('INT',{'default':22,'min':0,'max':123}),
                            'reference_scale':('FLOAT',{'default':.5,'min':.25,'max':1,'step':.25}),
                            'pose_strength':('FLOAT',{'default':.65,'min':0,'max':1,'step':.05})}}
    RETURN_TYPES=('VIDEO','STRING')
    RETURN_NAMES=('edited_video','validation')
    FUNCTION='generate'
    CATEGORY=CATEGORY

    def generate(self,clip,model,text_encoder,video_vae,audio_vae,controlnet,steps,chunk_frames,
                 overlap_frames,reference_scale,pose_strength):
        if (chunk_frames-5)%17 or not 0<=overlap_frames<chunk_frames:
            raise ValueError('Use chunk_frames=5+17k and overlap smaller than the chunk.')
        graph=GraphBuilder()
        meta=clip['meta'];previous=None
        prompt=('body_swap: Replace the main person in <Video 1> with the same person from <Picture 1>, wearing '+clip['outfit']+
                '. The reference picture defines the outfit. Precisely replicate the source motion, timing, hand positions and camera. ')
        # Explicit concatenation keeps one outfit definition for both Qwen and H3.
        prompt+= 'Same face, hairstyle, body proportions and background. Consistent realistic fabric, no old logos or lettering.'
        for start in range(0,meta['frames'],chunk_frames-overlap_frames):
            valid=min(chunk_frames,meta['frames']-start)
            length=5+17*max(0,math.ceil((valid-5)/17))
            args=dict(clip=clip,start=start,length=length,reference_scale=reference_scale)
            if previous is not None: args['previous']=previous.out(0)
            window=graph.node('AIGCClothingWindow',**args)
            conditioning=graph.node('MiniMaxH3ReferenceToVideo',clip=text_encoder,vae=video_vae,audio_vae=audio_vae,
                prompt=prompt,width=meta['work_width'],height=meta['work_height'],length=length,ref_image_size='match',
                **{'ref_images.ref_image_0':window.out(0),'ref_videos.ref_video_0':window.out(1)})
            controlled=graph.node('MiniMaxH3FunControlNetApply',model=model,model_patch=controlnet,vae=video_vae,
                strength=pose_strength,start_percent=0.0,end_percent=.85,control_video=window.out(2))
            guider=graph.node('BasicGuider',model=controlled.out(0),conditioning=conditioning.out(0))
            noise=graph.node('RandomNoise',noise_seed=clip['seed'])
            sampler=graph.node('KSamplerSelect',sampler_name='res_multistep')
            sigmas=graph.node('BasicScheduler',model=controlled.out(0),scheduler='simple',steps=steps,denoise=1.0)
            sampled=graph.node('SamplerCustomAdvanced',noise=noise.out(0),guider=guider.out(0),sampler=sampler.out(0),
                sigmas=sigmas.out(0),latent_image=conditioning.out(1))
            decoded=graph.node('VAEDecode',samples=sampled.out(0),vae=video_vae)
            kwargs=dict(clip=clip,images=decoded.out(0),start=start,valid=valid,model_frames=length,overlap=overlap_frames)
            if previous is not None: kwargs['previous']=previous.out(0)
            previous=graph.node('AIGCClothingCommit',**kwargs)
        final=graph.node('AIGCClothingComposite',clip=clip,segments=previous.out(0))
        return {'result':(final.out(0),final.out(1)),'expand':graph.finalize()}


class SaveClothingVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'video':('VIDEO',),'filename_prefix':('STRING',{'default':'clothing-change/video'}),
                            'validation':('STRING',{'forceInput':True})}}
    RETURN_TYPES=()
    OUTPUT_NODE=True
    FUNCTION='save'
    CATEGORY=CATEGORY

    def save(self,video,validation,filename_prefix):
        source=Path(video.get_stream_source())
        directory,name,counter,subfolder,_=folder_paths.get_save_image_path(filename_prefix,folder_paths.get_output_directory())
        filename=f'{name}_{counter:05}.mp4'
        target=Path(directory)/filename
        shutil.copy2(source,target)
        report=json.loads(validation)
        report.update(file=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        core.dump(target.with_suffix('.validation.json'),report)
        return {'ui':{'images':[{'filename':filename,'subfolder':subfolder,'type':'output'}],'animated':(True,),
                      'files':[{'filename':target.with_suffix('.validation.json').name,'subfolder':subfolder,'type':'output'}]}}


from .pose_export import NODE_CLASS_MAPPINGS as POSE_NODES
NODE_CLASS_MAPPINGS={**POSE_NODES,
    'AIGCClothingPrompt':ClothingPrompt,'AIGCClothingPrepare':ClothingPrepare,'AIGCClothingMasks':ClothingMasks,
    'AIGCClothingReference':ClothingReference,'AIGCClothingWindow':ClothingWindow,'AIGCClothingCommit':ClothingCommit,
    'AIGCClothingComposite':ClothingComposite,'AIGCClothingGenerate':ClothingGenerate,'AIGCSaveClothingVideo':SaveClothingVideo}
NODE_DISPLAY_NAME_MAPPINGS={
    'AIGCClothingPrompt':'服装提示词 / Outfit', 'AIGCClothingPrepare':'读取原视频与工作帧',
    'AIGCClothingMasks':'服装遮罩与保护区域','AIGCClothingReference':'Qwen 同人换装参考',
    'AIGCClothingGenerate':'H3 整段换装 · 自动分段','AIGCSaveClothingVideo':'保存换装视频与原音轨'}
