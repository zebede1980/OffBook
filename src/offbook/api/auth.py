"""Family login, and the profiles the kids pick from."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from offbook import attempts, auth
from offbook.api.deps import get_conn, require_family
from offbook.db import now_utc

router = APIRouter(prefix="/api")

COLOURS = ("#e4572e", "#f3a712", "#29a37e", "#2e86de", "#8e5fd9", "#e0569b", "#4b5d67", "#8aa62f")


class LoginIn(BaseModel):
    password: str


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=24)
    colour: str


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=24)
    colour: str | None = None
    patience: float | None = Field(default=None, ge=0.5, le=3.0)


class DeleteIn(BaseModel):
    password: str


def profile_out(p: sqlite3.Row) -> dict:
    return {"profile_id": p["profile_id"], "name": p["name"], "colour": p["colour"], "patience": p["patience"]}


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(400, "Type a name.")
    return name


def _check_colour(colour: str) -> str:
    if colour not in COLOURS:
        raise HTTPException(400, "Pick one of the colours on offer.")
    return colour


def _get(conn: sqlite3.Connection, profile_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM profile WHERE profile_id = ?", (profile_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such profile.")
    return row


@router.post("/login")
def login(body: LoginIn, request: Request, conn=Depends(get_conn)):
    ip = _ip(request)
    throttle = request.app.state.throttle
    if throttle.blocked(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not auth.check_family_password(conn, body.password):
        throttle.fail(ip)
        raise HTTPException(401, "Wrong password.")
    throttle.clear(ip)
    request.session.clear()
    request.session["fam"] = auth.session_stamp(conn)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", dependencies=[Depends(require_family)])
def me(request: Request, conn=Depends(get_conn)):
    pid = request.session.get("pid")
    row = conn.execute("SELECT * FROM profile WHERE profile_id = ?", (pid,)).fetchone() if pid else None
    return {"profile": profile_out(row) if row else None}


@router.get("/profiles", dependencies=[Depends(require_family)])
def list_profiles(conn=Depends(get_conn)):
    rows = conn.execute("SELECT * FROM profile ORDER BY name").fetchall()
    return {"profiles": [profile_out(r) for r in rows], "colours": COLOURS}


@router.post("/profiles", dependencies=[Depends(require_family)])
def create_profile(body: ProfileIn, conn=Depends(get_conn)):
    try:
        cur = conn.execute("INSERT INTO profile(name, colour, created_at) VALUES (?, ?, ?)",
                           (_clean_name(body.name), _check_colour(body.colour), now_utc()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Someone already has that name.")
    return profile_out(_get(conn, cur.lastrowid))


@router.patch("/profiles/{profile_id}", dependencies=[Depends(require_family)])
def update_profile(profile_id: int, body: ProfilePatch, conn=Depends(get_conn)):
    p = _get(conn, profile_id)
    try:
        conn.execute("UPDATE profile SET name = ?, colour = ?, patience = ? WHERE profile_id = ?",
                     (_clean_name(body.name) if body.name is not None else p["name"],
                      _check_colour(body.colour) if body.colour is not None else p["colour"],
                      body.patience if body.patience is not None else p["patience"], profile_id))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Someone already has that name.")
    return profile_out(_get(conn, profile_id))


@router.post("/profiles/{profile_id}/delete", dependencies=[Depends(require_family)])
def delete_profile(profile_id: int, body: DeleteIn, request: Request, conn=Depends(get_conn)):
    """Asks for the family password again: a speed bump against wiping a sibling's progress by accident."""
    _get(conn, profile_id)
    if not auth.check_family_password(conn, body.password):
        raise HTTPException(400, "Wrong password.")
    attempts.delete_files_for(conn, request.app.state.settings, profile_id=profile_id)
    conn.execute("DELETE FROM profile WHERE profile_id = ?", (profile_id,))
    if request.session.get("pid") == profile_id:
        request.session.pop("pid", None)
    return {"ok": True}


@router.post("/profiles/{profile_id}/select", dependencies=[Depends(require_family)])
def select_profile(profile_id: int, request: Request, conn=Depends(get_conn)):
    p = _get(conn, profile_id)
    request.session["pid"] = profile_id
    return profile_out(p)
