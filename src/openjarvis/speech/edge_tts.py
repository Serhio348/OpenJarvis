"""Edge TTS backend — Microsoft neural voices via edge-tts (no API key).

Default Russian male voice: ru-RU-DmitryNeural.
"""

from __future__ import annotations

import asyncio
from typing import List

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_DEFAULT_VOICE = "ru-RU-DmitryNeural"
_COMMON_VOICES = [
    "ru-RU-DmitryNeural",
    "ru-RU-SvetlanaNeural",
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-US-GuyNeural",
]


def _speed_to_rate(speed: float) -> str:
    """Map linear speed (1.0 = normal) to edge-tts rate string."""
    pct = int(round((float(speed) - 1.0) * 100))
    pct = max(-50, min(100, pct))
    if pct >= 0:
        return f"+{pct}%"
    return f"{pct}%"


def _synthesize_sync(text: str, voice: str, rate: str) -> bytes:
    async def _run() -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=rate)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                chunks.append(chunk["data"])
        if not chunks:
            raise RuntimeError("edge-tts returned no audio")
        return b"".join(chunks)

    # synthesize() is invoked via asyncio.to_thread from the API, so this
    # runs in a worker thread without a running event loop.
    return asyncio.run(_run())


@TTSRegistry.register("edge")
class EdgeTTSBackend(TTSBackend):
    """Microsoft Edge online neural TTS (free, no key)."""

    backend_id = "edge"

    def __init__(self, *, default_voice: str = _DEFAULT_VOICE) -> None:
        self._default_voice = default_voice or _DEFAULT_VOICE

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if not (text or "").strip():
            raise ValueError("empty text")
        if output_format and output_format.lower() not in ("mp3", "", "mpeg"):
            # edge-tts streams mp3; ignore other formats.
            pass

        voice = (voice_id or self._default_voice).strip() or _DEFAULT_VOICE
        rate = _speed_to_rate(speed)
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "edge-tts not installed. Install with: uv sync --extra speech"
            ) from exc

        audio = _synthesize_sync(text, voice, rate)
        return TTSResult(
            audio=audio,
            format="mp3",
            voice_id=voice,
            metadata={"backend": "edge", "rate": rate},
        )

    def available_voices(self) -> List[str]:
        return list(_COMMON_VOICES)

    def health(self) -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except ImportError:
            return False


__all__ = ["EdgeTTSBackend"]
