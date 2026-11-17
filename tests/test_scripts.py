import json

import pytest

from offbook import db, jobs, scripts
from tests.conftest import PASSWORD

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def speech(who, text, **extra):
    return {"kind": "dialogue", "speakers": [who] if isinstance(who, str) else who, "text": text, "spoken": text,
            "continues": False, "highlighted": False, "unsure": [], **extra}


def page_json(*items, number=None):
    return json.dumps({"page_number": number, "items": list(items)})


def upload(client, script_id, *blobs):
    files = [("files", (f"p{i}.jpg", b, "image/jpeg")) for i, b in enumerate(blobs)]
    return client.post(f"/api/scripts/{script_id}/pages", files=files)


def texts(detail):
    return [ln["text"] for ln in detail["lines"]]


@pytest.fixture
def script(kid):
    return kid.post("/api/scripts", json={"title": "The Pie Thief"}).json()["script"]["script_id"]


def read_pages(conn, settings):
    scripts  # the import registers the ocr handler
    return jobs.run_pending(conn, settings)


# --- reading pages

def test_two_pages_become_one_running_script(kid, script, conn, settings, fake_llm):
    fake_llm.queue += [
        page_json({"kind": "heading", "text": "SCENE 1"}, speech("JACK", "Who took the pie?"),
                  speech("JILL", "Not me. I was"), number=1),
        page_json(speech([], "upstairs all morning.", continues=True), speech("Jack", "A likely story."), number=2),
    ]
    assert upload(kid, script, JPEG, JPEG).status_code == 200
    assert read_pages(conn, settings) >= 1   # the reading job, plus the audio jobs it queues

    d = kid.get(f"/api/scripts/{script}").json()
    assert [p["status"] for p in d["pages"]] == ["done", "done"]
    assert [p["printed_number"] for p in d["pages"]] == [1, 2]
    assert texts(d) == ["SCENE 1", "Who took the pie?", "Not me. I was upstairs all morning.", "A likely story."]
    assert [ln["seq"] for ln in d["lines"]] == [1, 2, 3, 4]
    # "Jack" on page 2 is the JACK from page 1, not a new character
    assert [(c["name"], c["lines"]) for c in d["characters"]] == [("JACK", 2), ("JILL", 1)]
    # the second call was told who had been seen so far
    assert "JACK, JILL" in fake_llm.calls[1][1][1]["content"][0]["text"]


def test_a_failed_page_is_skipped_then_slots_in_on_retry(kid, script, conn, settings, fake_llm):
    fake_llm.queue += [page_json(speech("JACK", "One.")), "garbage", "more garbage", page_json(speech("JILL", "Three."))]
    upload(kid, script, JPEG, JPEG, JPEG)
    read_pages(conn, settings)
    d = kid.get(f"/api/scripts/{script}").json()
    assert [p["status"] for p in d["pages"]] == ["done", "failed", "done"]
    assert d["pages"][1]["error"].startswith("Couldn't read this page")
    assert texts(d) == ["One.", "Three."]

    middle = d["pages"][1]["page_id"]
    assert kid.post(f"/api/pages/{d['pages'][0]['page_id']}/retry").status_code == 400
    fake_llm.queue.append(page_json(speech("JACK", "Two.")))
    assert kid.post(f"/api/pages/{middle}/retry").status_code == 200
    read_pages(conn, settings)
    d = kid.get(f"/api/scripts/{script}").json()
    assert texts(d) == ["One.", "Two.", "Three."]
    assert [ln["seq"] for ln in d["lines"]] == [1, 2, 3]


def test_a_continuation_is_not_joined_across_a_failed_page(kid, script, conn, settings, fake_llm):
    fake_llm.queue += [page_json(speech("JACK", "One.")), "garbage", "more garbage",
                       page_json(speech(["JILL"], "the end of a speech from page two.", continues=True))]
    upload(kid, script, JPEG, JPEG, JPEG)
    read_pages(conn, settings)
    d = kid.get(f"/api/scripts/{script}").json()
    assert texts(d) == ["One.", "the end of a speech from page two."]


