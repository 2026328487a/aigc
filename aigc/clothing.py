"""Shared video clothing masks, motion repair and loss-aware compositing.

This module uses no local gateway, SSH or ComfyUI imports and is copied into
the standalone ComfyUI clothing node package at deployment.
"""
import hashlib
import json
from pathlib import Path
import subprocess

import av
import cv2
import numpy as np
from PIL import Image, ImageDraw

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def command(args):
    subprocess.run([str(a) for a in args], check=True)

def blue_clothing_mask(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    blue = cv2.inRange(hsv, np.array([93, 35, 12]), np.array([135, 255, 210]))
    blue[:round(len(blue) * .12)] = 0
    blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask = np.zeros(blue.shape, np.uint8)
    largest = max((cv2.contourArea(c) for c in contours), default=0)
    for c in contours:
        if cv2.contourArea(c) >= max(100, largest * .015):
            cv2.drawContours(mask, [c], -1, 255, cv2.FILLED)
    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    skin = ((r > 145) & (g > 95) & (b > 65) & (r-g >= 15) & (g-b >= 3)
            & (r-g < 90) & (g-b < 75)).astype(np.uint8)
    skin = cv2.dilate(skin, np.ones((3, 3), np.uint8))
    mask[skin > 0] = 0
    return mask

def prepare(source, out, width=576, height=1024, encode_videos=True, interrupt=None):
    frames_dir = out / 'frames'
    masks_dir = out / 'masks'
    frames_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate)
        dimensions = (stream.width, stream.height)
        pts = []
        for index, frame in enumerate(container.decode(video=0)):
            if interrupt: interrupt()
            rgb = frame.to_ndarray(format='rgb24')
            scaled = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
            mask = blue_clothing_mask(scaled)
            Image.fromarray(scaled).save(frames_dir / f'{index:05}.png')
            Image.fromarray(mask).save(masks_dir / f'{index:05}.png')
            pts.append(float(frame.pts * frame.time_base))
            if index in (0, 60, 105, 150, 210, 255, 315, 373):
                overlay = scaled.copy()
                selected = mask > 0
                overlay[selected] = (.45 * overlay[selected] + .55 * np.array([0, 220, 80])).astype(np.uint8)
                panel = Image.new('RGB', (360, 344), '#20252c')
                panel.paste(Image.fromarray(scaled).resize((180, 320)), (0, 24))
                panel.paste(Image.fromarray(overlay).resize((180, 320)), (180, 24))
                ImageDraw.Draw(panel).text((8, 5), f'Frame {index}  {pts[-1]:.2f}s  original / mask', fill='white')
                samples.append(panel)
    sheet = Image.new('RGB', (1440, 688), '#20252c')
    for i, panel in enumerate(samples):
        sheet.paste(panel, ((i % 4)*360, (i//4)*344))
    sheet.save(out / 'mask-preview.jpg', quality=94)
    metadata = {'source': str(source.resolve()), 'fps': fps, 'width': dimensions[0], 'height': dimensions[1],
                'work_width': width, 'work_height': height, 'frames': len(pts), 'timestamps': pts,
                'mask_method': 'source blue outfit contours, filled logos, visible skin exclusion; manually inspect'}
    dump(out / 'source.json', metadata)
    for folder, name, crf in ([(frames_dir, 'source-work.mp4', '16'), (masks_dir, 'mask-work.mp4', '0')] if encode_videos else []):
        command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-framerate', str(fps), '-i', folder/'%05d.png',
                 '-c:v', 'libx264', '-crf', crf, '-pix_fmt', 'yuv420p', out/name])
    print(json.dumps({k:v for k,v in metadata.items() if k != 'timestamps'}), flush=True)

def refine_masks(out, styles=(), encode_video=True, interrupt=None):
    pose = json.loads((out/'pose.json').read_text(encoding='utf-8'))
    meta = json.loads((out/'source.json').read_text(encoding='utf-8'))
    if len(pose) != meta['frames']:
        raise RuntimeError('Pose frames do not match the original video')
    panels = []
    person_container=av.open(str(out/'person-mask.mkv')) if (out/'person-mask.mkv').exists() else None
    person_frames=person_container.decode(video=0) if person_container else None
    for index, frame in enumerate(pose):
        if interrupt: interrupt()
        rgb = np.array(Image.open(out/'frames'/f'{index:05}.png'))
        person_mask=next(person_frames).to_ndarray(format='gray') if person_frames else np.full(rgb.shape[:2],255,np.uint8)
        person_mask=cv2.dilate((person_mask>127).astype(np.uint8)*255,np.ones((7,7),np.uint8))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        blue = cv2.inRange(hsv, np.array([93,20,8]), np.array([138,255,215]))
        r,g,b=rgb.astype(np.int16).transpose(2,0,1)
        neutral=(rgb.max(2).astype(np.int16)-rgb.min(2).astype(np.int16)<=12)&(rgb.max(2)>85)
        red=(r-g>22)&(np.abs(g-b)<10)&(r>90)
        nearby=cv2.dilate(blue,np.ones((33,33),np.uint8))>0
        blue=np.maximum(blue,((neutral|red)&nearby).astype(np.uint8)*255)
        blue[person_mask==0]=0
        blue = cv2.morphologyEx(blue, cv2.MORPH_CLOSE, np.ones((25,25),np.uint8))
        contours, _ = cv2.findContours(blue,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        mask = np.zeros(blue.shape,np.uint8)
        for c in contours:
            if cv2.contourArea(c)>80: cv2.drawContours(mask,[c],-1,255,cv2.FILLED)
        protected = np.zeros(blue.shape,np.uint8)
        seeds = np.zeros(blue.shape,np.uint8)
        for person in frame.get('people',[]):
            face = np.array(person['face_keypoints_2d']).reshape(-1,3)
            valid = face[(face[:,2]>.15)&(face[:,0]>=0)&(face[:,1]>=0),:2]
            if len(valid)>5:
                x,y,w,h = cv2.boundingRect(valid.astype(np.int32))
                # Cover hair above the face and exclude the original face itself.
                cv2.rectangle(protected,(max(0,x-25),max(0,y-65)),(x+w+25,y+h+6),255,-1)
                cv2.fillConvexPoly(seeds,cv2.convexHull(valid.astype(np.int32)),255)
            for key in ('hand_left_keypoints_2d','hand_right_keypoints_2d'):
                hand=np.array(person[key]).reshape(-1,3)
                valid=hand[(hand[:,2]>.15)&(hand[:,0]>=0)&(hand[:,1]>=0),:2]
                if len(valid)>2:
                    hand_seed=np.zeros(blue.shape,np.uint8)
                    cv2.fillConvexPoly(hand_seed,cv2.convexHull(valid.astype(np.int32)),255)
                    hand_seed=cv2.dilate(hand_seed,np.ones((13,13),np.uint8))
                    seeds=np.maximum(seeds,hand_seed)
        r,g,b=rgb.astype(np.int16).transpose(2,0,1)
        skin=((r>85)&(g>40)&(b>25)&(r-g>=14)&(g-b>=4)&(r-g<100)&(g-b<85)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(skin,8)
        # Printed logos are not protected merely because they share skin colours.
        selected=np.unique(labels[(seeds>0)&(skin>0)])
        bottom=np.unique(labels[-2:][skin[-2:]>0])
        keep=np.zeros(count,np.uint8)
        keep[np.concatenate([selected,bottom])]=255
        keep[0]=0
        skin_keep=keep[labels]
        # Protect visible skin itself. Expanding this into the neighbouring
        # jersey leaves a dark old-clothing outline around the hands.
        protected=np.maximum(protected,skin_keep)
        mask=cv2.dilate(mask,np.ones((9,9),np.uint8))
        mask[person_mask==0]=0
        mask[protected>0]=0
        Image.fromarray(mask).save(out/'masks'/f'{index:05}.png')
        if index in (0,60,105,150,210,255,315,373):
            overlay=rgb.copy(); selected=mask>0
            overlay[selected]=(.45*overlay[selected]+.55*np.array([0,220,80])).astype(np.uint8)
            panel=Image.new('RGB',(360,344),'#20252c')
            panel.paste(Image.fromarray(rgb).resize((180,320)),(0,24))
            panel.paste(Image.fromarray(overlay).resize((180,320)),(180,24))
            ImageDraw.Draw(panel).text((8,5),f'Frame {index}  original / refined clothing mask',fill='white')
            panels.append(panel)
    sheet=Image.new('RGB',(1440,688),'#20252c')
    for i,panel in enumerate(panels): sheet.paste(panel,((i%4)*360,(i//4)*344))
    sheet.save(out/'mask-preview-refined.jpg',quality=94)
    if person_container: person_container.close()
    if encode_video:
        command(['ffmpeg','-hide_banner','-loglevel','error','-y','-framerate',str(meta['fps']),'-i',out/'masks/%05d.png',
                 '-c:v','libx264','-crf','0','-pix_fmt','yuv420p',out/'mask-work.mp4'])
    # Rebuild the existing style references with the corrected masks, without new inference.
    rgb=np.array(Image.open(out/'frames/00000.png').convert('RGB'))
    mask=np.array(Image.open(out/'masks/00000.png'))
    alpha=cv2.GaussianBlur(mask.astype(np.float32)/255,(5,5),0)[...,None]
    for style in styles:
        reference_path=out/'references'/style/'result-00.png'
        if not reference_path.exists(): continue
        generated=np.array(Image.open(reference_path).convert('RGB'))
        generated=cv2.resize(generated,(rgb.shape[1],rgb.shape[0]),interpolation=cv2.INTER_LANCZOS4)
        result=np.rint(alpha*generated+(1-alpha)*rgb).astype(np.uint8)
        Image.fromarray(result).save(out/'references'/f'{style}.png')
    meta['mask_method']='YOLO person silhouette + blue outfit/printed areas + SDPose face/hand and connected visible skin protection'
    dump(out/'source.json',meta)
    print('REFINED',len(pose),'masks',flush=True)

class ClothingRepair:
    """Fill misplaced background inside the original outfit using source motion.

    Colour classification uses the approved outfit reference. Optical flow is
    computed between original frames, so the correction follows their timing.
    """
    def __init__(self,out,style):
        self.out=out
        self.reference=np.array(Image.open(out/'references'/f'{style}.png').convert('RGB'))
        mask=np.array(Image.open(out/'masks/00000.png'))
        inner=cv2.erode(mask,np.ones((15,15),np.uint8))>127
        h,w=mask.shape
        torso=np.zeros(mask.shape,bool)
        torso[round(.49*h):round(.82*h),round(.35*w):round(.65*w)]=True
        inner&=torso
        if inner.sum()<6:
            inner=cv2.erode(mask,np.ones((3,3),np.uint8))>127
        if inner.sum()<6:
            raise ValueError('Clothing mask is empty or too small; inspect or supply a corrected mask video.')
        lab=cv2.cvtColor(self.reference,cv2.COLOR_RGB2LAB)
        samples=lab[inner][::max(1,int(inner.sum())//6000)].astype(np.float32)
        self.target_mean=samples.mean(axis=0)
        self.torso=torso
        cv2.setRNGSeed(42)
        _,_,self.centres=cv2.kmeans(samples,6,None,(cv2.TERM_CRITERIA_MAX_ITER|cv2.TERM_CRITERIA_EPS,40,.2),
                                  3,cv2.KMEANS_PP_CENTERS)
        self.previous=None
        self.previous_gray=None
        self.previous_mask=None
        self.flow=cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.grid_x,self.grid_y=np.meshgrid(np.arange(w,dtype=np.float32),np.arange(h,dtype=np.float32))
        self.modified=[]

    def apply(self,candidate,index):
        original=np.array(Image.open(self.out/'frames'/f'{index:05}.png').convert('RGB'))
        candidate=cv2.resize(candidate,(original.shape[1],original.shape[0]),interpolation=cv2.INTER_LANCZOS4)
        mask=np.array(Image.open(self.out/'masks'/f'{index:05}.png'))>127
        gray=cv2.cvtColor(original,cv2.COLOR_RGB2GRAY)
        if index==0:
            candidate[mask]=self.reference[mask]
        lab=cv2.cvtColor(candidate,cv2.COLOR_RGB2LAB).astype(np.float32)
        valid_torso=mask&self.torso
        if np.count_nonzero(valid_torso)>100:
            # Keep the approved garment colour while retaining generated folds.
            delta=self.target_mean-lab[valid_torso].mean(axis=0)
            delta=np.clip(delta,[-30,-14,-20],[30,14,20])
            lab[mask]=np.clip(lab[mask]+delta,0,255)
            recoloured=cv2.cvtColor(np.rint(lab).astype(np.uint8),cv2.COLOR_LAB2RGB)
            candidate[mask]=recoloured[mask]
        distance=np.full(mask.shape,np.inf,np.float32)
        for centre in self.centres:
            distance=np.minimum(distance,np.linalg.norm(lab-centre,axis=2))
        ycrcb=cv2.cvtColor(candidate,cv2.COLOR_RGB2YCrCb)
        r,g,b=candidate.astype(np.int16).transpose(2,0,1)
        invented_skin=(ycrcb[...,1]>145)&(ycrcb[...,2]<125)&(r-g>25)&(g-b>4)
        bad=mask&((distance>20)|(invented_skin&(distance>12)))
        initial_bad=bad.copy()
        if self.previous is not None:
            flow=self.flow.calc(gray,self.previous_gray,None)
            map_x,map_y=self.grid_x+flow[...,0],self.grid_y+flow[...,1]
            warped=cv2.remap(self.previous,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
            previous_mask=cv2.remap(self.previous_mask.astype(np.uint8),map_x,map_y,cv2.INTER_NEAREST)>0
            replace=bad&previous_mask
            candidate[replace]=warped[replace]
            bad[replace]=False
        if np.any(bad):
            # Exclude the surrounding room from the spatial hole fill.
            canvas=candidate.copy()
            canvas[~mask]=np.median(self.reference[np.array(Image.open(self.out/'masks/00000.png'))>127],axis=0)
            repaired=cv2.inpaint(canvas,bad.astype(np.uint8)*255,5,cv2.INPAINT_TELEA)
            candidate[bad]=repaired[bad]
        self.previous=candidate.copy()
        self.previous_gray=gray
        self.previous_mask=mask.copy()
        self.modified.append(float(np.count_nonzero(initial_bad)/max(1,np.count_nonzero(mask))))
        return candidate

def restore_original_audio(target,source):
    """Copy the complete source audio after encoding all video frames.

    A frame-limited rawvideo encode can stop before its second input's final
    AAC packets. A separate stream-copy pass preserves the complete soundtrack.
    """
    temporary=target.with_name(target.stem+'.audio-remux.mp4')
    command(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',target,'-i',source,
             '-map','0:v:0','-map','1:a:0?','-c','copy','-map_metadata','-1',
             '-movflags','+faststart',temporary])
    temporary.replace(target)

def composite_video(out, style, clothing_frames, target, start=0, interrupt=None):
    meta=json.loads((out/'source.json').read_text(encoding='utf-8'))
    count=len(clothing_frames)
    command_args=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24',
        '-s',f"{meta['width']}x{meta['height']}",'-r',str(meta['fps']),'-i','pipe:0']
    if start==0 and count==meta['frames']:
        command_args+=['-i',meta['source'],'-map','0:v:0','-map','1:a:0?','-c:a','copy']
    else:
        command_args+=['-an']
    command_args+=['-c:v','libx264','-preset','medium','-crf','16','-pix_fmt','yuv420p',
                  '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709',
                  '-frames:v',str(count),'-map_metadata','-1',str(target)]
    target.parent.mkdir(parents=True,exist_ok=True)
    process=subprocess.Popen(command_args,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    max_protected_error, panels=0,[]
    repair=ClothingRepair(out,style)
    try:
        with av.open(meta['source']) as container:
            for index,frame in enumerate(container.decode(video=0)):
                if interrupt: interrupt()
                if index<start: continue
                local=index-start
                if local>=count: break
                rgb=frame.to_ndarray(format='rgb24')
                candidate=clothing_frames[local]
                if isinstance(candidate,Path): candidate=np.array(Image.open(candidate).convert('RGB'))
                candidate=repair.apply(candidate,index)
                generated=cv2.resize(candidate,(meta['width'],meta['height']),interpolation=cv2.INTER_LANCZOS4)
                mask=np.array(Image.open(out/'masks'/f'{index:05}.png'))
                alpha=cv2.GaussianBlur(mask.astype(np.float32)/255,(5,5),0)
                alpha=cv2.resize(alpha,(meta['width'],meta['height']),interpolation=cv2.INTER_LINEAR)
                result=np.rint(alpha[...,None]*generated+(1-alpha[...,None])*rgb).astype(np.uint8)
                protected=alpha==0
                error=int(np.max(np.abs(result[protected].astype(np.int16)-rgb[protected].astype(np.int16)))) if np.any(protected) else 0
                max_protected_error=max(max_protected_error,error)
                process.stdin.write(result.tobytes())
                if local in (0,count//4,count//2,3*count//4,count-1):
                    panel=Image.new('RGB',(360,344),'#20252c')
                    panel.paste(Image.fromarray(rgb).resize((180,320)),(0,24))
                    panel.paste(Image.fromarray(result).resize((180,320)),(180,24))
                    ImageDraw.Draw(panel).text((8,5),f'{style} / frame {index} / original and edit',fill='white')
                    panels.append(panel)
        process.stdin.close()
        error_output=process.stderr.read().decode('utf-8',errors='replace')
        if process.wait()!=0: raise RuntimeError(error_output)
    except BaseException:
        process.kill(); process.wait()
        raise
    if start==0 and count==meta['frames']:
        restore_original_audio(target,meta['source'])
    sheet=Image.new('RGB',(360*len(panels),344),'#20252c')
    for i,panel in enumerate(panels): sheet.paste(panel,(i*360,0))
    sheet.save(target.with_suffix('.contact.jpg'),quality=94)
    media=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(target)]))
    video=next(s for s in media['streams'] if s['codec_type']=='video')
    if int(video['nb_frames'])!=count or video['width']!=meta['width'] or video['height']!=meta['height']:
        raise RuntimeError('Output dimensions or frame count changed')
    if abs(float(video['avg_frame_rate'].split('/')[0])/float(video['avg_frame_rate'].split('/')[1])-meta['fps'])>1e-6:
        raise RuntimeError('Output frame rate changed')
    report={'style':style,'file':str(target),'frames':count,'source_start':start,
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'max_pixel_difference_outside_edit_mask_before_encoding':max_protected_error,
            'audio_stream_copied':start==0 and count==meta['frames'],
            'audio_remux_after_video_encoding':start==0 and count==meta['frames'],
            'motion_repair_mean_fraction':float(np.mean(repair.modified)),
            'media':media}
    dump(target.with_suffix('.validation.json'),report)
    print('COMPOSITED',str(target),'protected pixel error:',max_protected_error,flush=True)
    return target

def merge_window(cloth_dir,generated,start,valid,overlap):
    """Rebuild a window; blend only its overlap with the preceding window.

    Frames left by a previous run must not be mistaken for an overlap. Starting
    from window zero makes reconstruction from saved model videos idempotent.
    """
    cloth_dir.mkdir(parents=True,exist_ok=True)
    for local,candidate in enumerate(generated[:valid]):
        target=cloth_dir/f'{start+local:05}.png'
        if start and local<overlap and target.exists():
            previous=np.array(Image.open(target).convert('RGB'))
            amount=(local+1)/(overlap+1)
            candidate=np.rint((1-amount)*previous+amount*candidate).astype(np.uint8)
        Image.fromarray(candidate).save(target)
