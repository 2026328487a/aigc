"""Isolated Qwen inference called by the ComfyUI clothing reference node."""
import json
from pathlib import Path
import sys


def main(job):
    settings=json.loads(Path(job).read_text(encoding='utf-8'))
    source_root=settings['comfy_root']
    sys.path.insert(0,source_root)
    sys.argv=['clothing-reference-worker','--disable-api-nodes','--disable-all-custom-nodes']
    import os
    os.chdir(source_root)
    import nodes
    import torch
    import numpy as np
    from PIL import Image

    # These are the only extra nodes required. Loading unrelated UI/server
    # extensions in an offline inference worker would require a PromptServer.
    from comfy_extras.nodes_qwen import TextEncodeQwenImage21,QwenImage21Cache
    nodes.NODE_CLASS_MAPPINGS.update(TextEncodeQwenImage21=TextEncodeQwenImage21,QwenImage21Cache=QwenImage21Cache)

    def call(kind,**inputs):
        cls=nodes.NODE_CLASS_MAPPINGS[kind]
        if hasattr(cls,'define_schema'):
            value=cls.execute(**inputs)
        else:
            value=getattr(cls(),cls.FUNCTION)(**inputs)
        return value.result if hasattr(value,'result') else value

    image=torch.from_numpy(np.array(Image.open(settings['source_image']).convert('RGB')).astype(np.float32)/255)[None]
    with torch.inference_mode():
        model=call('UNETLoader',unet_name=settings['unet'],weight_dtype='default')[0]
        model=call('QwenImage21Cache',model=model,device='auto',dtype='default')[0]
        clip=call('CLIPLoader',clip_name=settings['clip'],type='qwen_image',device='default')[0]
        vae=call('VAELoader',vae_name=settings['vae'])[0]
        positive,negative,latent=call('TextEncodeQwenImage21',clip=clip,vae=vae,
            prompt=settings['prompt'],negative_prompt='',resolution=0,images={'image_1':image})
        sampled=call('KSampler',model=model,positive=positive,negative=negative,latent_image=latent,
            seed=settings['seed'],steps=settings['steps'],cfg=1.0,sampler_name='euler',scheduler='simple',denoise=1.0)[0]
        decoded=call('VAEDecode',samples=sampled,vae=vae)[0]
    rgb=np.rint(decoded[0].cpu().numpy().clip(0,1)*255).astype(np.uint8)
    Image.fromarray(rgb).save(settings['output_image'])
    print('CLOTHING_REFERENCE_SAVED',settings['output_image'],flush=True)


if __name__=='__main__':
    main(sys.argv[1])
