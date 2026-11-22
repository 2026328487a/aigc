import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('h3_helpers', ROOT / 'scripts/h3_helpers.py')
helpers = importlib.util.module_from_spec(spec)
with patch.dict('sys.modules', {'folder_paths': types.ModuleType('folder_paths')}):
    spec.loader.exec_module(helpers)


class H3ImageSizeTests(unittest.TestCase):
    def test_expected_geometry_preserves_content_and_aligns_only_the_canvas(self):
        cases = json.loads((ROOT / 'tests/h3-image-size-cases.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(case=case['name']):
                size = helpers.H3ImageResize.geometry(*case['original'], **case['values'])
                self.assertEqual([size['content_width'], size['content_height']], case['content'])
                self.assertEqual([size['width'], size['height']], case['canvas'])
                left, right, top, bottom = size['padding']
                self.assertEqual(case['content'][0] + left + right, case['canvas'][0])
                self.assertEqual(case['content'][1] + top + bottom, case['canvas'][1])
                self.assertTrue(all(value >= 0 for value in size['padding']))
                self.assertLessEqual(abs(left - right), 1)
                self.assertLessEqual(abs(top - bottom), 1)

    def test_invalid_sizes_fail_before_allocating_pixels(self):
        for values in ({'mode': '缩放倍数', 'scale': 0}, {'mode': '缩放倍数', 'scale': float('nan')},
                       {'mode': '缩放倍数', 'scale': 8}, {'mode': '手动宽高', 'width': 513},
                       {'mode': 'missing'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                helpers.H3ImageResize.geometry(1920, 1080, **values)


if __name__ == '__main__':
    unittest.main()
