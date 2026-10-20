# VPS monitoring panel

A standalone panel for traffic and resource metrics from virtual servers. Each server is registered as a node with its own id and key. The node program is a separate project.

## Server

On Debian or Ubuntu, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo -E bash
```

After this command, the menu is where you work: install, update, start, stop, restart, logs, domain and SSL, port, status, and uninstall. Installation installs packages before it asks for an admin account. The panel listens on port 443. Update keeps `config.py` and the database. Uninstall removes the service, program files, database, proxy site, and the certificate issued for this panel.

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

Install, backup, and deploy notes are in `docs/`.
