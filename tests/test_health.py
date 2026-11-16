def test_health_is_open(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["schema"] >= 1


def test_unknown_api_path_is_404_not_the_spa(client):
    assert client.get("/api/nope").status_code == 404


def test_spa_fallback_serves_index(client, settings):
    settings.web_dir.mkdir()
    (settings.web_dir / "index.html").write_text("<html>offbook</html>", encoding="utf-8")
    (settings.web_dir / "icon.svg").write_text("<svg/>", encoding="utf-8")
    assert "offbook" in client.get("/who").text
    assert client.get("/icon.svg").text == "<svg/>"


def test_spa_does_not_serve_files_outside_web_dir(client, settings, tmp_path):
    settings.web_dir.mkdir()
    (settings.web_dir / "index.html").write_text("<html>offbook</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    assert "nope" not in client.get("/..%2Fsecret.txt").text
