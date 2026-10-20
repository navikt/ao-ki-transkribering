"""Decode uploaded media to a bounded, disk-backed WAV for remote inference."""

import asyncio
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path

SAMPLE_RATE = 16000
MAX_AUDIO_SECONDS = 7200


class AudioFileError(ValueError):
    pass


@asynccontextmanager
async def decoded_audio(source: Path):
    with tempfile.TemporaryDirectory(prefix="upload-", dir=source.parent) as directory:
        target = Path(directory) / "audio.wav"
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-i", str(source), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-t", str(MAX_AUDIO_SECONDS + 1), "-c:a", "pcm_s16le", str(target),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise AudioFileError("Lydkonvertering er ikke tilgjengelig på serveren.") from None
        try:
            try:
                code = await asyncio.wait_for(process.wait(), timeout=120)
            except asyncio.TimeoutError:
                raise AudioFileError("Lydfilen tok for lang tid å klargjøre. Prøv en annen lydfil.") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if code:
            raise AudioFileError("Lydfilen kunne ikke leses. Kontroller at filen kan spilles av.")
        try:
            audio = wave.open(str(target), "rb")
        except (wave.Error, EOFError):
            raise AudioFileError("Lydfilen kunne ikke leses.") from None
        with audio:
            if audio.getnframes() > MAX_AUDIO_SECONDS * SAMPLE_RATE:
                raise AudioFileError("Lydfilen er lengre enn grensen på to timer.")
            if not audio.getnframes():
                raise AudioFileError("Lydfilen inneholder ingen lyd.")
            yield audio
