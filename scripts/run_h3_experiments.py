"""Run and archive real H3 workflows through the same gateway used by the UI."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import av
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aigc.h3_extended import TEMPLATES, build_h3_extended

def main():
    p=argparse.ArgumentParser();p.add_argument('names',nargs='+',choices=list(TEMPLATES));p.add_argument('--port',type=int,default=8766)
    p.add_argument('--width',type=int,default=320);p.add_argument('--height',type=int,default=192);p.add_argument('--length',type=int,default=39)
    p.add_argument('--source',default='source.mp4');p.add_argument('--reference',default='driver.png')
    p.add_argument('--replacement',default='person.png');p.add_argument('--audio',default='reference.wav')
    p.add_argument('--head-reference',default='person-head.png')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--timeout',type=int,default=900);a=p.parse_args()
    out=ROOT/'test-results/h3-extended';results_path=out/'experiments.json'
    results=json.loads(results_path.read_text(encoding='utf-8')) if results_path.exists() else []
    run_out=out/'runs'/time.strftime('%Y%m%d_%H%M%S');run_out.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url=f'http://127.0.0.1:{a.port}',headers={'X-AIGC-Client':'local'},trust_env=False,timeout=120) as c:
        state=c.get('/api/status').json()
        if state['session']['state']!='ready' or state['session']['engine']!='h3':raise RuntimeError('H3 is not ready')
        if state['active_job'] or state['comfy']['pending']:raise RuntimeError('Another job is active')
        info=c.get('/comfy/object_info').json();(out/'object-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
        for filename,source in [('reference.png',a.reference),('person.png',a.replacement),('person-head.png',a.head_reference),('source.mp4',a.source),('reference.wav',a.audio),('my_person.safetensors','my_person.safetensors')]:
            path=out/'assets'/source
            if path.exists():
                with path.open('rb') as f:r=c.post('/comfy/upload/image',files={'image':(filename,f)},data={'overwrite':'true'})
                r.raise_for_status()
        for name in a.names:
            graph=build_h3_extended(name,info,width=a.width,height=a.height,length=a.length,seed=a.seed)
            if name in {'h3-character-swap','h3-head-swap','h3-refmod'}:graph['20']['inputs']['image']='person.png'
            if name=='h3-head-swap' and (out/'assets'/a.head_reference).exists():graph['20']['inputs']['image']='person-head.png'
            started=time.monotonic();row={'name':name,'width':a.width,'height':a.height,'frames':a.length,'seed':a.seed,'started':time.strftime('%Y-%m-%d %H:%M:%S')}
            (out/(name+'.last.api.json')).write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
            (run_out/(name+'.api.json')).write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
            r=c.post('/comfy/prompt',json={'prompt':graph,'client_id':'h3-extended-experiments'})
            if r.is_error:
                row.update(state='validation_failed',error=r.text,seconds=time.monotonic()-started)
            else:
                row['prompt_id']=r.json()['prompt_id'];print('RUNNING',name,row['prompt_id'],flush=True)
                deadline=time.monotonic()+a.timeout
                while time.monotonic()<deadline:
                    status=c.get('/api/status').json()
                    record=next((x for x in status['comfy']['jobs'] if x['prompt_id']==row['prompt_id']),None)
                    if record and record['state'] in {'saved','failed','cancelled','download_error'}:
                        row.update(state=record['state'],archive=record,seconds=time.monotonic()-started)
                        history=c.get('/comfy/history/'+row['prompt_id']).json().get(row['prompt_id'],{})
                        (out/(name+'.last.history.json')).write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
                        (run_out/(name+'.history.json')).write_text(json.dumps(history,ensure_ascii=False,indent=2),encoding='utf-8')
                        if record['state']=='failed':
                            row['error']=[m for m in history.get('status',{}).get('messages',[]) if m[0]=='execution_error']
                        row['outputs']=[]
                        for f in record['files']:
                            content=c.get(f"/api/comfy/jobs/{record['id']}/files/{f['local_name']}").content
                            path=run_out/(name+'.result'+Path(f['local_name']).suffix)
                            path.write_bytes(content)
                            assert hashlib.sha256(content).hexdigest()==f['sha256']
                            media={'path':str(path),'bytes':len(content),'sha256':f['sha256']}
                            if path.suffix=='.mp4':
                                with av.open(str(path)) as video:
                                    media.update(duration=float(video.duration/av.time_base),streams=[x.type for x in video.streams])
                                    frame=next(video.decode(video=0));media.update(width=frame.width,height=frame.height)
                                    frame.to_image().save(path.with_suffix('.first.png'))
                            if name=='h3-refmod' and path.suffix=='.safetensors':
                                reference=out/'assets/my_person.safetensors';reference.write_bytes(content)
                                r=c.post('/comfy/upload/image',files={'image':('my_person.safetensors',content)},data={'overwrite':'true'})
                                r.raise_for_status()
                            row['outputs'].append(media)
                        break
                    time.sleep(2)
                else:
                    c.post('/comfy/interrupt',json={});row.update(state='timeout',seconds=time.monotonic()-started)
            results.append(row);results_path.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print('RESULT',json.dumps(row,ensure_ascii=False),flush=True)
            if row['state'] not in {'saved','failed','validation_failed'}:break

if __name__=='__main__':main()
