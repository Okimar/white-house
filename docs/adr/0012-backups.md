# 0012. Backups of the whole data folder, plugin versions, the doctor

Status: accepted (stage 9а, 2026-11-01)

## Context

Before stage 9 there were only database snapshots taken before a migration and by hand from the
command line. Pictures, themes and plugins were not in them, nothing ran on a schedule, and
restoring needed the server stopped. The owner's decisions are in ROADMAP «Этап 9: решения владельца».

## Decision

1. **A copy is one zip** (`tavern-<UTC stamp>-<kind>[-reason].zip`): `manifest.json` (format,
   kind, reason, time, app version, schema version, fingerprint), `tavern.sqlite3` (SQLite backup
   API, consistent while the app writes), `assets/`, `themes/`, `plugins/`. Not included:
   `secrets.json` (a copy can be handed over), `config.toml` (machine settings: host, port), logs,
   `.versions` folders (undo history) and caches. Pictures are stored without compression.
   The file is written as `.part` and renamed, so a failure (a full disk) never leaves a torn copy.
2. **Kinds and keeping.** start, daily, hourly, risky are pruned to their own counts; manual
   and upload copies are never pruned. Old database-only snapshots (`*.sqlite3`, before a
   migration) stay listable and restorable.
3. **No change, no copy.** The fingerprint hashes the database snapshot without its 100-byte header
   (change counters move without changed content) plus each data file's name, size and mtime.
4. **Triggers.** A scheduler thread (started by `serve`, never in `--sandbox`) takes the start
   copy and then checks once a minute for the daily (after an hour) and every-N-hours copies.
   Risky actions call `backups.before(reason)` from their route handlers, off the event loop:
   deleting a character, chat or lorebook, importing a character, chat or lorebook, replacing or
   rolling back a plugin, a doctor fix, and a restore. A failed copy never blocks the action. It
   is kept in `last_error` and shown in the page's warning line.
5. **Own folder.** Setting `backups.dir` is the only place Tavern writes outside the data folder.
   If the folder is unavailable, the copy goes to `data/backups` with a warning. A sandbox ignores it.
6. **Live restore.** It refuses while a reply is being written and checks the archive first:
   zip CRC, paths stay inside, database integrity, and a schema no newer than ours. Then it takes
   a forced copy of the current state, stops the plugins, and moves the three folders aside before
   moving the new ones in. Next it loads the database into the live connection (backup API), runs
   migrations, keeps the current backup settings and starts the plugins. Any failure moves the old
   folders back and reloads the database snapshot taken just before. The page reloads on the
   notice `restored`.
7. **Plugin versions.** Replacing a user plugin keeps the old folder and its approval in
   `data/plugins/.versions/<id>/<stamp>/` plus `<stamp>.json` (last 3). A rollback keeps the
   current one the same way. It is approved again only if its files hash to the approval stored
   with it. A plugin whose `setup()` fails at startup is switched off and its error starts with
   «Выключено само».
8. **Doctor.** `quick()` runs at startup (quick_check, disk space, own folder, plugins);
   `full()` adds integrity_check, foreign keys, lost pictures (`/assets/<name>` mentioned in the
   database without a file), broken themes, copy age and leftovers. Safe fixes only, after a copy:
   leftovers, rows pointing at deleted rows, a broken active theme, a fresh copy. A damaged
   database is never repaired in place: the answer is a restore.

## Consequences

- Each risky action costs one archive. With a big picture library that is seconds, done in a
  worker thread. The fingerprint skips it when nothing changed.
- Restoring old copies works across schema versions forward (migrations), never backward.
- 9б builds on the same archives and the `upload` kind (see below).

## 9б: copies on Google Drive (2026-11-02)

1. **The user's own OAuth client** («Desktop app»), made by a guide in the «Копии» tab; scope
   `drive.file` (Tavern sees only files it created, no Google review needed). The guide tells to
   publish the app: a «Testing» app loses its sign-in every 7 days.
2. **Sign-in:** authorization code with PKCE and state; redirect `http://127.0.0.1:<port>/`, which
   Desktop clients accept on any port. The page there hands the address to the server. When Tavern
   is opened from another device the redirect fails there, and the user pastes the address.
3. **Secrets** (client id, secret, refresh token) live in `secrets.json` under `gdrive`; machine state
   (auto, account, folder, since, local name -> Drive id, an open upload session, free space) in
   `data/gdrive.json`. Neither is in a copy, so a restore keeps the sign-in. A sandbox uses
   `gdrive_sandbox` and `gdrive-sandbox.json`: it never sees the real account.
4. **What goes up:** every copy made after sign-in (owner's choice), oldest first, by a worker thread
   that every new copy wakes. Nothing is ever deleted on Drive; a copy deleted there by hand is not
   sent again. Resumable upload in 8 MiB chunks; the session is kept, so a broken upload resumes
   after a restart. A failure pauses the queue 1, 2, 4… minutes up to an hour.
5. **Back from Drive:** a download becomes a local `upload` copy already marked as on Drive; restore
   is the 9а restore of it.
6. `services/gdrive.py`; routes `/api/backups/gdrive…` (writes closed to the assistant);
   `devtools/fake_gdrive.py` is a fake Google for tests and for manual runs with `TAVERN_GDRIVE_URL`.
   No new dependency: plain `httpx`.
