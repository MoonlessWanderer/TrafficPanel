# VPS monitoring panel

A standalone panel for traffic and resource metrics from virtual servers. Each VPS connects with its own node id and key. The agent only sends data outward.

## Server

On Debian or Ubuntu, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo bash
```

The installer asks for the first admin account. Leave the domain empty to serve plain HTTP on port 8080. Enter a domain and an email to put Nginx in front and issue a Let's Encrypt certificate.

```bash
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo bash -s -- update
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo bash -s -- uninstall
```

Update keeps `config.py` and the SQLite database. Uninstall removes the service, program files, database, proxy site, and the certificate issued for this panel.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in `config.py` so the first admin can be created. Then:

```bash
python3 main.py
```

On Windows, if `python3` is not on PATH:

```bash
py -3 main.py
```

The panel listens on `http://127.0.0.1:8080` by default. Paths in `config.py` are relative to the project root.

`config.py` holds only startup assignments: environment, address, database path, secret, first admin, TLS, and logging. There is no `.env` file. Thresholds, retention, and timezone live in SQLite and are edited from the Settings page. After you change `config.py`, restart the panel from that page.

Install, agent, backup, and deploy notes are in `docs/`.
