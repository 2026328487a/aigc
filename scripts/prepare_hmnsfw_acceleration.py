"""Build three HMNSFW acceleration comparisons from the existing baseline canvas."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / 'docs/h3-extended/workflows'
COMPARISONS = {
    'h3-hmnsfw-turbo8': {
        'label': 'HMNSFW + Turbo · 8 步',
        'lora': 'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors',
        'steps': 8, 'video_shift': 12.0,
    },
    'h3-hmnsfw-lightx4': {
        'label': 'HMNSFW + LightX2V · 4 步',
        'lora': 'minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors',
        'steps': 4, 'video_shift': 12.0,
    },
    'h3-hmnsfw-turbo4-768p': {
        'label': 'HMNSFW + Turbo 768p · 4 步',
        'lora': 'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors',
        'steps': 4, 'video_shift': 6.0,
    },
}


def prepare(name, spec, baseline_api, baseline_canvas):
    graph = deepcopy(baseline_api)
    graph['21'] = {'class_type': 'LoraLoaderModelOnly', 'inputs': {
        'model': ['1', 0], 'lora_name': spec['lora'], 'strength_model': 1.0,
    }}
    graph['2']['inputs']['model'] = ['21', 0]
    graph['22'] = {'class_type': 'MiniMaxH3SigmaShift', 'inputs': {
        'model': ['2', 0], 'shift_video': spec['video_shift'], 'shift_audio': 3.0,
    }}
    for key in ('8', '11'):
        graph[key]['inputs']['model'] = ['22', 0]
    graph['10']['inputs']['sampler_name'] = 'res_multistep'
    graph['11']['inputs']['steps'] = spec['steps']
    graph['16']['inputs']['filename_prefix'] = name + '/video'

    canvas = deepcopy(baseline_canvas)
    canvas['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, 'aigc-template:' + name))
    canvas['revision'] = 0
    canvas['extra']['workflow_title'] = spec['label']
    canvas['extra']['ds'] = {'scale': 0.52, 'offset': [1480, 400]}
    nodes = {node['id']: node for node in canvas['nodes']}
    acceleration = deepcopy(nodes[2])
    acceleration.update(id=21, title='加载加速 LoRA · 强度 1.0')
    acceleration['widgets_values'] = [spec['lora'], 1.0]
    canvas['nodes'].append(acceleration)
    shift = {
        'id': 22, 'type': 'MiniMaxH3SigmaShift', 'pos': [280, -220], 'size': [340, 150],
        'flags': {}, 'order': 0, 'mode': 0,
        'inputs': [{'name': 'model', 'type': 'MODEL', 'link': None}],
        'outputs': [{'name': 'MODEL', 'type': 'MODEL', 'links': [], 'slot_index': 0}],
        'properties': {'Node name for S&R': 'MiniMaxH3SigmaShift'},
        'widgets_values': [spec['video_shift'], 3.0],
        'title': f"H3 Shift · 视频 {spec['video_shift']:g} / 音频 3",
    }
    canvas['nodes'].append(shift)
    nodes.update({21: acceleration, 22: shift})
    positions = {1: [-980,-220], 21: [-560,-220], 2: [-140,-220], 22: [280,-220],
                 20: [-1400,140], 3: [-980,140], 4: [-980,360], 5: [-980,570],
                 7: [-480,140], 8: [70,30], 9: [70,200], 10: [70,350], 11: [70,500],
                 12: [510,60], 13: [930,10], 14: [930,260], 15: [1350,10], 16: [1770,10]}
    nodes[10]['widgets_values'] = ['res_multistep']
    nodes[10]['title'] = 'res_multistep 采样器'
    nodes[11]['widgets_values'] = ['simple', spec['steps'], 1.0]
    nodes[11]['title'] = f"{spec['steps']} 步 · Simple 调度"
    nodes[16]['widgets_values'][0] = graph['16']['inputs']['filename_prefix']
    for node_id, node in nodes.items():
        node['pos'] = positions[node_id]
        for slot in node.get('inputs', []):
            slot['link'] = None
        for index, slot in enumerate(node.get('outputs', [])):
            slot['links'] = []
            slot['slot_index'] = index

    links = []
    for target_id, definition in graph.items():
        target = nodes[int(target_id)]
        for name_input, value in definition['inputs'].items():
            if not isinstance(value, list):
                continue
            origin_id, origin_slot = int(value[0]), value[1]
            target_slot = next(i for i, slot in enumerate(target['inputs']) if slot['name'] == name_input)
            output = nodes[origin_id]['outputs'][origin_slot]
            assert output['type'] == target['inputs'][target_slot]['type']
            link_id = len(links) + 1
            links.append([link_id, origin_id, origin_slot, int(target_id), target_slot, output['type']])
            target['inputs'][target_slot]['link'] = link_id
            output['links'].append(link_id)
    canvas['links'] = links
    canvas['last_node_id'] = max(nodes)
    canvas['last_link_id'] = len(links)
    remaining = set(graph)
    order = []
    while remaining:
        ready = sorted((key for key in remaining if all(
            value[0] in order for value in graph[key]['inputs'].values() if isinstance(value, list)
        )), key=int)
        if not ready:
            raise ValueError('Workflow dependency cycle')
        order.extend(ready)
        remaining.difference_update(ready)
    for index, key in enumerate(order):
        nodes[int(key)]['order'] = index
    return graph, canvas


def main():
    api = json.loads((WORKFLOWS / 'h3-hmnsfw-i2v.api.json').read_text(encoding='utf-8'))
    canvas = json.loads((WORKFLOWS / 'h3-hmnsfw-i2v.workflow.json').read_text(encoding='utf-8'))
    for name, spec in COMPARISONS.items():
        graph, native = prepare(name, spec, api, canvas)
        for suffix, payload in [('api', graph), ('workflow', native)]:
            (WORKFLOWS / f'{name}.{suffix}.json').write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(name, 'nodes=', len(native['nodes']), 'links=', len(native['links']))


if __name__ == '__main__':
    main()
