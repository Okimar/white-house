# ADR 0002: Themes, theme scripts and the page security policy

Status: accepted (stage 3а)

## Context

The owner wants the interface to be rebuilt from text requests to an agent: pictures, animations,
interactivity, characters assembled from parts. Themes must be shareable as one file (a public
catalogue is planned), so a theme may come from a stranger. The app must never break because of a
theme, and it must stay smooth on phones and tablets.

## Decision

| Question | Decision | Rejected and why |
|---|---|---|
| What a theme is | A folder: `theme.json`, `theme.css`, optional `script.js`, pictures, fonts, sounds. Built-in in `web/themes`, user themes in `data/themes` | Themes in the database: hard to edit by hand or by an agent, no relative URLs |
| How it restyles | CSS variables plus stable `data-slot` names on elements | Themes overriding app class names: breaks on every UI change |
| Effects | An ES module `script.js` running in the page with a small API (`scene`, `overlay`, events, `signal`) | Sandboxed iframe per theme: cannot animate the real chat elements, costs memory on phones; no scripts at all: no interactivity |
| Trust in scripts | A user theme's script runs only after the user approved its exact content (sha256 stored in settings). Any edit asks again. Built-in scripts are trusted | Trust per theme id: an update could swap in different code silently |
| Containment | Page-wide CSP: everything only from this server (`default-src 'self'`, `connect-src 'self'`). Theme files are served with `no-cache`, inert files with `sandbox` CSP. No HTML in themes. Paths validated against traversal; zip import limits size, file count and real read size | Allow-listing remote fonts/CDNs: lets a shared theme send data out |
| Failure | Script errors are caught (import, setup, later errors traced to the theme's URL): the script is stopped, its layers emptied, the user told. `/?safe` starts with no theme at all | Reload loop or blank page on a bad theme |
| History | Replacing, restoring or deleting a user theme first moves it to `data/themes/.versions/<id>/<stamp>` (last 5 kept) | Overwriting: the owner asked for every replacement to be undoable |
| Pictures | Image library in `data/assets`, types decided by content; generation through an OpenAI-compatible `/images/generations` connection chosen in settings | A separate image-provider registry: unnecessary while one API shape covers the services in use |

## Consequences

- An approved theme script can do anything the page can: read chats, call the API. It cannot read
  API keys (they never reach the browser) or send data to other hosts (CSP). The approval dialog
  says this plainly.
- The slot names and variables in `web/styles.css` are a public contract: rename only with a
  deprecation period, and update `.claude/skills/make-theme/SKILL.md`.
- The in-app assistant (stage 8) edits themes through the same file API (`PUT
  /api/themes/<id>/files/<path>`) and inherits the same limits.
