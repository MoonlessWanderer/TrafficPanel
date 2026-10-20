from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models import Node, NodeCredential, TrafficHourlyAggregate, User
from app.services.aggregates import refresh_hourly
from app.utils.time import utcnow


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _headers(node_id: str, key: str, nonce: str) -> dict[str, str]:
    return {
        "X-Node-Id": node_id,
        "X-Node-Key": key,
        "X-Node-Timestamp": str(int(time.time())),
        "X-Node-Nonce": nonce,
    }


def _payload(moment: datetime, nonce: str, rx: int, tx: int, cpu: float = 20) -> dict:
    return {
        "nonce": nonce,
        "collected_at": _stamp(moment),
        "cpu_percent": cpu,
        "ram_total_bytes": 1_000_000,
        "ram_used_bytes": 400_000,
        "ram_free_bytes": 600_000,
        "disk_total_bytes": 10_000_000,
        "disk_used_bytes": 2_000_000,
        "disk_free_bytes": 8_000_000,
        "load_1": 0.2,
        "load_5": 0.2,
        "load_15": 0.1,
        "uptime_seconds": 500,
        "agent_version": "1.0.0",
        "os_name": "Debian",
        "kernel_version": "6.8.0",
        "interfaces": [
            {
                "name": "eth0",
                "rx_bytes": rx,
                "tx_bytes": tx,
                "rx_packets": 10,
                "tx_packets": 8,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_dropped": 0,
                "tx_dropped": 0,
            }
        ],
    }


def _ingest(client, node_id: str, key: str, moment: datetime, rx: int, tx: int, nonce: str, cpu: float = 20):
    return client.post(
        "/api/v1/ingest/metrics",
        json=_payload(moment, nonce, rx, tx, cpu),
        headers=_headers(node_id, key, nonce),
    )


def _create_node(client, name: str) -> dict:
    response = client.post("/api/v1/nodes", json={"name": name, "tags": ["edge"]})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_login_and_key_are_not_stored_raw(client):
    assert client.get("/login").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 302
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert bad.status_code == 401
    good = client.post("/api/v1/auth/login", json={"username": "admin", "password": "S3cure-Test-Pass"})
    assert good.status_code == 200
    client.headers["X-CSRF-Token"] = good.json()["data"]["csrf_token"]
    created = _create_node(client, "web")
    stored = NodeCredential.get()
    assert created["node_key"] not in stored.key_hash
    assert created["node_key"] not in stored.key_prefix
    assert User.get().password_hash != "S3cure-Test-Pass"


def test_duplicate_reset_gap_and_status(client, admin):
    created = _create_node(client, "edge")
    node_id = created["node_id"]
    key = created["node_key"]
    now = datetime.now(timezone.utc)
    first = _ingest(client, node_id, key, now - timedelta(seconds=30), 1_000, 2_000, "nonce-normal-0001")
    second = _ingest(client, node_id, key, now, 2_500, 2_400, "nonce-normal-0002")
    duplicate = _ingest(client, node_id, key, now, 2_500, 2_400, "nonce-normal-0002")
    assert first.status_code == 200
    assert second.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True

    summary = client.get("/api/v1/dashboard/summary?preset=1h")
    traffic = summary.json()["data"]["traffic"]
    assert traffic["rx_bytes"] == 1_500
    assert traffic["tx_bytes"] == 400
    assert abs(traffic["rx_bps_avg"] - 50) < 0.1
    assert abs(traffic["rx_bps_now"] - 50) < 0.1

    reset_node = _create_node(client, "reset")
    moment = datetime.now(timezone.utc)
    assert _ingest(client, reset_node["node_id"], reset_node["node_key"], moment - timedelta(seconds=30), 10_000, 8_000, "nonce-reset-00001").status_code == 200
    assert _ingest(client, reset_node["node_id"], reset_node["node_key"], moment, 400, 8_100, "nonce-reset-00002").status_code == 200
    stats = client.get(f"/api/v1/nodes/{reset_node['node_id']}/stats?preset=1h").json()["data"]
    assert stats["interfaces"][0]["reason"] == "reset"
    assert stats["traffic"]["rx_bytes"] == 400
    assert stats["traffic"]["rx_bytes"] >= 0

    gap_node = _create_node(client, "gap")
    later = datetime.now(timezone.utc)
    assert _ingest(client, gap_node["node_id"], gap_node["node_key"], later - timedelta(minutes=50), 100, 100, "nonce-gap-0000001").status_code == 200
    assert _ingest(client, gap_node["node_id"], gap_node["node_key"], later, 50_000, 50_000, "nonce-gap-0000002").status_code == 200
    gap_stats = client.get(f"/api/v1/nodes/{gap_node['node_id']}/stats?preset=24h").json()["data"]
    assert gap_stats["traffic"]["has_data"] is False
    assert gap_stats["traffic"]["rx_bytes"] is None

    online = client.get(f"/api/v1/nodes/{node_id}").json()["data"]
    assert online["status"] == "online"
    node = Node.get(Node.node_id == node_id)
    node.last_seen_at = utcnow() - timedelta(seconds=500)
    node.save()
    offline = client.get(f"/api/v1/nodes/{node_id}").json()["data"]
    assert offline["status"] == "offline"

    revoked = client.post(f"/api/v1/nodes/{node_id}/revoke-key")
    assert revoked.status_code == 200
    rejected = _ingest(client, node_id, key, datetime.now(timezone.utc), 10, 10, "nonce-revoked-0001")
    assert rejected.status_code == 401
    rotated = client.post(f"/api/v1/nodes/{node_id}/rotate-key")
    assert rotated.status_code == 200
    new_key = rotated.json()["data"]["node_key"]
    assert _ingest(client, node_id, key, datetime.now(timezone.utc), 11, 11, "nonce-oldkey-00001").status_code == 401
    assert _ingest(client, node_id, new_key, datetime.now(timezone.utc), 12, 12, "nonce-newkey-00001").status_code == 200


