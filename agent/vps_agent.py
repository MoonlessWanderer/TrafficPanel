#!/usr/bin/env python3
"""Agent لینوکس. فقط داده می‌خواند و به پنل می‌فرستد؛ شل راه دور ندارد."""

from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AGENT_VERSION = "1.0.0"


def parse_cpu_counters(text: str) -> tuple[int, int] | None:
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = [int(item) for item in line.split()[1:]]
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
            return idle, sum(parts)
    return None


def cpu_percent(previous: tuple[int, int] | None, current: tuple[int, int]) -> float | None:
    if previous is None:
        return None
    idle_delta = current[0] - previous[0]
    total_delta = current[1] - previous[1]
    if total_delta <= 0:
        return None
    used = 100.0 * (1 - (idle_delta / total_delta))
    return max(0.0, min(100.0, used))


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        number = raw.strip().split()[0]
        values[key] = int(number) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    return {
        "total": total,
        "free": available,
        "used": max(total - available, 0),
    }


def parse_loadavg(text: str) -> tuple[float, float, float]:
    parts = text.split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def parse_uptime(text: str) -> float:
    return float(text.split()[0])


def parse_os_release(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return "Linux"


def parse_net_dev(text: str) -> list[dict]:
    interfaces = []
    for line in text.splitlines():
        if ":" not in line or line.strip().startswith("Inter") or line.strip().startswith("face"):
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        columns = rest.split()
        if len(columns) < 16:
            continue
        interfaces.append(
            {
                "name": name,
                "rx_bytes": int(columns[0]),
                "rx_packets": int(columns[1]),
                "rx_errors": int(columns[2]),
                "rx_dropped": int(columns[3]),
                "tx_bytes": int(columns[8]),
                "tx_packets": int(columns[9]),
                "tx_errors": int(columns[10]),
                "tx_dropped": int(columns[11]),
            }
        )
    return interfaces


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        if key.startswith("VPS_AGENT_"):
            values[key.removeprefix("VPS_AGENT_")] = value
    return values


def build_payload(snapshot: dict, collected_at: datetime, nonce: str) -> dict:
    memory = snapshot["memory"]
    disk = snapshot["disk"]
    load = snapshot["load"]
    return {
        "nonce": nonce,
        "collected_at": collected_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "cpu_percent": snapshot["cpu_percent"],
        "ram_total_bytes": memory["total"],
        "ram_used_bytes": memory["used"],
        "ram_free_bytes": memory["free"],
        "disk_total_bytes": disk["total"],
        "disk_used_bytes": disk["used"],
        "disk_free_bytes": disk["free"],
        "load_1": load[0],
        "load_5": load[1],
        "load_15": load[2],
        "uptime_seconds": snapshot["uptime"],
        "agent_version": AGENT_VERSION,
        "os_name": snapshot["os_name"],
        "kernel_version": snapshot["kernel"],
        "interfaces": snapshot["interfaces"],
    }


def post_metrics(url: str, node_id: str, node_key: str, payload: dict, *, verify_tls: bool) -> dict:
    nonce = payload["nonce"]
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/v1/ingest/metrics",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"vps-monitor-agent/{AGENT_VERSION}",
            "X-Node-Id": node_id,
            "X-Node-Key": node_key,
            "X-Node-Timestamp": str(int(time.time())),
            "X-Node-Nonce": nonce,
        },
    )
    context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, context=context, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"The panel rejected the request ({exc.code}): {detail}") from exc


def read_system(root: Path, previous_cpu: tuple[int, int] | None) -> tuple[dict, tuple[int, int] | None]:
    cpu_text = (root / "proc/stat").read_text(encoding="utf-8")
    current = parse_cpu_counters(cpu_text)
    percent = cpu_percent(previous_cpu, current) if current else None
    if percent is None and current is not None and previous_cpu is None:
        time.sleep(0.2)
        current = parse_cpu_counters((root / "proc/stat").read_text(encoding="utf-8"))
        percent = cpu_percent(parse_cpu_counters(cpu_text), current) if current else 0.0
    memory = parse_meminfo((root / "proc/meminfo").read_text(encoding="utf-8"))
    load = parse_loadavg((root / "proc/loadavg").read_text(encoding="utf-8"))
    uptime = parse_uptime((root / "proc/uptime").read_text(encoding="utf-8"))
    interfaces = parse_net_dev((root / "proc/net/dev").read_text(encoding="utf-8"))
    os_release = root / "etc/os-release"
    os_name = parse_os_release(os_release.read_text(encoding="utf-8")) if os_release.is_file() else platform.system()
    usage = shutil.disk_usage(root if root != Path("/") else "/")
    # وقتی root خود سیستم است، ظرفیت دیسک / خوانده می‌شود.
    if root == Path("/"):
        usage = shutil.disk_usage("/")
    snapshot = {
        "cpu_percent": 0.0 if percent is None else round(percent, 2),
        "memory": memory,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "load": load,
        "uptime": uptime,
        "interfaces": interfaces,
        "os_name": os_name[:128],
        "kernel": platform.release()[:128],
    }
    return snapshot, current


def main() -> None:
    config_path = Path(os.environ.get("VPS_AGENT_CONFIG", "/etc/vps-monitor/agent.env"))
    config = load_config(config_path)
    panel = config.get("PANEL_URL", "").rstrip("/")
    node_id = config.get("NODE_ID", "")
    node_key = config.get("NODE_KEY", "")
    if not panel or not node_id or not node_key:
        raise SystemExit("PANEL_URL, NODE_ID, and NODE_KEY are required in the agent config.")
    interval = max(int(config.get("INTERVAL", "30")), 5)
    verify_tls = config.get("VERIFY_TLS", "1") not in {"0", "false", "False"}
    previous = None
    while True:
        started = time.monotonic()
        snapshot, previous = read_system(Path("/"), previous)
        if not snapshot["interfaces"]:
            print("No network interface other than loopback was found.")
        else:
            nonce = secrets.token_urlsafe(24)
            payload = build_payload(snapshot, datetime.now(timezone.utc), nonce)
            try:
                result = post_metrics(panel, node_id, node_key, payload, verify_tls=verify_tls)
                print(f"sent duplicate={result.get('data', {}).get('duplicate')}")
            except Exception as exc:
                print(f"send failed: {exc}")
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval - elapsed))


if __name__ == "__main__":
    main()
