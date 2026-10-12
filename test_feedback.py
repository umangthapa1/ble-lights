"""Speech settings, playback lifecycle and echo protection, without hardware."""

import asyncio
import copy
from datetime import datetime
import unittest
from unittest.mock import AsyncMock, Mock, patch

import speech_recognition as sr

from main import COLORS, MicrophoneController, apply_command, event_worker, light_snapshot, timer_worker
from speech_feedback import FeedbackFilteredStream, PlaybackGate, PlaybackInterrupted, SpokenFeedback


def make_state():
    return {"on": True, "color": COLORS["warm"], "color_name": "warm", "brightness": 1.0,
            "mode": None, "effect": None, "timer": None, "alarm": None}


class FeedbackCommandTests(unittest.TestCase):
    def test_settings_do_not_change_light_history_or_repeat(self):
        state = make_state()
        apply_command("ocean mode", state)
        light = light_snapshot(state)
        history = copy.deepcopy(state["_history"])
        for command in ("enable spoken feedback", "feedback volume thirty", "only announce alarms"):
            result = apply_command(command, state)
            self.assertTrue(result.changed)
            self.assertEqual(result.category, "feedback")
            self.assertEqual(light_snapshot(state), light)
            self.assertEqual(state["_history"], history)
            self.assertEqual(state["_last_command"], "ocean mode")
        self.assertEqual(state["feedback_mode"], "alarms")
        self.assertEqual(state["feedback_volume"], 30)
        apply_command("undo", state)
        self.assertIsNone(state["effect"])
        self.assertEqual(state["feedback_volume"], 30)
        apply_command("repeat", state)
        self.assertEqual(state["effect"], "ocean")
        self.assertIsNone(apply_command("mute feedback", state).spoken)
        self.assertEqual(state["feedback_mode"], "off")

    def test_volume_is_validated_without_accidentally_setting_light_brightness(self):
        state = make_state()
        for command, volume in (("feedback volume 0", 0), ("feedback volume 30%", 30),
                                ("set feedback volume to fifty percent", 50), ("feedback volume 100", 100)):
            with self.subTest(command=command):
                self.assertTrue(apply_command(command, state).changed)
                self.assertEqual(state["feedback_volume"], volume)
                self.assertEqual(state["brightness"], 1.0)
                self.assertTrue(state["on"])
        before = copy.deepcopy(state)
        for command in ("feedback volume 101", "feedback volume -20", "feedback volume blue",
                        "feedback volume 30% in 2 minutes", "enable feedback in 5 seconds",
                        "feedback volume thirty bananas", "feedback status"):
            with self.subTest(command=command):
                self.assertFalse(apply_command(command, state).changed)
                self.assertEqual(state, before)

    def test_short_spoken_confirmations_and_alarm_durations(self):
        state = make_state()
        self.assertIn("Ocean mode enabled.", apply_command("ocean mode", state).spoken)
        result = apply_command("alarm in ten minutes", state)
        self.assertEqual(result.spoken, "Alarm set for 10 minutes.")
        self.assertEqual(result.category, "alarm")
        result = apply_command("snooze for 1 hour 5 minutes", state)
        self.assertEqual(result.spoken, "Alarm snoozed for 1 hour and 5 minutes.")
        self.assertEqual(apply_command("cancel alarm", state).category, "alarm")
        self.assertIsNone(apply_command("unrecognized chatter", state).spoken)
        self.assertIsNone(apply_command("help", state).spoken)


