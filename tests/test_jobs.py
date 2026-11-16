import pytest

from offbook import jobs


@pytest.fixture
def ran(monkeypatch):
    """Register throwaway handlers for the real job kinds and record the order they run in."""
    order = []
    monkeypatch.setattr(jobs, "HANDLERS", {})
    for kind in ("grade", "ocr", "tts"):
        jobs.handler(kind)(lambda conn, s, payload, pid, kind=kind: order.append((kind, payload["n"])))
    return order


def test_marking_beats_page_reading_beats_voice_generation(conn, settings, ran):
    jobs.enqueue(conn, "tts", {"n": 1})
    jobs.enqueue(conn, "tts", {"n": 2})
    jobs.enqueue(conn, "ocr", {"n": 3})
    jobs.enqueue(conn, "grade", {"n": 4})
    jobs.enqueue(conn, "tts", {"n": 5}, priority=99)
    assert jobs.run_pending(conn, settings) == 5
    assert ran == [("tts", 5), ("grade", 4), ("ocr", 3), ("tts", 1), ("tts", 2)]


def test_result_is_stored(conn, settings, monkeypatch):
    monkeypatch.setattr(jobs, "HANDLERS", {"tts": lambda conn, s, payload, pid: {"clip": payload["n"]}})
    job_id = jobs.enqueue(conn, "tts", {"n": 7}, profile_id=3)
    jobs.run_pending(conn, settings)
    out = jobs.job_out(conn.execute("SELECT * FROM job WHERE job_id = ?", (job_id,)).fetchone())
    assert out["status"] == "done" and out["result"] == {"clip": 7} and out["attempts"] == 1


def test_failing_job_is_retried_then_given_up_on(conn, settings, monkeypatch):
    tries = []

    def boom(conn, s, payload, pid):
        tries.append(1)
        raise RuntimeError("kokoro fell over")

    monkeypatch.setattr(jobs, "HANDLERS", {"tts": boom})
    job_id = jobs.enqueue(conn, "tts", {})
    jobs.run_pending(conn, settings)
    row = conn.execute("SELECT * FROM job WHERE job_id = ?", (job_id,)).fetchone()
    assert len(tries) == jobs.MAX_ATTEMPTS
    assert row["status"] == "failed" and "kokoro fell over" in row["error"]


def test_permanent_error_is_not_retried(conn, settings, monkeypatch):
    tries = []

    def boom(conn, s, payload, pid):
        tries.append(1)
        raise jobs.PermanentError("bad payload")

    monkeypatch.setattr(jobs, "HANDLERS", {"tts": boom})
    jobs.enqueue(conn, "tts", {})
    jobs.run_pending(conn, settings)
    assert len(tries) == 1


def test_unknown_kind_fails_without_retries(conn, settings, monkeypatch):
    monkeypatch.setattr(jobs, "HANDLERS", {})
    job_id = jobs.enqueue(conn, "mystery", {})
    assert jobs.run_pending(conn, settings) == 1
    row = conn.execute("SELECT status, attempts FROM job WHERE job_id = ?", (job_id,)).fetchone()
    assert tuple(row) == ("failed", 1)


def test_interrupted_jobs_are_requeued(conn):
    job_id = jobs.enqueue(conn, "tts", {})
    assert jobs.claim(conn)["job_id"] == job_id
    assert conn.execute("SELECT status FROM job WHERE job_id = ?", (job_id,)).fetchone()[0] == "running"
    assert jobs.requeue_stale(conn) == 1
    assert conn.execute("SELECT status FROM job WHERE job_id = ?", (job_id,)).fetchone()[0] == "queued"
