"""Voices, who plays whom, and the audio a rehearsal plays."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from offbook import audio, tts
from offbook.api.deps import current_profile, get_conn

router = APIRouter(prefix="/api", dependencies=[Depends(current_profile)])


class VoiceIn(BaseModel):
    voice: str | None = None
    speed: float = Field(default=1.0, ge=0.7, le=1.3)


class CastingIn(BaseModel):
    character_ids: list[int]


def _script(conn: sqlite3.Connection, script_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM script WHERE script_id = ?", (script_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such script.")
    return row


def _catalogue(request: Request) -> list[tts.Voice]:
    try:
        return tts.voices(request.app.state.settings)
    except tts.TTSError:
        raise HTTPException(503, "The voices aren't available right now. Try again in a minute.")


def _mine(conn: sqlite3.Connection, script_id: int, profile_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        """SELECT ca.character_id FROM casting ca JOIN character c ON c.character_id = ca.character_id
           WHERE ca.profile_id = ? AND c.script_id = ? ORDER BY ca.character_id""", (profile_id, script_id))]


@router.get("/voices")
def list_voices(request: Request):
    return {"voices": [{"id": v.id, "label": v.label, "group": v.group} for v in _catalogue(request)]}


@router.get("/voices/{voice_id}/preview")
def preview_voice(voice_id: str, request: Request, conn=Depends(get_conn)):
    """A short sample, made on first request and kept. Slow the first time (a couple of seconds)."""
    s = request.app.state.settings
    if voice_id not in {v.id for v in _catalogue(request)}:
        raise HTTPException(404, "No such voice.")
    engine = tts.engine(s).name
    c = {"hash": audio.clip_hash(engine, voice_id, 1.0, audio.PREVIEW), "engine": engine, "voice": voice_id,
         "speed": 1.0, "text": audio.PREVIEW}
    audio.ensure_clip(conn, c, priority=audio.URGENT)
    clip = audio.render(conn, s, c["hash"])
    if clip is None or clip["status"] != "done":
        raise HTTPException(503, "That voice couldn't be played just now.")
    return FileResponse(s.data_dir / clip["path"], media_type="audio/mpeg",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.patch("/characters/{character_id}/voice")
def set_voice(character_id: int, body: VoiceIn, request: Request, conn=Depends(get_conn)):
    c = conn.execute("SELECT * FROM character WHERE character_id = ?", (character_id,)).fetchone()
    if c is None:
        raise HTTPException(404, "No such character.")
    if body.voice is not None and body.voice not in {v.id for v in _catalogue(request)}:
        raise HTTPException(400, "Pick one of the voices on offer.")
    conn.execute("UPDATE character SET voice = ?, speed = ? WHERE character_id = ?",
                 (body.voice, round(body.speed, 2), character_id))
    audio.sync_clips(conn, request.app.state.settings, c["script_id"])
    return {"character_id": character_id, "voice": body.voice, "speed": round(body.speed, 2)}


@router.post("/scripts/{script_id}/voices/auto")
def auto_voices(script_id: int, request: Request, conn=Depends(get_conn)):
    """Give every character without a voice one, and start generating the audio."""
    _script(conn, script_id)
    _catalogue(request)
    s = request.app.state.settings
    assigned = audio.auto_assign(conn, s, script_id)
    return {"assigned": assigned, "queued": audio.sync_clips(conn, s, script_id)}


@router.get("/scripts/{script_id}/casting")
def get_casting(script_id: int, profile=Depends(current_profile), conn=Depends(get_conn)):
    _script(conn, script_id)
    return {"character_ids": _mine(conn, script_id, profile["profile_id"])}


@router.put("/scripts/{script_id}/casting")
def set_casting(script_id: int, body: CastingIn, profile=Depends(current_profile), conn=Depends(get_conn)):
    """Which part(s) the current child plays in this script. Other children's choices are untouched."""
    _script(conn, script_id)
    ids = list(dict.fromkeys(body.character_ids))
    valid = {r[0] for r in conn.execute("SELECT character_id FROM character WHERE script_id = ?", (script_id,))}
    if not set(ids) <= valid:
        raise HTTPException(400, "One of those characters isn't in this script.")
    pid = profile["profile_id"]
    conn.execute("""DELETE FROM casting WHERE profile_id = ? AND character_id IN
                    (SELECT character_id FROM character WHERE script_id = ?)""", (pid, script_id))
    conn.executemany("INSERT INTO casting(profile_id, character_id) VALUES (?, ?)", [(pid, c) for c in ids])
    return {"character_ids": _mine(conn, script_id, pid)}


