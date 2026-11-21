import json
import pathlib
import unittest

import httpx

from aigc import app as workbench
from aigc.h3_extended import build_h3_extended
from aigc.remote_service import comfy_arguments
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
                         [768, 448, 192])
        self.assertEqual(graph["9"]["inputs"]["noise_seed"], 123)
        self.assertEqual(graph["8"]["inputs"]["model"], ["2", 0])
        self.assertEqual(graph["11"]["inputs"]["model"], ["2", 0])
        self.assertEqual(graph["2"]["inputs"]["lora_name"], "HMNSFW-Extreme-Realism.safetensors")
        self.assertEqual(graph["11"]["inputs"]["steps"], 18)


if __name__ == "__main__":
    unittest.main()
