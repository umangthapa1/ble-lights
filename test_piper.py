"""Piper protocol/cache/playback checks without downloading models or playing audio."""

import asyncio
import io
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

from piper_backend import MAX_WAV_BYTES, PiperSynthesizer
from speech_feedback import SpokenFeedback
from test_feedback import FakeProcess, make_state


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x01\x00" * 100)
    return buffer.getvalue()


class FakePiperProcess(FakeProcess):
    def __init__(self, *, respond=True):
        super().__init__()
        self.stdout = asyncio.StreamReader()
        self.stdin = Mock()
        self.stdin.drain = AsyncMock()
        self.requests = []
        self.wav = wav_bytes()

        def write(data):
            self.requests.append(json.loads(data))
            self.started.set()
            if respond:
                header = json.dumps({"bytes": len(self.wav)}).encode() + b"\n"
                self.stdout.feed_data(header + self.wav)

        self.stdin.write.side_effect = write


class PiperSynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.synth = PiperSynthesizer(Path("test-voice.onnx"))

    async def asyncTearDown(self):
        await self.synth.close()

    async def test_worker_is_reused_and_cache_distinguishes_volume(self):
        process = FakePiperProcess()
        with patch("piper_backend.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            first = await self.synth.synthesize("Ocean mode enabled.", 30)
            await self.synth.synthesize("Alarm set for ten minutes.", 30)
            cached = await self.synth.synthesize("Ocean mode enabled.", 30)
            await self.synth.synthesize("Ocean mode enabled.", 70)
            self.assertEqual(first, process.wav)
            self.assertIs(first, cached)
            self.assertEqual(spawn.await_count, 1)
            self.assertEqual(len(process.requests), 3)
            self.assertEqual([request["volume"] for request in process.requests], [30, 30, 70])
            await self.synth.close()
            self.assertTrue(process.terminated)

    async def test_lru_cache_has_entry_and_byte_limits(self):
        process = FakePiperProcess()
        self.synth.cache_limit = 2
        with patch("piper_backend.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            for text in ("one", "two", "one", "three", "two"):
                await self.synth.synthesize(text, 30)
            self.assertEqual([request["text"] for request in process.requests], ["one", "two", "three", "two"])
            self.synth.cache_byte_limit = len(process.wav)
            await self.synth.synthesize("four", 30)
            self.assertEqual(len(self.synth._cache), 1)
            self.assertLessEqual(self.synth._cache_bytes, self.synth.cache_byte_limit)

    async def test_timeout_kills_worker_and_next_request_can_restart(self):
        stuck = FakePiperProcess(respond=False)
        healthy = FakePiperProcess()
        self.synth.timeout = 0.02
        with patch("piper_backend.asyncio.create_subprocess_exec", new=AsyncMock(side_effect=[stuck, healthy])) as spawn:
            with self.assertRaises(asyncio.TimeoutError):
                await self.synth.synthesize("one", 30)
            self.assertTrue(stuck.terminated)
            self.assertIsNone(self.synth._process)
            self.assertFalse(self.synth._cache)
            self.synth.timeout = 1.0
            self.assertEqual(await self.synth.synthesize("two", 30), healthy.wav)
            self.assertEqual(spawn.await_count, 2)

    async def test_cancelled_synthesis_terminates_worker_without_caching_partial_audio(self):
        process = FakePiperProcess(respond=False)
        with patch("piper_backend.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            task = asyncio.create_task(self.synth.synthesize("one", 30))
            await asyncio.wait_for(process.started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(process.terminated)
            self.assertIsNone(self.synth._process)
            self.assertFalse(self.synth._cache)

    async def test_invalid_response_or_model_error_closes_worker(self):
        for header, data in (({"error": "invalid model"}, b""), ({"bytes": MAX_WAV_BYTES + 1}, b""),
                             ({"bytes": 44}, b"X" * 44)):
            with self.subTest(header=header):
                process = FakePiperProcess(respond=False)
                process.stdout.feed_data(json.dumps(header).encode() + b"\n" + data)
                with patch("piper_backend.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
                    with self.assertRaises(RuntimeError):
                        await self.synth.synthesize("one", 30)
                self.assertTrue(process.terminated)


class PiperFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = make_state()
        self.state.update(feedback_mode="all", feedback_volume=30)
        self.notify = Mock()
        with patch.dict("os.environ", {"TTS_ENGINE": "auto", "PIPER_MODEL": "/tmp/omnirush/test-voice.onnx"}):
            self.speaker = SpokenFeedback(self.state, self.notify)
        self.speaker.gate.cooldown = 0

    async def asyncTearDown(self):
        await self.speaker.close()

    async def test_selection_prefers_piper_then_espeak_for_missing_dependencies(self):
        tools = {"pw-play": "/usr/bin/pw-play", "espeak-ng": "/usr/bin/espeak-ng"}
        with patch("speech_feedback.importlib.util.find_spec", return_value=object()), \
             patch("speech_feedback.Path.is_file", return_value=True), \
             patch("speech_feedback.shutil.which", side_effect=tools.get):
            self.assertEqual(self.speaker._find_engine(), "piper")
            self.assertEqual(self.speaker._piper_player, ["/usr/bin/pw-play", "-"])
            self.assertIn("test-voice", self.speaker.engine_status())
            with patch("speech_feedback.Path.is_file", return_value=False):
                self.assertEqual(self.speaker._find_engine(), "/usr/bin/espeak-ng")
                self.assertIn("model/config missing", self.notify.call_args.args[0])
        with patch("speech_feedback.importlib.util.find_spec", return_value=None):
            self.assertIn("requirements-voice", self.speaker._piper_unavailable_reason())

    async def test_missing_player_uses_fallback(self):
        with patch("speech_feedback.importlib.util.find_spec", return_value=object()), \
             patch("speech_feedback.Path.is_file", return_value=True), \
             patch("speech_feedback.shutil.which", side_effect=lambda name: "/usr/bin/espeak" if name == "espeak" else None):
            self.assertEqual(self.speaker._find_engine(), "/usr/bin/espeak")
            self.assertIn("WAV playback", self.notify.call_args.args[0])

    async def test_synthesis_keeps_mic_open_and_playback_obeys_mute(self):
        process = FakeProcess()
        generating = asyncio.Event()
        generated = asyncio.Event()

        async def synthesize(text, volume):
            self.assertEqual(volume, 30)
            generating.set()
            await generated.wait()
            return wav_bytes()

        self.speaker._piper.synthesize = AsyncMock(side_effect=synthesize)
        self.speaker._piper_player = ["/usr/bin/pw-play", "-"]
        with patch.object(self.speaker, "_find_engine", return_value="piper"), \
             patch("speech_feedback.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.speaker.start()
            self.speaker.say("Ocean mode enabled.")
            await asyncio.wait_for(generating.wait(), 1)
            self.assertIsNotNone(self.speaker.gate.token())
            generated.set()
            await asyncio.wait_for(process.started.wait(), 1)
            self.assertIsNone(self.speaker.gate.token())
            self.assertEqual(process.input, wav_bytes())
            self.assertEqual(spawn.call_args.args, ("/usr/bin/pw-play", "-"))
            self.state["feedback_mode"] = "off"
            self.speaker.settings_changed()
            await asyncio.wait_for(self.speaker.queue.join(), 1)
            self.assertTrue(process.terminated)
            self.assertIsNotNone(self.speaker.gate.token())

    async def test_piper_failure_falls_back_for_current_and_subsequent_phrases(self):
        self.speaker._piper.synthesize = AsyncMock(side_effect=RuntimeError("bad model"))
        self.speaker._play = AsyncMock()
        with patch.object(self.speaker, "_piper_unavailable_reason", return_value=None), \
             patch("speech_feedback.shutil.which", return_value="/usr/bin/espeak-ng"):
            await self.speaker._speak("Ocean mode enabled.", "piper")
            self.assertTrue(self.speaker._piper_failed)
            self.assertEqual(self.speaker._find_engine(), "/usr/bin/espeak-ng")
            self.assertEqual(self.speaker._play.call_args.args[1][:3], ["/usr/bin/espeak-ng", "-a", "30"])
            self.assertIn("Piper failed", self.notify.call_args.args[0])
            self.speaker.settings_changed()
            self.assertEqual(self.speaker._find_engine(), "piper")


if __name__ == "__main__":
    unittest.main()
