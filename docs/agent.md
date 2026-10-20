# Agent install

The agent runs on the VPS and sends outbound requests to the panel. The VPS does not need an inbound port open.

## Install

1. Create a node in the panel and save the node id and key immediately. The key is not shown again.
2. Place `agent/vps_agent.py` at `/opt/vps-monitor/vps_agent.py`.
3. Create `/etc/vps-monitor/agent.env` from `agent/agent.env.example` and restrict it with `chmod 600`.
4. Copy `deploy/vps-monitor-agent.service` to `/etc/systemd/system/`.
5. Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vps-monitor-agent
```

The agent still reads `/etc/vps-monitor/agent.env`. That file is on the VPS. It is separate from the panel `config.py`.

## Behavior

Every `INTERVAL` seconds (default 30) the agent reads:

- CPU from two samples of `/proc/stat`
- RAM from `MemTotal` and `MemAvailable`
- root disk capacity, used space, and free space
- load average and uptime
- `/proc/net/dev` counters except `lo`
- operating system name, kernel, and agent version

Leave `VERIFY_TLS=1` unless you are testing against an invalid certificate.

## Key rotation

Use Rotate key in the panel, put the new key in `agent.env`, and restart the agent service once. The previous key is revoked immediately.
