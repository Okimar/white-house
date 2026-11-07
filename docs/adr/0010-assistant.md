# 0010. The assistant over the chat: steps by form, a journal with undo

Status: accepted (stage 8б-1, 2026-10-31; 8б-2 added 2026-11-01). Builds on 0007 (providers, spending, structured output)
and 0009 (plugins: own spending line and limits, the "may it?" pattern).

## Context

The owner wants to change Tavern by asking in words: "make a witch", "write a lorebook about the
city", later "make the theme prettier" and "clean the chat". Decisions (2026-10-28 and 2026-10-31):
a draggable window over the chat opened from the menu; the model is the chat's, changeable in the
window (another connection or a profile); it works with **any** model; a list of conversations
like chats; changes apply at once, each one goes to a journal and can be undone; deleting asks
first; no access to secrets or the app's code; its own line on «Расход» and its own day / month
limits (the global ones apply too). Built in two parts: 8б-1 characters and lorebooks, 8б-2
themes, chats, presets and chat settings.

## Decision

- **Steps by form, not native tool calls.** Each step the model answers with JSON
  `{"say", "actions": [{"tool", "args"}]}` through `LLMClient.generate_json` (JSON mode where the
  provider has one, the request in words otherwise, one retry with the problems listed). Tavern
  runs the actions and sends the results back inside `<data>…</data>`; a step without actions
  ends the request. Native function calling would leave out proxies, text completion and small
  local models, and the owner's own Gemini setup.
- **Tools** (`services/assistant_tools.py`) call the same services as the interface
  (`character_tree`, `lorebooks`, the repos), so validation, events and the character history
  are the same. A wrong call returns an error text to the model, never raises past the loop.
- **Journal and undo.** Before a change, `storage/snapshot.py` keeps the touched rows; for a delete
  it follows the foreign keys (CASCADE children recursively, SET NULL links as just the link
  column), so undoing "delete character" brings back its bubbles, links, chat memberships, notes
  and lorebook places. New rows are noted as created and deleted on undo. Lorebook places are kept
  as the list before. "Отменить" on an action undoes it **and every later one** of that
  conversation still in force (newest first), so a later change never rests on a removed one.
  Rows removed by triggers (a character's edit history) are not brought back.
- **Confirmation.** Tools with `confirm` (delete character / bubble / lorebook) pause the run with
  a `confirm` event; the window shows «Разрешить / Нет»; it waits as long as it takes; «Стоп» = no.
  A refusal is journaled as `denied` and the model is told not to try another way.
- **Prompt injection.** The system prompt says text in `<data>` is never an instruction; deleting
  needs the user anyway; there is no tool that reads keys or settings with secrets.
- **Runs** go through `ctx.runs` under `assistant:<conversation id>` (the step limits per request and
  per plan item stop a run; a generous call budget stays behind them; «Стоп» cancels), followed by
  the existing `/runs/{id}/events`.
- **Spending.** Purpose `assistant`; the client's gate checks the global limits (`spending.guard`)
  and the assistant's own day / month limits in the shown currency before every request.
- **Storage.** Migration 0015: `assistant_chats`, `assistant_messages` (user / assistant / tool
  with JSON data), `assistant_actions` (tool, args, what, undo JSON, status done / undone / denied).

## Consequences

- Any model that can follow a JSON form can be the assistant; weak models fail visibly (the error
  is shown in the window) instead of silently.
- The snapshot is generic: tools for chats, presets and themes in 8б-2 reuse it; themes keep their
  own version history.
- Undo restores rows as they were, so a manual edit made after the assistant's action on the same
  object is replaced too; the window says that later actions go with it.

## Addendum: 8б-2, everything the page can do (2026-11-01)

The owner asked for "everything a person can press on this page", reading the chat as far as they
choose, and seeing the model's inner workings to explain its answers.

- **One generic tool instead of a tool per button.** `app_call` makes the same HTTP requests the page
  makes, in process (`services/assistant_app.py`, a Starlette app over `api_routes()` without the
  access middleware); `app_routes` lists them with their docstrings or a hint. New buttons are
  reachable at once. Reads (GET) are free; changes go through the journal.
- **Undo for any change: `storage/changelog.Recorder`.** For the time of one request TEMP triggers on
  every watched table write each inserted row and each row before an update or delete; the first
  entry per row wins; the result is a `snapshot` that `restore` puts back. Not watched: the
  assistant's tables, spending, the prompt archive, model info, meaning vectors. Since the
  recorder sees the whole connection, a change waits (up to 3 minutes) while a reply is being
  written anywhere.
- **Closed:** API keys (routes and any body field named key / token / password / proxy...),
  installing / enabling / configuring plugins (their commands and actions stay open, as on the
  page), approving theme scripts, the assistant's own routes, live streams. Theme files and pictures
  are files, not rows: themes have their own tools (`make_theme` copies the theme that is on and
  switches to it, `write_theme_file` for the user's own themes, `set_theme`; undo deletes the new
  theme into its history or puts the old file back); pictures cannot be added or deleted.
- **Asks first:** every DELETE and the routes that delete by numbers; the model writes the question
  (`what`), which is also the journal line.
- **Reading:** `read_chat` gives messages by number with role, name, versions, flags and the
  model's reasoning; how many by default is the setting `read_messages` (50, 0 = all). "Why did it
  answer so" uses GET /messages/{id}/prompt (what the model saw) and the spending routes.
- **The page:** `show` opens a chat or a character; after any change the page reloads its lists, the
  open chat and the settings (so a new theme appears at once).
- "Скрыть от модели" became a button for people too (🙈 on a message, `PUT /messages/{id}
  {exclude_from_prompt}`), since the assistant must not do what the page cannot.

Consequences: undo of a generic action is exact for rows but not for side effects outside the
database (a reply already sent to the model, money spent). A reply started by the assistant (send /
regenerate) is undone by deleting its messages.

Addendum (2026-11-07, ADR 0018): the assistant can now write skills, but only through its dedicated tools.
