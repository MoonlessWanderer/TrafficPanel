# Install and run

## Requirements

- Python 3.12
- Write access to the project directory for `data/` and `logs/`

## Steps

1. Install dependencies: `python3.12 -m pip install -r requirements.txt`
2. Set `SECRET_KEY` in `config.py` to a random string of at least 32 characters.
3. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` for the first run only. The password must be at least 10 characters.
4. Start the panel: `python3.12 main.py`

On Windows, use `py -3.12 main.py` when `python3.12` is not on PATH.

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
python3.12 -m pytest
```
