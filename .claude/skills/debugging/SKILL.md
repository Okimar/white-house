---
name: debugging
description: How to investigate failures in Tavern - logs, sandbox runs, fake LLM, database inspection, common startup errors. Use when the server misbehaves, a request fails, or a test fails for unclear reasons.
---

# Debugging

## First steps

1. Reproduce on a throwaway data dir, never on the user's real `data/`:
   ```bash
   python -m tavern --sandbox --no-browser          # temp copy of data/, deleted on exit
   python -m tavern --data-dir /tmp/tv --no-browser # completely fresh
   ```
2. Turn on debug logs: `log_level = "debug"` in the data dir's `config.toml`.
3. Read the log: `data/logs/tavern.log` (rotates at 1 MB, 3 files). Unhandled request errors
   are logged with a traceback; the client only sees `{"error": {"code": "internal"}}`.
4. Run the health check: `python -m tavern check` (schema version, SQLite integrity).

## Reproduce in a test

Fastest loop: write a failing test in `tests/integration/` using the `client` fixture (see
write-tests skill), then run `python -m pytest path::test_name -x -vv`. Keep the test as the
regression test once fixed.

## LLM issues

Point a connection at the fake server to separate app bugs from provider bugs:

```bash
python -m tavern.devtools.fake_llm --port 5001 --delay 0.05
# OpenAI-compatible base URL: http://127.0.0.1:5001/v1
```

## Database

```bash
python -c "import sqlite3; c=sqlite3.connect('data/tavern.sqlite3'); print(c.execute('PRAGMA user_version').fetchone())"
```

Only read. To undo a bad change: stop the server, `python -m tavern backup list`, then
`python -m tavern backup restore NAME` (it snapshots the current state first).

## Common errors

| Message | Cause | Fix |
|---|---|---|
| `Data directory ... is in use by another Tavern process` | second instance on the same data dir | stop the other one; the lock is released when its process dies |
| `Database schema vN is newer than this app supports` | data from a newer Tavern | update the code or restore an older backup |
| `Migration NNNN_x failed and was rolled back` | bad SQL in a migration | fix the migration (only if uncommitted) and restart; db is unchanged |
| `config.toml: invalid TOML` / `server.port: expected int` | config typo | fix the named key; delete the file to get defaults |
| HTTP 403 `bad_host` | Host header is not loopback while remote access is off | open via `127.0.0.1`/`localhost`, or enable remote access |
| HTTP 403 `bad_origin` | POST from another site's page | expected CSRF protection |
| HTTP 401 `unauthorized` | remote client without token | open `/login?token=...` once (`python -m tavern token`) |

## Termux specifics

- Server killed in background: run via `./start.sh` (takes `termux-wake-lock`); disable battery
  optimisation for Termux.
- `pip` tries to compile something: a dependency lost its pure-Python wheel. Check with
  `pip install --dry-run --only-binary=:all: -r requirements.txt`.
