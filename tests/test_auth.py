from fastapi.testclient import TestClient

from offbook import auth
from tests.conftest import HEADERS, PASSWORD


def test_password_hash_round_trip():
    h = auth.hash_password("open sesame")
    assert auth.verify_password("open sesame", h)
    assert not auth.verify_password("open sesam", h)
    assert not auth.verify_password("open sesame", "not-a-hash")


def test_login_refused_until_a_password_is_set(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 401
    assert client.get("/api/me").status_code == 401


def test_short_password_rejected(conn):
    try:
        auth.set_family_password(conn, "short")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_login_and_me(family):
    assert family.get("/api/me").json() == {"profile": None}


def test_wrong_password_then_throttle(client, conn):
    auth.set_family_password(conn, PASSWORD)
    for _ in range(5):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 429


def test_state_changing_requests_need_the_xhr_header(app, conn):
    auth.set_family_password(conn, PASSWORD)
    with TestClient(app) as bare:
        assert bare.post("/api/login", json={"password": PASSWORD}).status_code == 403


def test_changing_the_password_signs_everyone_out(family, conn):
    auth.set_family_password(conn, "a-different-password")
    assert family.get("/api/me").status_code == 401


def test_profiles_need_the_family_login(client):
    assert client.get("/api/profiles").status_code == 401
    assert client.post("/api/profiles", json={"name": "Mia", "colour": "#2e86de"}).status_code == 401


def test_profile_create_select_and_rename(family):
    listing = family.get("/api/profiles").json()
    assert listing["profiles"] == [] and "#2e86de" in listing["colours"]

    mia = family.post("/api/profiles", json={"name": "  Mia  ", "colour": "#2e86de"}).json()
    assert mia["name"] == "Mia" and mia["patience"] == 1.0
    assert family.post("/api/profiles", json={"name": "mia", "colour": "#e4572e"}).status_code == 409
    assert family.post("/api/profiles", json={"name": "Sam", "colour": "red"}).status_code == 400

    assert family.post(f"/api/profiles/{mia['profile_id']}/select").status_code == 200
    assert family.get("/api/me").json()["profile"]["name"] == "Mia"

    r = family.patch(f"/api/profiles/{mia['profile_id']}", json={"patience": 1.5})
    assert r.json()["patience"] == 1.5 and r.json()["name"] == "Mia"
    assert family.patch(f"/api/profiles/{mia['profile_id']}", json={"patience": 9}).status_code == 422
    assert family.post("/api/profiles/999/select").status_code == 404


def test_delete_profile_needs_the_password_again(kid):
    pid = kid.get("/api/me").json()["profile"]["profile_id"]
    assert kid.post(f"/api/profiles/{pid}/delete", json={"password": "wrong"}).status_code == 400
    assert kid.get("/api/me").json()["profile"] is not None
    assert kid.post(f"/api/profiles/{pid}/delete", json={"password": PASSWORD}).status_code == 200
    assert kid.get("/api/me").json() == {"profile": None}


def test_each_device_picks_its_own_profile(app, conn):
    auth.set_family_password(conn, PASSWORD)
    with TestClient(app, headers=HEADERS) as a, TestClient(app, headers=HEADERS) as b:
        for c in (a, b):
            assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        mia = a.post("/api/profiles", json={"name": "Mia", "colour": "#2e86de"}).json()["profile_id"]
        sam = a.post("/api/profiles", json={"name": "Sam", "colour": "#e4572e"}).json()["profile_id"]
        a.post(f"/api/profiles/{mia}/select")
        b.post(f"/api/profiles/{sam}/select")
        assert a.get("/api/me").json()["profile"]["name"] == "Mia"
        assert b.get("/api/me").json()["profile"]["name"] == "Sam"
