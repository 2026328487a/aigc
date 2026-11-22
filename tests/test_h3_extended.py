import json
import pathlib
import tempfile
import unittest

import httpx

from aigc import app as workbench
from aigc.h3_extended import build_h3_extended
from aigc.remote_service import comfy_arguments, prepare_h3_helpers
from aigc.session import PROFILES


class H3StartupTests(unittest.TestCase):
    def config(self, **values):
        return {"root": "/dev/shm/aigc-test", "comfy_dir": "/test/ComfyUI", "port": 12345, **values}

    def test_baseline_profiles_keep_custom_nodes_disabled(self):
        arguments = comfy_arguments(self.config())
        self.assertIn("--disable-all-custom-nodes", arguments)
        self.assertIn("--disable-api-nodes", arguments)

    def test_h3_addons_use_separate_source_and_model_paths(self):
        arguments = comfy_arguments(self.config(**PROFILES["h3"]))
        self.assertNotIn("--disable-all-custom-nodes", arguments)
        self.assertEqual(arguments[0], "/home/jnu/src/ComfyUI-h3-extended/main.py")
        i = arguments.index("--extra-model-paths-config")
        self.assertEqual(arguments[i + 1], PROFILES["h3"]["model_paths"])
        for engine in ("qwen21", "music3"):
            self.assertNotIn("custom_nodes", PROFILES[engine])

    def test_updated_helpers_are_written_only_inside_the_h3_session(self):
        files = {'__init__.py': 'updated node', 'web/h3_reference.js': 'reference',
                 'web/h3_image_size.js': 'image size'}
        with tempfile.TemporaryDirectory() as directory:
            config = {'root': directory, 'engine': 'h3', 'h3_helper_files': files}
            path = prepare_h3_helpers(config)
            self.assertTrue(path.is_relative_to(pathlib.Path(directory)))
            for name, content in files.items():
                self.assertEqual((path / name).read_text(encoding='utf-8'), content)
            self.assertIsNone(prepare_h3_helpers({'root': directory, 'engine': 'qwen21',
                                                'h3_helper_files': files}))
            with self.assertRaises(ValueError):
                prepare_h3_helpers({'root': directory, 'engine': 'h3',
                                    'h3_helper_files': {'../escape.py': 'invalid'}})


class H3ReferenceTemplateTests(unittest.TestCase):
    def test_first_reference_uses_the_frontends_initial_zero_indexed_socket(self):
        generated = build_h3_extended("h3-refmod")
        shipped = json.loads((pathlib.Path(__file__).parents[1] /
                              "docs/h3-extended/workflows/h3-refmod.api.json").read_text(encoding="utf-8"))
        for graph in (generated, shipped):
            inputs = graph["40"]["inputs"]
            self.assertEqual(inputs["refs_image.ref_image_0"], ["20", 0])
            self.assertNotIn("refs_image.ref_image_1", inputs)


