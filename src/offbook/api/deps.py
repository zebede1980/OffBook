import sqlite3
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request

from offbook import auth, db


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def require_xhr(request: Request) -> None:
    """CSRF guard for state-changing routes: browsers will not add this header to a cross-site form post."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "offbook":
        raise HTTPException(403, "missing X-Requested-With header")


def require_family(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> None:
    """The family password has been entered on this device, and hasn't been changed since."""
    stamp = auth.session_stamp(conn)
    if stamp is None or request.session.get("fam") != stamp:
        request.session.clear()
        raise HTTPException(401, "not logged in")


def current_profile(request: Request, conn: sqlite3.Connection = Depends(get_conn),
                    _: None = Depends(require_family)) -> sqlite3.Row:
    pid = request.session.get("pid")
    row = conn.execute("SELECT * FROM profile WHERE profile_id = ?", (pid,)).fetchone() if pid else None
    if row is None:
        request.session.pop("pid", None)
        raise HTTPException(409, {"code": "no_profile", "message": "Pick who's rehearsing first."})
    return row
