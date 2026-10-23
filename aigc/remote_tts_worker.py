"""One cancellable VoxCPM2 inference process. Source arrives over stdin."""
import json
import sys
import wave


def main():
    config = json.loads(sys.stdin.readline())
    import numpy as np
    import torch
    from voxcpm import VoxCPM

    torch.set_num_threads(8)
    print('TTS_EVENT ' + json.dumps({'stage': '加载语音模型', 'progress': 10}), flush=True)
    model = VoxCPM.from_pretrained(config['model_dir'], load_denoiser=False,
                                   local_files_only=True, optimize=False, device='cuda')
    settings = config['settings']
    directions = {'zh': 'Mandarin Chinese', 'yue': 'Cantonese, 广东话', 'en': 'English'}
    style = ', '.join(filter(None, [directions.get(settings['language'], ''), settings['voice_style'].strip()]))
    text = (f'({style})' if style else '') + settings['prompt']
    kwargs = {'text': text, 'cfg_value': 2.0, 'inference_timesteps': settings['steps'],
              'normalize': False, 'denoise': False, 'retry_badcase': False,
              'max_len': 3072, 'seed': settings['seed']}
    if config.get('reference'):
        if settings['reference_text'].strip():
            kwargs.update(prompt_wav_path=config['reference'], prompt_text=settings['reference_text'].strip())
        else:
            kwargs['reference_wav_path'] = config['reference']
    sample_rate = model.tts_model.sample_rate
    samples = 0
    print('TTS_EVENT ' + json.dumps({'stage': '合成语音', 'progress': 25}), flush=True)
    with wave.open(config['output'], 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for chunk in model.generate_streaming(**kwargs):
            chunk = np.asarray(chunk).reshape(-1)
            if not np.isfinite(chunk).all():
                raise ValueError('Model produced invalid audio samples')
            output.writeframes((np.clip(chunk, -1, 1) * 32767).astype('<i2').tobytes())
            samples += len(chunk)
            print('TTS_EVENT ' + json.dumps({'stage': f'已合成 {samples / sample_rate:.1f} 秒语音',
                                              'progress': 50}), flush=True)
    if samples < sample_rate // 10:
        raise ValueError('Generated audio is empty')


if __name__ == '__main__':
    main()
