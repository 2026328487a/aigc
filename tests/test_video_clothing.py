import importlib.util
from pathlib import Path
import tempfile
import unittest

from PIL import Image


@unittest.skipUnless(importlib.util.find_spec('cv2'), 'Install requirements-clothing.txt for clothing tests')
class ClothingWindowTests(unittest.TestCase):
    def test_a_red_reference_outfit_is_not_rejected_as_invented_skin(self):
        import numpy as np
        from aigc.clothing import ClothingRepair
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for name in ['frames','masks','references']: (folder/name).mkdir()
            source=np.full((64,64,3),[20,40,90],dtype=np.uint8)
            reference=np.full((64,64,3),[180,30,10],dtype=np.uint8)
            mask=np.zeros((64,64),dtype=np.uint8);mask[20:60,18:50]=255
            Image.fromarray(source).save(folder/'frames/00000.png')
            Image.fromarray(mask).save(folder/'masks/00000.png')
            Image.fromarray(reference).save(folder/'references/custom.png')
            repair=ClothingRepair(folder,'custom')
            edited=repair.apply(np.zeros_like(reference),0)
            self.assertEqual(repair.modified,[0.0])
            self.assertLessEqual(np.abs(edited[mask>0].astype(int)-reference[mask>0]).max(),2)

    def test_cached_reconstruction_is_idempotent(self):
        import numpy as np
        from scripts.video_clothing import merge_window

        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            first=[np.full((3,2,3),40,dtype=np.uint8) for _ in range(5)]
            second=[np.full((3,2,3),200,dtype=np.uint8) for _ in range(5)]
            def rebuild():
                merge_window(folder,first,0,5,2)
                merge_window(folder,second,3,5,2)
                return [np.array(Image.open(folder/f'{i:05}.png')) for i in range(8)]
            original=rebuild()
            repeated=rebuild()
            for a,b in zip(original,repeated):
                np.testing.assert_array_equal(a,b)
            np.testing.assert_array_equal(repeated[0],first[0])
            np.testing.assert_array_equal(repeated[-1],second[-1])

    def test_stale_nonoverlap_frames_are_overwritten_without_extrapolation(self):
        import numpy as np
        from scripts.video_clothing import merge_window

        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for i in range(8):
                Image.fromarray(np.full((3,2,3),250,dtype=np.uint8)).save(folder/f'{i:05}.png')
            frames=[np.full((3,2,3),40,dtype=np.uint8) for _ in range(5)]
            merge_window(folder,frames,3,5,2)
            for i in range(5,8):
                np.testing.assert_array_equal(np.array(Image.open(folder/f'{i:05}.png')),frames[i-3])
            self.assertTrue(40<int(np.array(Image.open(folder/'00003.png'))[0,0,0])<250)


if __name__=='__main__':
    unittest.main()
