"""ComfyUI helpers for H3 image sizing and session-owned reference files."""
import hashlib
import importlib
import math
from pathlib import Path

import folder_paths

WEB_DIRECTORY = './web'


def refmod_module():
    import nodes
    return importlib.import_module(nodes.NODE_CLASS_MAPPINGS['MiniMaxH3RefModBundleSave'].__module__)


class RemoveLatentMask:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'samples': ('LATENT',)}}

    RETURN_TYPES = ('LATENT',)
    FUNCTION = 'remove'
    CATEGORY = 'AIGC/H3'

    def remove(self, samples):
        result = samples.copy()
        result.pop('noise_mask', None)
        return (result,)


class SaveH3Reference:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'mods': ('H3_REF_MODS',),
                             'filename_prefix': ('STRING', {'default': 'h3-reference'})}}

    RETURN_TYPES = ('H3_REF_MODS',)
    FUNCTION = 'save'
    CATEGORY = 'AIGC/H3'
    OUTPUT_NODE = True

    def save(self, mods, filename_prefix):
        if not mods:
            raise ValueError('No references to export.')
        directory, filename, counter, subfolder, _ = folder_paths.get_save_image_path(
            filename_prefix, folder_paths.get_output_directory())
        name = f'{filename}_{counter:05}_'
        refmod_module().save_bundle(str(Path(directory) / name), filename, mods)
        return {'ui': {'files': [{'filename': name + '.safetensors',
                                  'subfolder': subfolder, 'type': 'output'}]},
                'result': (mods,)}


class LoadH3Reference:
    @classmethod
    def INPUT_TYPES(cls):
        root = Path(folder_paths.get_input_directory())
        choices = sorted(p.relative_to(root).as_posix() for p in root.rglob('*.safetensors'))
        return {'required': {'reference': (choices or ['my_person.safetensors'],),
                             'strength': ('FLOAT', {'default': 1.0, 'min': 0.0, 'max': 1.0})}}

    RETURN_TYPES = ('H3_REF_MODS',)
    FUNCTION = 'load'
    CATEGORY = 'AIGC/H3'

    @staticmethod
    def path(reference):
        root = Path(folder_paths.get_input_directory()).resolve()
        path = (root / reference).resolve()
        if not path.is_relative_to(root) or path.suffix != '.safetensors':
            raise ValueError('Choose a reference inside the input folder.')
        return path

    @classmethod
    def VALIDATE_INPUTS(cls, reference, strength):
        try:
            return True if cls.path(reference).is_file() else 'Upload the RefMod first.'
        except ValueError as error:
            return str(error)

    @classmethod
    def IS_CHANGED(cls, reference, strength):
        return hashlib.sha256(cls.path(reference).read_bytes()).hexdigest()

    def load(self, reference, strength):
        mods = refmod_module().load_bundle(str(self.path(reference).with_suffix('')))
        return ([(mod, weight * strength) for mod, weight in mods],)


