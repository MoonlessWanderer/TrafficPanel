from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    import config

    monkeypatch.setattr(config, "ENV", "test")
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(config, "REPORTS_DIR", str(data / "reports"))
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(config, "DATABASE_PATH", str(data / "panel.db"))
    monkeypatch.setattr(config, "SECRET_KEY", "test-secret-key-with-32-characters-min")
    monkeypatch.setattr(config, "ADMIN_USERNAME", "admin")
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "S3cure-Test-Pass")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    from app.core.settings import settings

    settings.reload()
    from app.core.rate_limit import rate_limiter

    rate_limiter.reset()
    from app.factory import create_app

    application = create_app(enable_scheduler=False)
    with TestClient(application) as test_client:
        yield test_client
    from app.db.database import db

    if not db.is_closed():
        db.close()


@pytest.fixture
def admin(client):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "S3cure-Test-Pass"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
    return response.json()["data"]