class PlaybackGateTests(unittest.TestCase):
    def test_self_speech_is_drained_silenced_and_rejected_even_after_it_ends(self):
        gate = PlaybackGate()
        native = Mock(spec=["read", "close"])
        native.read.return_value = b"\x01\x02" * 8
        stream = FeedbackFilteredStream(native, gate)
        with patch("speech_feedback.time.monotonic", return_value=100.0) as clock:
            token = gate.token()
            self.assertEqual(stream.read(8), native.read.return_value)
            gate.begin()
            self.assertIsNone(gate.token())
            with self.assertRaises(PlaybackInterrupted):
                stream.read(8)
            self.assertEqual(stream.read(8), bytes(16))
            gate.end()
            self.assertFalse(gate.accepts(token))
            clock.return_value = 100.05
            self.assertEqual(stream.read(8), bytes(16))
            clock.return_value = 100.11
            with self.assertRaises(PlaybackInterrupted):
                stream.read(8)
            self.assertEqual(stream.read(8), native.read.return_value)
            self.assertFalse(gate.accepts(token))
        self.assertEqual(native.read.call_count, 4)  # Keep the native buffer drained.
        stream.close()
        native.close.assert_called_once()

    def test_feedback_starting_inside_one_read_interrupts_the_stale_capture(self):
        gate = PlaybackGate(cooldown=0)
        native = Mock(spec=["read", "close"])

        def read(size):
            gate.begin()
            gate.end()
            return b"\x01\x02"

        native.read.side_effect = read
        with self.assertRaises(PlaybackInterrupted):
            FeedbackFilteredStream(native, gate).read(1)
        native.close.assert_not_called()

    def test_audio_buffered_during_recognition_is_discarded_after_playback(self):
        gate = PlaybackGate(cooldown=0)
        native = Mock()
        native.pyaudio_stream.get_read_available.return_value = 4
        native.read.return_value = b"\x01\x02" * 2
        stream = FeedbackFilteredStream(native, gate)
        # An entire announcement happens while the recognizer is awaiting a
        # network response; no microphone reads run until afterward.
        gate.begin()
        gate.end()
        with self.assertRaises(PlaybackInterrupted):
            stream.read(2)
        self.assertEqual(stream.read(2), bytes(4))
        self.assertEqual(stream.read(2), bytes(4))
        self.assertEqual(stream.read(2), native.read.return_value)
        native.pyaudio_stream.get_read_available.assert_called_once()

    def test_recognizer_can_capture_the_first_phrase_after_playback(self):
        gate = PlaybackGate()
        native = Mock(spec=["read", "close"])
        source = Mock(spec=sr.AudioSource)
        source.CHUNK, source.SAMPLE_RATE, source.SAMPLE_WIDTH = 160, 1600, 2
        source.stream = FeedbackFilteredStream(native, gate)
        silence = bytes(320)
        speech = b"\xe8\x03" * 160
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 100
        recognizer.dynamic_energy_threshold = False
        recognizer.pause_threshold = 0.2
        recognizer.non_speaking_duration = 0.1
        recognizer.phrase_threshold = 0.1

        def start_playback(size):
            gate.begin()
            return speech

        with patch("speech_feedback.time.monotonic", return_value=100.0) as clock:
            native.read.side_effect = start_playback
            # Playback interrupts a recognizer already waiting for a phrase.
            with self.assertRaises(PlaybackInterrupted):
                recognizer.listen(source, timeout=1)
            native.read.side_effect = None
            native.read.return_value = speech
            with self.assertRaises(PlaybackInterrupted):
                source.stream.read(source.CHUNK)
            self.assertEqual(source.stream.read(source.CHUNK), silence)

            gate.end()
            clock.return_value = 100.11
            # Synchronize the new capture token without consuming user speech.
            with self.assertRaises(PlaybackInterrupted):
                source.stream.read(source.CHUNK)
            capture_token = gate.token()
            native.read.side_effect = [speech, speech, silence, silence, silence, b""]
            audio = recognizer.listen(source, timeout=1)
            self.assertTrue(audio.frame_data.startswith(speech * 2))
            self.assertTrue(gate.accepts(capture_token))
        native.close.assert_not_called()


class MicrophoneControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_stop_and_restart_manage_the_listener_callback(self):
        loop = asyncio.get_running_loop()
        events = asyncio.Queue()
        gate = PlaybackGate()
        stops = []

        def start_listener(*args):
            stop = Mock()
            stops.append(stop)
            return stop

        with patch("main.start_listening", side_effect=start_listener) as starter:
            controller = MicrophoneController(loop, events, gate)
            self.assertTrue(await controller.start())
            self.assertTrue(controller.enabled)
            self.assertTrue(await controller.standby())
            self.assertFalse(controller.enabled)
            self.assertTrue(controller.running)
            self.assertTrue(await controller.start())
            self.assertTrue(controller.enabled)
            self.assertEqual(starter.call_count, 1)
            self.assertTrue(await controller.stop())
            self.assertFalse(controller.enabled)
            stops[0].assert_called_once_with()
            self.assertTrue(await controller.start())
            self.assertEqual(starter.call_count, 2)
            await controller.close()
            stops[1].assert_called_once_with(False)


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.terminated = False
        self.input = None

    async def communicate(self, input):
        self.input = input
        self.started.set()
        await self.finished.wait()
        return b"", b""

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.finished.set()

    def kill(self):
        self.returncode = -9
        self.finished.set()

    async def wait(self):
        await self.finished.wait()
        return self.returncode


class SpokenFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = make_state()
        self.notify = Mock()
        # Keep eSpeak lifecycle tests independent of locally downloaded models.
        with patch.dict("os.environ", {"TTS_ENGINE": "espeak"}):
            self.speaker = SpokenFeedback(self.state, self.notify)
        self.speaker.gate.cooldown = 0

    async def asyncTearDown(self):
        await self.speaker.close()

    async def test_modes_and_missing_engine_have_no_playback_or_light_side_effects(self):
        with patch("speech_feedback.shutil.which", return_value="/usr/bin/espeak-ng"):
            self.speaker.say("Muted by default")
            self.assertTrue(self.speaker.queue.empty())
            apply_command("only announce alarms", self.state)
            self.speaker.say("Ocean mode enabled.")
            self.speaker.say("Timer finished.", "timer")
            self.assertTrue(self.speaker.queue.empty())
            self.speaker.say("Alarm set for ten minutes.", "alarm")
            self.assertEqual(self.speaker.queue.qsize(), 1)
            apply_command("mute feedback", self.state)
            self.speaker.settings_changed()
            self.assertTrue(self.speaker.queue.empty())
        with patch("speech_feedback.shutil.which", return_value=None):
            apply_command("enable spoken feedback", self.state)
            self.speaker.settings_changed()
            self.speaker.say("First confirmation.")
            self.speaker.say("Second confirmation.")
            self.assertTrue(self.speaker.queue.empty())
            self.notify.assert_called_once()
            self.assertIn("Install espeak-ng", self.notify.call_args.args[0])

    async def test_playback_does_not_block_and_mute_cancels_active_and_queued_speech(self):
        apply_command("enable spoken feedback", self.state)
        apply_command("feedback volume 30", self.state)
        process = FakeProcess()
        with patch("speech_feedback.shutil.which", return_value="/usr/bin/espeak-ng"), \
             patch("speech_feedback.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.speaker.start()
            self.speaker.say("Ocean mode enabled.")
            await asyncio.wait_for(process.started.wait(), 1)
            self.assertEqual(process.input, b"Ocean mode enabled.")
            self.assertEqual(spawn.call_args.args, ("/usr/bin/espeak-ng", "-a", "30", "-s", "175", "--stdin"))
            self.assertIsNone(self.speaker.gate.token())
            # A light command still runs while the fake engine is speaking.
            apply_command("blue", self.state)
            self.assertEqual(self.state["color"], COLORS["blue"])
            self.speaker.say("Blue lights on.")
            apply_command("mute feedback", self.state)
            self.speaker.settings_changed()
            await asyncio.wait_for(self.speaker.queue.join(), 1)
            self.assertTrue(process.terminated)
            self.assertEqual(spawn.await_count, 1)
            self.assertIsNotNone(self.speaker.gate.token())

    async def test_shutdown_reaps_the_speech_process(self):
        self.state["feedback_mode"] = "all"
        process = FakeProcess()
        with patch("speech_feedback.shutil.which", return_value="espeak-ng"), \
             patch("speech_feedback.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            self.speaker.start()
            self.speaker.say("An announcement.")
            await asyncio.wait_for(process.started.wait(), 1)
            await asyncio.wait_for(self.speaker.close(), 1)
            self.assertTrue(process.terminated)
            self.assertTrue(self.speaker._worker.done())
            self.assertIsNotNone(self.speaker.gate.token())

    async def test_mute_during_spawn_does_not_leave_an_orphan_process(self):
        self.state["feedback_mode"] = "all"
        process = FakeProcess()
        spawning = asyncio.Event()
        release_spawn = asyncio.Event()

        async def spawn(*args, **kwargs):
            spawning.set()
            await release_spawn.wait()
            return process

        with patch("speech_feedback.shutil.which", return_value="espeak-ng"), \
             patch("speech_feedback.asyncio.create_subprocess_exec", side_effect=spawn):
            self.speaker.start()
            self.speaker.say("A confirmation.")
            await asyncio.wait_for(spawning.wait(), 1)
            apply_command("mute feedback", self.state)
            self.speaker.settings_changed()
            release_spawn.set()
            await asyncio.wait_for(self.speaker.queue.join(), 1)
            self.assertTrue(process.terminated)
            self.assertFalse(process.started.is_set())
            self.assertIsNotNone(self.speaker.gate.token())

    async def test_zero_volume_is_silent_and_timeout_cleans_up(self):
        self.state.update(feedback_mode="all", feedback_volume=0)
        process = FakeProcess()
        process.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        with patch("speech_feedback.shutil.which", return_value="espeak-ng"), \
             patch("speech_feedback.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.speaker.start()
            self.speaker.say("Silent at volume zero.", "alarm")
            await asyncio.sleep(0)
            spawn.assert_not_awaited()
            self.state["feedback_volume"] = 30
            self.speaker.say("A confirmation.")
            await asyncio.wait_for(self.speaker.queue.join(), 1)
            self.assertTrue(process.terminated)
            self.assertIsNotNone(self.speaker.gate.token())
            self.assertTrue(any("timed out" in call.args[0] for call in self.notify.call_args_list))

    async def test_failed_synthesis_reports_error_and_releases_microphone(self):
        self.state["feedback_mode"] = "all"
        with patch("speech_feedback.shutil.which", return_value="espeak-ng"), \
             patch("speech_feedback.asyncio.create_subprocess_exec", new=AsyncMock(side_effect=OSError("no audio output"))):
            self.speaker.start()
            self.speaker.say("A confirmation.")
            await asyncio.wait_for(self.speaker.queue.join(), 1)
            self.assertIsNotNone(self.speaker.gate.token())
            self.assertTrue(any("Spoken feedback failed" in call.args[0] for call in self.notify.call_args_list))

    async def test_event_worker_announces_commands_and_alarm_only_filter(self):
        console = Mock()
        events = asyncio.Queue()
        for text in ("enable spoken feedback", "only announce alarms", "ocean mode", "alarm in ten minutes", "quit"):
            events.put_nowait({"kind": "command", "source": "keyboard", "text": text})
        with patch("speech_feedback.shutil.which", return_value="espeak-ng"):
            await event_worker(events, self.state, asyncio.Event(), console, self.speaker)
        self.assertEqual(self.speaker.queue.qsize(), 1)
        self.assertEqual(self.speaker.queue.get_nowait(), ("Alarm set for 10 minutes.", "alarm"))
        self.speaker.queue.task_done()

    async def test_event_worker_drops_own_voice_but_still_accepts_keyboard_commands(self):
        events = asyncio.Queue()
        old_token = self.speaker.gate.token()
        self.speaker.gate.begin()
        self.speaker.gate.end()
        events.put_nowait({"kind": "command", "source": "voice", "capture_token": old_token, "text": "red"})
        events.put_nowait({"kind": "command", "source": "keyboard", "text": "brightness 30"})
        events.put_nowait({"kind": "command", "source": "keyboard", "text": "quit"})
        await event_worker(events, self.state, asyncio.Event(), Mock(), self.speaker)
        self.assertEqual(self.state["color"], COLORS["warm"])
        self.assertEqual(self.state["brightness"], 0.3)

    async def test_clock_replies_use_personality_and_obey_speech_settings(self):
        expected = {
            "professional": "The time is 9:42 PM.",
            "minimal": "9:42 PM.",
            "playful": "The time is 9:42 PM. Consider yourself up to date!",
            "sarcastic": "The time is 9:42 PM. Keeping track so you don't have to.",
            "cinematic": "The time is 9:42 PM. At your service.",
        }
        for profile, answer in expected.items():
            for mode, volume in (("all", 70), ("off", 70), ("alarms", 70), ("all", 0)):
                with self.subTest(profile=profile, mode=mode, volume=volume):
                    self.state.update(personality=profile, feedback_mode=mode, feedback_volume=volume)
                    events = asyncio.Queue()
                    events.put_nowait({"kind": "command", "source": "voice", "text": "what time is it"})
                    events.put_nowait({"kind": "command", "source": "keyboard", "text": "quit"})
                    console = Mock()
                    with patch("main.datetime") as clock, \
                         patch("speech_feedback.shutil.which", return_value="espeak-ng"):
                        clock.now.return_value.astimezone.return_value = datetime(2026, 10, 10, 21, 42)
                        await event_worker(events, self.state, asyncio.Event(), console, self.speaker)
                    console.status.assert_any_call(answer)
                    console.light_state.assert_not_called()
                    if mode == "all" and volume:
                        self.assertEqual(self.speaker.queue.get_nowait(), (answer, "clock"))
                        self.speaker.queue.task_done()
                    self.assertTrue(self.speaker.queue.empty())

    async def test_alarm_announcement_occurs_once_even_in_alarms_only_mode(self):
        self.state.update(feedback_mode="alarms", alarm={"deadline": 0, "active": False})
        shutdown = asyncio.Event()
        with patch("speech_feedback.shutil.which", return_value="espeak-ng"):
            worker = asyncio.create_task(timer_worker(self.state, shutdown, Mock(), self.speaker))
            # Two scheduler ticks, without waiting for an actual alarm deadline.
            await asyncio.sleep(0.3)
            shutdown.set()
            await asyncio.wait_for(worker, 1)
        self.assertEqual(self.speaker.queue.qsize(), 1)
        text, category = self.speaker.queue.get_nowait()
        self.speaker.queue.task_done()
        self.assertEqual(category, "alarm")
        self.assertIn("alarm is ringing", text)


if __name__ == "__main__":
    unittest.main()