def test_current_speed_is_the_latest_interval_not_the_range_average(client, admin):
    created = _create_node(client, "live")
    node_id = created["node_id"]
    key = created["node_key"]
    end = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert _ingest(client, node_id, key, end - timedelta(seconds=60), 0, 0, "nonce-live-000001").status_code == 200
    assert _ingest(client, node_id, key, end - timedelta(seconds=30), 6_000, 0, "nonce-live-000002").status_code == 200
    assert _ingest(client, node_id, key, end, 6_300, 300, "nonce-live-000003").status_code == 200
    traffic = client.get("/api/v1/dashboard/summary?preset=1h").json()["data"]["traffic"]
    assert abs(traffic["rx_bps_avg"] - 105) < 0.1
    assert abs(traffic["rx_bps_now"] - 10) < 0.1
    detail = client.get(f"/api/v1/nodes/{node_id}").json()["data"]
    assert abs(detail["speed"]["rx_bps"] - 10) < 0.1


def test_group_stats_follow_membership(client, admin):
    alpha = _create_node(client, "alpha")
    beta = _create_node(client, "beta")
    group = client.post("/api/v1/groups", json={"name": "لبه", "color": "#0f6e6b", "tags": ["اصلی"]})
    assert group.status_code == 201
    group_id = group.json()["data"]["id"]
    added = client.post(f"/api/v1/groups/{group_id}/members", json={"node_ids": [alpha["node_id"], beta["node_id"]]})
    assert added.status_code == 200
    now = datetime.now(timezone.utc)
    _ingest(client, alpha["node_id"], alpha["node_key"], now - timedelta(seconds=30), 0, 0, "nonce-alpha-00001", cpu=80)
    _ingest(client, alpha["node_id"], alpha["node_key"], now, 1_000, 0, "nonce-alpha-00002", cpu=80)
    _ingest(client, beta["node_id"], beta["node_key"], now - timedelta(seconds=30), 0, 0, "nonce-beta-000001", cpu=10)
    _ingest(client, beta["node_id"], beta["node_key"], now, 3_000, 0, "nonce-beta-000002", cpu=10)
    before = client.get(f"/api/v1/groups/{group_id}/stats?preset=1h").json()["data"]
    assert before["traffic"]["rx_bytes"] == 4_000
    ranked = sorted(before["ranking"], key=lambda item: item["rx_bytes"], reverse=True)
    assert ranked[0]["rx_bytes"] == 3_000
    assert ranked[0]["tx_bytes"] == 0
    assert "share_percent" not in ranked[0]
    assert abs(before["traffic"]["rx_bps_avg"] - (1_000 / 30 + 3_000 / 30)) < 0.1
    assert abs(before["traffic"]["rx_bps_now"] - (1_000 / 30 + 3_000 / 30)) < 0.1
    assert before["counts"]["online"] == 2
    chart = client.get(f"/api/v1/charts/traffic?preset=1h&group_ids={group_id}").json()["data"]
    speeds = [value for value in chart["datasets"][2]["data"] if value]
    assert speeds
    assert abs(sum(speeds) - (1_000 / 30 + 3_000 / 30)) < 0.1
    client.delete(f"/api/v1/groups/{group_id}/members/{beta['node_id']}")
    after = client.get(f"/api/v1/groups/{group_id}/stats?preset=1h").json()["data"]
    assert after["traffic"]["rx_bytes"] == 1_000
    assert len(after["ranking"]) == 1


