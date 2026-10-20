# Production deploy

## Service

Edit `deploy/vps-monitor-panel.service` with the real project path and a non-root user. Keep a single process. SQLite and the in-process scheduler are built for one worker.

```bash
sudo systemctl enable --now vps-monitor-panel
```

## HTTPS

In `config.py`, set `ENV` to `production` and `COOKIE_SECURE` to `True`. Either:

- terminate TLS at a reverse proxy and keep the panel on `127.0.0.1`, or
- set `SSL_CERTFILE` and `SSL_KEYFILE` so Uvicorn serves TLS itself.

The agent must use the panel HTTPS address.

## Restart

The Settings page can replace the running process after a short delay so the HTTP response is sent first. Open connections drop for a moment. The same replacement is what `os.execl` does with the current Python and the original arguments.

## Logs

Logs rotate in `logs/panel.log`. The level is `LOG_LEVEL` in `config.py`. Passwords, keys, and tokens are masked in log lines. Sensitive actions are stored in `audit_log`.

## Retention

Retention is changed on the Settings page. Initial values:

- raw samples: 14 days
- hourly aggregates: 180 days
- daily aggregates: 730 days

Hourly and daily aggregates are written before raw rows are deleted. Older hours are not rebuilt from deleted samples.

## Health and access

Point a liveness check at `GET /health`. Limit database file access to the service user. The panel does not run remote commands or a shell on the VPS hosts.

## Known limit

Login and ingest rate limits live in this process. They are not shared across workers.
