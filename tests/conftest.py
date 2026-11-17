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
    """Stands in for Kokoro: four voices, and 'audio' that records what was asked for."""
    name = "fake"

    def __init__(self):
        self.calls = []
        self.broken = False

    def voices(self):
        from offbook.tts import Voice
        return [Voice("bf_emma", "Emma", "British women", "f"), Voice("bf_lily", "Lily", "British women", "f"),
                Voice("bm_george", "George", "British men", "m"), Voice("bm_lewis", "Lewis", "British men", "m")]

    def synthesize(self, text, voice, speed):
        from offbook.tts import TTSError
        self.calls.append((text, voice, speed))
        if self.broken:
            raise TTSError("fake engine is down")
        return f"MP3[{voice}@{speed}]{text}".encode()


@pytest.fixture(autouse=True)
def fake_tts(monkeypatch):
    from offbook import tts
    f = FakeTTS()
    monkeypatch.setattr(tts, "FAKE", f)
    return f
