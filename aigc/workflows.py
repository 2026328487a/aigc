from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Settings(BaseModel):
    engine: Literal["h3", "qwen21", "music3", "voxcpm2"] = "h3"
    mode: Literal["image", "reference", "text", "edit", "music", "speech", "clone"] = "image"
    prompt: str = Field(min_length=1, max_length=12000)
    width: int = Field(default=768, ge=128, le=2048, multiple_of=32)
    height: int = Field(default=448, ge=128, le=2048, multiple_of=32)
    seconds: float = Field(default=2, ge=0.2, le=300)
    steps: int = Field(default=8, ge=1, le=40)
    seed: int = Field(default=42, ge=0, le=2**53 - 1)
    lyrics: str = Field(default="", max_length=8000)
    language: Literal["auto", "zh", "yue", "en"] = "auto"
    reference_text: str = Field(default="", max_length=2000)
    voice_style: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def nonempty_prompt(self):
        if not self.prompt.strip():
            raise ValueError("请输入提示词")
        allowed = {"qwen21": ("text", "edit"), "h3": ("image", "reference"),
                   "music3": ("music",), "voxcpm2": ("speech", "clone")}[self.engine]
        if self.mode not in allowed:
            raise ValueError("生成模式与模型不匹配")
        if self.engine == "h3" and max(self.width, self.height) > 1536:
            raise ValueError("H3 尺寸上限为 1536")
        if self.engine == "h3" and self.seconds > 15:
            raise ValueError("H3 时长上限为 15 秒")
        if self.engine == "voxcpm2" and len(self.prompt) > 2000:
            raise ValueError("语音单次文本上限为 2000 字符，请分段生成")
        return self

    @property
    def frames(self):
        count = max(5, round(self.seconds * 24))
        return count + (5 - count % 17) % 17


def build_workflow(settings, images, job_id):
    if settings.engine == "music3":
        return build_music_workflow(settings, job_id)
    if settings.engine == "qwen21":
        return build_qwen_workflow(settings, images, job_id)
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    reference = settings.mode == "reference"
    model = "minimax_h3_ref2va_pruned_int8_convrot.safetensors" if reference else "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
    lora = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" if reference else "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
    graph = {
        "1": node("UNETLoader", unet_name=model, weight_dtype="default"),
        "2": node("LoraLoaderModelOnly", model=["1", 0], lora_name=lora, strength_model=1.0),
        "3": node("CLIPLoader", clip_name="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", type="minimax", device="default"),
        "4": node("VAELoader", vae_name="minimax_h3_video_vae_fp16.safetensors"),
        "5": node("VAELoader", vae_name="minimax_h3_audio_vae_fp32.safetensors"),
        "7": node("MiniMaxH3ReferenceToVideo" if reference else "MiniMaxH3ImageToVideo",
                  clip=["3", 0], vae=["4", 0], prompt=settings.prompt,
                  width=settings.width, height=settings.height, length=settings.frames),
        "8": node("BasicGuider", model=["2", 0], conditioning=["7", 0]),
        "9": node("RandomNoise", noise_seed=settings.seed),
        "10": node("KSamplerSelect", sampler_name="res_multistep"),
        "11": node("BasicScheduler", model=["2", 0], scheduler="simple", steps=settings.steps, denoise=1.0),
        "12": node("SamplerCustomAdvanced", noise=["9", 0], guider=["8", 0], sampler=["10", 0],
                   sigmas=["11", 0], latent_image=["7", 1]),
        "13": node("VAEDecode", samples=["12", 0], vae=["4", 0]),
        "14": node("VAEDecodeAudio", samples=["12", 0], vae=["5", 0]),
        "15": node("CreateVideo", images=["13", 0], fps=24.0, audio=["14", 0]),
        "16": node("SaveVideo", video=["15", 0], filename_prefix=f"{job_id}/video",
                   format="mp4", **{"format.codec": "h264", "format.codec.encoding": "re-encode", "format.codec.encoding.crf": 20}),
    }
    if reference:
        graph["7"]["inputs"].update(audio_vae=["5", 0], ref_image_size="match")
    for index, filename in enumerate(images):
        node_id = str(20 + index)
        graph[node_id] = node("LoadImage", image=filename)
        key = f"ref_images.ref_image_{index}" if reference else ("first_frame" if index == 0 else "last_frame")
        graph["7"]["inputs"][key] = [node_id, 0]
    return graph


def build_music_workflow(settings, job_id):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    return {
        "1": node("UNETLoader", unet_name="minimax_music3_dit_fp16.safetensors", weight_dtype="default"),
        "3": node("CLIPLoader", clip_name="minimax_music3_text_encoder_pruned_int8_convrot.safetensors", type="minimax", device="default"),
        "4": node("VAELoader", vae_name="minimax_music3_dav.safetensors"),
        "7": node("MiniMaxMusic3TextEncode", clip=["3", 0], caption=settings.prompt,
                  lyrics=settings.lyrics.strip() or "[Instrumental]", seed=settings.seed,
                  max_duration=settings.seconds, cfg_scale=1.7, top_k=50),
        "6": node("EmptyMiniMaxMusic3LatentAudio", seconds=["7", 1], batch_size=1),
        "8": node("ConditioningZeroOut", conditioning=["7", 0]),
        "12": node("KSampler", model=["1", 0], positive=["7", 0], negative=["8", 0],
                   latent_image=["6", 0], seed=settings.seed, steps=settings.steps, cfg=1.7,
                   sampler_name="euler", scheduler="simple", denoise=1.0),
        "13": node("VAEDecodeAudioTiled", samples=["12", 0], vae=["4", 0], tile_size=1536, overlap=64),
        "16": node("AIGCSaveAudioWAV", audio=["13", 0], filename_prefix=f"{job_id}/audio"),
    }


def build_qwen_workflow(settings, images, job_id):
    def node(kind, **inputs):
        return {"class_type": kind, "inputs": inputs}

    graph = {
        "1": node("UNETLoader", unet_name="qwen-image-2.1-UC-int8_convrot.safetensors", weight_dtype="default"),
        "2": node("QwenImage21Cache", model=["1", 0], device="auto", dtype="default"),
        "3": node("CLIPLoader", clip_name="qwen3vl_8b_bf16.safetensors", type="qwen_image", device="default"),
        "4": node("VAELoader", vae_name="qwen_image_2.1_vae_bf16.safetensors"),
        "6": node("EmptyLatentImage", width=settings.width, height=settings.height, batch_size=1),
        "7": node("TextEncodeQwenImage21", clip=["3", 0], prompt=settings.prompt,
                  negative_prompt="", vae=["4", 0], resolution=round((settings.width * settings.height) ** 0.5 / 32) * 32),
        "12": node("KSampler", model=["2", 0], positive=["7", 0], negative=["7", 1],
                   latent_image=["7", 2] if settings.mode == "edit" else ["6", 0],
                   seed=settings.seed, steps=settings.steps, cfg=1.0,
                   sampler_name="euler", scheduler="simple", denoise=1.0),
        "13": node("VAEDecode", samples=["12", 0], vae=["4", 0]),
        "16": node("SaveImage", images=["13", 0], filename_prefix=f"{job_id}/image"),
    }
    for index, filename in enumerate(images):
        node_id = str(20 + index)
        graph[node_id] = node("LoadImage", image=filename)
        graph["7"]["inputs"][f"images.image_{index + 1}"] = [node_id, 0]
    return graph
