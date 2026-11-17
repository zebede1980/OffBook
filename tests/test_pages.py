import io
import json

import pytest

from offbook import jobs, llm, parse
from tests.conftest import PASSWORD

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def speech(who, text, **extra):
    return {"kind": "dialogue", "speakers": [who] if isinstance(who, str) else who, "text": text, **extra}


def page_json(*items, number=None):
    return json.dumps({"page_number": number, "items": list(items)})


@pytest.fixture
def three_pages(kid, conn, settings, fake_llm):
    sid = kid.post("/api/scripts", json={"title": "Alice"}).json()["script"]["script_id"]
    fake_llm.queue += [page_json(speech("ALICE", "One."), number=1), page_json(speech("RABBIT", "Two."), number=2),
                       page_json(speech("DODO", "Three."), number=3)]
    kid.post(f"/api/scripts/{sid}/pages", files=[("files", (f"p{i}.jpg", JPEG, "image/jpeg")) for i in range(3)])
    jobs.run_pending(conn, settings)
    return sid


def texts(kid, sid):
    return [ln["text"] for ln in kid.get(f"/api/scripts/{sid}").json()["lines"]]


def pages(kid, sid):
    return kid.get(f"/api/scripts/{sid}").json()["pages"]


def test_moving_a_page_moves_its_lines(kid, three_pages):
    sid = three_pages
    p = pages(kid, sid)
    assert [x["lines"] for x in p] == [1, 1, 1]
    d = kid.post(f"/api/pages/{p[2]['page_id']}/move", json={"to": 1}).json()
    assert [x["page_id"] for x in d["pages"]] == [p[2]["page_id"], p[0]["page_id"], p[1]["page_id"]]
    assert [x["seq"] for x in d["pages"]] == [1, 2, 3]
    assert texts(kid, sid) == ["Three.", "One.", "Two."]
    kid.post(f"/api/pages/{p[2]['page_id']}/move", json={"to": 99})
    assert texts(kid, sid) == ["One.", "Two.", "Three."]


def test_a_line_typed_in_by_hand_moves_with_its_page(kid, three_pages):
    sid = three_pages
    first_line = kid.get(f"/api/scripts/{sid}").json()["lines"][0]["line_id"]
    kid.post(f"/api/scripts/{sid}/lines", json={"after_line_id": first_line, "kind": "direction", "text": "Exit."})
    p = pages(kid, sid)
    kid.post(f"/api/pages/{p[0]['page_id']}/move", json={"to": 3})
    assert texts(kid, sid) == ["Two.", "Three.", "One.", "Exit."]


def test_deleting_a_page_closes_the_gap_and_drops_characters_left_with_nothing(kid, three_pages):
    sid = three_pages
    p = pages(kid, sid)
    d = kid.post(f"/api/pages/{p[1]['page_id']}/delete").json()
    assert [x["seq"] for x in d["pages"]] == [1, 2]
    assert [c["name"] for c in d["characters"]] == ["ALICE", "DODO"]
    assert texts(kid, sid) == ["One.", "Three."]


def test_replacing_a_photo_rereads_it_in_place(kid, three_pages, conn, settings, fake_llm):
    sid = three_pages
    p = pages(kid, sid)
    old = conn.execute("SELECT image_path FROM page WHERE page_id = ?", (p[1]["page_id"],)).fetchone()[0]
    fake_llm.queue.append(page_json(speech("RABBIT", "Two, read properly.")))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    d = kid.post(f"/api/pages/{p[1]['page_id']}/replace", files={"file": ("new.png", png, "image/png")}).json()
    assert d["pages"][1]["status"] == "queued" and texts(kid, sid) == ["One.", "Three."]
    assert not (settings.data_dir / old).exists()
    jobs.run_pending(conn, settings)
    assert texts(kid, sid) == ["One.", "Two, read properly.", "Three."]
    assert kid.get(f"/api/pages/{p[1]['page_id']}/image").content == png
    assert kid.post(f"/api/pages/{p[1]['page_id']}/replace",
                    files={"file": ("x.gif", b"GIF89a", "image/gif")}).status_code == 415


