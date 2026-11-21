"""Verify clothing outputs and create a synchronized original/three-style video."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import av
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.video_clothing import restore_original_audio
LABELS={'knit':'米白针织','denim':'浅蓝牛仔','business':'商务休闲'}


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))


def audio_hash(path):
    return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0',
                                    '-c','copy','-f','hash','-hash','sha256','-']).decode().strip()


def font(size):
    path=Path('C:/Windows/Fonts/msyh.ttc')
    return ImageFont.truetype(str(path),size) if path.exists() else ImageFont.load_default()


def contact(path,indices):
    panels=[]
    with av.open(str(path)) as container:
        for index,frame in enumerate(container.decode(video=0)):
            if index not in indices: continue
            panel=Image.new('RGB',(180,344),'#20252c')
            panel.paste(frame.to_image().resize((180,320)),(0,24))
            ImageDraw.Draw(panel).text((6,5),f'{index:03} / {index/30:.2f}s',fill='white')
            panels.append(panel)
    sheet=Image.new('RGB',(1260,344*((len(panels)+6)//7)),'#20252c')
    for i,panel in enumerate(panels): sheet.paste(panel,((i%7)*180,(i//7)*344))
    target=path.with_suffix('.qa.jpg')
    sheet.save(target,quality=95)
    return target

def inference_stats(folder):
    records=[]
    for path in sorted(folder.glob('window-*/history.json')):
        history=json.loads(path.read_text(encoding='utf-8'))
        entry=next(iter(history.values()))
        messages=entry['status']['messages']
        times={name:data['timestamp'] for name,data in messages if 'timestamp' in data}
        graph=json.loads((path.parent/'workflow-api.json').read_text(encoding='utf-8'))
        records.append({'source_start':graph['21']['inputs']['skip_first_frames'],
                        'model_frames':graph['7']['inputs']['length'],
                        'seconds':(times['execution_success']-times['execution_start'])/1000,
                        'reference_video_scale':graph.get('46',{}).get('inputs',{}).get('scale_by',1),
                        'status':entry['status']['status_str']})
    return records


def compare(source,paths,folder,frames,fps):
    overlay=Image.new('RGBA',(1080,1920),(0,0,0,0))
    draw=ImageDraw.Draw(overlay)
    for i,label in enumerate(['原视频']+[LABELS[s] for s in paths]):
        x,y=(i%2)*540,(i//2)*960
        draw.rectangle((x,y,x+539,y+49),fill=(20,25,32,205))
        draw.text((x+16,y+5),label,font=font(28),fill='white')
    heading=folder/'comparison-labels.png'
    overlay.save(heading)
    args=['ffmpeg','-hide_banner','-loglevel','error','-y']
    for path in [source,*paths.values(),heading]: args+=['-i',str(path)]
    filters=';'.join(f'[{i}:v]scale=540:960:flags=lanczos,setsar=1,setpts=PTS-STARTPTS[v{i}]' for i in range(4))
    filters+=';[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|540_0|0_960|540_960[grid]'
    filters+=';[grid][4:v]overlay=0:0:format=auto[v]'
    target=folder/'comparison.mp4'
    args+=['-filter_complex',filters,'-map','[v]','-map','0:a:0?','-c:a','copy',
           '-c:v','libx264','-preset','medium','-crf','18','-pix_fmt','yuv420p',
           '-r',str(fps),'-frames:v',str(frames),'-movflags','+faststart',str(target)]
    subprocess.run(args,check=True)
    restore_original_audio(target,source)
    return target


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'data/clothing/test-20261115')
    p.add_argument('--styles',nargs='+',choices=list(LABELS),default=list(LABELS))
    p.add_argument('--compare',action='store_true')
    p.add_argument('--restore-audio',action='store_true',help='Restore complete original audio packets without re-encoding')
    a=p.parse_args()
    meta=json.loads((a.out/'source.json').read_text(encoding='utf-8'))
    source=Path(meta['source'])
    original_audio=audio_hash(source)
    indices=sorted(set(list(range(0,meta['frames'],20))+[101,102,123,203,204,225,305,306,327,meta['frames']-1]))
    results={}
    paths={s:a.out/'results'/f'{s}.mp4' for s in a.styles}
    for style,path in paths.items():
        if a.restore_audio:
            restore_original_audio(path,source)
            validation_path=path.with_suffix('.validation.json')
            validation=json.loads(validation_path.read_text(encoding='utf-8'))
            validation['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            validation['media']=probe(path)
            validation['audio_remux_after_video_encoding']=True
            validation_path.write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
        media=probe(path)
        video=next(s for s in media['streams'] if s['codec_type']=='video')
        with av.open(str(path)) as container:
            frames=sum(1 for _ in container.decode(video=0))
        same_audio=audio_hash(path)==original_audio
        validation=json.loads(path.with_suffix('.validation.json').read_text(encoding='utf-8'))
        assert validation['sha256']==hashlib.sha256(path.read_bytes()).hexdigest(),'Composition checksum changed'
        assert frames==meta['frames'] and int(video['nb_frames'])==frames
        assert video['width']==meta['width'] and video['height']==meta['height']
        numerator,denominator=map(int,video['avg_frame_rate'].split('/'))
        assert abs(numerator/denominator-meta['fps'])<1e-6
        assert abs(float(video['duration'])-meta['frames']/meta['fps'])<1e-5
        assert same_audio,'Original audio packets changed'
        assert validation['max_pixel_difference_outside_edit_mask_before_encoding']==0
        sheet=contact(path,indices)
        report={'file':str(path),'decoded_frames':frames,'width':video['width'],'height':video['height'],
                'fps':numerator/denominator,'duration':float(video['duration']),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'audio_packet_sha256':original_audio,
                'source_audio_packets_identical':same_audio,'contact_sheet':str(sheet),
                'protected_pixels_max_error_before_encoding':0,
                'motion_repair_mean_fraction':validation['motion_repair_mean_fraction'],
                'inference_windows':inference_stats(a.out/'results'/style)}
        results[style]=report
        print(json.dumps(report,ensure_ascii=False),flush=True)
    if a.compare:
        if list(paths)!=list(LABELS): raise ValueError('Comparison requires all three styles in the default order')
        comparison=compare(source,paths,a.out/'results',meta['frames'],meta['fps'])
        video=next(s for s in probe(comparison)['streams'] if s['codec_type']=='video')
        assert int(video['nb_frames'])==meta['frames']
        assert audio_hash(comparison)==original_audio
        results['comparison']={'file':str(comparison),'frames':int(video['nb_frames']),
                               'source_audio_packets_identical':True,
                               'sha256':hashlib.sha256(comparison.read_bytes()).hexdigest()}
        print('COMPARISON',comparison,flush=True)
    target=a.out/'results'/('review.json' if a.compare else 'review-'+'-'.join(a.styles)+'.json')
    target.write_text(json.dumps({'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                                  'results':results},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
