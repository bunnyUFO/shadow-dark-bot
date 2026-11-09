"""The bot only releases configured, read-only map tokens after sign-in."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from shadowdark_bot.web import drawcraft as drawcraft_config
from shadowdark_bot.web.app import STATIC_DIR, create_app
from shadowdark_bot.web.auth import SessionSigner
from shadowdark_bot.web.drawcraft import DrawcraftView, load_drawcraft_views

DRAWING_ID = str(uuid4())
VIEW_TOKEN = "x" * 43


def test_config_file_loads_only_explicit_drawings(tmp_path):
    path = tmp_path / "views.json"
    path.write_text(json.dumps([{"id": DRAWING_ID, "title": " Illmyre ",
                                 "token": VIEW_TOKEN}]), encoding="utf-8")
    assert load_drawcraft_views(str(path)) == (
        DrawcraftView(DRAWING_ID, "Illmyre", VIEW_TOKEN),
    )
    assert load_drawcraft_views("") == ()


@pytest.mark.parametrize("entries", [
    [{"id": "../api", "title": "Map", "token": VIEW_TOKEN}],
    [{"id": DRAWING_ID, "title": "Map", "token": "short"}],
    [{"id": DRAWING_ID, "title": "Map", "token": VIEW_TOKEN}] * 2,
    {"id": DRAWING_ID, "title": "Map", "token": VIEW_TOKEN},
])
def test_bad_config_fails_closed_without_echoing_token(tmp_path, entries):
    path = tmp_path / "views.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(ValueError, match="DRAWCRAFT_VIEWS_FILE") as failure:
        load_drawcraft_views(str(path))
    assert VIEW_TOKEN not in str(failure.value)


def test_missing_config_file_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="DRAWCRAFT_VIEWS_FILE"):
        load_drawcraft_views(str(tmp_path / "missing.json"))


def test_config_refuses_public_static_assets(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    path = assets / "views.json"
    path.write_text(json.dumps([{"id": DRAWING_ID, "title": "Illmyre",
                                 "token": VIEW_TOKEN}]), encoding="utf-8")
    monkeypatch.setattr(drawcraft_config, "PUBLIC_ASSETS", assets)
    with pytest.raises(ValueError, match="DRAWCRAFT_VIEWS_FILE"):
        load_drawcraft_views(str(path))


def _client(mode="discord", static_dir=None):
    signer = SessionSigner("x" * 40, ttl_seconds=3600)

    async def is_member(_):
        return True

    app = create_app(
        mode=mode,
        signer=signer,
        oauth=SimpleNamespace(client_id="test") if mode == "discord" else None,
        is_allowed_member=is_member if mode == "discord" else None,
        static_dir=static_dir,
        drawcraft_views=(DrawcraftView(DRAWING_ID, "Illmyre", VIEW_TOKEN),),
    )
    return TestClient(app, client=("127.0.0.1", 50000)), signer.issue("u1", "Player")


def test_map_token_requires_login_and_is_only_in_access_response():
    client, session = _client()
    assert client.get("/api/auth/config").json()["maps_enabled"] is True
    assert client.get("/api/maps").status_code == 401
    assert client.get(f"/api/maps/{DRAWING_ID}/access").status_code == 401
    headers = {"Authorization": f"Bearer {session}"}
    listing = client.get("/api/maps", headers=headers)
    assert listing.json() == [{"id": DRAWING_ID, "title": "Illmyre"}]
    assert VIEW_TOKEN not in listing.text
    access = client.get(f"/api/maps/{DRAWING_ID}/access", headers=headers)
    assert access.json() == {"id": DRAWING_ID, "title": "Illmyre", "token": VIEW_TOKEN}
    assert access.headers["cache-control"] == "no-store"
    assert client.get(f"/api/maps/{uuid4()}/access", headers=headers).status_code == 404


def test_local_no_login_app_never_serves_map_token():
    client, session = _client(mode="local")
    headers = {"Authorization": f"Bearer {session}"}
    assert client.get("/api/auth/config").json()["maps_enabled"] is False
    assert client.get("/api/maps", headers=headers).status_code == 404
    assert client.get(f"/api/maps/{DRAWING_ID}/access", headers=headers).status_code == 404


def test_web_and_activity_html_have_scoped_viewer_csp():
    client, _ = _client(static_dir=STATIC_DIR)
    web = client.get("/").headers["content-security-policy"]
    activity = client.get("/?frame_id=example").headers["content-security-policy"]
    assert "script-src 'self' https://draw.bunnyufo.net" in web
    assert "connect-src 'self' https://draw.bunnyufo.net" in web
    assert "script-src 'self'; connect-src 'self'" in activity
    assert "draw.bunnyufo.net" not in activity
    assert "unsafe-inline" not in web + activity


def test_static_assets_revalidate_after_deploy():
    client, _ = _client(static_dir=STATIC_DIR)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/assets/app.css").headers["cache-control"] == "no-cache"
    assert client.get("/assets/maps.js").headers["cache-control"] == "no-cache"
