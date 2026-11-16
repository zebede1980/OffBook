import json

import pytest

from offbook import llm, parse

PAGE = {
    "page_number": 12,
    "items": [
        {"kind": "heading", "text": "ACT ONE, SCENE 2"},
        {"kind": "direction", "text": "A kitchen. Morning."},
        {"kind": "dialogue", "speakers": ["JACK (CONT'D):"], "text": "(angrily) Get out!", "spoken": "Get out!",
         "continues": False, "highlighted": True, "unsure": []},
        {"kind": "dialogue", "speakers": ["JILL", "JACK"], "text": "We never said that.", "spoken": "We never said that."},
    ],
}


def test_validate_normalises_a_good_page():
    page = json.loads(json.dumps(PAGE))
    assert parse.validate_page(page) == []
    assert page["page_number"] == 12
    assert page["items"][0] == {"kind": "heading", "text": "ACT ONE, SCENE 2"}
    jack = page["items"][2]
    assert jack["speakers"] == ["JACK"] and jack["highlighted"] is True and jack["spoken"] == "Get out!"
    both = page["items"][3]
    assert both == {"kind": "dialogue", "speakers": ["JILL", "JACK"], "text": "We never said that.",
                    "spoken": "We never said that.", "continues": False, "highlighted": False, "unsure": []}
    assert parse.speakers_in(page) == ["JACK", "JILL"]


def test_missing_spoken_falls_back_to_stripping_brackets():
    page = {"items": [{"kind": "dialogue", "speakers": ["MUM"], "text": "(sighs) Fine. [exits] Goodbye."}]}
    assert parse.validate_page(page) == []
    assert page["items"][0]["spoken"] == "Fine. Goodbye."
    assert page["page_number"] is None


def test_continued_speech_may_have_no_speaker():
    page = {"items": [{"kind": "dialogue", "speakers": [], "text": "and that was that.", "continues": True}]}
    assert parse.validate_page(page) == []
    assert page["items"][0]["continues"] is True and page["items"][0]["speakers"] == []


@pytest.mark.parametrize("bad, fragment", [
    ({"items": "nope"}, "must be a list"),
    ({"items": [{"kind": "song", "text": "la"}]}, "kind must be one of"),
    ({"items": [{"kind": "direction", "text": "  "}]}, "non-empty string"),
    ({"items": [{"kind": "dialogue", "speakers": [], "text": "Hello"}]}, "no speaker"),
    ({"items": [{"kind": "dialogue", "speakers": "JACK", "text": "Hello"}]}, "list of names"),
    ({"items": ["JACK: hello"]}, "must be an object"),
])
def test_validate_rejects(bad, fragment):
    errors = parse.validate_page(bad)
    assert errors and fragment in errors[0]


def test_odd_page_numbers_become_null():
    for pn in ("12", True, -3, 1.5):
        page = {"page_number": pn, "items": []}
        assert parse.validate_page(page) == [] and page["page_number"] is None


def test_clean_speaker():
    assert parse.clean_speaker("JACK (CONT'D):") == "JACK"
    assert parse.clean_speaker("  Mrs.  Potts [V.O.] ") == "Mrs. Potts"
    assert parse.clean_speaker("(offstage)") == ""


def test_parse_page_sends_the_image_and_the_known_cast(conn, settings, fake_llm):
    fake_llm.queue.append("```json\n" + json.dumps(PAGE) + "\n```")
    page = parse.parse_page(conn, settings, b"\xff\xd8fakejpeg", "image/jpeg", ["JACK", "JILL"])
    assert len(page["items"]) == 4

    model, messages = fake_llm.calls[0]
    assert model == settings.parse_model
    text_part, image = messages[1]["content"]
    assert "JACK, JILL" in text_part["text"]
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert conn.execute("SELECT purpose, ok FROM llm_call").fetchall()[0][:] == ("parse_page", 1)


def test_parse_page_retries_once_with_the_errors(conn, settings, fake_llm):
    fake_llm.queue += [json.dumps({"items": [{"kind": "dialogue", "speakers": [], "text": "Hello"}]}),
                       json.dumps(PAGE)]
    page = parse.parse_page(conn, settings, b"x", "image/png", [])
    assert len(page["items"]) == 4
    retry_messages = fake_llm.calls[1][1]
    assert "no speaker" in retry_messages[-1]["content"]
    assert "first page" in retry_messages[1]["content"][0]["text"]


def test_parse_page_gives_up_after_two_bad_replies(conn, settings, fake_llm):
    fake_llm.queue += ["not json at all", "still not json"]
    with pytest.raises(llm.LLMError):
        parse.parse_page(conn, settings, b"x", "image/png", [])


def test_oversized_image_is_refused_before_any_call(conn, settings, fake_llm):
    with pytest.raises(ValueError):
        parse.parse_page(conn, settings, b"x" * (parse.MAX_IMAGE_BYTES + 1), "image/jpeg", [])
    assert fake_llm.calls == []


def test_daily_cap_stops_calls(conn, settings, fake_llm, monkeypatch):
    monkeypatch.setenv("LLM_DAILY_CAP", "1")
    fake_llm.queue += [json.dumps(PAGE), json.dumps(PAGE)]
    parse.parse_page(conn, settings, b"x", "image/png", [])
    with pytest.raises(llm.LLMError, match="cap"):
        parse.parse_page(conn, settings, b"x", "image/png", [])
