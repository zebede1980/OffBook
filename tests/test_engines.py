import dataclasses
import io
import math
import shutil
import struct

import pytest

from offbook import audio, db, jobs, scripts, sound, tts


@pytest.fixture
def scene(kid, conn):
    sid = kid.post("/api/scripts", json={"title": "Twelve Days"}).json()["script"]["script_id"]
    with db.tx(conn):
        mum, dad = scripts.character_for(conn, sid, "MUM"), scripts.character_for(conn, sid, "DAD")
        scripts.insert_line(conn, sid, 0, "dialogue", "Not there.", speakers=[mum])
        scripts.insert_line(conn, sid, 1, "dialogue", "Where, then?", speakers=[dad])
    kid.post(f"/api/scripts/{sid}/voices/auto")
    return {"id": sid, "mum": mum, "dad": dad}


def voices_of(kid, sid):
    return {c["name"]: c["voice"] for c in kid.get(f"/api/scripts/{sid}").json()["characters"]}


def test_new_scripts_use_the_default_engine(app, kid, settings):
    assert kid.post("/api/scripts", json={"title": "A"}).json()["script"]["engine"] == "kokoro"
    app.state.settings = dataclasses.replace(settings, default_engine="zonos")
    assert kid.post("/api/scripts", json={"title": "B"}).json()["script"]["engine"] == "zonos"
    # an engine this server doesn't have falls back to Kokoro
    app.state.settings = dataclasses.replace(settings, default_engine="espeak")
    assert kid.post("/api/scripts", json={"title": "C"}).json()["script"]["engine"] == "kokoro"


def test_voice_list_per_engine(kid):
    k = kid.get("/api/voices").json()
    assert k["engine"] == "kokoro" and {e["id"] for e in k["engines"]} == {"kokoro", "zonos"}
    z = kid.get("/api/voices", params={"engine": "zonos"}).json()
    assert [v["id"] for v in z["voices"]] == ["z_imogen", "z_rupert", "z_cillian", "z_witch"]
    assert kid.get("/api/voices", params={"engine": "espeak"}).status_code == 400


def test_switching_engine_keeps_the_old_voices_for_coming_back(kid, scene, conn, settings, fake_tts, fake_zonos):
    sid = scene["id"]
    jobs.run_pending(conn, settings)
    kokoro_voices = voices_of(kid, sid)
    kid.patch(f"/api/characters/{scene['dad']}/voice", json={"voice": "bm_lewis"})
    jobs.run_pending(conn, settings)
    kokoro_voices = voices_of(kid, sid)
    made_on_kokoro = len(fake_tts.calls)

    r = kid.put(f"/api/scripts/{sid}/engine", json={"engine": "zonos"}).json()
    assert r["engine"] == "zonos" and r["total"] == 2 and r["ready"] == 0
    z = voices_of(kid, sid)
    assert z["MUM"] == "z_imogen" and z["DAD"] in ("z_rupert", "z_cillian")   # never the witch automatically
    jobs.run_pending(conn, settings)
    assert sorted(c[0] for c in fake_zonos.calls) == ["Not there.", "Where, then?"]
    # a voice can only be one of this engine's
    assert kid.patch(f"/api/characters/{scene['mum']}/voice", json={"voice": "bf_emma"}).status_code == 400
    kid.patch(f"/api/characters/{scene['mum']}/voice", json={"voice": "z_witch"})

    # back to Kokoro: the hand-picked voice is restored and nothing needs making again
    r = kid.put(f"/api/scripts/{sid}/engine", json={"engine": "kokoro"}).json()
    assert voices_of(kid, sid) == kokoro_voices
    assert r["ready"] == 2 and r["pending"] == 0
    assert len(fake_tts.calls) == made_on_kokoro
    # and forward again: the witch is remembered too
    kid.put(f"/api/scripts/{sid}/engine", json={"engine": "zonos"})
    assert voices_of(kid, sid)["MUM"] == "z_witch"
    assert kid.put(f"/api/scripts/{sid}/engine", json={"engine": "espeak"}).status_code == 400


def test_preview_per_engine(kid, fake_zonos):
    r = kid.get("/api/voices/z_rupert/preview", params={"engine": "zonos"})
    assert r.status_code == 200 and b"z_rupert" in r.content
    assert kid.get("/api/voices/z_rupert/preview").status_code == 404     # not a Kokoro voice


