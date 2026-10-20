# Install and run

## Server

On Debian or Ubuntu, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo -E bash
```

Run that command again whenever you need the panel. The menu is where you install, update, start, stop, restart, read logs, set the domain and certificate, change the port, check status, or uninstall.

Installation installs packages first, then asks for the directory and the first admin account. Leaving the directory empty installs `traffic-panel` in the current directory, usually `/root/traffic-panel`. The panel listens on port 443 without asking for a domain. It is not installed under `/opt`. Code, the virtual environment, `config.py`, the database, logs, the service file, and the Nginx site file all stay in that directory. Systemd and Nginx only receive a link, because those programs look in their own directories. A Let's Encrypt certificate stays in `/etc/letsencrypt`, where Certbot renews it. If a parent directory such as `/root` blocks the service user, the installer adds execute permission for others on that parent so the service can enter the panel directory.

Menu, in order:

1. Install
2. Update
3. Start
4. Stop
5. Restart
6. View logs
7. Domain and SSL
8. Change port
9. Status
10. Uninstall

Domain and SSL installs Nginx, requests a certificate, and keeps the panel on localhost. Port 80 must reach the server, and the domain must point at it. Changing the port changes the public port. With SSL, the application itself stays on an internal port.

`sudo` without `-E` drops `http_proxy` and `https_proxy`. The installer also reads those variables from `/etc/environment` and from `Acquire::http::Proxy` and `Acquire::https::Proxy` in apt configuration, then passes them to downloads and pip.

Update downloads the current `main` branch, installs Python dependencies again, and restarts the service. The saved `config.py` and the database stay in place. New settings added by the project keep their default until you change them.

Uninstall asks you to type `DELETE`. It then stops the service, deletes the install directory (including the database and logs), removes the Nginx site, deletes the certificate issued for the configured domain, and removes the `trafficpanel` user. It does not uninstall Nginx, Certbot, or Python, because other sites may use them.

The same commands work from a checkout:

```bash
sudo -E bash install.sh
sudo -E bash install.sh update
sudo -E bash install.sh uninstall
```

Non-interactive install:

```bash
sudo -E bash install.sh install --admin admin --password 'a-long-password' --port 443
```

## Requirements

- Python 3.9 or newer, including the venv module (`apt install python3-venv` on Debian or Ubuntu if it is missing)
- Write access to the project directory for `data/` and `logs/`

## Steps

1. Install dependencies: `python3 -m pip install -r requirements.txt`
2. Set `SECRET_KEY` in `config.py` to a random string of at least 32 characters.
3. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` for the first run only. The password must be at least 10 characters.
4. Start the panel: `python3 main.py`

On Windows, use `py -3 main.py` when `python3` is not on PATH.

If an admin already exists, a later start does not create another user. Data stays in SQLite.

## Settings

Startup values are assignments in `config.py`: address, port, database path, secret, first admin, TLS, and log level. The panel does not read a `.env` file.

Other settings, such as online thresholds, alerts, retention, and timezone, are stored in the database and changed from the Settings page. Starting the process again does not overwrite saved values.

In development, an empty `SECRET_KEY` creates `data/.secret_key`. In `ENV=production` the process stops when that key is missing or shorter than 32 characters.

Changes in `config.py` apply after a restart. Use Restart panel on the Settings page, or stop and start the process yourself.

## Health

- `GET /health`
- `GET /api/v1/health`

A successful response means the database answered `SELECT 1`.

## Tests

```bash
python3 -m pytest
```
