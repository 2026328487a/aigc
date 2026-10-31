"""Small compatibility repairs in the dedicated H3 checkout only."""
from pathlib import Path
import json

SOURCE=Path('/home/jnu/src/ComfyUI-h3-extended')

def main():
    package=SOURCE/'custom_nodes/ComfyUI-MiniMax-Tools'
    path=package/'__init__.py'
    text=path.read_text()
    original='from .minimax_advanced_add_guide import MiniMaxH3AdvancedAddGuide'
    replacement='''try:
    from .minimax_advanced_add_guide import MiniMaxH3AdvancedAddGuide
except ModuleNotFoundError as error:
    if error.name != "comfy.audio":
        raise
    MiniMaxH3AdvancedAddGuide = None
'''
    if original in text and 'except ModuleNotFoundError' not in text:
        text=text.replace(original,replacement)
        text+='\nif MiniMaxH3AdvancedAddGuide is None:\n    NODE_CLASS_MAPPINGS.pop("MiniMaxH3AdvancedAddGuide", None)\n    NODE_DISPLAY_NAME_MAPPINGS.pop("MiniMaxH3AdvancedAddGuide", None)\n'
        path.write_text(text)
    helper=SOURCE/'custom_nodes/AIGC-H3-Helpers'
    helper.mkdir(exist_ok=True)
    helper_source=Path('/home/jnu/models/aigc/h3-extended/staging/h3_helpers.py')
    (helper/'__init__.py').write_text(helper_source.read_text())
    web=helper/'web';web.mkdir(exist_ok=True)
    (web/'h3_reference.js').write_text((helper_source.parent/'h3_reference.js').read_text())
    manifest=Path('/home/jnu/models/aigc/h3-extended/deployment.json')
    record=json.loads(manifest.read_text())
    record['compatibility_repairs']=['MiniMax Tools: optional advanced-guide node needs absent comfy.audio; other nodes load independently.',
                                     'AIGC RemoveLatentMask+ replaces the unrelated Essentials dependency for the imported graph.',
                                     'AIGC reference export/import uses session output/input, with automatic local archive.']
    manifest.write_text(json.dumps(record,ensure_ascii=False,indent=2))
    print('H3_COMPATIBILITY_PATCHED')

if __name__=='__main__':main()
