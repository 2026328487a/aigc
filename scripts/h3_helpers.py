"""ComfyUI nodes for session-owned H3 reference exports and imports."""
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


NODE_CLASS_MAPPINGS = {'RemoveLatentMask+': RemoveLatentMask,
                       'AIGCSaveH3Reference': SaveH3Reference,
                       'AIGCLoadH3Reference': LoadH3Reference,
                       'AIGCH3AudioWindow': H3AudioWindow}
