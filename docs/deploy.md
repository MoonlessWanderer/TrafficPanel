# Production deploy

## Service

The supported install is `install.sh`. With no arguments it opens a menu. It keeps the program, database, configuration, service file, and Nginx site in one directory: `traffic-panel` inside the directory where you ran the installer, usually `/root/traffic-panel`, unless you choose another path outside `/opt`. A new install listens on port 443, runs one worker, and links the service into systemd.

```bash
sudo systemctl enable --now traffic-panel
```

`deploy/vps-monitor-panel.service` matches that layout if you install by hand. SQLite and the in-process scheduler are built for one worker.

## HTTPS

In `config.py`, set `ENV` to `production` and `COOKIE_SECURE` to `True`. Either:

- terminate TLS at a reverse proxy and keep the panel on `127.0.0.1`, or
- set `SSL_CERTFILE` and `SSL_KEYFILE` so Uvicorn serves TLS itself.

A node reports to the panel HTTPS address.

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
