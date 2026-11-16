"""The family password and login throttling (hashing ported from Trellis).

One shared password guards the whole site; after that the kids just pick a profile. The hash lives in the `meta`
table rather than in .env, because scrypt hashes are full of `$` and compose mangles those.
The session is a signed cookie, so there is no session table.
"""

import base64
import hashlib
import hmac
import os
import sqlite3
import time
from collections import defaultdict, deque

_N, _R, _P = 2**14, 8, 1
MIN_PASSWORD = 8
_KEY = "family_pw_hash"


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def family_hash(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (_KEY,)).fetchone()
    return row[0] if row else None


def set_family_password(conn: sqlite3.Connection, pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (_KEY, hash_password(pw)))


_DUMMY = hash_password("timing-equaliser")


def check_family_password(conn: sqlite3.Connection, pw: str) -> bool:
    stored = family_hash(conn)
    # verify against a dummy hash when none is set, so the response time is the same either way
    ok = verify_password(pw, stored or _DUMMY)
    return ok and stored is not None


def session_stamp(conn: sqlite3.Connection) -> str | None:
    """Fingerprint of the current password hash. A session holding a different stamp is logged out, so changing
    the password signs every device out."""
    stored = family_hash(conn)
    return hashlib.sha256(stored.encode()).hexdigest()[:16] if stored else None


class Throttle:
    """At most `limit` failed logins per IP per `window` seconds."""

    def __init__(self, limit: int = 5, window: int = 900):
        self.limit, self.window = limit, window
        self.fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, ip: str) -> bool:
        q = self.fails[ip]
        while q and q[0] < time.monotonic() - self.window:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, ip: str) -> None:
        self.fails[ip].append(time.monotonic())

    def clear(self, ip: str) -> None:
        self.fails.pop(ip, None)
