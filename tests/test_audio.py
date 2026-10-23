import asyncio
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

import httpx

from aigc import app as workbench
from aigc.workflows import Settings, build_workflow


def wav(seconds=4, rate=16000):
    data = io.BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x01\x00' * int(rate * seconds))
    return data.getvalue()


class AudioMediaTests(unittest.TestCase):
    def test_reference_normalizes_and_strips_container_metadata(self):
        data = workbench.normalize_reference(wav())
        with wave.open(io.BytesIO(data)) as audio:
            self.assertEqual(audio.getframerate(), 24000)
            self.assertEqual(audio.getnchannels(), 1)
            self.assertAlmostEqual(audio.getnframes() / 24000, 4, places=2)

    def test_reference_rejects_short_long_and_invalid_audio(self):
        for data in [wav(0.2), wav(31), b'not audio']:
            with self.subTest(length=len(data)), self.assertRaises(Exception):
                workbench.normalize_reference(data)

    def test_download_validation_catches_truncated_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.wav'
            path.write_bytes(wav())
            self.assertEqual(workbench.validate_audio(path)['duration'], 4)
            path.write_bytes(wav()[:-100])
            with self.assertRaises(ValueError):
                workbench.validate_audio(path)

    def test_music_duration_does_not_expand_h3_limits(self):
        settings = Settings(engine='music3', mode='music', prompt='piano', seconds=60)
        graph = build_workflow(settings, [], 'a' * 32)
        self.assertEqual(graph['6']['inputs']['seconds'], ['7', 1])
        self.assertEqual(graph['7']['inputs']['max_duration'], 60)
        self.assertEqual(graph['16']['inputs']['filename_prefix'], 'a' * 32 + '/audio')
        with self.assertRaises(ValueError):
            Settings(prompt='video', seconds=60)


class AudioApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_clone_requires_reference_and_speech_rejects_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(workbench, 'DATA', Path(directory)), \
             patch.object(workbench.session, 'state', 'ready'), patch.object(workbench.session, 'engine', 'voxcpm2'), \
             patch.object(workbench, 'active_job', None), patch.object(workbench, 'active_task', None), \
             patch.object(workbench, 'run_job', new_callable=AsyncMock):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workbench.app), base_url='http://127.0.0.1:8765',
                                         headers={'X-AIGC-Client': 'local'}) as client:
                clone = Settings(engine='voxcpm2', mode='clone', prompt='你好')
                self.assertEqual((await client.post('/api/jobs', data={'settings': clone.model_dump_json()})).status_code, 422)
                speech = Settings(engine='voxcpm2', mode='speech', prompt='你好')
                self.assertEqual((await client.post('/api/jobs', data={'settings': speech.model_dump_json()},
                                                   files={'reference_audio': ('test.wav', wav())})).status_code, 422)
                response = await client.post('/api/jobs', data={'settings': clone.model_dump_json()},
                                            files={'reference_audio': ('test.wav', wav())})
                self.assertEqual(response.status_code, 202, response.text)
                job = response.json()
                await workbench.active_task
                self.assertEqual(job['reference_audio'], 'reference.wav')
                self.assertEqual(job['images'], [])
                reference = await client.get(f"/api/jobs/{job['id']}/files/reference.wav")
                self.assertEqual(reference.status_code, 200)
                self.assertTrue(reference.content.startswith(b'RIFF'))


if __name__ == '__main__':
    unittest.main()
