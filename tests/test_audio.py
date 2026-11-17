import json

import pytest
from fastapi.testclient import TestClient

from offbook import audio, db, jobs, scripts
from tests.conftest import HEADERS, PASSWORD


@pytest.fixture
def scene(kid, conn):
    """MUM: Not there. / DAD: (sighing) Where, then? / PARTRIDGE: Coo. / MUM & DAD: Out! / a direction"""
    sid = kid.post("/api/scripts", json={"title": "Twelve Days"}).json()["script"]["script_id"]
    with db.tx(conn):
        mum, dad, bird = (scripts.character_for(conn, sid, n) for n in ("MUM", "DAD", "PARTRIDGE"))
        scripts.insert_line(conn, sid, 0, "dialogue", "Not there.", speakers=[mum])
        scripts.insert_line(conn, sid, 1, "dialogue", "(sighing) Where, then?", speakers=[dad])
        scripts.insert_line(conn, sid, 2, "dialogue", "Coo.", speakers=[bird])
        scripts.insert_line(conn, sid, 3, "dialogue", "Out!", speakers=[mum, dad])
        scripts.insert_line(conn, sid, 4, "direction", "They leave.")
    return {"id": sid, "mum": mum, "dad": dad, "bird": bird}


def voices_of(kid, sid):
    return {c["name"]: c["voice"] for c in kid.get(f"/api/scripts/{sid}").json()["characters"]}


def test_gender_guess():
    assert audio.guess_gender("MUM") == "f"
    assert audio.guess_gender("Uncle Nicholas") == "m"
    assert audio.guess_gender("MRS. POTTS") == "f"
    assert audio.guess_gender("PARTRIDGE") is None
    assert audio.guess_gender("HUMAN") is None      # "man" inside a word does not count


def test_voice_list(kid):
    v = kid.get("/api/voices").json()["voices"]
    assert v[0] == {"id": "bf_emma", "label": "Emma", "group": "British women"} and len(v) == 4


def test_auto_assign_matches_names_and_spreads_voices(kid, scene):
    r = kid.post(f"/api/scripts/{scene['id']}/voices/auto").json()
    assert r["assigned"] == 3 and r["queued"] == 4
    got = voices_of(kid, scene["id"])
    assert got["MUM"].startswith("bf_") and got["DAD"].startswith("bm_")
    assert got["PARTRIDGE"] not in (got["MUM"], got["DAD"])     # the least-used voice, so parts sound different
    # running it again changes nothing
    assert kid.post(f"/api/scripts/{scene['id']}/voices/auto").json() == {"assigned": 0, "queued": 0}


def test_clips_are_generated_in_script_order_from_the_spoken_words(kid, scene, conn, settings, fake_tts):
    kid.post(f"/api/scripts/{scene['id']}/voices/auto")
    jobs.run_pending(conn, settings)
    # the stage direction inside DAD's speech is not read out; the shared line uses the first speaker's voice
    assert [c[0] for c in fake_tts.calls] == ["Not there.", "Where, then?", "Coo.", "Out!"]
    assert fake_tts.calls[3][1] == voices_of(kid, scene["id"])["MUM"]

    r = kid.get(f"/api/scripts/{scene['id']}/rehearsal").json()
    assert r["audio"] == {"total": 4, "ready": 4, "failed": 0}
    assert [ln["clip_status"] for ln in r["lines"]] == ["done", "done", "done", "done", None]
    clip = kid.get(f"/api/clips/{r['lines'][0]['clip']}")
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/mpeg"
    assert clip.content.endswith(b"Not there.")


def test_changing_a_voice_or_a_line_only_remakes_what_changed(kid, scene, conn, settings, fake_tts):
    kid.post(f"/api/scripts/{scene['id']}/voices/auto")
    jobs.run_pending(conn, settings)
    fake_tts.calls.clear()

    r = kid.patch(f"/api/characters/{scene['bird']}/voice", json={"voice": "bm_lewis", "speed": 1.1})
    assert r.status_code == 200
    jobs.run_pending(conn, settings)
    assert fake_tts.calls == [("Coo.", "bm_lewis", 1.1)]

    fake_tts.calls.clear()
    line = kid.get(f"/api/scripts/{scene['id']}").json()["lines"][0]["line_id"]
    kid.patch(f"/api/lines/{line}", json={"text": "Not THERE."})
    jobs.run_pending(conn, settings)
    assert [c[0] for c in fake_tts.calls] == ["Not THERE."]

    assert kid.patch(f"/api/characters/{scene['bird']}/voice", json={"voice": "nope"}).status_code == 400
    assert kid.patch(f"/api/characters/{scene['bird']}/voice", json={"voice": "bm_lewis", "speed": 3}).status_code == 422


