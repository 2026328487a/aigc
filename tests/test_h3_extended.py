import unittest

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


if __name__ == "__main__":
    unittest.main()
