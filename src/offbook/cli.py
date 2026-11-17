import argparse
import getpass
import json
import logging
import mimetypes
import sys
from pathlib import Path

from offbook import config, db


def _open():
    s = config.load()
    conn = db.connect(s.db_path)
    db.migrate(conn)
    return s, conn


def _ask_password(prompt: str, from_stdin: bool = False) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    pw = getpass.getpass(prompt)
    if pw != getpass.getpass("again: "):
        raise SystemExit("passwords did not match")
    return pw


def cmd_set_password(args):
    from offbook import auth

    _, conn = _open()
    try:
        auth.set_family_password(conn, _ask_password("family password: ", args.password_stdin))
    except ValueError as e:
        raise SystemExit(f"error: {e}")
    print("family password set (every device is now signed out)")


def cmd_check(args):
    """What a deploy needs to know: is the database there, is there a password, can we reach Kokoro."""
    import httpx

    from offbook import auth

    s, conn = _open()
    print(f"data dir     {s.data_dir}")
    print(f"schema       v{db.schema_version(conn)}")
    print(f"password     {'set' if auth.family_hash(conn) else 'NOT SET - run: offbook set-password'}")
    print(f"profiles     {conn.execute('SELECT COUNT(*) FROM profile').fetchone()[0]}")
    print(f"nanogpt key  {'set' if s.nanogpt_api_key else 'NOT SET'} (model {s.parse_model})")
    try:
        r = httpx.get(f"{s.kokoro_url.rstrip('/')}/v1/audio/voices", timeout=10)
        r.raise_for_status()
        print(f"kokoro       ok, {len(r.json().get('voices', []))} voices at {s.kokoro_url}")
    except Exception as e:
        print(f"kokoro       UNREACHABLE at {s.kokoro_url}: {type(e).__name__}: {e}")
        raise SystemExit(1)


def cmd_parse_spike(args):
    """Stage 0 gate: read real page photos and show what the model makes of them. Each page is a paid AI call."""
    from offbook import parse

    s, conn = _open()
    model = args.model or s.parse_model
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    known: list[str] = []
    for path in map(Path, args.images):
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        before = conn.execute("SELECT COALESCE(MAX(call_id), 0) FROM llm_call").fetchone()[0]
        try:
            page = parse.parse_page(conn, s, path.read_bytes(), mime, known, model=model)
        except Exception as e:
            print(f"{path.name}: FAILED {type(e).__name__}: {e}")
            continue
        known = list(dict.fromkeys(known + parse.speakers_in(page)))
        (out_dir / f"{path.stem}.json").write_text(json.dumps(page, indent=1, ensure_ascii=False), encoding="utf-8")
        calls = conn.execute("SELECT prompt_tokens, completion_tokens, duration_ms FROM llm_call WHERE call_id > ?",
                             (before,)).fetchall()
        tok_in, tok_out = (sum(c[i] or 0 for c in calls) for i in (0, 1))
        print(f"\n=== {path.name}: page {page['page_number']}, {len(page['items'])} items, {len(calls)} call(s), "
              f"{tok_in} in / {tok_out} out tokens, {sum(c[2] or 0 for c in calls) / 1000:.1f}s")
        for it in page["items"]:
            if it["kind"] == "dialogue":
                flags = "".join(f" [{f}]" for f in ("continues", "highlighted") if it[f])
                flags += f" [unsure: {', '.join(it['unsure'])}]" if it["unsure"] else ""
                print(f"  {' & '.join(it['speakers']) or '?'}:{flags} {it['text']}")
            else:
                print(f"  ({it['kind']}) {it['text']}")
    print(f"\ncast: {', '.join(known) or '(none)'}\nJSON written to {out_dir.resolve()}")


DEMO = [  # the opening of Wilde's "The Importance of Being Earnest" (1895, public domain)
    ("heading", None, "FIRST ACT"),
    ("direction", None, "Morning-room in Algernon's flat in Half-Moon Street. The sound of a piano is heard in the "
                        "adjoining room. Lane is arranging afternoon tea on the table."),
    ("dialogue", "ALGERNON", "Did you hear what I was playing, Lane?"),
    ("dialogue", "LANE", "I didn't think it polite to listen, sir."),
    ("dialogue", "ALGERNON", "I'm sorry for that, for your sake. I don't play accurately - any one can play "
                             "accurately - but I play with wonderful expression. As far as the piano is concerned, "
                             "sentiment is my forte. I keep science for Life."),
    ("dialogue", "LANE", "Yes, sir."),
    ("dialogue", "ALGERNON", "And, speaking of the science of Life, have you got the cucumber sandwiches cut for "
                             "Lady Bracknell?"),
    ("dialogue", "LANE", "Yes, sir. [Hands them on a salver.]"),
    ("dialogue", "ALGERNON", "[Inspects them, takes two, and goes over to the sofa.] Oh! ... by the way, Lane, I see "
                             "from your book that on Thursday night, when Lord Shoreman and Mr. Worthing were dining "
                             "with me, eight bottles of champagne are entered as having been consumed."),
    ("dialogue", "LANE", "Yes, sir; eight bottles and a pint."),
    ("dialogue", None, "Why is it that at a bachelor's establishment the servants invariably drink the champagne?"),
]


