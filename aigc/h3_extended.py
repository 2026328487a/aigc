"""Small, inspectable H3 graphs for the dedicated extension environment."""
from copy import deepcopy

from .workflows import Settings, build_workflow

TEMPLATES = {
    'h3-lightx': 'LightX2V 首帧视频',
    'h3-continuation': '两段连续镜头',
    'h3-digital-human': '录音驱动数字人',
    'h3-vocal-human': '提取人声并驱动数字人',
    'h3-character-swap': '动作迁移与人物替换',
    'h3-head-swap': '视频换头',
    'h3-anime': '真人转动漫',
    'h3-motion': '稳定身体动作',
    'h3-sharpen': '视频细节增强',
    'h3-latent-upscale': '潜空间放大与二次采样',
    'h3-face-refine': '跟踪修脸与表情保留',
    'h3-union-control': 'Union 2.0 边缘动作控制',
    'h3-effects': '子弹时间特效',
    'h3-refmod': '可复用人物参考素材',
    'h3-refmod-reuse': '载入已保存的人物参考',
}
REF_TURBO='minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors'
IMAGE_TURBO='minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors'

def default_inputs(info, kind):
    result={}
    for key, spec in info.get(kind,{}).get('input',{}).get('required',{}).items():
        if not isinstance(spec,list):continue
        options=spec[1] if len(spec)>1 and isinstance(spec[1],dict) else {}
        if 'default' in options: result[key]=deepcopy(options['default'])
        elif isinstance(spec[0],list) and spec[0]:result[key]=spec[0][0]
        elif spec[0]=='COMBO' and options.get('options'):result[key]=options['options'][0]
    return result

