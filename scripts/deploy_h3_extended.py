"""Create a dedicated DGX H3 checkout/venv, preserving the existing installation.

Run through scripts/dgx_exec.py after uploading plugins.tar.gz and manifest.json
to /home/jnu/models/aigc/h3-extended/staging.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

BASE=Path('/home/jnu/src/ComfyUI')
SOURCE=Path('/home/jnu/src/ComfyUI-h3-extended')
ENV=Path('/home/jnu/venvs/comfyui-h3-extended')
MODELS=Path('/home/jnu/models/aigc/h3-extended')

def run(args):
    print('RUN',*args,flush=True)
    subprocess.run([str(a) for a in args],check=True)

def main():
    manifest=json.loads((MODELS/'staging/manifest.json').read_text())
    revision=manifest['base_comfy_revision']
    if not SOURCE.exists():
        run(['git','clone','--local','--no-hardlinks',BASE,SOURCE])
        run(['git','-C',SOURCE,'checkout','--detach',revision])
    else:
        actual=subprocess.check_output(['git','-C',SOURCE,'rev-parse','HEAD'],text=True).strip()
        if actual!=revision:raise RuntimeError('Unexpected dedicated checkout revision: '+actual)
    if not ENV.exists():
        run(['cp','-a','--reflink=auto','/home/jnu/venvs/comfyui-ltx23',ENV])
    custom=SOURCE/'custom_nodes'
    with tarfile.open(MODELS/'staging/plugins.tar.gz') as tar:
        for member in tar.getmembers():
            path=custom/member.name
            if not path.resolve().is_relative_to(custom.resolve()) or not member.isfile():
                raise RuntimeError('Invalid plugin archive member')
            path.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(member) as src,path.open('wb') as dest:shutil.copyfileobj(src,dest)
    # Preserve the special ARM64/CUDA torch stack while resolving additional CPU dependencies.
    version_script="import importlib.metadata as m,json;print(json.dumps({x.metadata['Name']:x.version for x in m.distributions()}))"
    versions=json.loads(subprocess.check_output([str(ENV/'bin/python'),'-B','-c',version_script],text=True))
    fixed=[f'{n}=={v}' for n,v in versions.items() if n.lower() in {'torch','torchvision','torchaudio','transformers','triton','comfy-kitchen'} or n.lower().startswith(('nvidia-','cuda-'))]
    constraints=MODELS/'staging/core-constraints.txt';constraints.write_text('\n'.join(fixed)+'\n')
    additions=['accelerate','clip-interrogator>=0.6.0','lark','onnxruntime','peft','imageio-ffmpeg',
               'ultralytics','scenedetect>=0.7','librosa','rotary-embedding-torch','nvidia-ml-py']
    run(['/home/jnu/.local/bin/uv','pip','install','--python',ENV/'bin/python','--constraint',constraints,*additions])
    # Shared baseline weights and distinct addon paths; never overwrite a baseline weight.
    paths={'h3_baseline':{'base_path':str(BASE/'models'),'is_default':False},
           'h3_extended':{'base_path':str(MODELS/'models'),'is_default':False}}
    for category in ['diffusion_models','text_encoders','vae','vae_approx','loras','embeddings','model_patches',
                     'latent_upscale_models','ultralytics','sams','audio_encoders']:
        paths['h3_baseline'][category]=category;paths['h3_extended'][category]=category
        (MODELS/'models'/category).mkdir(parents=True,exist_ok=True)
    (MODELS/'extra_model_paths.yaml').write_text(json.dumps(paths,indent=2)+'\n')
    manifest.update(source_root=str(SOURCE),python=str(ENV/'bin/python'),model_paths=str(MODELS/'extra_model_paths.yaml'))
    (MODELS/'deployment.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    run([ENV/'bin/python','-B','-c',"import torch,torchaudio,transformers;print('CORE',torch.__version__,torchaudio.__version__,transformers.__version__)"])
    print('H3_EXTENDED_ENV_READY',flush=True)

if __name__=='__main__':main()
