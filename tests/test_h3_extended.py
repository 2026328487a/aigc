import json
import pathlib
import unittest

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


if __name__ == "__main__":
    unittest.main()
