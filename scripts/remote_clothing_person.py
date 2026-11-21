"""Derive person silhouettes in the active AIGC session using an existing YOLO model."""
import subprocess
import sys

code = r'''
import json, os, subprocess, sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
if root.parent!=Path('/dev/shm') or not root.name.startswith('aigc-'):
    raise RuntimeError('Expected the active AIGC session directory')
os.environ['YOLO_CONFIG_DIR']=str(root/'scratch/yolo')
os.environ['YOLO_AUTOINSTALL']='false'
os.environ['MPLCONFIGDIR']=str(root/'scratch/matplotlib')
import cv2
import numpy as np
from ultralytics import YOLO
source=root/'input/source-work.mp4'
target=root/'output/clothing-person.mkv'
capture=cv2.VideoCapture(str(source))
w,h=round(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps=capture.get(cv2.CAP_PROP_FPS)
capture.release()
model=YOLO('/home/jnu/models/aigc/h3-extended/models/ultralytics/segm/person_yolov8m-seg.pt')
encoder=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','gray',
    '-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0','-c:v','ffv1',str(target)],stdin=subprocess.PIPE)
count=0
try:
    for result in model.predict(source=str(source),stream=True,imgsz=640,device=0,verbose=False):
        mask=np.zeros((h,w),np.uint8)
        if result.masks is not None:
            for cls,points in zip(result.boxes.cls.tolist(),result.masks.xy):
                if int(cls)==0:
                    cv2.fillPoly(mask,[points.astype(np.int32)],255)
        encoder.stdin.write(mask.tobytes())
        count+=1
        if count%60==0: print('PERSON MASK',count,flush=True)
    encoder.stdin.close()
    if encoder.wait()!=0: raise RuntimeError('Mask encoder failed')
except BaseException:
    encoder.kill(); encoder.wait()
    raise
print(json.dumps({'frames':count,'output':str(target)}),flush=True)
'''
raise SystemExit(subprocess.run(['/home/jnu/venvs/comfyui-h3-extended/bin/python','-u','-c',code,sys.argv[1]]).returncode)