def test_daily_cap_fails_the_rest_without_calling(kid, script, conn, settings, fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_CAP", "1")
    fake_llm.queue.append(page_json(speech("JACK", "One.")))
    upload(kid, script, JPEG, JPEG, JPEG)
    read_pages(conn, settings)
    d = kid.get(f"/api/scripts/{script}").json()
    assert [p["status"] for p in d["pages"]] == ["done", "failed", "failed"]
    assert "limit" in d["pages"][2]["error"]
    assert len(fake_llm.calls) == 1


def test_a_continuation_with_nothing_to_join_stays_a_speech_needing_a_speaker(kid, script, conn, settings, fake_llm):
    fake_llm.queue.append(page_json(speech([], "...and so it ended.", continues=True)))
    upload(kid, script, JPEG)
    read_pages(conn, settings)
    d = kid.get(f"/api/scripts/{script}").json()
    assert d["lines"][0]["kind"] == "dialogue" and d["lines"][0]["speakers"] == []


# --- uploads

def test_upload_checks_the_whole_batch_first(kid, script, settings):
    assert upload(kid, script, JPEG, b"GIF89a not allowed").status_code == 415
    assert upload(kid, script, JPEG, b"\xff\xd8\xff" + b"0" * 3_600_000).status_code == 413
    assert kid.get(f"/api/scripts/{script}").json()["pages"] == []
    assert not (settings.data_dir / "pages" / str(script)).exists()


def test_page_image_is_served_and_needs_login(kid, script, client):
    d = upload(kid, script, PNG).json()
    page_id = d["pages"][0]["page_id"]
    r = kid.get(f"/api/pages/{page_id}/image")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    kid.post("/api/logout")
    assert client.get(f"/api/pages/{page_id}/image").status_code == 401


def test_delete_page_removes_its_lines_and_file(kid, script, conn, settings, fake_llm):
    fake_llm.queue += [page_json(speech("JACK", "One.")), page_json(speech("JILL", "Two."))]
    d = upload(kid, script, JPEG, JPEG).json()
    read_pages(conn, settings)
    first = d["pages"][0]["page_id"]
    path = settings.data_dir / conn.execute("SELECT image_path FROM page WHERE page_id = ?", (first,)).fetchone()[0]
    assert path.is_file()
    d = kid.post(f"/api/pages/{first}/delete").json()
    assert texts(d) == ["Two."] and d["lines"][0]["seq"] == 1 and len(d["pages"]) == 1
    assert not path.exists()


# --- scripts

def test_scripts_need_a_profile(family):
    r = family.get("/api/scripts")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_profile"


def test_list_rename_delete(kid, script, settings):
    upload(kid, script, JPEG)
    listing = kid.get("/api/scripts").json()["scripts"]
    assert listing[0]["title"] == "The Pie Thief" and listing[0]["pages"] == 1 and listing[0]["reading"] == 1
    assert kid.patch(f"/api/scripts/{script}", json={"title": "Pie!"}).json()["title"] == "Pie!"
    assert kid.post(f"/api/scripts/{script}/delete", json={"password": "wrong"}).status_code == 400
    assert kid.post(f"/api/scripts/{script}/delete", json={"password": PASSWORD}).status_code == 200
    assert kid.get(f"/api/scripts/{script}").status_code == 404
    assert not (settings.data_dir / "pages" / str(script)).exists()


# --- review edits

@pytest.fixture
def scene(kid, script, conn):
    """SCENE 1 / JACK: Who took the pie? / JILL: (crossly) Not me. / (direction) She exits."""
    with db.tx(conn):
        jack, jill = scripts.character_for(conn, script, "JACK"), scripts.character_for(conn, script, "JILL")
        scripts.insert_line(conn, script, 0, "heading", "SCENE 1")
        scripts.insert_line(conn, script, 1, "dialogue", "Who took the pie?", speakers=[jack])
        scripts.insert_line(conn, script, 2, "dialogue", "(crossly) Not me.", speakers=[jill], unsure=["crossly"])
        scripts.insert_line(conn, script, 3, "direction", "She exits.")
    d = kid.get(f"/api/scripts/{script}").json()
    return {"id": script, "jack": jack, "jill": jill, "lines": [ln["line_id"] for ln in d["lines"]]}


def test_spoken_is_derived_when_not_given(kid, scene):
    d = kid.get(f"/api/scripts/{scene['id']}").json()
    assert d["lines"][2]["spoken"] == "Not me." and d["lines"][2]["unsure"] == ["crossly"]
    assert d["lines"][0]["spoken"] is None


def test_edit_text_recomputes_spoken_and_clears_doubt(kid, scene):
    r = kid.patch(f"/api/lines/{scene['lines'][2]}", json={"text": " (sweetly) Not I. "}).json()
    assert r["text"] == "(sweetly) Not I." and r["spoken"] == "Not I." and r["unsure"] == []
    assert kid.patch(f"/api/lines/{scene['lines'][2]}", json={"text": "   "}).status_code == 400


def test_change_speaker_and_kind(kid, scene):
    line = scene["lines"][1]
    assert kid.patch(f"/api/lines/{line}", json={"speakers": [scene["jill"], scene["jack"]]}).json()["speakers"] == \
        sorted([scene["jack"], scene["jill"]])
    assert kid.patch(f"/api/lines/{line}", json={"speakers": [9999]}).status_code == 400
    as_direction = kid.patch(f"/api/lines/{line}", json={"kind": "direction"}).json()
    assert as_direction["speakers"] == [] and as_direction["spoken"] is None
    back = kid.patch(f"/api/lines/{line}", json={"kind": "dialogue", "speakers": [scene["jack"]]}).json()
    assert back["spoken"] == "Who took the pie?" and back["speakers"] == [scene["jack"]]


def test_insert_delete_keep_order(kid, scene):
    new = kid.post(f"/api/scripts/{scene['id']}/lines",
                   json={"after_line_id": scene["lines"][1], "kind": "dialogue", "text": "Well?",
                         "speakers": [scene["jack"]]}).json()
    assert new["seq"] == 3
    top = kid.post(f"/api/scripts/{scene['id']}/lines", json={"kind": "heading", "text": "ACT ONE"}).json()
    assert top["seq"] == 1
    d = kid.get(f"/api/scripts/{scene['id']}").json()
    assert texts(d) == ["ACT ONE", "SCENE 1", "Who took the pie?", "Well?", "(crossly) Not me.", "She exits."]
    assert kid.post(f"/api/lines/{new['line_id']}/delete").status_code == 200
    d = kid.get(f"/api/scripts/{scene['id']}").json()
    assert [ln["seq"] for ln in d["lines"]] == [1, 2, 3, 4, 5]


def test_split_then_merge_round_trip(kid, scene):
    line = scene["lines"][1]
    r = kid.post(f"/api/lines/{line}/split", json={"at": 9}).json()
    assert r["first"]["text"] == "Who took" and r["second"]["text"] == "the pie?"
    assert r["second"]["speakers"] == [scene["jack"]] and r["second"]["seq"] == r["first"]["seq"] + 1
    assert kid.post(f"/api/lines/{line}/split", json={"at": 999}).status_code == 400

    merged = kid.post(f"/api/lines/{r['second']['line_id']}/merge-up").json()
    assert merged["line_id"] == line and merged["text"] == "Who took the pie?"
    # a speech can't be joined onto a heading, and the first line has nothing above it
    assert kid.post(f"/api/lines/{line}/merge-up").status_code == 400
    assert kid.post(f"/api/lines/{scene['lines'][0]}/merge-up").status_code == 400
    assert len(kid.get(f"/api/scripts/{scene['id']}").json()["lines"]) == 4


def test_characters_rename_merge_delete(kid, scene):
    sid = scene["id"]
    assert kid.patch(f"/api/characters/{scene['jack']}", json={"name": "Jack Horner"}).json()["name"] == "Jack Horner"
    assert kid.patch(f"/api/characters/{scene['jack']}", json={"name": "jill"}).status_code == 409
    assert kid.post(f"/api/scripts/{sid}/characters", json={"name": "JILL."}).json()["character_id"] == scene["jill"]
    extra = kid.post(f"/api/scripts/{sid}/characters", json={"name": "Narrator"}).json()["character_id"]
    assert kid.post(f"/api/scripts/{sid}/characters", json={"name": "!!!"}).status_code == 400

    assert kid.post(f"/api/characters/{scene['jill']}/delete").status_code == 400
    assert kid.post(f"/api/characters/{extra}/delete").status_code == 200

    d = kid.post(f"/api/characters/{scene['jill']}/merge", json={"into": scene["jack"]}).json()
    assert [(c["name"], c["lines"]) for c in d["characters"]] == [("Jack Horner", 2)]
    assert kid.post(f"/api/characters/{scene['jack']}/merge", json={"into": scene["jack"]}).status_code == 400