def test_progress_counts_and_estimates(kid, scene, conn, settings):
    p = kid.get(f"/api/scripts/{scene['id']}/audio").json()
    assert p["total"] == 2 and p["ready"] == 0 and p["pending"] == 2 and p["eta_seconds"] > 0
    jobs.run_pending(conn, settings)
    p = kid.get(f"/api/scripts/{scene['id']}/audio").json()
    assert (p["ready"], p["pending"], p["eta_seconds"]) == (2, 0, 0)
    listing = kid.get("/api/scripts").json()["scripts"][0]
    assert (listing["voices_ready"], listing["voices_total"]) == (2, 2)


def test_failed_audio_can_be_retried(kid, scene, conn, settings, fake_tts):
    fake_tts.broken = True
    jobs.run_pending(conn, settings)
    assert kid.get(f"/api/scripts/{scene['id']}/audio").json()["failed"] == 2
    fake_tts.broken = False
    assert kid.post(f"/api/scripts/{scene['id']}/audio/retry").json()["pending"] == 2
    jobs.run_pending(conn, settings)
    assert kid.get(f"/api/scripts/{scene['id']}/audio").json()["ready"] == 2


# --- Zonos voice list parsing

def test_zonos_voice_names():
    v = tts._zonos_voice
    assert v({"id": "a", "original_name": "BritishFemale.mp3"}) == tts.Voice("a", "British woman", "British & Irish women", "f")
    assert v({"id": "b", "original_name": "Rupert_en-British_M_1.wav"}).group == "British & Irish men"
    assert v({"id": "c", "original_name": "Cillian_en-Irish_M_1.wav"}).label == "Cillian"
    witch = v({"id": "d", "original_name": "Morgana_en-witch_F_1.mp3"})
    assert witch.label == "Morgana (witch)" and witch.group == "Character voices" and witch.auto is False
    assert v({"id": "e", "original_name": "Wyatt_en-American_M_1.ogg"}).group == "American men"
    assert v({"id": "f", "original_name": "Gaspard_fr_M_1.mp3"}) is None


# --- levelling (real ffmpeg)

def _tone_wav(amplitude: float, seconds: float = 2.0, rate: int = 24000) -> bytes:
    n = int(rate * seconds)
    body = b"".join(struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(n))
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + \
        b"data" + struct.pack("<I", len(body)) + body


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_levelling_brings_quiet_and_loud_clips_together(monkeypatch):
    monkeypatch.undo()   # the autouse fixture replaced sound.level with a pass-through
    from importlib import reload
    reload(sound)
    quiet, loud = sound.level(_tone_wav(0.02)), sound.level(_tone_wav(0.7))
    assert quiet[:3] == b"ID3" or quiet[:2] == b"\xff\xfb" or quiet[0] == 0xFF
    a, b = sound.measure(quiet), sound.measure(loud)
    assert abs(float(a["input_i"]) - float(b["input_i"])) < 1.5
    assert abs(float(a["input_i"]) - sound.TARGET_LUFS) < 2
    # silence can't be measured, but still comes out as audio
    assert sound.level(_tone_wav(0.0, 0.5))


def test_progress_queues_audio_that_was_never_queued(kid, scene, conn, settings):
    # as after an upgrade that changes how clips are made: the clips this script needs simply don't exist yet
    conn.execute("DELETE FROM job")
    conn.execute("DELETE FROM clip")
    p = kid.get(f"/api/scripts/{scene['id']}/audio").json()
    assert p["pending"] == 2
    jobs.run_pending(conn, settings)
    assert kid.get(f"/api/scripts/{scene['id']}/audio").json()["ready"] == 2


def test_startup_sync_queues_everything_missing(kid, scene, conn, settings):
    conn.execute("DELETE FROM job")
    conn.execute("DELETE FROM clip")
    assert audio.sync_all(conn, settings) == 2


def test_progress_checks_do_not_retry_failures_forever(kid, scene, conn, settings, fake_tts):
    fake_tts.broken = True
    jobs.run_pending(conn, settings)
    for _ in range(3):
        assert kid.get(f"/api/scripts/{scene['id']}/audio").json()["failed"] == 2
    assert jobs.run_pending(conn, settings) == 0


def test_switching_engine_cancels_the_old_engines_unmade_clips(kid, scene, conn, settings, fake_tts, fake_zonos):
    # the Kokoro clips are queued but not made yet when the script moves to Zonos
    kid.put(f"/api/scripts/{scene['id']}/engine", json={"engine": "zonos"})
    jobs.run_pending(conn, settings)
    assert fake_tts.calls == [] and len(fake_zonos.calls) == 2
    # and coming back to Kokoro queues them afresh
    kid.put(f"/api/scripts/{scene['id']}/engine", json={"engine": "kokoro"})
    jobs.run_pending(conn, settings)
    assert len(fake_tts.calls) == 2