def cmd_dev_seed(args):
    """A small public-domain scene for trying the screens without paying to read photos. It includes the things
    the review screen has to flag: a word the reader was unsure of, and a speech with no speaker."""
    from offbook import scripts

    _, conn = _open()
    with db.tx(conn):
        script_id = scripts.create_script(conn, "The Importance of Being Earnest (demo)", None)
        for n, (kind, who, text) in enumerate(DEMO):
            scripts.insert_line(conn, script_id, n, kind, text,
                                speakers=[scripts.character_for(conn, script_id, who)] if who else None,
                                highlighted=who == "LANE", unsure=["Shoreman"] if "Shoreman" in text else None)
    print(f"demo script created (id {script_id})")


def cmd_import_pages(args):
    """Create a script from page photos already on this machine and queue them to be read, as the upload
    screen would. The running server reads them (one PAID AI call per page)."""
    import uuid

    from offbook import jobs, scripts
    from offbook.api.scripts import sniff
    from offbook.db import now_utc
    from offbook.parse import MAX_IMAGE_BYTES

    s, conn = _open()
    batch = []
    for path in map(Path, args.images):
        data = path.read_bytes()
        kind = sniff(data)
        if kind is None or len(data) > MAX_IMAGE_BYTES:
            raise SystemExit(f"error: {path.name} must be a JPEG, PNG or WebP under {MAX_IMAGE_BYTES / 1e6:.1f} MB")
        batch.append((data, *kind))
    script_id = scripts.create_script(conn, args.title, None)
    folder = s.data_dir / "pages" / str(script_id)
    folder.mkdir(parents=True, exist_ok=True)
    for seq, (data, mime, ext) in enumerate(batch, 1):
        name = f"{uuid.uuid4().hex}{ext}"
        (folder / name).write_bytes(data)
        conn.execute("INSERT INTO page(script_id, seq, image_path, mime, created_at) VALUES (?, ?, ?, ?, ?)",
                     (script_id, seq, f"pages/{script_id}/{name}", mime, now_utc()))
    jobs.enqueue(conn, "ocr", {"script_id": script_id})
    print(f"script {script_id} created with {len(batch)} page(s) queued; the server will read them shortly")


def cmd_serve(args):
    import uvicorn

    # One worker only: the job thread lives in this process and must not be duplicated.
    uvicorn.run("offbook.api.app:create_app", factory=True, host=args.host, port=args.port, workers=1,
                proxy_headers=True, forwarded_allow_ips="*")


def main(argv=None):
    config.load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="offbook")
    sub = p.add_subparsers(required=True)

    sp = sub.add_parser("serve")
    sp.add_argument("--host", default="0.0.0.0")
    sp.add_argument("--port", type=int, default=8000)
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("set-password", help="set the shared family password (prompts)")
    sp.add_argument("--password-stdin", action="store_true", help="read the password from stdin (for scripts)")
    sp.set_defaults(fn=cmd_set_password)

    sub.add_parser("check", help="report database, password, AI key and Kokoro status").set_defaults(fn=cmd_check)

    sp = sub.add_parser("import-pages", help="create a script from page photos on disk (PAID: one AI call a page)")
    sp.add_argument("--title", required=True)
    sp.add_argument("images", nargs="+", help="page photos, in script order")
    sp.set_defaults(fn=cmd_import_pages)

    sub.add_parser("dev-seed", help="add a small public-domain demo script").set_defaults(fn=cmd_dev_seed)

    sp = sub.add_parser("parse-spike", help="read page photos with the vision model and print the result (PAID)")
    sp.add_argument("images", nargs="+", help="page photos, in script order")
    sp.add_argument("--model", help="model id; default PARSE_MODEL")
    sp.add_argument("--out", default="spike", help="folder for the JSON results")
    sp.set_defaults(fn=cmd_parse_spike)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
