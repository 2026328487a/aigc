"""Local audio extraction using the FFmpeg libraries bundled with PyAV."""
from array import array
import io
import math
import sys
import wave

import av


RATE = 24000
MAX_MEDIA_BYTES = 128 * 1024 * 1024


def extract_reference(media, start=0.0, end=15.0, trim_silence=False):
    if not all(math.isfinite(value) for value in (start, end)) or start < 0 or not 3 <= end - start <= 30:
        raise ValueError('请选择 3–30 秒的片段，结束时间必须晚于开始时间')
    # A fixed-size buffer bounds decoded memory even for long source videos.
    pcm = bytearray(round((end - start) * RATE) * 2)
    written = 0
    with av.open(media, mode='r') as source:
        if not source.streams.audio:
            raise ValueError('这个文件没有音轨，请选择带声音的视频或录音')
        stream = source.streams.audio[0]
        origin = (source.start_time or 0) / av.time_base
        if start:
            source.seek(round((origin + start) / stream.time_base), stream=stream, backward=True)
        resampler = av.AudioResampler(format='s16', layout='mono', rate=RATE)
        next_time = None

        def append(frame):
            nonlocal written, next_time
            timestamp = float(frame.time) - origin if frame.pts is not None else next_time
            if timestamp is None:
                raise ValueError('无法读取音轨时间，请先转换为 WAV 后重试')
            next_time = timestamp + frame.samples / RATE
            offset = round((timestamp - start) * RATE)
            left = max(0, -offset)
            right = min(frame.samples, len(pcm) // 2 - offset)
            if right > left:
                data = bytes(frame.planes[0])
                pcm[(offset + left) * 2:(offset + right) * 2] = data[left * 2:right * 2]
                written = max(written, (offset + right) * 2)
            return timestamp >= end

        finished = False
        for frame in source.decode(stream):
            for converted in resampler.resample(frame):
                if append(converted):
                    finished = True
                    break
            if finished:
                break
        if not finished:
            for converted in resampler.resample(None):
                append(converted)

    pcm = pcm[:written]
    if len(pcm) < 3 * RATE * 2:
        raise ValueError('所选范围内不足 3 秒音频，请调整起止时间')
    removed_start = removed_end = 0
    if trim_silence:
        samples = array('h', pcm)
        if sys.byteorder != 'little':
            samples.byteswap()
        # Match a -40 dBFS silence threshold and retain 250 ms at each edge.
        threshold = round(32768 * 10 ** (-40 / 20))
        first = next((i for i, value in enumerate(samples) if abs(value) > threshold), None)
        if first is None:
            raise ValueError('所选片段音量过低或只有静音，请换一段或关闭首尾静音裁剪')
        last = len(samples) - next(i for i, value in enumerate(reversed(samples)) if abs(value) > threshold)
        left, right = max(0, first - RATE // 4), min(len(samples), last + RATE // 4)
        removed_start, removed_end = left / RATE, (len(samples) - right) / RATE
        pcm = pcm[left * 2:right * 2]
        if len(pcm) < 3 * RATE * 2:
            raise ValueError('裁剪首尾静音后不足 3 秒，请扩大选取范围或关闭首尾静音裁剪')
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(RATE)
        target.writeframes(pcm)
    return output.getvalue(), {
        'duration': round(len(pcm) / (RATE * 2), 3),
        'trimmed_start': round(removed_start, 3), 'trimmed_end': round(removed_end, 3),
    }
