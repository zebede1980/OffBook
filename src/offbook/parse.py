"""Turn one photographed script page into structured items, using a vision model.

The model both reads the text and works out who says what. Everything it returns is checked and normalised by
`validate_page` before anything else sees it.
"""

import io
import logging
import re
import sqlite3

from offbook import llm
from offbook.config import Settings

log = logging.getLogger(__name__)

KINDS = ("dialogue", "direction", "heading")
MAX_ITEMS = 400
# base64 inflates by a third, and vision APIs commonly reject images over ~5 MB
MAX_IMAGE_BYTES = 3_500_000
# a dense two-column page holds ~100 speeches, which is ~5,000 tokens of JSON; leave headroom
MAX_TOKENS = 14000

SYSTEM = """You are transcribing a photographed page of a play, musical or film script for an app that helps children learn their lines. A child will memorise exactly what you write, so copy the words on the page exactly: never tidy, modernise, correct or complete them.

Return one JSON object and nothing else:
{"page_number": <the printed page number, or null>, "items": [ ...in reading order... ]}

Each item is one of:
{"kind": "dialogue", "speakers": ["NAME"], "text": "..."}
{"kind": "direction", "text": "..."}
{"kind": "heading", "text": "..."}

A dialogue item may also carry these, but ONLY when they apply. Leave them out otherwise, to keep the reply short:
"spoken": "..."      the words said aloud, when "text" contains a stage direction that is not spoken
"continues": true    the page opens mid-speech with no character name above it (the speech began on the last page)
"highlighted": true  the speech or its character name is marked with a highlighter pen or underlined by hand
"unsure": ["word"]   words you could not read with confidence (blur, glare, a fold, show-through)

Rules
- Reading order: if the page has two columns, read the whole left column top to bottom, then the right column.
- dialogue: one item per speech. "speakers" holds the character name(s) as printed, without the colon and without "(CONT'D)", "(V.O.)" or similar. A line said by several characters together lists each name; for "ALL" or "BOTH" use that word as the single name.
- "text" is the speech as printed, including a stage direction that sits inside it, e.g. "(angrily) Get out!". Then "spoken" is the same speech without it, e.g. "Get out!".
- A stage direction that follows a speech on the same line (often in italics, e.g. "Silence.") is not part of the speech: make it its own direction item straight after.
- For a "continues" speech, "speakers" is your best guess from the known characters, or [].
- direction: stage directions, scene descriptions and anything else not spoken. These are often in italics and may have no brackets.
- heading: the play's title, act and scene titles, song titles.
- Join lines that the page layout wrapped. Keep the author's punctuation and spelling.
- Leave out running headers, footers, the page number itself, anything handwritten on the page (names, notes), and faint or mirrored text showing through from the other side of the paper.
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


_PART_NOTE = {
    ("first", "columns"): "This image is only the LEFT half of the page; the right half is read separately.",
    ("second", "columns"): "This image is only the RIGHT half of the page; the left half was read separately. If it "
                           "opens mid-speech with no name above, that speech began in the left half: set "
                           '"continues".',
    ("first", "rows"): "This image is only the TOP half of the page; the bottom half is read separately.",
    ("second", "rows"): "This image is only the BOTTOM half of the page; the top half was read separately. If it "
                        'opens mid-speech with no name above, that speech began in the top half: set "continues".',
}


def parse_page(conn: sqlite3.Connection, s: Settings, image: bytes, mime: str, known: list[str],
               profile_id: int | None = None, model: str | None = None,
               part: tuple[str, str] | None = None) -> dict:
    """One AI call (two if the first reply fails validation). `known` is the cast seen on earlier pages, so that
    names come back spelt the same way throughout a script. `part` says which half of a split page this is."""
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError(f"image is {len(image) / 1e6:.1f} MB; shrink it below {MAX_IMAGE_BYTES / 1e6:.1f} MB first")
    cast = ("Characters seen so far in this script: " + ", ".join(known)
            + ". Use these exact spellings wherever the page means the same character."
            if known else "This is the first page; no characters are known yet.")
    if part is not None:
        cast += " " + _PART_NOTE[part]
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": [{"type": "text", "text": cast}, llm.image_part(image, mime)]}]
    return llm.call_json(conn, s, purpose="parse_page", model=model or s.parse_model, messages=messages,
                         validate=validate_page, profile_id=profile_id, max_tokens=MAX_TOKENS, temperature=0.0)


def speakers_in(page: dict) -> list[str]:
    return list(dict.fromkeys(sp for it in page["items"] if it["kind"] == "dialogue" for sp in it["speakers"]))


def split_image(data: bytes) -> tuple[bytes, bytes, str]:
    """Cut a page photo in two along its emptiest line near the middle: between the columns of a landscape
    two-column page, otherwise between two rows of text. Returns (first, second, "columns" | "rows")."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    w, h = im.size
    grey = im.convert("L")
    columns = w > h * 1.15
    size = w if columns else h
    # a box resize to one pixel high (or wide) averages each column (or row): brighter = emptier
    strip = grey.resize((size, 1) if columns else (1, size), Image.Resampling.BOX)
    flat = getattr(strip, "get_flattened_data", None) or strip.getdata   # renamed in Pillow 13
    profile = list(flat())
    lo, hi = int(size * 0.35), int(size * 0.65)
    win = max(1, size // 100)
    smoothed = [sum(profile[max(0, i - win):i + win + 1]) / len(profile[max(0, i - win):i + win + 1])
                for i in range(lo, hi)]
    cut = lo + max(range(len(smoothed)), key=lambda i: (smoothed[i], -abs(lo + i - size // 2)))
    boxes = ((0, 0, cut, h), (cut, 0, w, h)) if columns else ((0, 0, w, cut), (0, cut, w, h))
    out = []
    for box in boxes:
        buf = io.BytesIO()
        im.crop(box).save(buf, "JPEG", quality=90)
        out.append(buf.getvalue())
    return out[0], out[1], "columns" if columns else "rows"


def merge_parts(first: dict, second: dict) -> dict:
    """Put a split page back together. A speech that runs across the cut is joined."""
    a, b = list(first["items"]), list(second["items"])
    if a and b and a[-1]["kind"] == "dialogue" and b[0]["kind"] == "dialogue" and b[0]["continues"]:
        head, tail = a[-1], b.pop(0)
        a[-1] = {**head, "text": f"{head['text']} {tail['text']}", "spoken": f"{head['spoken']} {tail['spoken']}",
                 "highlighted": head["highlighted"] or tail["highlighted"], "unsure": head["unsure"] + tail["unsure"]}
    return {"page_number": first["page_number"] or second["page_number"], "items": a + b, "split": True}


def read_page(conn: sqlite3.Connection, s: Settings, image: bytes, mime: str, known: list[str],
              profile_id: int | None = None) -> dict:
    """parse_page, made robust against a reply that runs out of room. That happens two ways: now and then the
    model gets stuck and fills its whole allowance (one more try usually works), and a very dense page can
    genuinely need more than one reply holds. So: try, try again, then read the page in two halves."""
    for attempt in (1, 2):
        try:
            return parse_page(conn, s, image, mime, known, profile_id)
        except llm.Truncated as e:
            log.warning("page reply ran out of room (try %d): %s", attempt, e)
    first, second, how = split_image(image)
    a = parse_page(conn, s, first, "image/jpeg", known, profile_id, part=("first", how))
    b = parse_page(conn, s, second, "image/jpeg", list(dict.fromkeys(known + speakers_in(a))), profile_id,
                   part=("second", how))
    return merge_parts(a, b)
