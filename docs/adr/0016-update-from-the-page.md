# ADR 0016: Updating from the page

Status: accepted

## Context

The app is installed by `get.sh` (a git clone) and updated by running it again. The owner wants a button in the
browser. An update replaces code that is running, may need new libraries, and can break the start.

## Decision

`services/updates.py` + `api/updates.py` (`GET /api/updates`, `POST /api/updates/check`, `POST /api/updates/apply`),
the block «Обновление программы» in Settings → Основное (`web/components/Updates.js`).

- **Only a fast-forward of the branch's upstream.** `git fetch`, then `git merge --ff-only @{u}`. Commands are
  fixed lists run without a shell; the page sends no parameters. Remote addresses are never shown or logged
  (git's messages are scrubbed of `user:token@`).
- **Refused with a plain Russian reason, nothing changed:** not a git install (with how to update by hand), no git
  program, detached HEAD, no upstream, no network, own edits in tracked files, diverged history, a reply or the
  assistant running.
- **Order:** whole-data copy first (no copy, no update); fast-forward; `pip install` when `requirements.txt` changed
  (the same call as the start scripts); a fresh interpreter must import the new code. Any failure after the merge
  puts the old commit back (`reset --hard`, safe because the tree was clean and the move a fast-forward).
- **Restart:** the process leaves with exit code 75 after the answer is sent; `start.sh` / `start.bat` (which set
  `TAVERN_SUPERVISED=1`) start it again with the same arguments, so the same address returns; the data lock is
  released by the normal shutdown. `TAVERN_RESTARTED=1` stops it opening a second browser tab. Started any other
  way (`python -m tavern`), the app just stops, and the window says so. The page waits for a different `boot` id
  and reloads itself. The scripts keep their loop inside one command so an update replacing the script file does
  not confuse the run in progress; in `start.bat` the loop is left with `goto`, because `exit /b` inside `for`
  loses the exit code.
- The first update by an app started with an old script (no loop) stops instead of restarting; one manual start
  fixes it for good.
- **Look:** the update changes `web/`, so the first start applies the look-parking of ADR 0015.
- **Security:** normal access control (loopback / token / Origin). The route is closed to the assistant
  (`ALWAYS_CLOSED`); plugins have no way to reach HTTP routes.

## Consequences

- Zip installs cannot use the button; the window says how to update.
- Installing libraries may fail without internet: the old version is put back and the data copy stays.
- A running assistant task that is only waiting for the person does not block an update; a running run does.
