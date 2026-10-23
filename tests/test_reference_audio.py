from array import array
import io
import math
import sys
import unittest
from unittest.mock import patch
import wave

import av
import httpx
from PIL import Image

from aigc import app as workbench
from aigc.reference_audio import extract_reference


def recording(seconds=8, silence=0, channels=1, rate=24000):
    samples = array('h')
    for i in range(round(seconds * rate)):
        value = round(8000 * math.sin(2 * math.pi * 440 * i / rate)) if silence <= i / rate < seconds - silence else 0
        samples.extend([value] * channels)
    if sys.byteorder != 'little':
        samples.byteswap()
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples.tobytes())
    return output.getvalue()


def mp4(audio=True):
    output = io.BytesIO()
    with av.open(output, 'w', format='mp4') as target:
        video = target.add_stream('libx264', rate=1)
        video.width = video.height = 32
        video.pix_fmt = 'yuv420p'
        if audio:
            stream = target.add_stream('aac', rate=24000)
            stream.layout = 'mono'
        for _ in range(8):
            for packet in video.encode(av.VideoFrame.from_image(Image.new('RGB', (32, 32), 'green'))):
                target.mux(packet)
        for packet in video.encode():
            target.mux(packet)
        if audio:
            with av.open(io.BytesIO(recording(silence=1))) as source:
                for frame in source.decode(audio=0):
                    for packet in stream.encode(frame):
                        target.mux(packet)
                for packet in stream.encode():
                    target.mux(packet)
    return output.getvalue()


class ReferenceExtractionTests(unittest.TestCase):
    def test_selected_range_matches_source_samples(self):
        original = recording(seconds=42)
        data, info = extract_reference(io.BytesIO(original), 35.25, 40.75)
        self.assertEqual(info['duration'], 5.5)
        with wave.open(io.BytesIO(original)) as source, wave.open(io.BytesIO(data)) as result:
            source.setpos(round(35.25 * 24000))
            self.assertEqual(result.readframes(result.getnframes()), source.readframes(round(5.5 * 24000)))

    def test_stereo_resampling_and_silence_trimming(self):
        source = recording(silence=1, channels=2, rate=48000)
        full, _ = extract_reference(io.BytesIO(source), 0, 8)
        trimmed, info = extract_reference(io.BytesIO(source), 0, 8, True)
        self.assertAlmostEqual(info['duration'], 6.5, places=2)
        self.assertAlmostEqual(info['trimmed_start'], 0.75, places=2)
        for data in (full, trimmed):
            workbench.normalize_reference(data)
            with wave.open(io.BytesIO(data)) as result:
                self.assertEqual((result.getnchannels(), result.getframerate(), result.getsampwidth()), (1, 24000, 2))

    def test_rejects_invalid_ranges_and_missing_audio(self):
        source = recording()
        for start, end in [(-1, 4), (4, 2), (0, 2), (0, 31), (0, math.inf), (math.nan, 8), (20, 25), (7, 12)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                extract_reference(io.BytesIO(source), start, end)
        with self.assertRaisesRegex(ValueError, '没有音轨'):
            extract_reference(io.BytesIO(mp4(audio=False)), 0, 8)

    def test_rejects_silent_and_too_short_trimmed_clips(self):
        for source in [recording(silence=8), recording(silence=3)]:
            with self.assertRaises(ValueError):
                extract_reference(io.BytesIO(source), 0, 8, True)

    def test_mp4_audio_and_seek(self):
        source = mp4()
        for start, end in [(0, 8), (2, 7)]:
            data, info = extract_reference(io.BytesIO(source), start, end)
            self.assertAlmostEqual(info['duration'], end - start, delta=0.06)
            workbench.normalize_reference(data)


class ReferenceExtractionApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_extract_download_and_validation_errors(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url='http://127.0.0.1:8765',
                                     headers={'X-AIGC-Client': 'local'}) as client:
            with patch.object(workbench.session, 'state', 'disconnected'):
                response = await client.post('/api/reference-audio/extract', data={'start': 0, 'end': 8, 'trim_silence': 'true'},
                                             files={'media': ('recording.wav', recording(silence=1))})
            self.assertEqual(response.status_code, 200, response.text[:100])
            self.assertEqual(response.headers['content-type'], 'audio/wav')
            self.assertIn('reference.wav', response.headers['content-disposition'])
            self.assertAlmostEqual(float(response.headers['x-audio-duration']), 6.5, places=2)
            workbench.normalize_reference(response.content)
            for data in [b'', b'bad mp4']:
                response = await client.post('/api/reference-audio/extract', files={'media': ('bad.mp4', data)})
                self.assertEqual(response.status_code, 422)
            response = await client.post('/api/reference-audio/extract', data={'start': 'NaN'}, files={'media': ('test.wav', recording())})
            self.assertEqual(response.status_code, 422)
            with patch.object(workbench, 'MAX_MEDIA_BYTES', 16):
                response = await client.post('/api/reference-audio/extract', files={'media': ('large.wav', recording())})
                self.assertEqual(response.status_code, 413)


if __name__ == '__main__':
    unittest.main()
