"""Run inspectable clothing edits using the project's Qwen and H3 environments.

The colour mask is specific to a blue outfit against a neutral background.
Inspect mask-preview.jpg before using it; supply corrected masks for other input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import av
import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aigc.h3_extended import build_h3_extended
from aigc.workflows import Settings, build_qwen_workflow

STYLES = {
    'knit': 'an ivory cable-knit sweater with a modest neckline and loose long sleeves, with matching ivory knit shorts of the original length',
    'denim': 'a light blue denim button-front overshirt with loose long sleeves and matching light blue denim shorts of the original length',
    'business': 'a charcoal grey loose tailored blazer over a plain modest white top, with matching charcoal grey tailored shorts of the original length',
}





class Gateway:
    def __init__(self, port=8766):
        self.client = httpx.Client(base_url=f'http://127.0.0.1:{port}', headers={'X-AIGC-Client': 'local'},
                                   trust_env=False, timeout=180)

    def connect(self, engine):
        status = self.client.get('/api/status').json()
        if status.get('active_job') or status.get('comfy', {}).get('pending'):
            raise RuntimeError('Another task is active; wait before switching environments')
        self.client.post('/api/connect', params={'engine': engine}).raise_for_status()
        deadline = time.monotonic()+300
        while time.monotonic() < deadline:
            state = self.client.get('/api/status').json()['session']
            if state['state'] == 'ready' and state['engine'] == engine:
                print('READY', engine, flush=True)
                return self.client.get('/comfy/object_info').json()
            if state['state'] == 'error':
                raise RuntimeError(state)
            time.sleep(2)
        raise TimeoutError('Environment startup timeout')

    def upload(self, path, name=None):
        with path.open('rb') as f:
            r = self.client.post('/comfy/upload/image', files={'image': (name or path.name, f)}, data={'overwrite': 'true'})
        r.raise_for_status()
        return r.json()['name']

    def run(self, graph, folder, timeout=1800):
        folder.mkdir(parents=True, exist_ok=True)
        dump(folder/'workflow-api.json', graph)
        r = self.client.post('/comfy/prompt', json={'prompt': graph, 'client_id': 'video-clothing'})
        if r.is_error:
            raise RuntimeError(r.text[:6000])
        prompt = r.json()['prompt_id']
        dump(folder/'submitted.json', {'prompt_id': prompt})
        print('SUBMITTED', folder.name, prompt, flush=True)
        deadline, reported = time.monotonic()+timeout, 0
        while time.monotonic() < deadline:
            state = self.client.get('/api/status').json()
            record = next((x for x in state['comfy']['jobs'] if x['prompt_id'] == prompt), None)
            if time.monotonic()-reported > 25:
                print('PROGRESS', folder.name, record['state'] if record else 'queued', flush=True)
                reported = time.monotonic()
            if record and record['state'] in {'saved', 'failed', 'cancelled', 'download_error'}:
                dump(folder/'archive.json', record)
                history = self.client.get('/comfy/history/'+prompt).json()
                dump(folder/'history.json', history)
                if record['state'] != 'saved':
                    raise RuntimeError(json.dumps(history, ensure_ascii=False)[-12000:])
                paths = []
                for i, item in enumerate(record['files']):
                    data = self.client.get(f"/api/comfy/jobs/{record['id']}/files/{item['local_name']}").content
                    if hashlib.sha256(data).hexdigest() != item['sha256']:
                        raise RuntimeError('Download checksum mismatch')
                    path = folder/f'result-{i:02}{Path(item["local_name"]).suffix}'
                    path.write_bytes(data)
                    paths.append(path)
                print('SAVED', folder.name, [str(p) for p in paths], flush=True)
                return paths
            time.sleep(2)
        # Only cancel the prompt created by this process.
        queue = self.client.get('/comfy/queue').json()
        if any(row[1] == prompt for row in queue.get('queue_running', [])):
            self.client.post('/comfy/interrupt', json={}).raise_for_status()
        else:
            self.client.post('/comfy/queue', json={'delete': [prompt]}).raise_for_status()
        raise TimeoutError('Owned prompt timed out')

from aigc.clothing import (dump, command, blue_clothing_mask, prepare, ClothingRepair,
                           restore_original_audio, composite_video, merge_window)
from aigc.clothing import refine_masks as _refine_masks

def refine_masks(out):
    return _refine_masks(out, styles=list(STYLES))

def references(out, styles, steps, port):
    api = Gateway(port)
    api.connect('qwen21')
    metadata = json.loads((out/'source.json').read_text(encoding='utf-8'))
    source = api.upload(out/'frames/00000.png', 'clothing-source.png')
    for style in styles:
        prompt = ('Edit only the clothing of the same person in the input photo. Replace the dark blue sports jersey and shorts with '
                  + STYLES[style] + '. Preserve exactly the original body pose, proportions, face, hairstyle, expression, hands, legs, '
                  'camera viewpoint, framing, lighting and background. Keep the loose sleeve silhouette and the shorts coverage. '
                  'Remove all original numbers, sports logos, patches and lettering. Realistic fabric, accurate seams and folds. '
                  'One single photograph, no collage, no additional people.')
        settings = Settings(engine='qwen21', mode='edit', prompt=prompt, width=metadata['work_width'],
                            height=metadata['work_height'], steps=steps, seed=42)
        graph = build_qwen_workflow(settings, [source], 'clothing-'+style)
        paths = api.run(graph, out/'references'/style)
        generated = np.array(Image.open(next(p for p in paths if p.suffix == '.png')).convert('RGB'))
        rgb = np.array(Image.open(out/'frames/00000.png').convert('RGB'))
        generated = cv2.resize(generated, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        mask = np.array(Image.open(out/'masks/00000.png'))
        alpha = cv2.GaussianBlur(mask.astype(np.float32)/255, (5, 5), 0)[..., None]
        # The mask is from the original frame, never from a possibly drifting generated face.
        result = np.rint(alpha*generated+(1-alpha)*rgb).astype(np.uint8)
        Image.fromarray(result).save(out/'references'/f'{style}.png')

def poses(out, port):
    api = Gateway(port)
    api.client.post('/api/disconnect').raise_for_status()
    info = api.connect('h3')
    dump(out/'h3-object-info.json', info)
    if 'AIGCSaveClothingPose' not in info:
        raise RuntimeError('Clothing pose export helper is not installed')
    source = api.upload(out/'source-work.mp4')
    meta = json.loads((out/'source.json').read_text(encoding='utf-8'))
    graph = {
        '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':'sdpose_wholebody_fp16.safetensors'}},
        '2':{'class_type':'VHS_LoadVideo','inputs':{'video':source,'force_rate':meta['fps'],
              'custom_width':meta['work_width'],'custom_height':meta['work_height'],
              'frame_load_cap':meta['frames'],'skip_first_frames':0,'select_every_nth':1,'format':'None'}},
        '3':{'class_type':'SDPoseKeypointExtractor','inputs':{'model':['1',0],'vae':['1',2],'image':['2',0],'batch_size':8}},
        '4':{'class_type':'SDPoseDrawKeypoints','inputs':{'keypoints':['3',0],'draw_body':True,'draw_hands':True,
              'draw_face':True,'draw_feet':False,'stick_width':3,'face_point_size':1,'score_threshold':.2,'draw_head':True}},
        '5':{'class_type':'CreateVideo','inputs':{'images':['4',0],'fps':meta['fps']}},
        '6':{'class_type':'SaveVideo','inputs':{'video':['5',0],'filename_prefix':'clothing-pose','format':'mp4',
              'format.codec':'h264','format.codec.encoding':'re-encode','format.codec.encoding.crf':12}},
        '7':{'class_type':'AIGCSaveClothingPose','inputs':{'keypoints':['3',0],'filename_prefix':'clothing-pose-data'}},
    }
    paths = api.run(graph, out/'pose')
    for path in paths:
        if path.suffix == '.json': (out/'pose.json').write_bytes(path.read_bytes())
        if path.suffix == '.mp4': (out/'pose-work.mp4').write_bytes(path.read_bytes())

def person_masks(out,port):
    api=Gateway(port)
    status=api.client.get('/api/status').json()
    if status.get('active_job') or status.get('comfy',{}).get('pending'):
        raise RuntimeError('Wait for the active inference before person segmentation')
    if status['session']['state']!='ready' or status['session']['engine']!='h3':
        api.connect('h3')
    api.upload(out/'source-work.mp4')
    root=api.client.get('/api/diagnostics').json()['remote']['root']
    command([sys.executable,ROOT/'scripts/dgx_exec.py',ROOT/'scripts/remote_clothing_person.py',root])
    response=api.client.get('/comfy/view',params={'filename':'clothing-person.mkv','type':'output'})
    response.raise_for_status()
    (out/'person-mask.mkv').write_bytes(response.content)
    print('PERSON MASK SAVED',flush=True)


def inpaint_graph(info, source, mask, reference, style, start, length, width, height, fps, steps=4, control='masked_edges', pose_video=None, method='union',ref_scale=.5):
    if method in {'character','character-pose'}:
        graph=build_h3_extended('h3-character-swap',info,width=width,height=height,length=length)
        graph['20']['inputs']['image']=reference
        graph['21']['inputs'].update(video=source,force_rate=fps,skip_first_frames=start,frame_load_cap=length,
                                     custom_width=width,custom_height=height,format='None')
        graph['7']['inputs']['prompt']=('body_swap: Replace the main person in <Video 1> with the same person from <Picture 1>, wearing '
            +STYLES[style]+'. The reference picture defines the new outfit. Precisely replicate the original motion, expressions, '
            'timing, hand positions and camera from <Video 1>. Same face, hairstyle, proportions and room. '
            'Photorealistic fabric, consistent colour and garment details. No sports numbers, patches or lettering.')
        graph['11']['inputs']['steps']=steps
        graph['15']['inputs']['fps']=fps
        graph['15']['inputs'].pop('audio',None)
        graph.pop('14',None)
        graph['16']['inputs']['format.codec.encoding.crf']=14
        if ref_scale<1 and width>=576:
            graph['46']={'class_type':'ImageScaleBy','inputs':{'image':['21',0],'upscale_method':'area','scale_by':ref_scale}}
            graph['7']['inputs']['ref_videos.ref_video_0']=['46',0]
        if method=='character-pose':
            graph['45']={'class_type':'VHS_LoadVideo','inputs':dict(graph['21']['inputs'],video=pose_video)}
            graph['25']={'class_type':'ModelPatchLoader','inputs':{'name':'minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors'}}
            graph['26']={'class_type':'MiniMaxH3FunControlNetApply','inputs':{'model':['23',0],'model_patch':['25',0],
                'vae':['4',0],'strength':.65,'start_percent':0.0,'end_percent':.85,'control_video':['45',0]}}
            graph['8']['inputs']['model']=['26',0]
            graph['11']['inputs']['model']=['26',0]
        return graph
    graph = build_h3_extended('h3-union-control', info, width=width, height=height, length=length)
    graph['20']['inputs']['image'] = reference
    graph['21']['inputs'].update(video=source, force_rate=fps, skip_first_frames=start, frame_load_cap=length,
                                 custom_width=width, custom_height=height, format='None')
    graph['40'] = {'class_type':'VHS_LoadVideo', 'inputs':dict(graph['21']['inputs'], video=mask)}
    graph['41'] = {'class_type':'ImageToMask', 'inputs':{'image':['40',0], 'channel':'red'}}
    graph['42'] = {'class_type':'GrowMask', 'inputs':{'mask':['41',0], 'expand':5, 'tapered_corners':True}}
    graph['26']['inputs'].update(mask=['42',0], source_video=['21',0], strength=.9, end_percent=1.0)
    if method=='union-control':
        graph['26']['inputs'].pop('mask',None)
        graph['26']['inputs'].pop('source_video',None)
    if control == 'masked_edges':
        # Suppress old jersey numbers and cloth texture in the edge control.
        graph['43'] = {'class_type':'ImageCompositeMasked', 'inputs':{'destination':['24',0], 'source':['44',0],
                     'x':0, 'y':0, 'resize_source':False, 'mask':['42',0]}}
        graph['44'] = {'class_type':'EmptyImage', 'inputs':{'width':width,'height':height,'batch_size':length,'color':0}}
        graph['26']['inputs']['control_video'] = ['43',0]
    elif control == 'none':
        graph['26']['inputs'].pop('control_video', None)
    elif control == 'pose':
        graph['45']={'class_type':'VHS_LoadVideo','inputs':dict(graph['21']['inputs'],video=pose_video)}
        graph['26']['inputs']['control_video']=['45',0]
    graph['7']['inputs']['prompt'] = ('The exact person and the exact motion of the source video. Change only the clothes to '
          + STYLES[style] + '. The first picture defines the new outfit. Photorealistic clothing follows the original body motion '
          'and occlusion by the original hands. Same face, hair, expression, body proportions, legs, camera and room. '
          'No sports logos, no numbers, no text, no extra limbs, no additional people.')
    graph['11']['inputs']['steps'] = steps
    graph['15']['inputs']['fps'] = fps
    graph['15']['inputs'].pop('audio', None)
    graph.pop('14', None)
    graph['16']['inputs']['format.codec.encoding.crf'] = 14
    return graph

def sample(out, style, start, length, width, height, steps, control, port,method='union'):
    api = Gateway(port)
    info = api.connect('h3')
    dump(out/'h3-object-info.json', info)
    source = api.upload(out/'source-work.mp4')
    mask = api.upload(out/'mask-work.mp4')
    reference = api.upload(out/'references'/f'{style}.png')
    pose_video = api.upload(out/'pose-work.mp4') if control=='pose' else None
    metadata = json.loads((out/'source.json').read_text(encoding='utf-8'))
    graph = inpaint_graph(info, source, mask, reference, style, start, length, width, height, metadata['fps'], steps, control, pose_video,method)
    return api.run(graph, out/'samples'/f'{style}-{start}-{length}-{width}-{steps}-{control}-{method}')

def read_video(path):
    with av.open(str(path)) as container:
        return [frame.to_ndarray(format='rgb24') for frame in container.decode(video=0)]




def compose_sample(out,style,folder):
    graph=json.loads((folder/'workflow-api.json').read_text(encoding='utf-8'))
    start=graph['21']['inputs']['skip_first_frames']
    path=next(folder.glob('result-*.mp4'))
    frames=read_video(path)
    meta=json.loads((out/'source.json').read_text(encoding='utf-8'))
    frames=frames[:min(len(frames),meta['frames']-start)]
    return composite_video(out,style,frames,folder/'composited.mp4',start)


def compose_full(out,styles):
    """Reassemble cached windows and composite without contacting the GPU."""
    meta=json.loads((out/'source.json').read_text(encoding='utf-8'))
    for style in styles:
        folder=out/'results'/style
        segments=json.loads((folder/'segments.json').read_text(encoding='utf-8'))
        cloth_dir=folder/'cloth-frames'
        covered=0
        for segment in segments:
            start=segment['start']
            if start>covered: raise RuntimeError('A cached window is missing')
            paths=sorted((folder/f'window-{start:05}').glob('result-*.mp4'))
            if not paths: raise RuntimeError(f'Missing model video at frame {start}')
            generated=read_video(paths[0])
            if len(generated)!=segment['model_frames']:
                raise RuntimeError('Cached model frame count changed')
            merge_window(cloth_dir,generated,start,segment['valid_frames'],covered-start)
            covered=start+segment['valid_frames']
        if covered!=meta['frames']: raise RuntimeError('Cached video is incomplete')
        frames=[cloth_dir/f'{i:05}.png' for i in range(meta['frames'])]
        composite_video(out,style,frames,out/'results'/f'{style}.mp4')

def full_video(out,styles,width,height,steps,control,port,chunk=124,overlap=22,method='union'):
    if (chunk-5)%17 or chunk<=overlap: raise ValueError('Invalid H3 chunk length or overlap')
    api=Gateway(port)
    info=api.connect('h3')
    meta=json.loads((out/'source.json').read_text(encoding='utf-8'))
    source=api.upload(out/'source-work.mp4')
    mask=api.upload(out/'mask-work.mp4')
    pose_video=api.upload(out/'pose-work.mp4') if control=='pose' else None
    for style in styles:
        cloth_dir=out/'results'/style/'cloth-frames'
        cloth_dir.mkdir(parents=True,exist_ok=True)
        reference_path=out/'references'/f'{style}.png'
        segments=[]
        for start in range(0,meta['frames'],chunk-overlap):
            valid=min(chunk,meta['frames']-start)
            length=5+17*max(0,int(np.ceil((valid-5)/17)))
            folder=out/'results'/style/f'window-{start:05}'
            reference_path=folder/'first-reference.png' if start else out/'references'/f'{style}.png'
            if start and not (reference_path.exists() and list(folder.glob('result-*.mp4'))):
                original=np.array(Image.open(out/'frames'/f'{start:05}.png').convert('RGB'))
                previous=np.array(Image.open(cloth_dir/f'{start:05}.png').convert('RGB'))
                previous=cv2.resize(previous,(original.shape[1],original.shape[0]),interpolation=cv2.INTER_LANCZOS4)
                previous=ClothingRepair(out,style).apply(previous,start)
                first_mask=np.array(Image.open(out/'masks'/f'{start:05}.png'))
                alpha=cv2.GaussianBlur(first_mask.astype(np.float32)/255,(5,5),0)[...,None]
                reference_path=folder/'first-reference.png'
                folder.mkdir(parents=True,exist_ok=True)
                Image.fromarray(np.rint(alpha*previous+(1-alpha)*original).astype(np.uint8)).save(reference_path)
            reference=api.upload(reference_path,f'clothing-{style}-{start}-reference.png')
            graph=inpaint_graph(info,source,mask,reference,style,start,length,width,height,meta['fps'],steps,control,pose_video,method)
            saved=list(folder.glob('result-*.mp4'))
            old=json.loads((folder/'workflow-api.json').read_text(encoding='utf-8')) if (folder/'workflow-api.json').exists() else None
            if saved and old==graph:
                paths=saved
                print('REUSING',style,start,flush=True)
            else:
                paths=api.run(graph,folder)
            generated=read_video(next(p for p in paths if p.suffix=='.mp4'))
            if len(generated)!=length: raise RuntimeError(f'H3 produced {len(generated)} rather than {length} frames')
            merge_window(cloth_dir,generated,start,valid,overlap)
            segments.append({'start':start,'valid_frames':valid,'model_frames':length,'workflow':str(folder/'workflow-api.json')})
            dump(out/'results'/style/'segments.json',segments)
            print('WINDOW',style,start,valid,'of',meta['frames'],flush=True)
        frames=[cloth_dir/f'{i:05}.png' for i in range(meta['frames'])]
        if not all(p.exists() for p in frames): raise RuntimeError('An original video frame is missing')
        composite_video(out,style,frames,out/'results'/f'{style}.mp4')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare','references','pose','person','refine-masks','sample','compose-sample','compose-full','full','run'])
    p.add_argument('--source', type=Path, default=ROOT/'test.mp4')
    p.add_argument('--out', type=Path, default=ROOT/'data/clothing/test-20261115')
    p.add_argument('--styles', nargs='+', choices=list(STYLES), default=list(STYLES))
    p.add_argument('--port', type=int, default=8766)
    p.add_argument('--steps', type=int)
    p.add_argument('--width', type=int)
    p.add_argument('--height', type=int)
    p.add_argument('--start', type=int, default=0)
    p.add_argument('--length', type=int, default=73)
    p.add_argument('--control', choices=['masked_edges','none','pose'], default='pose')
    p.add_argument('--sample-folder', type=Path)
    p.add_argument('--method',choices=['union','union-control','character','character-pose'],default='character-pose')
    a = p.parse_args()
    if a.steps is None: a.steps=25 if a.action=='references' else 4
    if a.width is None: a.width=576 if a.action in {'full','run'} else 288
    if a.height is None: a.height=1024 if a.action in {'full','run'} else 512
    a.out.mkdir(parents=True, exist_ok=True)
    if a.action == 'prepare':
        prepare(a.source, a.out)
    elif a.action == 'references':
        references(a.out, a.styles, a.steps, a.port)
    elif a.action == 'pose':
        poses(a.out,a.port)
    elif a.action == 'person':
        person_masks(a.out,a.port)
    elif a.action == 'refine-masks':
        refine_masks(a.out)
    elif a.action == 'compose-sample':
        compose_sample(a.out,a.styles[0],a.sample_folder)
    elif a.action == 'compose-full':
        compose_full(a.out,a.styles)
    elif a.action == 'full':
        full_video(a.out,a.styles,a.width,a.height,a.steps,a.control,a.port,method=a.method)
    elif a.action == 'run':
        if (a.out/'source.json').exists():
            saved_source=json.loads((a.out/'source.json').read_text(encoding='utf-8'))['source']
            if Path(saved_source).resolve()!=a.source.resolve():
                raise ValueError('Use a new --out directory for a different source video')
        if not (a.out/'source.json').exists(): prepare(a.source,a.out)
        if not all((a.out/'references'/s/'result-00.png').exists() for s in a.styles):
            references(a.out,a.styles,25,a.port)
        if not (a.out/'pose.json').exists(): poses(a.out,a.port)
        if not (a.out/'person-mask.mkv').exists(): person_masks(a.out,a.port)
        refine_masks(a.out)
        for style in a.styles:
            path=a.out/'references'/f'{style}.png'
            Image.fromarray(ClothingRepair(a.out,style).apply(np.array(Image.open(path).convert('RGB')),0)).save(path)
        full_video(a.out,a.styles,a.width,a.height,a.steps,a.control,a.port,method=a.method)
    else:
        sample(a.out, a.styles[0], a.start, a.length, a.width, a.height, a.steps, a.control, a.port,a.method)

if __name__ == '__main__':
    main()
