"""Text-to-speech engines: Kokoro and Zonos3-Mini, behind one small interface.

Each script picks its engine; voices are engine-specific. Tests set `FAKE` to a {engine name: Engine} map so
nothing is synthesised.
"""

import re
import time
import zlib
from typing import NamedTuple, Protocol

import httpx

from offbook.config import Settings

ENGINES = ("kokoro", "zonos")
LABELS = {"kokoro": "Kokoro", "zonos": "Zonos"}


class Voice(NamedTuple):
    id: str
    label: str     # "Emma"
    group: str     # "British women"
    gender: str    # "f" or "m"
    auto: bool = True   # may be handed out automatically; False for novelty voices (a witch, an announcer)


class Engine(Protocol):
    name: str

    def voices(self) -> list[Voice]: ...

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        """Return audio (any format ffmpeg reads); it is levelled and turned into MP3 afterwards."""
        ...


class TTSError(Exception):
    pass


# --- Kokoro

# Kokoro voice ids are <accent><gender>_<name>. Only the English ones are offered.
_K_GROUPS = {"bf": ("British women", "f"), "bm": ("British men", "m"),
             "af": ("American women", "f"), "am": ("American men", "m")}
# Kokoro's own quality grades put these first; the rest follow alphabetically.
_K_BEST = ("bf_emma", "bf_isabella", "bm_george", "bm_fable", "af_heart", "af_bella", "af_nicole", "am_michael",
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
            if prefix in _K_GROUPS and name and not name.startswith("v0"):   # v0* are superseded legacy voices
                group, gender = _K_GROUPS[prefix]
                out.append(Voice(vid, name.capitalize(), group, gender))
        order = list(_K_GROUPS)
        out.sort(key=lambda v: (order.index(v.id[:2]), _K_BEST.index(v.id) if v.id in _K_BEST else len(_K_BEST),
                                v.label))
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


# --- Zonos3-Mini

_Z_GROUP_ORDER = ("British & Irish women", "British & Irish men", "American women", "American men", "Character voices")
_Z_PLAIN = {"BritishFemale": ("British woman", "British & Irish women", "f"),
            "AmericanFemale": ("American woman", "American women", "f"),
            "AmericanMale": ("American man", "American men", "m")}
_Z_NAMED = re.compile(r"^(?P<name>[A-Za-z]+)_en-(?P<tag>[A-Za-z]+)_(?P<g>[FM])_\d+")
_Z_TAGS = {"witch": "witch", "anime": "cartoon", "announcervoice": "announcer", "asrm": "whispery",
           "asmr": "whispery", "energetic": "lively"}


def _zonos_voice(speaker: dict) -> Voice | None:
    """Turn one /v2/speakers entry into a Voice, or None for the non-English ones."""
    original = str(speaker.get("original_name", "")).rsplit(".", 1)[0]
    if original in _Z_PLAIN:
        label, group, gender = _Z_PLAIN[original]
        return Voice(speaker["id"], label, group, gender)
    m = _Z_NAMED.match(original)
    if not m:
        return None
    gender = m["g"].lower()
    tag = m["tag"].lower()
    if tag in ("british", "irish"):
        group = "British & Irish women" if gender == "f" else "British & Irish men"
        return Voice(speaker["id"], m["name"], group, gender)
    if tag == "american":
        return Voice(speaker["id"], m["name"], "American women" if gender == "f" else "American men", gender)
    return Voice(speaker["id"], f"{m['name']} ({_Z_TAGS.get(tag, tag)})", "Character voices", gender, auto=False)


class Zonos:
    """A Zonos3-Mini server (the ggml build), speaking British English."""

    name = "zonos"

    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")

    def voices(self) -> list[Voice]:
        try:
            r = httpx.get(f"{self.base}/v2/speakers", timeout=15)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TTSError(f"voice list unavailable: {e}") from e
        speakers = body if isinstance(body, list) else body.get("speakers", [])
        out = [v for v in (_zonos_voice(sp) for sp in speakers if isinstance(sp, dict) and sp.get("id")) if v]
        out.sort(key=lambda v: (_Z_GROUP_ORDER.index(v.group), v.label != "British woman", v.label))
        return out

    def synthesize(self, text: str, voice: str, speed: float) -> bytes:
        # Zonos has no speed setting on this endpoint. A fixed seed per line and voice makes regeneration repeatable.
        seed = zlib.crc32(f"{voice}\n{text}".encode())
        try:
            r = httpx.post(f"{self.base}/v1/audio/speech", timeout=600,
                           json={"model": "zonos3", "input": text, "voice": voice, "response_format": "wav",
                                 "language": "en_gb", "seed": seed})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise TTSError(f"speech failed: {e}") from e
        if len(r.content) < 100:
            raise TTSError("speech came back empty")
        return r.content


# --- choosing

FAKE: dict[str, Engine] | None = None
_voices: dict[str, tuple[float, list[Voice]]] = {}


def available(s: Settings) -> list[str]:
    """Engines this server is set up for. Kokoro always; Zonos when ZONOS_URL is set."""
    if FAKE is not None:
        return [e for e in ENGINES if e in FAKE]
    return [e for e in ENGINES if e != "zonos" or s.zonos_url]


def engine(s: Settings, name: str) -> Engine:
    if FAKE is not None:
        if name not in FAKE:
            raise TTSError(f"no {name} engine")
        return FAKE[name]
    if name == "zonos":
        if not s.zonos_url:
            raise TTSError("Zonos is not set up on this server")
        return Zonos(s.zonos_url)
    if name == "kokoro":
        return Kokoro(s.kokoro_url)
    raise TTSError(f"unknown engine {name!r}")


def voices(s: Settings, name: str) -> list[Voice]:
    """An engine's voice list, remembered for ten minutes (it only changes when the TTS server is upgraded)."""
    if FAKE is not None:
        return engine(s, name).voices()
    hit = _voices.get(name)
    if hit is None or time.monotonic() - hit[0] > 600:
        hit = (time.monotonic(), engine(s, name).voices())
        _voices[name] = hit
    return hit[1]