@router.get("/scripts/{script_id}/rehearsal")
def rehearsal(script_id: int, request: Request, profile=Depends(current_profile), conn=Depends(get_conn)):
    """Everything the rehearsal player needs: the lines in order, which are the child's own, and the clip for
    each speech. Also queues any audio that is missing."""
    script = _script(conn, script_id)
    s = request.app.state.settings
    audio.sync_clips(conn, s, script_id)
    clips = audio.line_clips(conn, s, script_id)
    status = {r["clip_hash"]: r["status"] for r in conn.execute(
        f"SELECT clip_hash, status FROM clip WHERE clip_hash IN ({','.join('?' * len(clips))})",
        [c["hash"] for c in clips.values()])} if clips else {}
    mine = set(_mine(conn, script_id, profile["profile_id"]))
    speakers: dict[int, list[int]] = {}
    for line_id, character_id in conn.execute(
            """SELECT ls.line_id, ls.character_id FROM line_speaker ls JOIN line l ON l.line_id = ls.line_id
               WHERE l.script_id = ? ORDER BY ls.character_id""", (script_id,)):
        speakers.setdefault(line_id, []).append(character_id)
    lines = []
    for r in conn.execute("SELECT * FROM line WHERE script_id = ? ORDER BY seq", (script_id,)):
        who = speakers.get(r["line_id"], [])
        clip = clips.get(r["line_id"])
        lines.append({"line_id": r["line_id"], "seq": r["seq"], "kind": r["kind"], "text": r["text"],
                      "spoken": r["spoken"], "speakers": who, "mine": bool(mine & set(who)),
                      "clip": clip["hash"] if clip else None,
                      "clip_status": status.get(clip["hash"]) if clip else None})
    wanted = [ln for ln in lines if ln["clip"]]
    return {"script": {"script_id": script["script_id"], "title": script["title"]},
            "mine": sorted(mine),
            "characters": [{"character_id": c["character_id"], "name": c["name"], "voice": c["voice"]}
                           for c in conn.execute("SELECT * FROM character WHERE script_id = ? ORDER BY character_id",
                                                 (script_id,))],
            "lines": lines,
            "audio": {"total": len(wanted), "ready": sum(ln["clip_status"] == "done" for ln in wanted),
                      "failed": sum(ln["clip_status"] == "failed" for ln in wanted)}}


@router.get("/clips/{clip_hash}")
def get_clip(clip_hash: str, request: Request, conn=Depends(get_conn)):
    """The audio if it's ready. If not, 202: the clip is moved to the front of the queue and the player asks again."""
    clip = conn.execute("SELECT * FROM clip WHERE clip_hash = ?", (clip_hash,)).fetchone()
    if clip is None:
        raise HTTPException(404, "No such clip.")
    s = request.app.state.settings
    if clip["status"] == "done" and (s.data_dir / clip["path"]).is_file():
        return FileResponse(s.data_dir / clip["path"], media_type="audio/mpeg",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})
    if clip["status"] == "done":   # the file has gone missing: make it again
        conn.execute("UPDATE clip SET status = 'failed' WHERE clip_hash = ?", (clip_hash,))
    c = {"hash": clip_hash, "engine": clip["engine"], "voice": clip["voice"], "speed": clip["speed"],
         "text": clip["text"]}
    if not audio.ensure_clip(conn, c, priority=audio.URGENT):
        audio.hurry(conn, clip_hash)
    return JSONResponse({"status": "queued"}, status_code=202)
