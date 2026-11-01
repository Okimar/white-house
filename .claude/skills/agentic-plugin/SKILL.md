---
name: agentic-plugin
description: How to write an agentic reply writer for Tavern - several model calls per reply (in parallel, in rounds), live progress in the page (trace events, a tree under the message or a side panel), data kept with each version of a reply, budget, stop and model access. Use when making or changing a plugin that thinks before answering, like the built-in tree of thoughts.
---

# Agentic plugins (reply writers that think)

Read `create-plugin` first (folder, plugin.json, access rules). Decisions: `docs/adr/0011-tree-of-thoughts.md`.
Worked example: `tavern/builtin_plugins/thoughts` (server.py + web.js), tests in
`tests/unit/test_thoughts.py` and `tests/integration/test_thoughts_api.py`.

## Server part

```python
class Writer:
    id = "my-writer"

    async def generate(self, job):                      # yields the reply text
        messages = await job.build_messages()           # the standard prompt (cards, lore, history)
        job.meta["mine"] = {"steps": []}                 # kept with this version (swipe_meta), even on Stop
        job.run.trace("step", id=job.message_id, plugin="my-plugin", state="wait")
        results = await asyncio.gather(*(job.llm.generate([*messages, ChatMessage("user", task)])
                                         for task in tasks), return_exceptions=True)
        async for event in job.llm.stream([*messages, ChatMessage("system", note)], **job.params):
            if event.kind == "text":
                yield event.text

def setup(api):
    api.add_generator(Writer(), "Моё")
```

- `job.llm` is the reply's client: its budget, «Стоп» and the plugin's access gate (permission,
  limits, per-reply count) cover every call. Another model: `job.llm.fork(model=...)`; another
  connection: `job.llm.fork(provider=api.ctx.providers.create(conn), model=..., meter=spending.meter(...,
  purpose=f"plugin:{api.id}"))` and close that provider afterwards.
- Re-raise `PluginRefused` and `BudgetExceeded` (and anything not an `Exception`, like cancellation);
  catch other failures per step so one bad call does not cost the reply.
- `job.meta` keys must not be core ones (`reasoning`, `usage`, `via`, `images`): the core wins.
- Never add your notes to the chat as messages if the model should not see them later; keep them in
  `job.meta`.

## Web part

```js
t.on("trace", (e) => { if (e.plugin === t.id && e.chatId === t.chatId()) { /* update live state */ t.refresh(); } });
t.messageTree((m) => live.get(m.id) || m.swipe_meta?.mine ? {title, open, items: [{title, text, state, children}]} : null);
const panel = t.panel({ icon: "🌳", title: "…", render: () => tree | null });  // panel.open() / panel.close()
```
`state` is "wait" | "done" | "error" | "". Everything is plain text. Clear live state on
`t.on("generation", d => !d.running)`: the finished message then carries `swipe_meta`.

## Tests

The fake model answers "Echo: <last user message>" when nothing is queued; parallel calls take queued
replies in any order, so prefer the echo. `fake_llm.fail_next(400)` fails one call. Read the run's
events (`GET /api/runs/{id}/events`) for trace events; check `swipe_meta` of the done message and
`fake_llm.requests` for what each call was sent.
