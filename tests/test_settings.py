from __future__ import annotations

from app.core.settings import settings
from app.services.runtime_settings import reset_cache


def test_settings_require_admin(client):
    assert client.get("/api/v1/settings").status_code == 401
    page = client.get("/settings", follow_redirects=False)
    assert page.status_code == 302


def test_panel_settings_persist_and_reject_invalid_relation(admin, client):
    current = client.get("/api/v1/settings")
    assert current.status_code == 200
    assert any(item["label"] == "Environment" for item in current.json()["data"]["startup"])

    rejected = client.put("/api/v1/settings", json={"values": {"stale_after_seconds": 5, "report_interval_seconds": 30}})
    assert rejected.status_code == 422
    assert settings.stale_after_seconds == 90

    equal = client.put("/api/v1/settings", json={"values": {"offline_after_seconds": 90, "stale_after_seconds": 90}})
    assert equal.status_code == 422
    assert settings.offline_after_seconds == 180

    saved = client.put("/api/v1/settings", json={"values": {"offline_after_seconds": 120, "timezone": "Asia/Tehran"}})
    assert saved.status_code == 200, saved.text
    reset_cache()
    assert settings.offline_after_seconds == 120
    assert settings.timezone == "Asia/Tehran"

    unknown = client.put("/api/v1/settings", json={"values": {"secret_key": "nope"}})
    assert unknown.status_code == 422