def build_h3_extended(name, info=None, *, width=512, height=320, length=124, seed=42):
    if name not in TEMPLATES:raise KeyError(name)
    info=info or {}
    image_mode=name in {'h3-lightx','h3-latent-upscale','h3-union-control','h3-effects'}
    prompt=('A calm cinematic shot of the adult person in the reference picture. '
            'The person gently turns their head and smiles. Stable face and clothing, natural motion. '
            'Quiet room ambience, no music.')
    if not image_mode:prompt='<Picture 1> defines the adult person. '+prompt
    s=Settings(engine='h3',mode='image' if image_mode else 'reference',prompt=prompt,width=width,height=height,steps=4,seed=seed)
    graph=build_workflow(s,['reference.png'],'h3-'+name.removeprefix('h3-'))
    graph['2']['inputs']['lora_name']=IMAGE_TURBO if image_mode else REF_TURBO
    graph['4']['inputs']['vae_name']='minimax_h3_video_vae_int8_convrot.safetensors'
    graph['7']['inputs']['length']=length

    def add(key,kind,**inputs):
        graph[str(key)]={'class_type':kind,'inputs':{**default_inputs(info,kind),**inputs}}
        return [str(key),0]
    def model(value):
        graph['8']['inputs']['model']=value;graph['11']['inputs']['model']=value
    def decoded(value):
        graph['13']['inputs']['samples']=value;graph['14']['inputs']['samples']=value
    def video():
        return add(21,'VHS_LoadVideo',video='source.mp4',force_rate=24,custom_width=width,custom_height=height,
                   frame_load_cap=length,skip_first_frames=0,select_every_nth=1,format='H3')
    def lora(filename,strength=1.0):
        value=add(23,'LoraLoaderModelOnly',model=['2',0],lora_name=filename,strength_model=strength)
        model(value);return value

    if name=='h3-continuation':
        graph['27']=deepcopy(graph['7'])
        latent=add(28,'MiniMaxH3LatentHeadReplace',latent_tail=['12',0],latent_base=['27',1],
                   replace_frames=22,head_mask_strength=0.0,apply_mask=True,replace_audio=True)
        guide=add(29,'BasicGuider',model=['2',0],conditioning=['27',0])
        noise=add(30,'RandomNoise',noise_seed=seed+1)
        next_clip=add(31,'SamplerCustomAdvanced',noise=noise,guider=guide,sampler=['10',0],sigmas=['11',0],latent_image=latent)
        joined=add(32,'MiniMaxH3PureStitcher',samples1=['12',0],samples2=next_clip,overlap_frames=22)
        decoded(joined)
    elif name in {'h3-digital-human','h3-vocal-human'}:
        audio=add(22,'LoadAudio',audio='reference.wav')
        if name=='h3-vocal-human':
            mel=add(24,'MelBandRoFormerModelLoader',model_name='MelBandRoformer_fp16.safetensors')
            audio=add(25,'MelBandRoFormerSampler',model=mel,audio=audio)
        add(28,'AIGCH3AudioWindow',audio=audio,max_frames=length)
        audio=['28',1]
        graph['7']['inputs']['length']=['28',0]
        graph['7']['inputs']['prompt']=('<Picture 1> defines the adult speaker. <Audio 1> is the exact speech track. '
            'A medium close-up of the speaker talking naturally in sync with the speech, with subtle head motion. '
            'Preserve the face, hairstyle, clothes and background. No added music.')
        graph['7']['inputs']['ref_audios.ref_audio_0']=audio
        masked=add(26,'MiniMaxH3AudioMasker',av_latent=['7',1],audio=audio,audio_vae=['5',0])
        graph['12']['inputs']['latent_image']=masked
        graph['15']['inputs']['audio']=audio
        graph.pop('14')
    elif name in {'h3-character-swap','h3-head-swap','h3-anime','h3-sharpen'}:
        frames=video()
        graph['7']['inputs']['ref_videos.ref_video_0']=frames
        if name=='h3-character-swap':
            lora('minimax_h3_character_swap_v1.1_ref2va_fro.safetensors')
            graph['7']['inputs']['prompt']='body_swap: Replace the adult main person in <Video 1> with the adult person from <Picture 1>. Preserve the source motion, expressions, camera and background. The replacement keeps the reference face, hairstyle and outfit.'
        elif name=='h3-head-swap':
            lora('minimax_h3_head_swap_v1.0_r32.safetensors',0.8)
            graph['7']['inputs']['prompt']='head_swap: The adult person in <Video 1> has the face and hairstyle of the adult person in <Picture 1>. Preserve the original body motion, outfit, camera and background.'
        elif name=='h3-anime':
            lora('h3_anime_tx.safetensors',0.8)
            graph['7']['inputs']['prompt']='Convert <Video 1> into polished hand-drawn anime with expressive eyes, clean outlines and cel shading. Preserve the source action, framing and timing. <Picture 1> defines the adult main character.'
        else:
            lora('minimax_h3_lms_v1.0_r64.safetensors',0.4)
            graph['7']['inputs']['prompt']='Enhance <Video 1> with sharp, crisp details while preserving a natural photorealistic appearance, the exact motion and the face from <Picture 1>.'
            injected=add(24,'H3InjectVideoLatent',av_latent=['7',1],images=frames,vae=['4',0])
            graph['12']['inputs']['latent_image']=injected
            graph['11']['inputs']['denoise']=0.15
    elif name=='h3-motion':
        lora('h3_movement_v1.safetensors',0.8)
        graph['7']['inputs']['prompt']='mvmt. The adult person from <Picture 1> makes one slow, natural head turn and settles into a relaxed pose. Stable anatomy, smooth body motion, a fixed camera.'
    elif name=='h3-latent-upscale':
        high_width=(round(width*1.5/32))*32;high_height=(round(height*1.5/32))*32
        split=add(31,'LTXVSeparateAVLatent',av_latent=['12',0])
        upscale=add(32,'MinimaxH3LatentUpscaler3D',latent=split,model_name='minimax_h3_latent_upscaler_3d_fp16.safetensors',
                    mode='target dimensions',**{'mode.width':high_width,'mode.height':high_height},align=32,
                    enable_temporal_chunking=True,force_unload=True,device='cuda',precision='fp16')
        joined=add(33,'LTXVConcatAVLatent',video_latent=upscale,audio_latent=['31',1])
        graph['34']=deepcopy(graph['7']);graph['34']['inputs'].update(width=high_width,height=high_height)
        guide=add(35,'BasicGuider',model=['2',0],conditioning=['34',0])
        sigmas=add(36,'BasicScheduler',model=['2',0],scheduler='simple',steps=2,denoise=0.2)
        refined=add(37,'SamplerCustomAdvanced',noise=['9',0],guider=guide,sampler=['10',0],sigmas=sigmas,latent_image=joined)
        decoded(refined)
    elif name=='h3-face-refine':
        frames=video()
        tracked=add(30,'H3FaceTrackCrop',images=frames,detector='bbox/face_yolov8m.pt',confidence=0.3,crop_factor=2.5,
                    canvas_width=512,canvas_height=512,canvas_mode='manual',identity_track=False,identity_model='insightface',
                    cut_detection='none',fallback_detector='none')
        names=info.get('H3FaceTrackCrop',{}).get('output_name',[])
        def output(label,fallback):return ['30',names.index(label) if label in names else fallback]
        transform=output('transform',1)
        graph['7']['inputs'].update(width=output('width',4),height=output('height',5),length=output('num_frames',6))
        injected=add(31,'H3InjectVideoLatent',av_latent=['7',1],images=tracked,vae=['4',0])
        adjusted=add(32,'H3PerFrameDenoise',model=['2',0],av_latent=injected,transform=transform,
                     denoise_multiplier_small_face=1.0,denoise_multiplier_large_face=0.4,
                     scale_mode='absolute_px',face_px_small=30.0,face_px_large=120.0,gamma=1.0,smooth_frames=9)
        graph['8']['inputs']['model']=['32',2];graph['11']['inputs']['model']=['32',2]
        graph['11']['inputs']['denoise']=0.4;graph['12']['inputs']['latent_image']=adjusted
        stitched=add(35,'H3FaceStitch',base_images=frames,refined_crops=['13',0],transform=transform,
                     paste_region='face_only',mask_dilation=16,feather=6,colour_match=1.0,blend=1.0,
                     undetected_frames='fade_out',feather_scales_with_crop=False)
        graph['15']['inputs'].update(images=stitched,audio=['21',2])
    elif name=='h3-union-control':
        frames=video()
        edges=add(24,'Canny',image=frames,low_threshold=0.2,high_threshold=0.6)
        patch=add(25,'ModelPatchLoader',name='minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors')
        controlled=add(26,'MiniMaxH3FunControlNetApply',model=['2',0],model_patch=patch,vae=['4',0],
                       strength=0.8,start_percent=0.0,end_percent=0.8,control_video=edges,source_video=frames)
        model(controlled)
    elif name=='h3-effects':
        graph['7']['inputs']['prompt']='embedding:minimaxh3_bullet_time. A cinematic bullet-time shot of the adult person, the camera slowly orbiting while their movement nearly freezes. Preserve face and clothes.'
    elif name in {'h3-refmod','h3-refmod-reuse'}:
        if name=='h3-refmod':
            extracted=add(40,'MiniMaxH3RefModExtract',name='my_person',mode='Full Reference',concept_type='identity',
                      background_retention=0.0,ref_resolution=256,pool_h=4,pool_w=4,latent_frames=1,identity=0,
                      merge=False,motion_only=False,multiplier=1,max_tokens=5120,description='Reusable character reference',
                          save=False,vae=['4',0],**{'refs_image.ref_image_1':['20',0]})
            add(42,'AIGCSaveH3Reference',mods=extracted,filename_prefix='my_person')
        else:
            extracted=add(40,'AIGCLoadH3Reference',reference='my_person.safetensors',strength=1.0)
            # The saved reference supplies appearance without re-encoding a picture.
            graph['7']['inputs'].pop('ref_images.ref_image_0',None)
            graph.pop('20')
        applied=add(41,'MiniMaxH3RefModApply',conditioning=['7',0],mods=extracted,override=False,retention=1.0,
                    curve_direction='constant',scramble_seed=-1,curve_shape='linear',curve_value=1.0)
        graph['8']['inputs']['conditioning']=applied
    return graph
