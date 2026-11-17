"""Text-to-speech engines. One today (Kokoro); the interface is here so another can be tried beside it.

Tests set `FAKE` to an Engine so nothing is synthesised.
"""

import time
from typing import NamedTuple, Protocol

import httpx

from offbook.config import Settings


class Voice(NamedTuple):
    id: str
    label: str     # "Emma"
    group: str     # "British women"
    gender: str    # "f" or "m"


class Engine(Protocol):
    name: str

    def voices(self) -> list[Voice]: ...

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        """Return MP3 audio."""
        ...


class TTSError(Exception):
    pass


# Kokoro voice ids are <accent><gender>_<name>. Only the English ones are offered.
_GROUPS = {"bf": ("British women", "f"), "bm": ("British men", "m"),
           "af": ("American women", "f"), "am": ("American men", "m")}
# Kokoro's own quality grades put these first; the rest follow alphabetically.
_BEST = ("bf_emma", "bf_isabella", "bm_george", "bm_fable", "af_heart", "af_bella", "af_nicole", "am_michael",
         "am_fenrir", "am_puck")


class Kokoro:
    """A Kokoro-FastAPI server (OpenAI-compatible speech endpoint)."""

    name = "kokoro"

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def voices(self) -> list[Voice]:
        try:
            r = httpx.get(f"{self.base}/v1/audio/voices", timeout=15)
            r.raise_for_status()
            raw = r.json().get("voices", [])
        except (httpx.HTTPError, ValueError) as e:
            raise TTSError(f"voice list unavailable: {e}") from e
        ids = [v["id"] if isinstance(v, dict) else v for v in raw]
        out = []
        for vid in ids:
            prefix, _, name = vid.partition("_")
            if prefix in _GROUPS and name and not name.startswith("v0"):   # v0* are superseded legacy voices
                group, gender = _GROUPS[prefix]
                out.append(Voice(vid, name.capitalize(), group, gender))
        order = list(_GROUPS)
        out.sort(key=lambda v: (order.index(v.id[:2]), _BEST.index(v.id) if v.id in _BEST else len(_BEST), v.label))
        return out

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        try:
            r = httpx.post(f"{self.base}/v1/audio/speech", timeout=180,
                           json={"model": "kokoro", "voice": voice, "input": text, "response_format": "mp3",
                                 "speed": speed})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise TTSError(f"speech failed: {e}") from e
        if not r.content:
            raise TTSError("speech came back empty")
        return r.content


FAKE: Engine | None = None
_voices: tuple[float, list[Voice]] | None = None


def engine(s: Settings) -> Engine:
    return FAKE or Kokoro(s.kokoro_url)


def voices(s: Settings) -> list[Voice]:
    """The engine's voice list, remembered for ten minutes (it only changes when the TTS server is upgraded)."""
    global _voices
    if FAKE is not None:
        return FAKE.voices()
    if _voices is None or time.monotonic() - _voices[0] > 600:
        _voices = (time.monotonic(), engine(s).voices())
    return _voices[1]
