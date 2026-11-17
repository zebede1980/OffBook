import struct
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from offbook import attempts, db, scripts
from tests.conftest import HEADERS, PASSWORD

RUN = "run-abcdef123456"


def wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    body = b"\x01\x00" * int(rate * seconds) * channels
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits)
    return b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<I", 16) + fmt + \
        b"data" + struct.pack("<I", len(body)) + body


@pytest.fixture
def line(kid, conn):
    sid = kid.post("/api/scripts", json={"title": "Twelve Days"}).json()["script"]["script_id"]
    with db.tx(conn):
        jack = scripts.character_for(conn, sid, "JACK")
        line_id = scripts.insert_line(conn, sid, 0, "dialogue", "(grinning) Obviously.", speakers=[jack])
        scripts.insert_line(conn, sid, 1, "direction", "He exits.")
    return {"script": sid, "line": line_id}


def post(client, line_id, data, **form):
    fields = {"line_id": str(line_id), "run_id": RUN, "advanced_by": "auto", "hints": "0", **form}
    return client.post("/api/attempts", data=fields, files={"audio": ("line.wav", data, "audio/wav")})


def test_wav_duration():
    assert attempts.wav_duration_ms(wav(1.5)) == 1500
    for bad in (b"", b"RIFF1234WAVE", b"ID3" + b"0" * 100, wav(1, channels=2), wav(1, bits=8), wav(1, rate=96000)):
        with pytest.raises(attempts.BadAudio):
            attempts.wav_duration_ms(bad)


def test_save_list_and_play_back(kid, line):
    r = post(kid, line["line"], wav(2.0), hints="3", advanced_by="tap")
    assert r.status_code == 200, r.text
    listing = kid.get("/api/attempts", params={"run_id": RUN}).json()["attempts"]
    assert listing == [{"attempt_id": r.json()["attempt_id"], "line_id": line["line"], "expected": "Obviously.",
                        "duration_ms": 2000, "advanced_by": "tap", "hints": 3, "has_audio": True}]
    audio = kid.get(f"/api/attempts/{listing[0]['attempt_id']}/audio")
    assert audio.status_code == 200 and audio.content == wav(2.0) and audio.headers["content-type"] == "audio/wav"


def test_rejects_things_that_are_not_recordings(kid, line):
    assert post(kid, line["line"], b"<html>not audio</html>").status_code == 400
    assert post(kid, line["line"], wav(1.0), advanced_by="magic").status_code == 400
    assert post(kid, line["line"], wav(1.0), run_id="../../etc").status_code == 400
    assert post(kid, 9999, wav(1.0)).status_code == 404
    assert post(kid, line["line"] + 1, wav(1.0)).status_code == 404        # a stage direction, not a speech
    assert post(kid, line["line"], wav(130.0)).status_code == 400          # over two minutes
    assert kid.get("/api/attempts", params={"run_id": RUN}).json()["attempts"] == []


def test_a_sibling_cannot_see_or_hear_them(app, kid, line):
    attempt = post(kid, line["line"], wav(1.0)).json()["attempt_id"]
    with TestClient(app, headers=HEADERS) as sibling:
        sibling.post("/api/login", json={"password": PASSWORD})
        pid = sibling.post("/api/profiles", json={"name": "Sam", "colour": "#e4572e"}).json()["profile_id"]
        sibling.post(f"/api/profiles/{pid}/select")
        assert sibling.get("/api/attempts", params={"run_id": RUN}).json()["attempts"] == []
        assert sibling.get(f"/api/attempts/{attempt}/audio").status_code == 404


def test_recordings_need_a_login(client, kid, line):
    attempt = post(kid, line["line"], wav(1.0)).json()["attempt_id"]
    kid.post("/api/logout")
    assert client.get(f"/api/attempts/{attempt}/audio").status_code == 401


def test_old_recordings_are_purged_but_the_attempt_remains(kid, line, conn, settings):
    attempt = post(kid, line["line"], wav(1.0)).json()["attempt_id"]
    rel = conn.execute("SELECT audio_path FROM attempt WHERE attempt_id = ?", (attempt,)).fetchone()[0]
    assert (settings.data_dir / rel).is_file()
    assert attempts.purge_old(conn, settings) == 0

    old = (datetime.now(UTC) - timedelta(days=settings.attempt_days + 1)).isoformat(timespec="seconds")
    conn.execute("UPDATE attempt SET created_at = ? WHERE attempt_id = ?", (old, attempt))
    assert attempts.purge_old(conn, settings) == 1
    assert not (settings.data_dir / rel).exists()
    listing = kid.get("/api/attempts", params={"run_id": RUN}).json()["attempts"]
    assert listing[0]["has_audio"] is False
    assert kid.get(f"/api/attempts/{attempt}/audio").status_code == 404


def test_deleting_a_profile_or_script_removes_the_files(kid, line, conn, settings):
    post(kid, line["line"], wav(1.0))
    post(kid, line["line"], wav(1.0))
    folder = settings.data_dir / "attempts"
    assert len(list(folder.rglob("*.wav"))) == 2
    assert kid.post(f"/api/scripts/{line['script']}/delete", json={"password": PASSWORD}).status_code == 200
    assert list(folder.rglob("*.wav")) == []
    assert conn.execute("SELECT COUNT(*) FROM attempt").fetchone()[0] == 0


def test_deleting_a_line_keeps_the_attempt(kid, line):
    post(kid, line["line"], wav(1.0))
    kid.post(f"/api/lines/{line['line']}/delete")
    listing = kid.get("/api/attempts", params={"run_id": RUN}).json()["attempts"]
    assert listing[0]["line_id"] is None and listing[0]["expected"] == "Obviously." and listing[0]["has_audio"]