def test_a_clip_that_is_not_ready_jumps_the_queue(kid, scene, conn):
    kid.post(f"/api/scripts/{scene['id']}/voices/auto")
    r = kid.get(f"/api/scripts/{scene['id']}/rehearsal").json()
    assert r["audio"]["ready"] == 0
    last = r["lines"][3]["clip"]
    assert kid.get(f"/api/clips/{last}").status_code == 202
    first_job = conn.execute(
        "SELECT payload FROM job WHERE status = 'queued' ORDER BY priority DESC, job_id LIMIT 1").fetchone()[0]
    assert last in first_job
    assert kid.get("/api/clips/unknown").status_code == 404


def test_tts_down_marks_clips_failed_and_a_later_sync_retries(kid, scene, conn, settings, fake_tts):
    fake_tts.broken = True
    kid.post(f"/api/scripts/{scene['id']}/voices/auto")
    jobs.run_pending(conn, settings)
    r = kid.get(f"/api/scripts/{scene['id']}/rehearsal").json()   # this call re-queues the failures
    assert r["audio"]["ready"] == 0
    fake_tts.broken = False
    jobs.run_pending(conn, settings)
    assert kid.get(f"/api/scripts/{scene['id']}/rehearsal").json()["audio"] == {"total": 4, "ready": 4, "failed": 0}


def test_preview(kid, fake_tts):
    r = kid.get("/api/voices/bm_george/preview")
    assert r.status_code == 200 and b"bm_george" in r.content
    kid.get("/api/voices/bm_george/preview")
    assert len(fake_tts.calls) == 1                 # made once, then kept
    assert kid.get("/api/voices/zz_nobody/preview").status_code == 404


def test_casting_is_per_child_and_marks_their_lines(app, kid, scene):
    sid = scene["id"]
    assert kid.put(f"/api/scripts/{sid}/casting", json={"character_ids": [scene["mum"]]}).json() == \
        {"character_ids": [scene["mum"]]}
    assert kid.put(f"/api/scripts/{sid}/casting", json={"character_ids": [9999]}).status_code == 400
    mine = [ln["mine"] for ln in kid.get(f"/api/scripts/{sid}/rehearsal").json()["lines"]]
    assert mine == [True, False, False, True, False]     # her own line, and the one she shares with DAD

    with TestClient(app, headers=HEADERS) as sibling:
        sibling.post("/api/login", json={"password": PASSWORD})
        pid = sibling.post("/api/profiles", json={"name": "Sam", "colour": "#e4572e"}).json()["profile_id"]
        sibling.post(f"/api/profiles/{pid}/select")
        assert sibling.get(f"/api/scripts/{sid}/casting").json() == {"character_ids": []}
        # doubling: one child, two parts
        sibling.put(f"/api/scripts/{sid}/casting", json={"character_ids": [scene["dad"], scene["bird"]]})
        mine = [ln["mine"] for ln in sibling.get(f"/api/scripts/{sid}/rehearsal").json()["lines"]]
        assert mine == [False, True, True, True, False]
    assert kid.get(f"/api/scripts/{sid}/casting").json() == {"character_ids": [scene["mum"]]}


def test_reading_pages_voices_new_characters_automatically(kid, conn, settings, fake_llm, fake_tts):
    sid = kid.post("/api/scripts", json={"title": "Auto"}).json()["script"]["script_id"]
    fake_llm.queue.append(json.dumps({"page_number": 1, "items": [
        {"kind": "dialogue", "speakers": ["DAD"], "text": "Where do you want these?"}]}))
    kid.post(f"/api/scripts/{sid}/pages", files=[("files", ("p.jpg", b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg"))])
    jobs.run_pending(conn, settings)
    assert voices_of(kid, sid)["DAD"].startswith("bm_")
    assert fake_tts.calls == [("Where do you want these?", voices_of(kid, sid)["DAD"], 1.0)]