def test_combined_filters_and_aggregate(client, admin):
    alpha = _create_node(client, "alpha-node")
    beta = _create_node(client, "beta-node")
    group = client.post("/api/v1/groups", json={"name": "فیلتر", "color": "#1d4e89"}).json()["data"]
    client.post(f"/api/v1/groups/{group['id']}/members", json={"node_ids": [alpha["node_id"]]})
    now = datetime.now(timezone.utc)
    _ingest(client, alpha["node_id"], alpha["node_key"], now - timedelta(seconds=30), 0, 0, "nonce-filter-a001", cpu=85)
    _ingest(client, alpha["node_id"], alpha["node_key"], now, 5_000, 0, "nonce-filter-a002", cpu=85)
    _ingest(client, beta["node_id"], beta["node_key"], now - timedelta(seconds=30), 0, 0, "nonce-filter-b001", cpu=5)
    _ingest(client, beta["node_id"], beta["node_key"], now, 100, 0, "nonce-filter-b002", cpu=5)
    quiet = _create_node(client, "quiet")
    _ingest(client, quiet["node_id"], quiet["node_key"], now, 10, 10, "nonce-quiet-000001", cpu=1)
    response = client.get(
        "/api/v1/nodes",
        params={
            "preset": "1h",
            "q": "alpha",
            "status": "online",
            "group_ids": group["id"],
            "cpu_min": 50,
            "traffic_min": 1000,
            "traffic": "rx",
            "sort": "-traffic",
        },
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]
    assert [item["node_id"] for item in items] == [alpha["node_id"]]
    missing = client.get("/api/v1/nodes", params={"preset": "1h", "traffic_min": 0, "q": "quiet"})
    assert missing.json()["data"] == []
    refresh_hourly(now - timedelta(hours=1), now + timedelta(minutes=1))
    row = TrafficHourlyAggregate.get(
        (TrafficHourlyAggregate.node == Node.get(Node.node_id == alpha["node_id"]))
        & (TrafficHourlyAggregate.interface_name == "*")
        & (TrafficHourlyAggregate.sample_count > 0)
    )
    assert row.rx_bytes == 5_000
    assert row.cpu_avg == 85


def test_virtual_interfaces_are_left_out_of_traffic(client, admin):
    created = _create_node(client, "uplink")
    node_id = created["node_id"]
    key = created["node_key"]
    now = datetime.now(timezone.utc)

    def send(moment, eth_rx, eth_tx, docker_rx, docker_tx, nonce):
        payload = _payload(moment, nonce, eth_rx, eth_tx)
        payload["interfaces"] = [
            {**payload["interfaces"][0], "name": "eth0", "primary": True},
            {
                "name": "eth1",
                "rx_bytes": docker_rx,
                "tx_bytes": docker_tx,
                "rx_packets": 1,
                "tx_packets": 1,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_dropped": 0,
                "tx_dropped": 0,
                "primary": False,
            },
            {
                "name": "docker0",
                "rx_bytes": docker_rx,
                "tx_bytes": docker_tx,
                "rx_packets": 1,
                "tx_packets": 1,
                "rx_errors": 0,
                "tx_errors": 0,
                "rx_dropped": 0,
                "tx_dropped": 0,
                "primary": False,
            },
        ]
        return client.post("/api/v1/ingest/metrics", json=payload, headers=_headers(node_id, key, nonce))

    assert send(now - timedelta(seconds=30), 1_000, 1_000, 50_000, 50_000, "nonce-uplink-00001").status_code == 200
    assert send(now, 1_500, 1_300, 90_000, 80_000, "nonce-uplink-00002").status_code == 200
    stats = client.get(f"/api/v1/nodes/{node_id}/stats?preset=1h").json()["data"]
    assert stats["traffic"]["rx_bytes"] == 500
    assert stats["traffic"]["tx_bytes"] == 300
    assert [item["name"] for item in stats["interfaces"]] == ["eth0"]
    chart = client.get(f"/api/v1/charts/traffic?preset=1h&node_ids={node_id}").json()["data"]
    assert [value for value in chart["datasets"][0]["data"] if value] == [500]


