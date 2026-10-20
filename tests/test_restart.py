from __future__ import annotations

import time


def test_restart_requires_a_signed_in_admin(client):
    assert client.post("/api/v1/system/restart").status_code == 401


def test_restart_schedules_a_process_replace(admin, client, monkeypatch):
    called = []
    monkeypatch.setattr("app.core.restart.restart_process", lambda: called.append(True))
    response = client.post("/api/v1/system/restart")
    assert response.status_code == 200
    assert response.json()["data"]["message"] == "The panel is restarting."
    time.sleep(0.9)
    assert called == [True]
