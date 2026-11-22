"""Validate the shipped H3 sizing plugin and workflows on DGX using CPU only."""
import base64
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('h3-hmnsfw-i2v', 'h3-hmnsfw-turbo8', 'h3-hmnsfw-lightx4', 'h3-hmnsfw-turbo4-768p')

REMOTE_CHECK = r'''
import os,sys
os.environ['CUDA_VISIBLE_DEVICES']=''
sys.argv=['h3-size-validation','--cpu','--disable-all-custom-nodes']
sys.path.insert(0,'/home/jnu/src/ComfyUI-h3-extended')
os.chdir('/home/jnu/src/ComfyUI-h3-extended')
import comfy.options
comfy.options.enable_args_parsing()
import asyncio,json,tempfile
from pathlib import Path
import nodes,folder_paths,execution,torch
from PIL import Image
from utils.extra_config import load_extra_path_config
load_extra_path_config('/home/jnu/models/aigc/h3-extended/extra_model_paths.yaml')
asyncio.run(nodes.init_extra_nodes(init_custom_nodes=False,init_api_nodes=False))
rows=[];pixel_checks=[]
with tempfile.TemporaryDirectory(prefix='h3-size-check-',dir='/dev/shm') as tmp:
    root=Path(tmp)
    helper=root/'AIGC-H3-Helpers';helper.mkdir()
    for name,content in PAYLOAD['helpers'].items():
        path=helper/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    assert asyncio.run(nodes.load_custom_node(str(helper)))
    resize=nodes.NODE_CLASS_MAPPINGS['AIGCH3ImageResize']()
    for case in PAYLOAD['cases']:
        w,h=case['original']
        image=torch.linspace(0,1,w*h*3).reshape(1,h,w,3)
        values={'mode':'最长边','longest_edge':512,'scale':1.0,'width':512,'height':320,**case['values']}
        result=resize.resize(image,**values)
        pixels,out_w,out_h=result['result']
        assert [out_w,out_h]==case['canvas'],case['name']
        assert list(pixels.shape)==[1,out_h,out_w,3] and pixels.isfinite().all(),case['name']
        geometry=resize.geometry(w,h,**values)
        left,right,top,bottom=geometry['padding']
        if left: assert torch.equal(pixels[:,:,:left],pixels[:,:,left:left+1].expand(-1,-1,left,-1))
        if right: assert torch.equal(pixels[:,:,-right:],pixels[:,:,-right-1:-right].expand(-1,-1,right,-1))
        if top: assert torch.equal(pixels[:,:top],pixels[:,top:top+1].expand(-1,top,-1,-1))
        if bottom: assert torch.equal(pixels[:,-bottom:],pixels[:,-bottom-1:-bottom].expand(-1,bottom,-1,-1))
        pixel_checks.append({'case':case['name'],'canvas':[out_w,out_h],'passed':True})
    # With scale 1, original pixels survive unchanged inside the padded canvas.
    image=torch.rand(2,35,43,3)
    pixels,w,h=resize.resize(image,'缩放倍数',512,1.0,512,320)['result']
    assert torch.equal(pixels[:,14:49,10:53],image)
    folder_paths.set_input_directory(tmp)
    Image.new('RGB',(108,192),(127,127,127)).save(str(root/'reference.png'))
    for name,graph in PAYLOAD['graphs'].items():
        result=asyncio.run(execution.validate_prompt('offline-'+name,graph,None))
        rows.append({'name':name,'passed':result[0],'error':result[1],'node_errors':result[3]})
report={'graphs':rows,'pixel_checks':pixel_checks,'original_pixels_preserved':True,
        'cuda_initialized':torch.cuda.is_initialized(),'model_loaded':False,'generation_submitted':False}
print('REPORT_JSON',json.dumps(report))
assert all(row['passed'] for row in rows) and not report['cuda_initialized']
'''


def main():
    payload = {
        'graphs': {name: json.loads((ROOT / f'docs/h3-extended/workflows/{name}.api.json').read_text(encoding='utf-8'))
                   for name in NAMES},
        'cases': json.loads((ROOT / 'tests/h3-image-size-cases.json').read_text(encoding='utf-8')),
        'helpers': {'__init__.py': (ROOT / 'scripts/h3_helpers.py').read_text(encoding='utf-8'),
                    **{f'web/{name}': (ROOT / 'scripts' / name).read_text(encoding='utf-8')
                       for name in ('h3_reference.js', 'h3_image_size.js')}},
    }
    source = 'PAYLOAD=' + repr(payload) + '\n' + REMOTE_CHECK
    command = '/home/jnu/venvs/comfyui-h3-extended/bin/python -B -u -c ' + shlex.quote(
        'import base64;exec(compile(base64.b64decode(input()),"<h3-size-validation>","exec"))')
    result = subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                             '-o', 'ConnectTimeout=10', 'dgx', command],
                            input=base64.b64encode(source.encode()) + b'\n', capture_output=True, timeout=120)
    output = result.stdout.decode(errors='replace')
    audit = ROOT / 'test-results/h3-image-size'
    audit.mkdir(parents=True, exist_ok=True)
    (audit / 'backend.log').write_text(output + result.stderr.decode(errors='replace'), encoding='utf-8')
    for line in output.splitlines():
        if line.startswith('REPORT_JSON '):
            report = json.loads(line[len('REPORT_JSON '):])
            (audit / 'backend-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report))
    if result.returncode:
        print((output + result.stderr.decode(errors='replace'))[-5000:])
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
