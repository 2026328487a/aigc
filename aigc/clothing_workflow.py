"""One ComfyUI graph for full-video clothing editing, with native H3 models."""

DEFAULT_OUTFIT='an ivory cable-knit sweater with loose long sleeves and matching ivory knit shorts of the original length, matte fabric, no logos or lettering'


def build_clothing_workflow(video='test.mp4',outfit=DEFAULT_OUTFIT):
    def n(kind,**inputs): return {'class_type':kind,'inputs':inputs}
    return {
        '101':n('LoadVideo',file=video),
        '102':n('AIGCClothingPrompt',outfit=outfit),
        '103':n('AIGCClothingPrepare',video=['101',0],work_long_edge=1024),
        '104':n('CheckpointLoaderSimple',ckpt_name='sdpose_wholebody_fp16.safetensors'),
        '105':n('SDPoseKeypointExtractor',model=['104',0],vae=['104',2],image=['103',1],batch_size=8),
        '106':n('SDPoseDrawKeypoints',keypoints=['105',0],draw_body=True,draw_hands=True,draw_face=True,draw_feet=False,
                stick_width=3,face_point_size=1,score_threshold=.2,draw_head=True),
        '107':n('AIGCClothingMasks',clip=['103',0],keypoints=['105',0],pose_images=['106',0],mode='blue outfit (tested)'),
        '108':n('PreviewImage',images=['107',1]),
        '109':n('AIGCClothingReference',clip=['107',0],outfit=['102',0],steps=25,seed=42),
        '110':n('PreviewImage',images=['109',1]),
        '111':n('UNETLoader',unet_name='minimax_h3_ref2va_pruned_int8_convrot.safetensors',weight_dtype='default'),
        '112':n('LoraLoaderModelOnly',model=['111',0],lora_name='minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors',strength_model=1.0),
        '113':n('LoraLoaderModelOnly',model=['112',0],lora_name='minimax_h3_character_swap_v1.1_ref2va_fro.safetensors',strength_model=1.0),
        '114':n('CLIPLoader',clip_name='qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',type='minimax',device='default'),
        '115':n('VAELoader',vae_name='minimax_h3_video_vae_int8_convrot.safetensors'),
        '116':n('VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors'),
        '117':n('ModelPatchLoader',name='minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors'),
        '118':n('AIGCClothingGenerate',clip=['109',0],model=['113',0],text_encoder=['114',0],video_vae=['115',0],
                audio_vae=['116',0],controlnet=['117',0],steps=4,chunk_frames=124,overlap_frames=22,reference_scale=.5,pose_strength=.65),
        '119':n('AIGCSaveClothingVideo',video=['118',0],validation=['118',1],filename_prefix='clothing-change/video'),
    }
