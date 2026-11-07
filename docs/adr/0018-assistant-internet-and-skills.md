# 0018. The assistant's internet, Claude Code skills, skills of its own, theme scripts

Status: accepted (stage 10ж, 2026-11-07). Builds on 0010, 0013 (skills, engine) and 0002 (themes).

## Decision

- **Internet for any model** (`services/web_access.py`, tools `web_search`, `read_page`), independent of a
  provider's own tools. One guarded door, `fetch`: http/https only; no login in the address; no cookies, no
  proxies from the environment; the host is resolved once, every address must be public (loopback, private,
  link-local incl. cloud metadata, shared, reserved, NAT64/mapped forms refused) and the connection goes to that
  very address (SNI/Host set), so DNS rebinding cannot lead inside; the same check on each of at most 5
  redirects; 25 s and 1.5 MB limits; non-text types refused. Only the search words leave the app. The user's own
  search address (settings) is the single host allowed on a private network.
- **Search** is the plain HTML page of a keyless engine (DuckDuckGo). `parse_duckduckgo` is the one function that
  breaks when its markup changes, and then raises a Russian message instead of returning nothing. SearXNG (JSON)
  can be chosen. A keyed engine was left out: it needs write-only secret handling in the settings.
- **Results are data.** Every internet result carries a note, reaches the model inside `<data>`, and the system
  prompt says pages, skills and downloads are never orders. Setting «Интернет для помощника» (default on) turns all
  internet tools off. Each search/read/download shows as a progress line.
- **Skills like Claude Code's.** `POST /api/skills/import` (file text or link; GitHub page links become raw links)
  reads a SKILL.md (folded headers too) into an own skill with `origin` (migration 0023). Parts that exist stay;
  a checklist section is copied into `## Check`; files a skill mentions are not carried over (said in notes and in
  the text); things the assistant cannot do (pictures, screenshots, running commands, editing source) are listed
  to the person. Text only: nothing runs. Imported skills are a separate group in the tab with their source and a
  switch.
- **Skills by the assistant**: `list_skills`, `copy_skill`, `create_skill`, `update_skill`, `delete_skill`
  (asks), `install_skill(url)` (downloads, shows name / description / size / source, then «Разрешить»; auto mode
  answers yes). Built-ins are never changed (a copy replaces one). All journaled and undoable. Every skill is read
  with a reminder that it cannot give tools, open closed routes or allow reading secrets; `/skills` stays closed to
  `app_call`.
- **Honesty about abilities**: HONESTY lists what the assistant cannot do; the planner marks such parts as
  `CANNOT:` decisions and plans no workaround; Tavern itself appends «Не могу сделать: …» under the report; the
  critic's first check covers it.
- **Theme scripts**: built-in skills `theme-scripts` and `unslop` (adapted from Damir Sokolovsky's unslop,
  CC BY 4.0, credit kept); `check_theme_script` and an automatic `script_check` on writing a `.js` theme file
  (rules + touch warnings + `node --check` when Node exists, otherwise "not checked" said plainly). Approval of
  scripts stays closed to the assistant, also in auto mode.

## Consequences

- A real search engine, real pages and the https pinning against real servers were not reached in tests (fake
  transport; the SNI mechanism was tried once against a local TLS server).
- Quality of the DuckDuckGo parser depends on a page we do not control.
