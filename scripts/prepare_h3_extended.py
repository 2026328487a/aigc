"""Prepare selected H3 plugins and a hash manifest from the user's local bundle."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

PLUGINS = (
    'ComfyUI-KJNodes', 'ComfyUI-Easy-Use', 'ComfyUI-VideoHelperSuite',
    'ComfyUI-Custom-Scripts', 'comfyui-h3-facerefine', 'ComfyUI-MelBandRoFormer',
    'ComfyUI-MiniMax-Tools', 'Comfyui_Minimax_h3_latent_Upscaler',
    'ComfyUI_Fictiverse', 'reservedvram', 'ComfyUI-MiniMaxH3Mod',
)
WEIGHTS = {
    'loras': [
        'minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors',
        'minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors',
        'minimax_h3_character_swap_v1.1_ref2va_fro.safetensors',
        'minimax_h3_head_swap_v1.0_r32.safetensors', 'minimax_h3_lms_v1.0_r64.safetensors',
        'anime_tx-真人转动漫.safetensors', '自然且更稳定的身体动作.safetensors',
        'minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors',
    ],
    'latent_upscale_models': ['minimax_h3_latent_upscaler_3d_fp16.safetensors'],
    'vae_approx': ['taeh3.safetensors'],
    'diffusion_models': ['MelBandRoformer_fp16.safetensors'],
    'model_patches': ['minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors'],
    'ultralytics/bbox': ['face_yolov8m.pt'],
    'ultralytics/segm': ['person_yolov8m-seg.pt'],
}
ALIASES = {'anime_tx-真人转动漫.safetensors': 'h3_anime_tx.safetensors',
           '自然且更稳定的身体动作.safetensors': 'h3_movement_v1.safetensors'}

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8*1024*1024), b''): h.update(block)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('download_root', type=Path)
    parser.add_argument('--out', type=Path, default=Path('test-results/h3-extended'))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = args.download_root/'MINIMAX-H3新手整合包/ComfyUI-aki-v3.2--1028.zip'
    plugin_records=[]
    with zipfile.ZipFile(archive) as z, tarfile.open(args.out/'plugins.tar.gz', 'w:gz') as tar:
        for pkg in PLUGINS:
            prefix='ComfyUI/custom_nodes/'+pkg+'/'
            h=hashlib.sha256();count=0
            for item in sorted(z.infolist(),key=lambda x:x.filename):
                if not item.filename.startswith(prefix) or item.is_dir(): continue
                rel=Path(item.filename[len(prefix):])
                if '..' in rel.parts or any(part in {'.git','__pycache__','.venv','node_modules'} for part in rel.parts):continue
                if rel.suffix.lower() in {'.pyc','.pyd','.dll','.exe','.mp4','.gif'}:continue
                content=z.read(item)
                h.update(str(rel).encode());h.update(content)
                info=tarfile.TarInfo(pkg+'/'+rel.as_posix());info.size=len(content);info.mode=0o644
                tar.addfile(info,io.BytesIO(content));count+=1
            if not count: raise RuntimeError('Missing plugin '+pkg)
            plugin_records.append({'name':pkg,'file_count':count,'source_digest':h.hexdigest(),
                                   'source':'user_download/ComfyUI-aki-v3.2--1028.zip'})
    by_name={}
    for p in args.download_root.rglob('*'):
        if p.is_file() and p.suffix in {'.safetensors','.pt'}:by_name.setdefault(p.name,p)
    old_path=args.out/'manifest.json'
    old=json.loads(old_path.read_text(encoding='utf-8')) if old_path.exists() else {}
    old_weights={r['source']:r for r in old.get('weights',[])}
    weights=[]
    for category,names in WEIGHTS.items():
        for name in names:
            p=by_name[name];previous=old_weights.get(str(p))
            sha=previous['sha256'] if previous and previous['size_bytes']==p.stat().st_size else digest(p)
            weights.append({'source':str(p),'original_name':name,'relative_path':category+'/'+ALIASES.get(name,name),
                            'size_bytes':p.stat().st_size,'sha256':sha})
            print(name,round(p.stat().st_size/2**30,3),'GiB',flush=True)
    manifest={'plugins':plugin_records,'weights':weights,'base_comfy_revision':'a8686f2b33fc540f137df50c0f0719953830a5e7'}
    old_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Prepared',len(plugin_records),'plugins',len(weights),'weights',round(sum(r['size_bytes'] for r in weights)/2**30,3),'GiB')

if __name__=='__main__':main()
