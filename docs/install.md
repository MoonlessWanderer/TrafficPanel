# Install and run

## Server

On Debian or Ubuntu, as root:

```bash
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo -E bash
```

The script uses the server's `python3` when it is Python 3.9 or newer and can create a virtual environment. It asks for an install directory. Leaving that answer empty installs `traffic-panel` in the directory where you ran the command, usually `/root/traffic-panel`. The panel is not installed under `/opt`. It starts the `traffic-panel` service. Code, the virtual environment, `config.py`, the database, logs, the service file, and the Nginx site file all stay in that directory. Systemd and Nginx only receive a link, because those programs look in their own directories. A Let's Encrypt certificate stays in `/etc/letsencrypt`, where Certbot renews it. If a parent directory such as `/root` blocks the service user, the installer adds execute permission for others on that parent so the service can enter the panel directory.

`sudo` without `-E` drops `http_proxy` and `https_proxy`. The installer also reads those variables from `/etc/environment` and from `Acquire::http::Proxy` and `Acquire::https::Proxy` in apt configuration, then passes them to git and pip.

- Leave the domain empty to listen on port 8080 without TLS.
- Enter a domain and an email to install Nginx and request a Let's Encrypt certificate. Port 80 must reach the server, and the domain must point at it.

Update and remove:

```bash
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo -E bash -s -- update
curl -fsSL https://raw.githubusercontent.com/MoonlessWanderer/TrafficPanel/main/install.sh | sudo -E bash -s -- uninstall
```

Update fetches the current `main` branch, installs Python dependencies again, and restarts the service. The saved `config.py` and the database stay in place. New settings added by the project keep their default until you change them.

Uninstall asks you to type `DELETE`. It then stops the service, deletes the install directory (including the database and logs), removes the Nginx site, deletes the certificate issued for the configured domain, and removes the `trafficpanel` user. It does not uninstall Nginx, Certbot, or Python, because other sites may use them.

The same commands work from a checkout:

```bash
sudo bash install.sh
sudo bash install.sh update
sudo bash install.sh uninstall
```

Non-interactive install:

```bash
sudo bash install.sh install --admin admin --password 'a-long-password' --domain panel.example.com --email you@example.com
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
