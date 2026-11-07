# ADR 0017: Regex rules

Status: accepted (stage 11)

## Context

SillyTavern users hide status blocks, clean the model's habits, strip thinking from the history and show
trackers as styled panels with "regex scripts". The owner: «РЕГЕКСОВ НЕТ! Это буквально одна из самых важных
функций.» Patterns in the wild are JavaScript regular expressions (`/pattern/flags`); our server is Python.
A rule can change what the model receives, and a screen rule can write HTML, so two risks come with it: a pattern
that never finishes, and markup from a file somebody sent.

Written from the public shape of exported script files and documentation, never from SillyTavern code. No real
ST file was available: the test fixtures are built by hand from the documented fields (see `formats/st_regex.py`).

## Decision

**A rule** (table `regex_scripts`, migration 0022): name, on/off, `find` (`/pattern/flags` or a plain pattern),
`replace` (`$1`, `$<name>`, `$&`, `$0`, `` $` ``, `$'`, `$$`, `{{match}}`; `{{char}}` and `{{user}}` in the plain text), `trim`
(strings cut out of what is inserted), `places` (user messages, model replies, the model's reasoning, lorebook entries), `when`
(`display` = only the screen, `prompt` = only what the model gets, `both`, `stored` = the saved text, once), min /
max depth (0 = newest message; not for lorebook entries), `run_on_edit`, `macros` in the pattern (none / raw / escaped),
`render_html`, order. Fields of an ST script we do not use are kept in `extra` and written back on export.
Depth counts the whole active path (messages hidden from the prompt and ones cut by the budget count; 0 = the newest message handed to the prompt builder). `/pattern/flags` splits at the LAST slash, so an unescaped `/` inside is fine. Slash commands (ST placement 3) have no equivalent in this app and are not applied; they survive
an import / export round trip. Messages with the role «Система» are never touched.

**Order.** Global rules, then the rules of the chat's preset, then the rules of every character in the chat (members, in
chat order); inside a list by position. The list can be reordered by the person.

**Scope and permission.** `global`, `character` (carried on the card as `data.extensions.regex_scripts`),
`preset` (in the preset file as `extensions.regex_scripts`). On import the scripts are moved into the table with
`approved = 0`; an unapproved rule does nothing anywhere until the person presses «Разрешить» for that card or
preset (once; per-owner button). Created by hand, through the assistant, or imported into the global list = allowed.
The assistant cannot press «Разрешить» (closed in `assistant_app`, like the approval of theme scripts). Export puts
the rules back into the card / preset (`extensions.regex_scripts`) next to the unknown keys we kept.
Deleting a character or a preset deletes its rules (database triggers); the doctor finds orphans and rules whose
pattern cannot run.

**JavaScript syntax on a Python engine.** One approach: translate (`core/regex.py`), do not reimplement the engine.
The translator walks the pattern and writes the JavaScript meaning in Python terms: `\d`, `\w`, `\s` are the
JavaScript sets (ASCII digits and word characters; the JavaScript whitespace list), `\b`/`\B` follow `\w`, `^`/`$`
are string ends (without `m`: `\A` / `\Z`, so `$` never matches before a final newline), with `m` they use the four
JavaScript line terminators, `.` stops at `\n \r    `, `[^]` / `[]` / `[\s\S]` work, classes are written
as explicit ranges, `(?<n>…)` / `\k<n>` become Python groups, a backreference to a group that took no part
matches empty (as in JavaScript), identity escapes (`\-`, `\_`) mean the character, `\uXXXX`, `\u{…}` (flag `u`),
`\xHH`, `\cX`. Flags `g i m s u y d`: `g` and `y` are handled by the replacement loop (sticky = glued matches).
What cannot be said is **refused with a Russian message that names the problem**, never changed quietly:
`\p{…}` (Python has no Unicode properties), a variable-length lookbehind, possessive `a*+`, inline flags `(?i)`,
atomic groups, flag `v`, octal escapes outside classes, unknown flags, bad group names, unbalanced brackets, empty
patterns. A script with such a pattern is still imported, switched off, with the reason shown. Known differences
that remain (documented, not hidden): case-insensitive matching uses Python's Unicode folding for letters outside
ASCII (JavaScript without `u` folds a little differently); JavaScript strings are UTF-16, so `.` on an emoji without
`u` matches half of it in JavaScript and all of it here.

**Runaway patterns.** Python's `re` cannot be interrupted and the pure-Python rule excludes the `regex` module
(it has a timeout but needs a C compiler on Termux). So every match runs in a worker **process**
(`core/regex_worker.py`, started with `python -I -S`, standard library only, talks JSON lines) managed by
`core/regex_runner.py`: one long-lived process, started on first use. It prints `R <rule>` before each rule; if
no line comes within `RULE_SECONDS` (2) or the job takes more than `JOB_SECONDS` (10) the process is killed, the
named rule is skipped (remembered as slow until its pattern is edited, so it does not stall again), the job runs
again without it, and every open page gets the notice `regex_skipped` with the rule's name; the rule list shows a
warning. A message cannot grow past 8 million characters. If the process cannot be started the rules are skipped and
reported; nothing is ever matched in the app's own process. Results are cached by (rule fingerprints, text hash) so a
chat of hundreds of messages with dozens of rules is one pipe round trip at most, and nothing on a repeat view.
Measured on the dev machine: 300 messages x 31 rules, first view about 100 ms, cached about 25 ms, a worker start about
60 ms, a catastrophic pattern stopped after the limit (1 s in the tests).

**A thousand rules (measured 2026-11-08, dev PC).** Before: opening a chat of 300 messages 370 to 570 ms and 35 to 85 ms
cached, sending 300 ms to the model, the rule list 85 ms. Causes: the worker said «R n» before every rule and the
parent handed each line over a queue (800 thread hand-offs); it looked up every rule in every item's list (quadratic);
the rules were read from the database and translated again on every request; `pick` ran `applies` for each rule and
message. Now: the parent only keeps the last «R n» (no queue), the worker groups items by rule, keeps compiled
patterns between jobs, skips a plain-text rule whose text is not in the message, and only the rules a job uses go over
the pipe; the service keeps the rules and the built rule set while `RegexRepo.stamp()` is unchanged (one small
query), and the rules of a place and depth are chosen once per set. Result: view 130 to 150 ms first time after a
rule change or start, about 10 ms after; send to the model 60 to 90 ms (30 without rules); the rule list 14 ms. The
limit of rules per list is 2000 (it was 300, and an import cut the rest silently; it now says so).

**Display and HTML.** The page asks the server how each visible message looks (`POST /api/regex/display`),
because the engine is there; the answer is `{text, changed, nodes}`. `nodes` exists when a rule with
`render_html` changed the text. It is a **tree**, built by `core/safe_html.py` (stdlib `html.parser`): only listed tags
(text, lists, tables, `details`, `progress`, links, our own `/assets/…` pictures), only `title`, `style` and a few numeric
attributes; `<style>`, scripts, frames, objects, forms, svg, math, media, templates are dropped WITH their content; unknown
tags are shown as text; `style` is filtered to a list of properties with no `url()`, `position`, negative sizes or
`@`; `href` only http, https, mailto; depth and node count are capped. The page builds elements from the tree
(`components/Regex.js`) with the same lists again, so there is **no `innerHTML` and no `dangerouslySetInnerHTML`**, the
page CSP is untouched and `modify-ui`'s «never HTML» rule still holds. Streaming: while a reply is written the page asks
at most every 350 ms for the text so far; a half-written tag at the end is held back, open tags are closed, and the last
answer stays on screen between requests, so nothing jumps back to the raw text. Safe mode (`/?safe`) shows messages as
stored, with no rules. Parking after an update (ADR 0015) concerns the look (themes); regex rules are data, so
**screen rules stay on after an update**.

**The model.** `services/regex.prompt_texts()` rewrites the history (and lorebook entries) before
`core/assembly.py` builds the prompt (`PromptInput.rewritten`, `lore_rewritten`); the archive and «Что видела модель»
therefore show the rewritten text. Order of one reply: the model writes `<thinking>`; it is split out while streaming into the
version's reasoning; the saved-text rules run on the clean reply (never on the thinking); later, for the prompt, the
prompt rules run on the saved text, and only after that the speaker's name is put in front and old thinking is wrapped
back (preset option), so a rule never sees the thinking frame.

**Saved text.** Applied once when a message is saved (new message, new version of a reply, a message added by hand) and
on edit only for rules marked `run_on_edit`. The text before the rules is kept in the version's `swipe_meta.regex_before`;
the message shows «Вернуть как было», which puts it back without running the rules again. A rule never turns a message
into an empty one. The narrator's scene and greetings of a new chat are not processed.

**Copies, groups.** The table is in the database, so whole-data copies and restore carry it (tested). Groups of
characters/lorebooks/chats are unrelated to rules; deleting a character removes its rules by trigger.

## Consequences

- One extra process on first use (about 10 MB), none for a chat without rules. On a phone without the ability to start
  processes the rules are reported as not run; the chat itself works.
- The display needs one request per view; the stored text stays the source of truth, so turning a rule off brings
  the original back at once (except `stored` rules, whose original is in `regex_before`).
- Two copies of the tag whitelist (server, page) must stay in step; the server's is the authority.
- Reasoning (ST placement 6) is applied to old reasoning sent to the model and by `POST /api/regex/display` with role `reasoning`; the page's 💭 does not ask yet. Not covered: slash commands, the narrator, per-message rules on greetings, SillyTavern's
  `\p{…}` and variable lookbehind patterns.