def test_charts_keep_points_outside_the_last_hour(client, admin, monkeypatch):
    from app.utils.time import as_utc, utcnow
    import app.services.ingest as ingest

    monkeypatch.setattr(ingest, "_checked_time", lambda collected_at: (as_utc(collected_at), utcnow()))
    created = _create_node(client, "history")
    node_id = created["node_id"]
    key = created["node_key"]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for hour in range(5):
        start = now - timedelta(hours=5 - hour, seconds=40)
        nonce_a = f"nonce-hist-a-{hour:04d}"
        nonce_b = f"nonce-hist-b-{hour:04d}"
        assert _ingest(client, node_id, key, start, 1_000_000 + hour * 10_000, 500_000, nonce_a).status_code == 200
        assert _ingest(client, node_id, key, start + timedelta(seconds=30), 1_000_000 + hour * 10_000 + 2_000, 500_000 + 800, nonce_b).status_code == 200
    chart = client.get("/api/v1/charts/traffic?preset=24h&node_ids=" + node_id)
    assert chart.status_code == 200, chart.text
    body = chart.json()["data"]
    rx = [value for value in body["datasets"][0]["data"] if value]
    assert body["range"]["bucket"] == "5min"
    assert len(rx) >= 4
    resources = client.get("/api/v1/charts/resources?preset=24h&node_ids=" + node_id)
    cpu = [value for value in resources.json()["data"]["datasets"][0]["data"] if value is not None]
    assert len(cpu) >= 4
    week = client.get("/api/v1/charts/traffic?preset=7d&node_ids=" + node_id)
    assert week.json()["data"]["range"]["bucket"] == "hour"
    week_rx = [value for value in week.json()["data"]["datasets"][0]["data"] if value]
    assert len(week_rx) >= 4


def _label_gaps(labels: list[str]) -> list[float]:
    moments = [datetime.fromisoformat(item.replace("Z", "+00:00")) for item in labels]
    return [(moments[index + 1] - moments[index]).total_seconds() for index in range(len(moments) - 1)]


def test_chart_points_get_coarser_as_the_range_grows(client, admin, monkeypatch):
    from app.utils.time import as_utc, local_hour_start, utcnow
    import app.services.ingest as ingest

    monkeypatch.setattr(ingest, "_checked_time", lambda collected_at: (as_utc(collected_at), utcnow()))
    created = _create_node(client, "coarse")
    node_id = created["node_id"]
    key = created["node_key"]
    anchor = local_hour_start(datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=timezone.utc)
    first = anchor + timedelta(minutes=55)
    second = anchor + timedelta(hours=1, minutes=4)
    assert _ingest(client, node_id, key, first, 0, 0, "nonce-coarse-0001", cpu=10).status_code == 200
    assert _ingest(client, node_id, key, first + timedelta(seconds=30), 2_400, 0, "nonce-coarse-0002", cpu=40).status_code == 200
    assert _ingest(client, node_id, key, second, 2_400, 0, "nonce-coarse-0003", cpu=15).status_code == 200
    assert _ingest(client, node_id, key, second + timedelta(seconds=30), 3_000, 0, "nonce-coarse-0004", cpu=55).status_code == 200

    fine = client.get(f"/api/v1/charts/traffic?preset=6h&node_ids={node_id}").json()["data"]
    assert fine["range"]["bucket"] == "5min"
    assert sorted(value for value in fine["datasets"][0]["data"] if value) == [600, 2_400]

    week = client.get(f"/api/v1/charts/traffic?preset=7d&node_ids={node_id}").json()["data"]
    assert week["range"]["bucket"] == "hour"
    assert len(week["labels"]) in {167, 168}
    assert set(_label_gaps(week["labels"])) == {3600}
    assert sorted(value for value in week["datasets"][0]["data"] if value) == [600, 2_400]
    cpu = client.get(f"/api/v1/charts/resources?preset=7d&node_ids={node_id}").json()["data"]
    assert cpu["range"]["bucket"] == "hour"
    assert sorted(value for value in cpu["datasets"][0]["data"] if value is not None) == [40, 55]

    month = client.get("/api/v1/charts/traffic?preset=30d").json()["data"]
    assert month["range"]["bucket"] == "day"
    assert len(month["labels"]) == 30
    assert set(_label_gaps(month["labels"])) == {86400}
    assert client.get("/api/v1/charts/traffic?preset=24h").json()["data"]["range"]["bucket"] == "5min"
    assert len(client.get("/api/v1/charts/traffic?preset=24h").json()["data"]["labels"]) == 288
    assert client.get("/api/v1/charts/traffic?preset=yesterday").json()["data"]["range"]["bucket"] == "5min"
    assert client.get("/api/v1/charts/traffic?preset=month").json()["data"]["range"]["bucket"] == "day"

    now = datetime.now(timezone.utc)
    custom_hour = client.get(
        "/api/v1/charts/traffic",
        params={"from": _stamp(now - timedelta(hours=2)), "to": _stamp(now)},
    ).json()["data"]
    assert custom_hour["range"]["bucket"] == "5min"
    assert len(custom_hour["labels"]) == 24
    custom_week = client.get(
        "/api/v1/charts/traffic",
        params={"from": _stamp(now - timedelta(days=3)), "to": _stamp(now)},
    ).json()["data"]
    assert custom_week["range"]["bucket"] == "hour"
    assert len(custom_week["labels"]) in {71, 72}
    custom_month = client.get(
        "/api/v1/charts/traffic",
        params={"from": _stamp(now - timedelta(days=10)), "to": _stamp(now)},
    ).json()["data"]
    assert custom_month["range"]["bucket"] == "day"
    assert len(custom_month["labels"]) == 10


