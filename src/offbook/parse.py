"""Turn one photographed script page into structured items, using a vision model.

The model both reads the text and works out who says what. Everything it returns is checked and normalised by
`validate_page` before anything else sees it.
"""

import re
import sqlite3

from offbook import llm
from offbook.config import Settings

KINDS = ("dialogue", "direction", "heading")
MAX_ITEMS = 400
# base64 inflates by a third, and vision APIs commonly reject images over ~5 MB
MAX_IMAGE_BYTES = 3_500_000

SYSTEM = """You are transcribing a photographed page of a play, musical or film script for an app that helps \
children learn their lines. A child will memorise exactly what you write, so copy the words on the page exactly: \
never tidy, modernise, correct or complete them.

Return one JSON object and nothing else:
{"page_number": <the printed page number, or null>, "items": [ ...in reading order... ]}

Each item is one of:
{"kind": "dialogue", "speakers": ["NAME"], "text": "...", "spoken": "...", "continues": false, "highlighted": false, "unsure": []}
{"kind": "direction", "text": "..."}
{"kind": "heading", "text": "..."}

Rules
- dialogue: one item per speech. "speakers" holds the character name(s) as printed, without "(CONT'D)", "(V.O.)" \
or similar. A line said by several characters together lists each name; for "ALL" or "BOTH" use that word as the \
single name.
- "text" is the speech as printed, including any stage direction inside it, e.g. "(angrily) Get out!".
- "spoken" is only the words said aloud: the same speech with bracketed or italic stage directions removed, \
e.g. "Get out!".
- "continues" is true only when the page opens mid-speech with no character name above it, because the speech \
began on the previous page. Then "speakers" is your best guess from the known characters, or [].
- "highlighted" is true when the speech or its character name has been marked with a highlighter pen or \
underlined by hand.
- "unsure" lists words in this speech you could not read with confidence (blur, glare, a fold, handwriting over \
the print). Still give your best reading in "text".
- direction: stage directions, scene descriptions and anything else not spoken that stands apart from a speech.
- heading: act and scene titles, song titles.
- Join lines that the page layout wrapped. Keep the author's punctuation and spelling.
- Leave out running headers, footers, the page number itself and anything handwritten in the margins.
- If the image is not a script page, return {"page_number": null, "items": []}."""


def clean_speaker(name: str) -> str:
    """'JACK (CONT'D):' -> 'JACK'. Spelling and case stay as printed; matching names up is the caller's job."""
    name = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", name)
    return re.sub(r"\s+", " ", name).strip(" :.-\t")


def strip_directions(text: str) -> str:
    """Fallback for a missing "spoken": drop anything in round or square brackets."""
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)).strip()


def validate_page(obj: dict) -> list[str]:
    """Check one page's JSON and normalise it in place. Returns human-readable errors (empty when valid)."""
    errors: list[str] = []
    pn = obj.get("page_number")
    obj["page_number"] = pn if isinstance(pn, int) and not isinstance(pn, bool) and 0 < pn < 10000 else None
    items = obj.get("items")
    if not isinstance(items, list):
        return ['"items" must be a list']
    if len(items) > MAX_ITEMS:
        return [f'"items" has {len(items)} entries; a single page cannot have more than {MAX_ITEMS}']
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f"items[{i}] must be an object")
            continue
        kind = it.get("kind")
        if kind not in KINDS:
            errors.append(f'items[{i}].kind must be one of {", ".join(KINDS)}')
            continue
        text = it.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"items[{i}].text must be a non-empty string")
            continue
        text = text.strip()
        if kind != "dialogue":
            out.append({"kind": kind, "text": text})
            continue
        speakers = it.get("speakers") or []
        if not isinstance(speakers, list) or not all(isinstance(s, str) for s in speakers):
            errors.append(f"items[{i}].speakers must be a list of names")
            continue
        speakers = [c for c in dict.fromkeys(clean_speaker(s) for s in speakers) if c]
        continues = it.get("continues") is True
        if not speakers and not continues:
            errors.append(f'items[{i}] is dialogue with no speaker; give "speakers", or set "continues" if the '
                          "speech began on the previous page")
            continue
        spoken = it.get("spoken")
        spoken = spoken.strip() if isinstance(spoken, str) and spoken.strip() else strip_directions(text)
        unsure = it.get("unsure") or []
        out.append({"kind": "dialogue", "speakers": speakers, "text": text, "spoken": spoken,
                    "continues": continues, "highlighted": it.get("highlighted") is True,
                    "unsure": [u.strip() for u in unsure if isinstance(u, str) and u.strip()]
                    if isinstance(unsure, list) else []})
    if not errors:
        obj["items"] = out
    return errors


def parse_page(conn: sqlite3.Connection, s: Settings, image: bytes, mime: str, known: list[str],
               profile_id: int | None = None, model: str | None = None) -> dict:
    """One AI call (two if the first reply fails validation). `known` is the cast seen on earlier pages, so that
    names come back spelt the same way throughout a script."""
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError(f"image is {len(image) / 1e6:.1f} MB; shrink it below {MAX_IMAGE_BYTES / 1e6:.1f} MB first")
    cast = ("Characters seen so far in this script: " + ", ".join(known)
            + ". Use these exact spellings wherever the page means the same character."
            if known else "This is the first page; no characters are known yet.")
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": [{"type": "text", "text": cast}, llm.image_part(image, mime)]}]
    return llm.call_json(conn, s, purpose="parse_page", model=model or s.parse_model, messages=messages,
                         validate=validate_page, profile_id=profile_id, max_tokens=6000, temperature=0.0)


def speakers_in(page: dict) -> list[str]:
    return list(dict.fromkeys(sp for it in page["items"] if it["kind"] == "dialogue" for sp in it["speakers"]))
