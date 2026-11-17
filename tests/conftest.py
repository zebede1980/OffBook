import dataclasses

import pytest
from fastapi.testclient import TestClient

from offbook import config
from offbook.api.app import create_app

PASSWORD = "curtain-up-123"
HEADERS = {"X-Requested-With": "offbook"}


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(config.load(), data_dir=tmp_path, web_dir=tmp_path / "web", secure_cookies=False,
                               start_worker=False, nanogpt_api_key="", kokoro_url="http://kokoro.invalid")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, headers=HEADERS) as c:
        yield c


@pytest.fixture
def conn(settings, app):
    from offbook import db
    c = db.connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def family(client, conn):
    """A client that has entered the family password but not picked a profile."""
    from offbook import auth
    auth.set_family_password(conn, PASSWORD)
    r = client.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def kid(family):
    """A logged-in client with a profile selected."""
    pid = family.post("/api/profiles", json={"name": "Mia", "colour": "#2e86de"}).json()["profile_id"]
    assert family.post(f"/api/profiles/{pid}/select").status_code == 200
    return family


@pytest.fixture
def fake_llm(monkeypatch):
    """Install a scripted LLM. Push responses (str or callable(model, messages) -> str) onto `.queue`."""
    from offbook import llm

    class Fake:
        def __init__(self):
            self.queue = []
            self.calls = []

        def __call__(self, model, messages):
            self.calls.append((model, messages))
            if not self.queue:
                raise AssertionError("fake LLM called with nothing queued")
            r = self.queue.pop(0)
            return r(model, messages) if callable(r) else r

    f = Fake()
    monkeypatch.setattr(llm, "FAKE", f)
    return f


class FakeTTS:
    """Stands in for a speech engine: a few voices, and 'audio' that records what was asked for."""

    def __init__(self, name, voices):
        self.name = name
        self._voices = voices
        self.calls = []
        self.broken = False

    def voices(self):
        return self._voices

    def synthesize(self, text, voice, speed):
        from offbook.tts import TTSError
        self.calls.append((text, voice, speed))
        if self.broken:
            raise TTSError("fake engine is down")
        return f"MP3[{voice}@{speed}]{text}".encode()


def _kokoro():
    from offbook.tts import Voice
    return FakeTTS("kokoro", [Voice("bf_emma", "Emma", "British women", "f"),
                              Voice("bf_lily", "Lily", "British women", "f"),
                              Voice("bm_george", "George", "British men", "m"),
                              Voice("bm_lewis", "Lewis", "British men", "m")])


def _zonos():
    from offbook.tts import Voice
    return FakeTTS("zonos", [Voice("z_imogen", "Imogen", "British & Irish women", "f"),
                             Voice("z_rupert", "Rupert", "British & Irish men", "m"),
                             Voice("z_cillian", "Cillian", "British & Irish men", "m"),
                             Voice("z_witch", "Morgana (witch)", "Character voices", "f", auto=False)])


@pytest.fixture(autouse=True)
def fake_engines(monkeypatch):
    """Both engines faked, and levelling (ffmpeg) skipped, for every test."""
    from offbook import sound, tts
    engines = {"kokoro": _kokoro(), "zonos": _zonos()}
    monkeypatch.setattr(tts, "FAKE", engines)
    monkeypatch.setattr(sound, "level", lambda data: data)
    return engines


@pytest.fixture
def fake_tts(fake_engines):
    return fake_engines["kokoro"]


@pytest.fixture
def fake_zonos(fake_engines):
    return fake_engines["zonos"]
