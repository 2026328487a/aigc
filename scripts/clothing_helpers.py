"""Session-owned pose export for the clothing video pipeline."""
import json
from pathlib import Path

import folder_paths


class SaveClothingPose:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"keypoints": ("POSE_KEYPOINT",),
                             "filename_prefix": ("STRING", {"default": "clothing-pose"})}}

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "AIGC/Clothing"

    def save(self, keypoints, filename_prefix):
        directory, name, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory())
        filename = f"{name}_{counter:05}.json"
        path = Path(directory) / filename
        path.write_text(json.dumps(keypoints), encoding="utf-8")
        return {"ui": {"files": [{"filename": filename, "subfolder": subfolder, "type": "output"}]}}


NODE_CLASS_MAPPINGS = {"AIGCSaveClothingPose": SaveClothingPose}
