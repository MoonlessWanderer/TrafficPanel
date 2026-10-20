# API

Every path is under `/api/v1`. Times are stored as UTC and returned as ISO 8601 with a `Z` suffix.

Success:

```json
{"ok": true, "data": {}}
```

Error:

```json
{"ok": false, "error": {"code": "invalid_credentials", "message": "..."}}
```

## Admin

The session uses an HttpOnly cookie. Changing requests also need the CSRF token in `X-CSRF-Token`.

- `POST /auth/login`
- `POST /auth/logout`
- `GET /auth/me`
- `GET|POST|PATCH|DELETE /nodes`
- `POST /nodes/{node_id}/enable|disable|rotate-key|revoke-key`
- `GET|POST|PATCH|DELETE /groups`
- `POST /groups/{id}/members`
- `DELETE /groups/{id}/members/{node_id}`
- `POST /groups/{id}/transfer`
- `GET /dashboard/summary`
- `GET /nodes/{node_id}/stats`
- `GET /nodes/{node_id}/samples`
- `GET /groups/{id}/stats`
- `GET /charts/traffic?mode=total|nodes|groups`
- `GET /charts/resources`
- `GET /compare/nodes`
- `GET /compare/groups`
- `GET /alerts?alert_status=open|resolved`
- `POST /alerts/{id}/resolve`
- `GET /export/traffic.csv`
- `GET|PUT /settings`
- `POST /system/restart`

Shared query filters: `preset`, `from`, `to`, `node_ids`, `group_ids`, `q`, `status`, `traffic`, `traffic_min`, `traffic_max`, `cpu_min`, `cpu_max`, `ram_min`, `ram_max`, `disk_min`, `disk_max`, `sort`, `page`, `page_size`.

`preset` is one of `1h`, `6h`, `24h`, `7d`, `30d`, `today`, `yesterday`, `month`, `custom`. When it is omitted, the range is the last hour. Today and month boundaries use the timezone stored in panel settings.

`POST /system/restart` needs an admin session and CSRF. It writes an audit row, then replaces the process after a short delay.

## Node reports

Headers:

- `X-Node-Id`
- `X-Node-Key`
- `X-Node-Timestamp` as Unix seconds
- `X-Node-Nonce` equal to the body `nonce`

Paths:

- `POST /ingest/heartbeat`
- `POST /ingest/metrics`

A sample with the same collection time is not calculated again and returns `duplicate: true`. Reusing a nonce for a new sample is rejected with status 409.
