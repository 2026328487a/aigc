import json
from pathlib import Path
import unittest

from aigc.clothing_workflow import build_clothing_workflow
from aigc.h3_extended import build_h3_extended


class ClothingWorkflowTests(unittest.TestCase):
    def test_one_prompt_drives_reference_and_complete_video(self):
        graph=build_clothing_workflow('custom.mp4','a green cotton outfit')
        self.assertEqual(graph['101']['inputs']['file'],'custom.mp4')
        self.assertEqual(graph['102']['inputs']['outfit'],'a green cotton outfit')
        self.assertEqual(graph['109']['inputs']['outfit'],['102',0])
        self.assertEqual(graph['118']['inputs']['clip'],['109',0])
        self.assertEqual(graph['119']['inputs']['video'],['118',0])
        self.assertNotIn('frame_load_cap',graph['103']['inputs'])

    def test_shipped_template_uses_the_verified_models_and_preserves_original_audio(self):
        graph=build_h3_extended('h3-clothing-change')
        shipped=json.loads((Path(__file__).parents[1]/'docs/h3-extended/workflows/h3-clothing-change.api.json').read_text(encoding='utf-8'))
        self.assertEqual(graph,shipped)
        self.assertEqual(graph['118']['inputs']['chunk_frames'],124)
        self.assertEqual(graph['118']['inputs']['overlap_frames'],22)
        self.assertEqual(graph['118']['inputs']['video_vae'],['115',0])
        self.assertEqual(graph['117']['inputs']['name'],'minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors')
        self.assertEqual(graph['119']['class_type'],'AIGCSaveClothingVideo')


if __name__=='__main__':
    unittest.main()
