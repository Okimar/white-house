---
name: core-changes
description: Safe procedure for deep changes to Tavern's core - database schema and migrations, config, security, storage, startup, public APIs that plugins depend on. Use before editing anything under tavern/storage, tavern/security.py, tavern/config.py, tavern/context.py or the plugin API.
---

# Changing the core safely

The core is what plugins and user data depend on. A mistake here can lose chats or break every
plugin, so changes follow this procedure.

## Procedure

1. **Baseline.** `python -m pytest` must be green before you start. Note the commit:
   `git status` clean or your work stashed.
2. **Write down the contract.** Which behaviour changes, which stays. If plugins or stored data
   are affected, add an ADR in `docs/adr/NNNN-title.md` (context, decision, consequences).
3. **Tests first.** Add tests that pin the current behaviour you must keep, and failing tests
   for the new behaviour.
4. **Change in small steps**, running the relevant tests after each.
5. **Try it on real-shaped data** in a sandbox: `python -m tavern --sandbox`.
6. **Full suite** green, then update AGENTS.md (map, rules) and affected skills.
7. **Commit** in a focused commit. Rolling back code is `git revert`.

## Database schema

- New file `tavern/storage/migrations/NNNN_short_name.sql`, next number, contiguous.
- Never edit or renumber a committed migration; users have already applied it.
- One migration = one transaction together with `PRAGMA user_version`; on error everything
  rolls back. Do not put `BEGIN`/`COMMIT` in the file.
- Use `STRICT` tables, explicit `NOT NULL`, foreign keys with `ON DELETE` behaviour.
- Destructive changes (drop/rename column) use the SQLite 12-step pattern: create new table,
  copy, drop old, rename. Never lose data silently.
- Existing databases get an automatic snapshot before migrations run
  (`pre-migrate-vX-to-vY` in `data/backups`). Test the upgrade path, not just a fresh database:
  see `test_schema_upgrade_takes_backup_first` in `tests/integration/test_app.py`.

## Config

- Add a field with a default to the dataclass in `tavern/config.py`, add it with a comment to
  `DEFAULT_CONFIG_TOML`, validate ranges in `_validate`.
- `test_default_toml_matches_dataclass_defaults` fails if the two drift apart.
- Never rename or remove a key without accepting the old name for at least one release.

## Security (`tavern/security.py`)

Invariants, each covered by `tests/integration/test_access_control.py`:
- Loopback-only by default; non-loopback clients rejected.
- Host header must be loopback while remote access is off (DNS rebinding).
- Remote clients need the token; `remote_access = true` requires it from everyone.
- Cross-origin state-changing requests are rejected.
- Secrets (`data/secrets.json`) never appear in responses or logs.

Weakening any of these requires an ADR and explicit approval from the project owner.

## Public APIs (plugin API, HTTP API)

- Additive changes only within a major `api_version`. Removing or changing a signature needs a
  deprecation period and a version bump.
- Keep old behaviour working behind the new one until plugins migrate.

## Rollback options

| What broke | Undo |
|---|---|
| Code | `git revert <commit>` or `git checkout -- <file>` for uncommitted work |
| User database | stop server, `python -m tavern backup restore NAME` |
| Experiment data | it was in `--sandbox`, nothing to undo |
