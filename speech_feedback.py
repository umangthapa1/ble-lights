"""Offline, non-blocking speech output and microphone echo suppression."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from typing import Any, Callable


DEFAULT_FEEDBACK_VOLUME = 70


class PlaybackGate:
    """Share playback state between asyncio and the microphone's own thread."""

    def __init__(self, cooldown: float = 0.4) -> None:
        self._lock = threading.Lock()
        self._active = False
        self._until = 0.0
        self._generation = 0
        self.cooldown = cooldown

    def begin(self) -> None:
        with self._lock:
            self._generation += 1
            self._active = True

    def end(self) -> None:
        with self._lock:
            self._active = False
            self._until = time.monotonic() + self.cooldown

    def token(self) -> int | None:
        with self._lock:
            if self._active or time.monotonic() < self._until:
                return None
            return self._generation

    def accepts(self, token: int | None) -> bool:
        # Compare generations too: a complete playback can happen during one
        # listen/recognition call, leaving the gate open again at its end.
        return token is not None and self.token() == token


class FeedbackFilteredStream:
    """Drain captured audio normally, replacing speaker output with silence.

    All native reads and closes stay on the original microphone thread. Keeping
    reads flowing prevents the device buffer from replaying feedback afterward.
    """

    def __init__(self, stream: Any, gate: PlaybackGate) -> None:
        self.stream = stream
        self.gate = gate
        self._last_token = gate.token()
        self._discard_frames = 0

    def read(self, size: int) -> bytes:
        token = self.gate.token()
        if token is not None and token != self._last_token:
            native = getattr(self.stream, "pyaudio_stream", None)
            if native is not None:
                # Recognition can block between reads. Discard audio buffered
                # during playback too, even if speech finished in that gap.
                self._discard_frames = max(0, native.get_read_available())
        self._last_token = token
        data = self.stream.read(size)
        if self._discard_frames > 0:
            self._discard_frames = max(0, self._discard_frames - size)
            return bytes(len(data))
        return data if self.gate.accepts(token) else bytes(len(data))

    def close(self) -> None:
        self.stream.close()


class SpokenFeedback:
    """One bounded speech queue; external synthesis never blocks the light loop."""

    def __init__(self, state: dict[str, Any], notify: Callable[[str, str], None]) -> None:
        self.state = state
        self.notify = notify
        self.gate = PlaybackGate()
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=8)
        self._worker: asyncio.Task | None = None
        self._playback: asyncio.Task | None = None
        self._cancel_requested = False
        self._closing = False
        self._warned = False

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="spoken-feedback")

    def allows(self, category: str) -> bool:
        mode = self.state.get("feedback_mode", "off")
        return (
            self.state.get("feedback_volume", DEFAULT_FEEDBACK_VOLUME) > 0
            and mode != "off"
            and (mode == "all" or category in {"alarm", "feedback"})
        )

    def _clear_queue(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()

    def _cancel_playback(self) -> None:
        if self._playback and not self._cancel_requested:
            self._cancel_requested = True
            self._playback.cancel()

    def settings_changed(self) -> None:
        # Muting/changing the filter cancels speech already playing as well as
        # queued confirmations. Volume changes take effect immediately too.
        self._clear_queue()
        self._cancel_playback()
        self._warned = False
        if self.allows("feedback"):
            self._find_engine()

    def _find_engine(self) -> str | None:
        engine = shutil.which("espeak-ng") or shutil.which("espeak")
        if not engine and not self._warned:
            self.notify(
                "Spoken feedback needs eSpeak NG. Install espeak-ng, then use "
                "'enable spoken feedback' again (see README).", "warning",
            )
            self._warned = True
        return engine

    def say(self, text: str | None, category: str = "command") -> None:
        if self._closing or not text or not self.allows(category):
            return
        if not self._find_engine():
            return
        if category == "alarm" or self.queue.full():
            # Avoid reading out a backlog of old settings ahead of an alarm.
            self._clear_queue()
        self.queue.put_nowait((text[:240], category))

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    async def _speak(self, text: str, engine: str) -> None:
        process = None
        self.gate.begin()
        self.notify(f'Speaking: "{text}" (microphone commands paused during playback)', "info")
        try:
            volume = self.state.get("feedback_volume", DEFAULT_FEEDBACK_VOLUME)
            # Use stdin and an argument list, never a shell. eSpeak's amplitude
            # 100 is its normal full volume; map our percentage directly to it.
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                engine, "-a", str(volume), "-s", "175", "--stdin",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            ))
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                # If mute arrives during process creation, obtain the handle
                # first so the finally block can terminate/reap it too.
                process = await spawning
                raise
            _, error = await asyncio.wait_for(
                process.communicate(input=text.encode("utf-8")), timeout=20.0,
            )
            if process.returncode:
                detail = error.decode("utf-8", errors="replace").strip()[:200]
                raise RuntimeError(detail or f"eSpeak exited with code {process.returncode}")
        finally:
            try:
                if process:
                    await self._stop_process(process)
            finally:
                self.gate.end()

    async def _run(self) -> None:
        try:
            while not self._closing:
                text, category = await self.queue.get()
                try:
                    engine = self._find_engine()
                    if not engine or not self.allows(category):
                        continue
                    self._playback = asyncio.create_task(self._speak(text, engine))
                    try:
                        # The owner cancels playback separately so repeated mute
                        # or shutdown requests cannot interrupt native cleanup.
                        await asyncio.shield(self._playback)
                    except asyncio.CancelledError:
                        if self._closing:
                            raise
                    except Exception as exc:
                        self._clear_queue()
                        if not self._warned:
                            detail = str(exc) or "speech output timed out"
                            self.notify(f"Spoken feedback failed: {detail}. Check audio output; light controls remain available.", "warning")
                            self._warned = True
                    finally:
                        self._playback = None
                        self._cancel_requested = False
                finally:
                    self.queue.task_done()
        finally:
            self.gate.end()

    async def close(self) -> None:
        self._closing = True
        self._clear_queue()
        if self._playback:
            self._cancel_playback()
            await asyncio.gather(self._playback, return_exceptions=True)
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