class H3LoRATemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_template_serves_api_and_native_canvas(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app),
                                     base_url="http://127.0.0.1:8765") as client:
            api = await client.get("/api/comfy/templates/h3-hmnsfw-i2v")
            native = await client.get("/api/comfy/templates/h3-hmnsfw-i2v?format=native")
        self.assertEqual(api.status_code, 200)
        self.assertEqual(native.status_code, 200)
        self.assertIn("h3-hmnsfw-i2v.workflow.json", native.headers["content-disposition"])
        graph, canvas = api.json(), native.json()
        self.assertEqual(graph, build_h3_extended("h3-hmnsfw-i2v"))
        loras = [node for node in canvas["nodes"] if node["type"] == "LoraLoaderModelOnly"]
        self.assertEqual(len(loras), 1)
        self.assertEqual(loras[0]["widgets_values"], [graph["2"]["inputs"]["lora_name"], 0.7])
        self.assertEqual(len(canvas["nodes"]), len(graph))
        self.assertEqual(graph["10"]["inputs"]["sampler_name"], "euler")
        self.assertEqual(graph["11"]["inputs"]["steps"], 18)

    async def test_custom_dimensions_and_seed_preserve_the_lora_sampling_route(self):
        graph = build_h3_extended("h3-hmnsfw-i2v", width=768, height=448, length=192, seed=123)
        self.assertEqual([graph["7"]["inputs"][key] for key in ("width", "height", "length")],
                         [["23", 1], ["23", 2], 192])
        self.assertEqual([graph["23"]["inputs"][key] for key in ("mode", "width", "height")],
                         ["手动宽高", 768, 448])
        self.assertEqual(graph["9"]["inputs"]["noise_seed"], 123)
        self.assertEqual(graph["8"]["inputs"]["model"], ["2", 0])
        self.assertEqual(graph["11"]["inputs"]["model"], ["2", 0])
        self.assertEqual(graph["2"]["inputs"]["lora_name"], "HMNSFW-Extreme-Realism.safetensors")
        self.assertEqual(graph["11"]["inputs"]["steps"], 18)

    async def test_acceleration_comparisons_keep_common_inputs_and_coherent_av_shifts(self):
        variants = {
            "h3-hmnsfw-turbo8": (8, 12.0, "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"),
            "h3-hmnsfw-lightx4": (4, 12.0, "minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy_resized_avg_rank_21_bf16.safetensors"),
            "h3-hmnsfw-turbo4-768p": (4, 6.0, "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"),
        }
        baseline = build_h3_extended("h3-hmnsfw-i2v")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app),
                                     base_url="http://127.0.0.1:8765") as client:
            for name, (steps, video_shift, accelerator) in variants.items():
                with self.subTest(name=name):
                    response = await client.get(f"/api/comfy/templates/{name}")
                    native_response = await client.get(f"/api/comfy/templates/{name}?format=native")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(native_response.status_code, 200)
                    graph, canvas = response.json(), native_response.json()
                    self.assertEqual(graph, build_h3_extended(name))
                    for key in ("1", "3", "4", "5", "7", "9", "12", "13", "14", "15", "20", "23"):
                        self.assertEqual(graph[key], baseline[key])
                    self.assertEqual(graph["21"]["inputs"], {
                        "model": ["1", 0], "lora_name": accelerator, "strength_model": 1.0,
                    })
                    self.assertEqual(graph["2"]["inputs"]["model"], ["21", 0])
                    self.assertEqual(graph["2"]["inputs"]["strength_model"], 0.7)
                    self.assertEqual(graph["22"], {"class_type": "MiniMaxH3SigmaShift", "inputs": {
                        "model": ["2", 0], "shift_video": video_shift, "shift_audio": 3.0,
                    }})
                    self.assertEqual(graph["10"]["inputs"]["sampler_name"], "res_multistep")
                    self.assertEqual(graph["11"]["inputs"]["steps"], steps)
                    nodes = {node["id"]: node for node in canvas["nodes"]}
                    links = {link[0]: link for link in canvas["links"]}
                    self.assertEqual(nodes[22]["widgets_values"], [video_shift, 3.0])
                    self.assertEqual(nodes[21]["widgets_values"], [accelerator, 1.0])
                    self.assertEqual(len(nodes), 19)
                    self.assertEqual(len(links), 24)
                    for key in ("8", "11"):
                        self.assertEqual(graph[key]["inputs"]["model"], ["22", 0])
                        slot = next(slot for slot in nodes[int(key)]["inputs"] if slot["name"] == "model")
                        self.assertEqual(links[slot["link"]][1:3], [22, 0])

    async def test_all_four_templates_connect_the_sized_image_and_dimensions_to_h3(self):
        for name in ('h3-hmnsfw-i2v', 'h3-hmnsfw-turbo8', 'h3-hmnsfw-lightx4', 'h3-hmnsfw-turbo4-768p'):
            with self.subTest(name=name):
                graph = build_h3_extended(name)
                self.assertEqual(graph['23']['inputs']['image'], ['20', 0])
                self.assertEqual(graph['23']['inputs']['mode'], '最长边')
                canvas = json.loads((pathlib.Path(__file__).parents[1] /
                    f'docs/h3-extended/workflows/{name}.workflow.json').read_text(encoding='utf-8'))
                nodes = {node['id']: node for node in canvas['nodes']}
                links = {link[0]: link for link in canvas['links']}
                for slot_index, name_input in enumerate(('first_frame', 'width', 'height')):
                    self.assertEqual(graph['7']['inputs'][name_input], ['23', slot_index])
                    slot = next(slot for slot in nodes[7]['inputs'] if slot['name'] == name_input)
                    self.assertEqual(links[slot['link']][1:3], [23, slot_index])


if __name__ == "__main__":
    unittest.main()