def test_five_minute_chart_keeps_one_bucket_and_uses_the_latest_cpu(client, admin):
    created = _create_node(client, "bucket")
    node_id = created["node_id"]
    key = created["node_key"]
    moment = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    anchor = moment.replace(minute=(moment.minute // 5) * 5)
    if (datetime.now(timezone.utc) - anchor).total_seconds() < 45:
        anchor -= timedelta(minutes=5)
    assert _ingest(client, node_id, key, anchor + timedelta(seconds=5), 1_000, 1_000, "nonce-bucket-0001", cpu=10).status_code == 200
    assert _ingest(client, node_id, key, anchor + timedelta(seconds=35), 1_600, 1_250, "nonce-bucket-0002", cpu=30).status_code == 200
    chart = client.get(f"/api/v1/charts/traffic?preset=1h&node_ids={node_id}")
    assert chart.status_code == 200, chart.text
    body = chart.json()["data"]
    assert body["range"]["preset"] == "1h"
    assert body["range"]["bucket"] == "5min"
    assert len(body["labels"]) == 12
    assert [value for value in body["datasets"][0]["data"] if value] == [600]
    assert [value for value in body["datasets"][1]["data"] if value] == [250]
    resources = client.get(f"/api/v1/charts/resources?preset=1h&node_ids={node_id}").json()["data"]
    cpu = [value for value in resources["datasets"][0]["data"] if value is not None]
    assert resources["datasets"][0]["label"] == "CPU"
    assert cpu == [30]
    six = client.get("/api/v1/charts/traffic?preset=6h")
    assert six.status_code == 200
    assert len(six.json()["data"]["labels"]) == 72
    missing = client.get("/api/v1/charts/traffic")
    assert missing.json()["data"]["range"]["preset"] == "1h"


def test_restart_keeps_data(client, admin, tmp_path, monkeypatch):
    created = _create_node(client, "persistent")
    from app.db.database import db

    if not db.is_closed():
        db.close()
    from app.factory import create_app
    from fastapi.testclient import TestClient

    application = create_app(enable_scheduler=False)
    with TestClient(application) as again:
        assert User.select().count() == 1
        login = again.post("/api/v1/auth/login", json={"username": "admin", "password": "S3cure-Test-Pass"})
        again.headers["X-CSRF-Token"] = login.json()["data"]["csrf_token"]
        listed = again.get("/api/v1/nodes")
        assert listed.json()["data"][0]["node_id"] == created["node_id"]
        assert again.get("/health").json()["ok"] is True


def test_wrong_keys_do_not_spend_the_node_budget(client, admin):
    created = _create_node(client, "budget")
    saved = client.put("/api/v1/settings", json={"values": {"ingest_rate_limit": 10}})
    assert saved.status_code == 200, saved.text
    node_id = created["node_id"]
    key = created["node_key"]
    moment = datetime.now(timezone.utc)
    for index in range(3):
        rejected = client.post(
            "/api/v1/ingest/metrics",
            json=_payload(moment - timedelta(seconds=40 - index), f"nonce-wrong-key-{index:04d}", 10, 10),
            headers=_headers(node_id, "nk_wrong-key-value", f"nonce-wrong-key-{index:04d}"),
        )
        assert rejected.status_code == 401
    accepted = []
    for index in range(10):
        accepted.append(
            _ingest(
                client,
                node_id,
                key,
                moment - timedelta(seconds=30 - index),
                100 + index,
                100,
                f"nonce-budget-{index:04d}",
            )
        )
    blocked = _ingest(client, node_id, key, moment + timedelta(seconds=5), 200, 160, "nonce-budget-over")
    assert [item.status_code for item in accepted] == [200] * 10
    assert blocked.status_code == 429


def test_new_node_can_join_a_group(client, admin):
    group = client.post("/api/v1/groups", json={"name": "edge", "color": "#0f6e6b"}).json()["data"]
    created = client.post("/api/v1/nodes", json={"name": "web", "group_ids": [group["id"]]})
    assert created.status_code == 201, created.text
    assert created.json()["data"]["groups"] == [{"id": group["id"], "name": "edge", "color": "#0f6e6b"}]
    bare = client.post("/api/v1/nodes", json={"name": "bare"})
    assert bare.status_code == 201
    assert bare.json()["data"]["groups"] == []


def test_removed_node_keeps_its_history(client, admin):
    created = _create_node(client, "kept")
    node_id = created["node_id"]
    key = created["node_key"]
    group = client.post("/api/v1/groups", json={"name": "kept-group", "color": "#0f6e6b"}).json()["data"]
    assert client.post(f"/api/v1/groups/{group['id']}/members", json={"node_ids": [node_id]}).status_code == 200
    now = datetime.now(timezone.utc)
    assert _ingest(client, node_id, key, now - timedelta(seconds=30), 1_000, 400, "nonce-kept-0000001").status_code == 200
    assert _ingest(client, node_id, key, now, 1_600, 700, "nonce-kept-0000002").status_code == 200
    removed = client.delete(f"/api/v1/nodes/{node_id}")
    assert removed.status_code == 200, removed.text
    listed = client.get("/api/v1/nodes").json()["data"]
    assert node_id not in [item["node_id"] for item in listed]
    assert client.get(f"/api/v1/nodes/{node_id}").status_code == 404
    assert client.get(f"/api/v1/nodes/{node_id}/stats?preset=1h").status_code == 404
    dashboard = client.get("/api/v1/dashboard/summary?preset=1h").json()["data"]
    assert dashboard["traffic"]["has_data"] is False
    assert dashboard["nodes"]["total"] == 0
    assert dashboard["top_nodes"] == []
    group_stats = client.get(f"/api/v1/groups/{group['id']}/stats?preset=1h").json()["data"]
    assert group_stats["traffic"]["has_data"] is False
    assert group_stats["members"] == []
    assert group_stats["ranking"] == []
    assert group_stats["counts"]["total"] == 0
    assert _ingest(client, node_id, key, now + timedelta(seconds=30), 2_000, 800, "nonce-kept-0000003").status_code == 401
    assert client.post(f"/api/v1/nodes/{node_id}/enable").status_code == 409


def test_node_program_body_is_accepted(client, admin):
    import importlib.util

    source = Path(__file__).resolve().parents[2] / "TrafficNode" / "node.py"
    spec = importlib.util.spec_from_file_location("traffic_node_contract", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    created = _create_node(client, "contract")
    body, nonce = module.build_body(
        {
            "cpu_percent": 12.5,
            "ram_total_bytes": 2_000_000,
            "ram_used_bytes": 500_000,
            "ram_free_bytes": 1_500_000,
            "disk_total_bytes": 8_000_000,
            "disk_used_bytes": 1_000_000,
            "disk_free_bytes": 7_000_000,
            "load_1": 0.1,
            "load_5": 0.2,
            "load_15": 0.3,
            "uptime_seconds": 90,
            "os_name": "Debian GNU/Linux 12",
            "kernel_version": "6.8.0",
            "interfaces": [
                {
                    "name": "eth0",
                    "rx_bytes": 1000,
                    "tx_bytes": 400,
                    "rx_packets": 3,
                    "tx_packets": 2,
                    "rx_errors": 0,
                    "tx_errors": 0,
                    "rx_dropped": 0,
                    "tx_dropped": 0,
                }
            ],
        }
    )
    response = client.post(
        "/api/v1/ingest/metrics",
        json=body,
        headers=_headers(created["node_id"], created["node_key"], nonce),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["duplicate"] is False
    listed = client.get(f"/api/v1/nodes/{created['node_id']}")
    assert listed.json()["data"]["status"] == "online"
    assert listed.json()["data"]["agent_version"] == body["agent_version"]
