# 0013. The assistant's engine: phases, roles, a pinned task and a critic

Status: accepted (stage 10а, 2026-11-02). Builds on 0010 (steps by form, the journal with undo).

## Context

The owner calls what the assistant and agents can do the most important part of the app. The only
models available for testing (Gemini 3.8 Flash low / medium / high, 3.1 and 3.5 Flash Lite) flatter
the user and lose rules after three or four messages. A request such as «сделай крутой лорбук про
фэнтези РПГ» must still give a good result; slower is acceptable. The 8б loop put all rules in one
system prompt at the start of a growing conversation, which is where a weak model loses them.

Owner decisions (2026-11-02, ROADMAP «Этап 10»): questions with ready options before big work, a plan
that waits for «Давай», a strict critic that always checks creative work (up to 2 rounds by default,
then an honest report), honest pushback without praise, messages sent during the work fold into the
plan, a visible «Что я о вас знаю», a model per role, work that goes on with the window closed,
progress shown in full or as short lines.

## Decision

- **Two paths.** Small requests keep the 8б step loop. Its first step may answer `big: true`
  (create something new or rewrite a large part); then the request becomes a **task** stored on the
  conversation (`assistant_chats.task`, migration 0016) and goes through phases, each phase a run
  that ends where the user must act: questions → (answers) → plan → («Давай» or words: re-plan or
  approve) → items → critic ⇄ fix → report.
- **Structure instead of long prompts.** Every role call is built fresh from the task: the request,
  every decision, the language, what the user added, the plan with item states and what each finished
  item reported. This block is *pinned*: it is in every call, never a message far back. A short
  reminder of the current item and the language closes every request (just before the JSON form).
  A writer works one item in its own short loop, so its context stays small; it may split its item
  into sub-items once (the plan deepens agent-style, `2.1`, `2.2`).
- **The critic** gets the pinned task, the journal of changes and read-only tools, never the
  conversation, so it has nobody to please. Issues return to the writer as a fix item; after
  `critic_rounds` the report states what the critic did not accept.
- **Roles**: planner (questions, plan), writer (items, fixes), critic, small (folding messages into the
  plan, the report). Each has a source (connection / profile, "" = the assistant's) and a model name;
  spending is tallied per role on the task.
- **Inbox**: a message sent while items are being done is saved with `inbox` and folded between steps;
  if it would break finished work the user is asked (the same «Разрешить / Нет»). Confirmations no
  longer time out: they wait as long as it takes (the run lives on the server).
- **Memory**: `assistant_memory` rows go to every role; roles add lasting tastes with `remember`, shown
  as «Запомнил: …» lines that can be taken back.
- **Live test stand** (`tavern/devtools/assistant_bench.py`): scenarios played against the real model in
  a sandbox copy of the owner's data, reports with the full text of what was made. Quality is judged
  on what was written, compared with a reference Claude writes by the same instructions.

## Consequences

- Big work costs more calls (questions, plan, an item loop each, critic rounds, report); role models
  let cheap models take the simple calls.
- The engine never trusts the model's form beyond the schema: missing ids, empty plans and unknown
  tools come back as errors or plain failures the user sees.
- Skills (10б) plug into the same calls: the writer and critic will get the matching skill text and
  its checklist.
