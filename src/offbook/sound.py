"""Levelling: every clip, whichever engine and voice made it, comes out at the same loudness, as MP3.

Without this some characters are noticeably quieter than others. Uses ffmpeg's loudnorm in two passes: measure,
then apply one fixed gain (`linear=true`), so the voice's own rises and falls are left alone.
"""

import json
import re
import shutil
import subprocess

TARGET_LUFS = -18.0   # comfortable speech level on a phone speaker
PEAK_DB = -1.5


class SoundError(Exception):
    pass


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise SoundError("ffmpeg is not installed")
    return exe


def _run(args: list[str], data: bytes) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([_ffmpeg(), "-hide_banner", "-nostdin", *args], input=data, capture_output=True,
                              timeout=120, check=True)
    except subprocess.CalledProcessError as e:
        tail = e.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["(no output)"]
        raise SoundError(f"ffmpeg failed: {tail[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise SoundError("ffmpeg took too long") from e


def measure(data: bytes) -> dict | None:
    """loudnorm's first-pass figures, or None for audio too short or too quiet to measure."""
    r = _run(["-i", "pipe:0", "-af", f"loudnorm=I={TARGET_LUFS}:TP={PEAK_DB}:LRA=11:print_format=json",
              "-f", "null", "-"], data)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr.decode(errors="replace"))
    if not m:
        return None
    stats = json.loads(m.group(0))
    try:
        if float(stats["input_i"]) < -70:   # silence comes back as -inf or near it
            return None
    except (KeyError, ValueError):
        return None
    return stats


def level(data: bytes) -> bytes:
    """Any audio in, levelled mono MP3 out."""
    stats = measure(data)
    chain = []
    if stats is not None:
        chain.append(f"loudnorm=I={TARGET_LUFS}:TP={PEAK_DB}:LRA=11:measured_I={stats['input_i']}:"
                     f"measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}:"
                     f"measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true")
    args = ["-i", "pipe:0"] + (["-af", ",".join(chain)] if chain else []) + \
        ["-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "5", "-f", "mp3", "pipe:1"]
    out = _run(args, data).stdout
    if not out:
        raise SoundError("ffmpeg produced no audio")
    return out