class H3AudioWindow:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'audio': ('AUDIO',),
                             'max_frames': ('INT', {'default': 124, 'min': 5, 'max': 3626})}}

    RETURN_TYPES = ('INT', 'AUDIO')
    RETURN_NAMES = ('frames', 'audio')
    FUNCTION = 'window'
    CATEGORY = 'AIGC/H3'

    def window(self, audio, max_frames):
        import torch.nn.functional as F
        waveform = audio['waveform']
        sample_rate = audio['sample_rate']
        duration = waveform.shape[-1] / sample_rate
        maximum = 5 + 17 * ((max_frames - 5) // 17)
        needed = 5 + 17 * max(0, math.ceil((duration * 24 - 5) / 17 - 1e-6))
        frames = min(needed, maximum)
        samples = round(frames / 24 * sample_rate)
        trimmed = waveform[..., :samples]
        if trimmed.shape[-1] < samples:
            trimmed = F.pad(trimmed, (0, samples - trimmed.shape[-1]))
        return (frames, {**audio, 'waveform': trimmed})


class H3ImageResize:
    MODES = ['最长边', '缩放倍数', '手动宽高']
    MAX_SIDE = 1536

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'image': ('IMAGE',),
            'mode': (cls.MODES,),
            'longest_edge': ('INT', {'default': 512, 'min': 32, 'max': cls.MAX_SIDE, 'step': 32}),
            'scale': ('FLOAT', {'default': 1.0, 'min': 0.01, 'max': 8.0, 'step': 0.05}),
            'width': ('INT', {'default': 512, 'min': 32, 'max': cls.MAX_SIDE, 'step': 32}),
            'height': ('INT', {'default': 320, 'min': 32, 'max': cls.MAX_SIDE, 'step': 32}),
        }}

    RETURN_TYPES = ('IMAGE', 'INT', 'INT')
    RETURN_NAMES = ('image', 'width', 'height')
    FUNCTION = 'resize'
    CATEGORY = 'AIGC/H3'
    DESCRIPTION = '按原图比例缩放，边缘填充到 32 的倍数；输出图片及实际画布宽高。'

    @classmethod
    def geometry(cls, original_width, original_height, mode, longest_edge=512, scale=1.0,
                 width=512, height=320):
        if original_width <= 0 or original_height <= 0:
            raise ValueError('图片宽高必须大于 0。')
        if mode == '最长边':
            factor = longest_edge / max(original_width, original_height)
        elif mode == '缩放倍数':
            factor = scale
        elif mode == '手动宽高':
            if not all(32 <= side <= cls.MAX_SIDE and side % 32 == 0 for side in (width, height)):
                raise ValueError('手动宽高必须是 32 的倍数，范围为 32–1536。')
            factor = min(width / original_width, height / original_height)
        else:
            raise ValueError('请选择最长边、缩放倍数或手动宽高。')
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('缩放值必须是大于 0 的有限数值。')
        # Round halves up, also used by the browser's immediate size preview.
        content_width = max(1, math.floor(original_width * factor + 0.5))
        content_height = max(1, math.floor(original_height * factor + 0.5))
        if mode != '手动宽高':
            width = ((content_width + 31) // 32) * 32
            height = ((content_height + 31) // 32) * 32
        if max(width, height) > cls.MAX_SIDE:
            raise ValueError(f'目标画布 {width}×{height} 超过 1536，请降低最长边或缩放倍数。')
        pad_width, pad_height = width - content_width, height - content_height
        return {'content_width': content_width, 'content_height': content_height,
                'width': width, 'height': height,
                'padding': (pad_width // 2, pad_width - pad_width // 2,
                            pad_height // 2, pad_height - pad_height // 2)}

    def resize(self, image, mode, longest_edge, scale, width, height):
        import torch.nn.functional as F
        from comfy.utils import common_upscale

        original_height, original_width = image.shape[1:3]
        size = self.geometry(original_width, original_height, mode, longest_edge, scale, width, height)
        pixels = image.movedim(-1, 1).to('cpu')
        if (original_width, original_height) != (size['content_width'], size['content_height']):
            pixels = common_upscale(pixels, size['content_width'], size['content_height'],
                                    'lanczos', 'disabled')
        if any(size['padding']):
            pixels = F.pad(pixels, size['padding'], mode='replicate')
        description = (f"原图 {original_width}×{original_height}\n"
                       f"等比缩放 {size['content_width']}×{size['content_height']}\n"
                       f"生成画布 {size['width']}×{size['height']}"
                       + (' · 边缘补齐' if any(size['padding']) else ' · 无补边'))
        return {'ui': {'text': [description]},
                'result': (pixels.movedim(1, -1), size['width'], size['height'])}


NODE_CLASS_MAPPINGS = {'RemoveLatentMask+': RemoveLatentMask,
                       'AIGCSaveH3Reference': SaveH3Reference,
                       'AIGCLoadH3Reference': LoadH3Reference,
                       'AIGCH3AudioWindow': H3AudioWindow,
                       'AIGCH3ImageResize': H3ImageResize}

NODE_DISPLAY_NAME_MAPPINGS = {'AIGCH3ImageResize': '等比尺寸 · 自动连接 H3'}
