"""Persistent Piper synthesis isolated from the microphone's native libraries.

The child loads one voice and accepts JSON lines on stdin. Each response is a
JSON header followed by exactly that many WAV bytes. No audio files or servers
are needed; repeated phrases use a bounded in-memory cache in the parent.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import io
import json
from pathlib import Path
import sys
import wave


MAX_WAV_BYTES = 8 * 1024 * 1024


class PiperSynthesizer:
    def __init__(self, model: Path, *, timeout: float = 30.0) -> None:
        self.model = model
        self.timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[tuple[str, int], bytes] = OrderedDict()
        self._cache_bytes = 0
        self.cache_limit = 32
        self.cache_byte_limit = 16 * 1024 * 1024

    async def _ensure_worker(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            sys.executable, "-u", str(Path(__file__).resolve()), "--worker", str(self.model),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        ))
        try:
            self._process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            self._process = await spawning
            await self._stop_worker()
            raise

    async def _exchange(self, text: str, volume: int) -> bytes:
        await self._ensure_worker()
        process = self._process
        assert process and process.stdin and process.stdout
        request = json.dumps({"text": text, "volume": volume}).encode("utf-8") + b"\n"
        process.stdin.write(request)
        await process.stdin.drain()
        header = await process.stdout.readline()
        if not header:
            raise RuntimeError("Piper worker stopped before returning audio")
        response = json.loads(header)
        if "error" in response:
            raise RuntimeError(response["error"])
        size = response.get("bytes")
        if not isinstance(size, int) or not 44 <= size <= MAX_WAV_BYTES:
            raise RuntimeError("Piper returned an invalid audio length")
        audio = await process.stdout.readexactly(size)
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise RuntimeError("Piper did not return WAV audio")
        return audio

    async def synthesize(self, text: str, volume: int) -> bytes:
        key = (text, volume)
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            try:
                audio = await asyncio.wait_for(self._exchange(text, volume), self.timeout)
            except (Exception, asyncio.CancelledError):
                # Partial packets cannot be reused after cancellation/timeout.
                await self._stop_worker()
                raise
            self._cache[key] = audio
            self._cache_bytes += len(audio)
            while len(self._cache) > self.cache_limit or self._cache_bytes > self.cache_byte_limit:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= len(old)
            return audio

    async def _stop_worker(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 1.0)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    async def close(self) -> None:
        async with self._lock:
            await self._stop_worker()
            self._cache.clear()
            self._cache_bytes = 0


def worker_main(model: str) -> None:
    # Reserve stdout exclusively for protocol output, including during imports.
    output = sys.stdout.buffer
    sys.stdout = sys.stderr
    voice = None
    for line in sys.stdin.buffer:
        try:
            from piper import PiperVoice, SynthesisConfig

            request = json.loads(line)
            text, volume = request["text"], request["volume"]
            if not isinstance(text, str) or len(text) > 240 or not 0 <= volume <= 100:
                raise ValueError("Invalid Piper synthesis request")
            if voice is None:
                voice = PiperVoice.load(model)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(volume=volume / 100.0))
            audio = buffer.getvalue()
            if not 44 <= len(audio) <= MAX_WAV_BYTES:
                raise ValueError("Synthesized audio is too large or empty")
            output.write(json.dumps({"bytes": len(audio)}).encode("utf-8") + b"\n")
            output.write(audio)
        except Exception as exc:
            output.write(json.dumps({"error": str(exc)[:500]}).encode("utf-8") + b"\n")
        output.flush()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("This module is started automatically by Lumen.")
    worker_main(sys.argv[2])
