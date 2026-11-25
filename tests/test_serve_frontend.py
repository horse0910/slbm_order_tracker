"""Phase 7.1: the backend serves the built frontend on 127.0.0.1:8010 with a
fallback to index.html, so /live and /slb/IRFC load on refresh."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from slbm_tracker.api.app import create_app
from slbm_tracker.config import ALLOWED_ORIGINS, get_settings


@pytest.fixture()
def dist(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root>shell</div>")
    (root / "assets" / "index-abc.js").write_text("console.log('app')")
    (root / "manifest.json").write_text('{"name": "SLBM Tracker"}')
    (root / "sw.js").write_text("self.addEventListener('install', () => {});")
    monkeypatch.setattr(get_settings(), "frontend_dist_dir", str(root))
    return root


@pytest.fixture()
def app_client(dist: Path):
    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/live", "/slb/IRFC", "/contracts", "/settings"])
def test_app_routes_return_the_shell(app_client: TestClient, path: str):
    res = app_client.get(path)
    assert res.status_code == 200 and "shell" in res.text
    assert res.headers["content-type"].startswith("text/html")


def test_static_files_are_served_as_themselves(app_client: TestClient):
    assert app_client.get("/assets/index-abc.js").text == "console.log('app')"
    assert app_client.get("/manifest.json").json() == {"name": "SLBM Tracker"}
    assert "install" in app_client.get("/sw.js").text


def test_api_is_untouched_and_unknown_api_paths_stay_404(app_client: TestClient):
    assert app_client.get("/api/health").status_code == 200
    res = app_client.get("/api/does-not-exist")
    assert res.status_code == 404 and res.headers["content-type"].startswith("application/json")


def test_paths_cannot_escape_the_dist_folder(app_client: TestClient, dist: Path):
    (dist.parent / "secret.txt").write_text("no")
    res = app_client.get("/..%2Fsecret.txt")
    assert "no" != res.text and "shell" in res.text


def test_no_build_means_no_static_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "frontend_dist_dir", str(tmp_path / "missing"))
    with TestClient(create_app()) as c:
        assert c.get("/live").status_code == 404
        assert c.get("/api/health").status_code == 200


def test_the_served_origin_may_open_the_websocket():
    assert "http://127.0.0.1:8010" in ALLOWED_ORIGINS and "http://localhost:8010" in ALLOWED_ORIGINS
