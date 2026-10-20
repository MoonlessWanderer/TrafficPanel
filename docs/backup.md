# SQLite backup and restore

The default database is `data/panel.db`. In WAL mode, `panel.db-wal` and `panel.db-shm` are part of the database.

## Consistent backup

Use SQLite's backup command so a partial WAL is not copied:

```bash
sqlite3 data/panel.db ".backup 'data/reports/panel-backup.db'"
```

If `sqlite3` is not installed, stop the panel first and copy all three `panel.db*` files together. `data/reports` is the folder intended for those outputs.

Keep `data/.secret_key` and `SECRET_KEY` in `config.py` private and separate from the database copy. Without that secret, sessions stop working. Node keys are hashed with their own salt and do not depend on this file. Panel settings are rows in the same database and come back with the SQLite backup.

## Restore

1. Stop the panel.
2. Move the current `data/panel.db*` files aside.
3. Put the backup file in place as `data/panel.db`.
4. Start the panel again.

Starting the panel again creates any missing tables and does not wipe existing rows.