def test_tidy_removes_characters_without_lines(kid, three_pages):
    sid = three_pages
    kid.post(f"/api/scripts/{sid}/characters", json={"name": "Nobody"})
    assert len(kid.get(f"/api/scripts/{sid}").json()["characters"]) == 4
    d = kid.post(f"/api/scripts/{sid}/characters/tidy").json()
    assert [c["name"] for c in d["characters"]] == ["ALICE", "RABBIT", "DODO"]


# --- reading a page that runs out of room

def _truncated(model, messages):
    raise llm.Truncated("reply cut off at max_tokens=14000 (0 chars)")


def _two_column_page() -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1200, 800), "white")
    d = ImageDraw.Draw(im)
    for y in range(40, 760, 30):
        d.rectangle((40, y, 560, y + 12), fill="black")      # left column of "text"
        d.rectangle((660, y, 1160, y + 12), fill="black")    # right column
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def test_split_cuts_between_the_columns():
    from PIL import Image
    a, b, how = parse.split_image(_two_column_page())
    wa, wb = Image.open(io.BytesIO(a)).size[0], Image.open(io.BytesIO(b)).size[0]
    assert how == "columns" and 560 < wa < 660 and wa + wb == 1200


def test_split_cuts_between_rows_on_a_portrait_page():
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (800, 1200), "white")
    d = ImageDraw.Draw(im)
    for y in list(range(40, 560, 30)) + list(range(640, 1160, 30)):
        d.rectangle((40, y, 760, y + 14), fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    a, b, how = parse.split_image(buf.getvalue())
    h = Image.open(io.BytesIO(a)).size[1]
    assert how == "rows" and 560 < h < 640


def test_a_stuck_reply_is_tried_again(kid, conn, settings, fake_llm):
    sid = kid.post("/api/scripts", json={"title": "Alice"}).json()["script"]["script_id"]
    fake_llm.queue += [_truncated, page_json(speech("ALICE", "Curiouser."))]
    kid.post(f"/api/scripts/{sid}/pages", files=[("files", ("p.jpg", JPEG, "image/jpeg"))])
    jobs.run_pending(conn, settings)
    assert texts(kid, sid) == ["Curiouser."] and len(fake_llm.calls) == 2


def test_a_page_too_dense_for_one_reply_is_read_in_halves(kid, conn, settings, fake_llm):
    sid = kid.post("/api/scripts", json={"title": "Alice"}).json()["script"]["script_id"]
    fake_llm.queue += [_truncated, _truncated,
                       page_json(speech("ALICE", "I wonder if"), number=4),
                       page_json(speech([], "I've been changed in the night?", continues=True),
                                 speech("MOUSE", "Ahem!"))]
    kid.post(f"/api/scripts/{sid}/pages", files=[("files", ("p.jpg", _two_column_page(), "image/jpeg"))])
    jobs.run_pending(conn, settings)
    d = kid.get(f"/api/scripts/{sid}").json()
    assert d["pages"][0]["status"] == "done" and d["pages"][0]["printed_number"] == 4
    assert texts(kid, sid) == ["I wonder if I've been changed in the night?", "Ahem!"]
    halves = [m[1]["content"][0]["text"] for _, m in fake_llm.calls[2:]]
    assert "LEFT half" in halves[0] and "RIGHT half" in halves[1] and "ALICE" in halves[1]


def test_if_even_the_halves_are_too_much_the_page_fails_helpfully(kid, conn, settings, fake_llm):
    sid = kid.post("/api/scripts", json={"title": "Alice"}).json()["script"]["script_id"]
    fake_llm.queue += [_truncated, _truncated, _truncated]
    kid.post(f"/api/scripts/{sid}/pages", files=[("files", ("p.jpg", _two_column_page(), "image/jpeg"))])
    jobs.run_pending(conn, settings)
    p = pages(kid, sid)[0]
    assert p["status"] == "failed" and "Replace photo" in p["error"]
