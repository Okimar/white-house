# Совпадение с SillyTavern (ST_PARITY)

Статус на 2026-11-14. Ниже сначала короткий русский текст для владельца, потом английские таблицы для агентов.

## Для владельца

**Цель.** Те же вводные (пресет, карточка, лорбук, чат, регексы) дают тот же запрос к модели, что в SillyTavern, а значит и тот же ответ. Сейчас главная боль: один и тот же «джейлбрейк» в SillyTavern проходит, а у нас модель отказывается. Причина почти наверняка в разнице того, что именно уходит в запросе, а не в модели.

**План (согласован 2026-11-14).** Цель: приложение лучше, а функции те же, что в SillyTavern.

Решение принимается по каждой разнице отдельно:
- у нас уже лучше: остаётся как есть и записывается как «отличается нарочно» (с одной строкой, почему);
- у нас хуже: делаем как в ST;
- непонятно, что лучше: спрашиваем владельца и показываем пример.

Чем проверяем:
- эталоны: запросы, которые ST реально отправил (`tests/fixtures/st_goldens/`), и тест «наш запрос = эталон»;
- экзамен на настройках владельца: его пресет, карточка, лорбук и регексы в нашем приложении против ST;
- живой ответ модели на том же запросе.

Наши значения по умолчанию = значения ST по умолчанию: на телефоне нет папки ST, взять их неоткуда.

| Волна | Что | Статус |
|---|---|---|
| 0 | Основа: эталоны, тест по ним, запись запроса (`capture.py`), сравнение (`compare_requests.py`), экзамен | идёт (эталоны и тест есть, экзамена нет) |
| 1 | Путь владельца: макросы, начало истории («[Start a new Chat]»), слова вокруг карточки, персоны и примеров, параметры запроса из пресета | не начато |
| 2 | Точность лорбука и заметок автора | не начато |
| 3 | Длинные чаты: что выпадает из окна и точный подсчёт текста как в ST (на чистом Python, с запоминанием по сообщению) | не начато |
| 4 | Прямые подключения Gemini и Claude | не начато |
| 5 | Чего нет: «продолжить это сообщение», когда срабатывает блок пресета, режимы карточек в группе | не начато |
| 6 | Текстовый режим | не начато |

**Правила.**
- Наши функции не выкидываем. Где у нас лучше, остаётся наше; где хуже, делаем как в ST.
- Код SillyTavern не копируем и не переписываем. Работаем «чистой комнатой»: отчёты описывают поведение словами, агенты по ним пишут своё. Папку с SillyTavern агенты, которые пишут код, не открывают.
- Чаты и данные владельца агенты не читают. Сверяем на своих тестовых данных и на эталонах (см. этап 2).
- Что сделано, но не видно на экране, так и помечаем: «не проверено на экране».

**Этапы описи** (разбивка до плана волн; остаётся как карта по областям).

| Этап | Что | Статус |
|---|---|---|
| 0 | Приборы: `tavern/devtools/capture.py` (снять реальный запрос), `tavern/devtools/compare_requests.py` (сравнить два запроса по частям) | сделано |
| 1 | Опись: что ST кладёт в запрос и в каком порядке, по областям (раздел «Опись» ниже) | сделано |
| 2 | Эталоны: записанные запросы ST в `tests/fixtures/st_goldens/`, по ним тесты «наш запрос = эталон» | сделано (12 случаев, `tests/integration/test_st_goldens.py`) |
| 3а | Пресет и сборка: порядок блоков, карточка тремя частями, примеры, разделители «[Start a new Chat]», имена, подсказка группе, impersonate, continue, префилл только Claude | частично (глубина, склейка и «Склеивать соседние системные» сделаны) |
| 3б | Лорбук: без заголовков, склейка на одной глубине, бюджет 25 %, порядок «Order», рекурсия, глубина скана, позиции | частично (сделано главное, остальное в таблице) |
| 3в | Поля запроса по источникам: OpenAI-совместимый, Gemini, Claude (system-роль, safety, семплинг, имена, постобработка) | не начато |
| 3г | Макросы, регексы, размышление | частично (регексы и размышление сделаны, макросы нет) |
| 3д | Обрезка окна: запас под ответ, порядок выбрасывания, примеры | не начато |
| 3е | Заметка автора, персона, Character's Note, группы | не начато |
| 3ж | Текстовый режим | не начато |

**Журнал** (что сделано, словами пользователя; всё ниже «не проверено на экране»).

- 2026-11-14, волна 0: тест по 12 эталонам ST (`tests/integration/test_st_goldens.py`). Файлы входят в приложение так же, как их принёс бы человек, и запрос сравнивается с эталоном. Итог: во всех 12 случаях у нас нет одной строки «[Start a new Chat]» в начале истории (это волна 1). Есть одна нарочная разница: мы просим у сервиса счёт токенов, чтобы расход был точным. Одна разница ждёт решения владельца: ST всегда шлёт штрафы за повторы, равные 0, а мы при нуле их не шлём. Всё остальное совпало: лорбук на глубине, роли, склейка, место до и после карточки, склейка системных, параметры.

- 2026-11-13, сделано: записи лорбука «на глубине» и из файлов ST уходят модели голыми, без приписки «[Книга › Запись]».
- 2026-11-13, сделано: всё, что стоит на одной глубине с одной ролью и одним порядком, склеивается в одно сообщение через один перевод строки; роли идут как в ST (помощник, пользователь, система).
- 2026-11-13, сделано: бюджет лорбука 25 % окна, отбрасывание по «Order» из файла ST (больший остаётся), «не считать в бюджете» сохраняется, «outlet» хранится, но не отправляется.
- 2026-11-13, сделано: в пресете есть переключатель «Склеивать соседние системные сообщения» и «Порядок на глубине»; при импорте из ST оба переносятся.
- 2026-11-13, сделано: при импорте из ST последнее размышление уходит обратно в том теге, в котором его написала модель.
- 2026-11-13, сделано: глобальные регексы читаются из `settings.json` SillyTavern; в чате есть метка «Регекс ждёт: N», если правила ещё не разрешены.
- 2026-11-13, сделано: глубина регексов считается только по реально отправленным сообщениям.
- 2026-11-13, инструменты: `capture.py` и `compare_requests.py` написаны. Эталоны в `tests/fixtures/st_goldens/` записываются (в работе).

---

# For agents: parity inventory

Clean-room rule: this file describes **behaviour in plain words**. No code, no internal names of the other program. Names in backticks are keys of data files (preset JSON, world file, card, settings.json) that we have to read anyway. Do not open the SillyTavern folder; do not read the owner's chats or `data/`.

Status words: **same**, **partly** (what differs), **missing**, **done** (finished 2026-11-13, not yet seen on screen), **in work**.

Our main files: `tavern/core/assembly.py` (A), `core/presets.py` (P), `formats/st_preset.py` (SP), `core/retrieval.py` (R), `services/lorebooks.py` (L), `formats/st_lorebook.py` (SL), `core/prompt.py` (macros, M), `services/notes.py` (N), `providers/openai_compat.py` / `gemini.py` / `anthropic.py`, `services/chat.py` (C), `services/speakers.py` (S), `core/thinking.py` (T), `formats/cards.py`, `services/regex.py` / `formats/st_regex.py` / `core/regex.py` (RX).

Every "test idea" is meant as a unit test on `assemble()` (or a provider `build_body()`), comparing the message list with the expected one written by hand from the rule in the first column. Goldens (recorded real requests) go to `tests/fixtures/st_goldens/`; `tavern/devtools/compare_requests.py` diffs two requests by part.

## 0. Golden cases (wave 0, 2026-11-14)

`tests/integration/test_st_goldens.py` brings each case in through our real routes (ST preset import with the model
settings accepted into the connection, card JSON, world file switched on globally, chat typed in), sends the last
message to the fake model and compares the body with `request.json`. Two tests per case:
`…_but_for_known_differences` (must pass: guards what already matches) and the full one (strict xfail while a wave is
pending; remove the `WAVES` entry when it turns red). Differences are kept as body transforms in three lists there:
`ALLOWED` (ours better, stays), `UNDECIDED` (asked the owner) and `WAVES` (ST's way, not done yet).

| Golden case | Status |
|---|---|
| C0 baseline | xfail wave 1 (no «[Start a new Chat]») |
| C1 @D depth 0 system | xfail wave 1 (no «[Start a new Chat]»); lore matches |
| C2 @D depth 1 system | xfail wave 1 (no «[Start a new Chat]»); lore matches |
| C3 @D depth 1 user | xfail wave 1 (no «[Start a new Chat]»); lore matches, not merged with the user message |
| C4 @D depth 1 assistant | xfail wave 1 (no «[Start a new Chat]»); lore matches, not merged with the reply |
| C5 @D depth 2 system | xfail wave 1 (no «[Start a new Chat]»); lore matches |
| C6 two @D depth 1, orders 50 / 100 | xfail wave 1 (no «[Start a new Chat]»); one joined message, order 50 first: matches |
| C7 before char | xfail wave 1 (no «[Start a new Chat]»); lore matches |
| C8 after char | xfail wave 1 (no «[Start a new Chat]»); lore matches |
| C9 squash on | xfail wave 1 (no «[Start a new Chat]», which ST keeps unsquashed); squash matches |
| C10 preset in-chat d1 + lore d1 | xfail wave 1 (no «[Start a new Chat]»); «PRESET-INJECT\nJB-TEXT» matches |
| C11 keyword `river` | xfail wave 1 (no «[Start a new Chat]»); keyword fires, lore matches |

Differences in every case:

| Difference | Verdict |
|---|---|
| ST's system «[Start a new Chat]» before the first history message is missing | wave 1 (ST way) |
| We add `stream_options.include_usage: true` | allowed difference: real token counts for spending |
| ST sends `presence_penalty: 0`, `frequency_penalty: 0`; our ST import drops zeros as «off» | undecided, asked the owner |

Parameters `model, temperature 1, max_tokens 300, stream, top_p 1` match once the import's model settings are put
into the connection (the page asks; the test answers yes).

## 1. Prompt manager: which prompts, in which order

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| The order list used is the one for the "global" pseudo-character (id 100001) in `prompt_order`; each listed prompt that is on is added at its place. Unknown ids skipped. | Importer picks 100001, else the first list. | same | SP | Preset with two order lists: blocks follow 100001. |
| Default order: main, worldInfoBefore, personaDescription, charDescription, charPersonality, scenario, enhanceDefinitions (off), nsfw, worldInfoAfter, dialogueExamples, chatHistory, jailbreak. | Ours: main, lore_before, persona, cast, lore_after, examples, memory, history, post_history (+ behaviour blocks). | partly (no separate personality / scenario slots; extra memory slot) | P | Card with all three fields: three separate system texts in ST order. |
| Each marker / prompt is its own message (description, personality, scenario, persona separate). Empty ones not sent. Each marker has its own on/off flag. | `cast` is one entry (description + personality + scenario joined); takes the first marker's flag. | partly | A `card_text`, SP `MARKER_KINDS` | Order list with scenario after main, description after WI: ST gives scenario before WI; ours with the description. Flags differ between the three: ST sends the ones that are on. |
| Prompt `role` and `injection_position` 0 = relative, 1 = in chat at `injection_depth` (default 4) with `injection_order` (default 100). | `position: depth`, `depth`, `order`, `role` mapped; order now imported and used (see section 4). | same / done for order | SP, A | Role user, position 1, depth 2, order 50. |
| A prompt switched off is not sent, but the main prompt off still leaves an empty anchor for author's note "before / after main". | Off block skipped entirely. | partly (matters only with AN before/after main, which we lack) | A | AN "after main", main off. |
| Per-prompt generation-type trigger list (`injection_trigger`: normal, continue, impersonate, swipe, regenerate, quiet); empty = always. | Not read. | missing | SP, P, A, C | Trigger ["impersonate"]: absent in a normal reply, present when writing for the user. |
| Card's system prompt replaces main; card's post-history instructions replace jailbreak; `{{original}}` = preset text. Only with "prefer card prompt" (default on) and prompt on and not `forbid_overrides`. | `prefer_card_prompts` and `{{original}}` handled; `forbid_overrides` ignored. | partly | SP, P, A | Main with `forbid_overrides: true` + card system prompt: ST keeps preset text. |
| Chat-level overrides of system prompt / scenario / examples. | None. | missing (rare) | C, A | — |
| `enhanceDefinitions` and `nsfw` are ordinary text prompts. | Imported as text blocks. | same | SP | — |
| Main default text uses `{{charIfNotGroup}}`. | Ours says `{{char}}`; imported text kept, macro not expanded. | partly | P, M | Group chat, import default ST preset. |

## 2. Card, persona, examples

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| Description sent raw (macros expanded). Personality through `personality_format`, scenario through `scenario_format` (default just the field); format used only when the field is non-empty. | With a format: raw parts. With empty formats: "Description:\n…", "Personality:\n…", "Scenario:\n…" headers added. | partly (ST with empty format sends the bare field) | A `card_text` | `personality_format: ""`: bare text, no header. |
| Persona: one bare system text at the persona marker. Other positions (per persona): top / bottom of AN, in chat at depth with role, none. | Persona = the "me" member through `card_text`, prefixed "{user} (played by the user):". Marker position only. | partly | A | Persona "Tall man." → exactly "Tall man.". Persona at depth 2 role user. |
| Character's Note (card `extensions.depth_prompt`: prompt, depth default 4, role default system) injected in chat every reply. | Not read. | missing | cards.py, A, C | Card with depth_prompt, depth 1: before the last message. |
| Example dialogues: `mes_example` split by `<START>`; each block preceded by a system separator `new_example_chat_prompt` (default "[Example Chat]"); each line group is its own system message named example_user / example_assistant, leading "Name:" removed. | One system text "Example dialogue of X:\n…". | missing (structure differs) | A `examples`, new formatter | `<START>\n{{user}}: hi\n{{char}}: yo` → [sys "[Example Chat]", sys(name example_user) "hi", sys(name example_assistant) "yo"]. |
| Named example messages reach each API differently: Claude / Gemini / merge modes put "Name: " back in front and drop the name; plain OpenAI-compatible sends the `name` field. Named system messages never squashed. | No names anywhere. | missing | providers, A | Same input to each provider body. |
| Example behaviour (settings.json): normal = only if room after history; always keep; never = stripped. Whole blocks dropped together. | Always sent, never cut. | partly | A | Tight window: ST drops examples first. |
| WI entries "before / after examples" are parsed like example text and put at the start / end of the example blocks. | Plain text around the examples block. | partly | A `place_lore("examples_*")` | Position 5 with `<START>` content. |
| Groups, join mode: fields of all members wrapped with join prefix / suffix; muted left out unless "join, include muted". Default is **swap**: only the speaker's card. | All cards always, each "### Name". | partly | A `cast`, S | A and B, A speaks, swap: only A's description. |

## 3. Chat history, names, separators, nudges, continue, impersonate, prefill

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| History starts with a system message `new_chat_prompt` ("[Start a new Chat]"; groups: `new_group_chat_prompt`), never squashed. | Nothing; only with no user turn at all we add a user "[Start]". | missing | A, P/SP | System "[Start a new Chat]" right before the first history message. |
| Hidden messages skipped; narrator / system-type messages go as system; carriage returns removed; macros expanded in every history message. | Hidden skipped, macros expanded. | same (check `\r`) | A | "a\r\nb" → "a\nb". |
| Names (`names_behavior`): default = in group chats every non-user message gets "Name: " (including the current speaker); "content" = every message incl. user; "completion" = `name` field (sanitised); "none". | Groups: only other characters' messages get "Name: ". No setting. | partly | A `message_entry`, SP | Group A, B; history [user, A, B], A speaks: ST "A: …", "B: …". |
| `send_if_empty`: when the last history message is from the assistant, a user message with this text is appended. | Missing. | missing | A, SP | "[continue]" + last message assistant. |
| Group nudge: system message right after the last history message, before post-history prompts; not when writing for the user; never squashed. | `speaker_reply` block at the very end (after post-history), default text differs. | partly (position) | A `tail`, P | ST order …last msg, nudge, jailbreak. |
| Impersonation prompt: a **system** message at the very end. Claude uses `assistant_impersonation` as prefill. | Behaviour block `impersonate` with role user; no impersonation prefill. | partly | P, SP, anthropic | Last message role system. |
| Continue (append to the last message): last message moved to the end; then a system nudge `continue_nudge_prompt` or, with `continue_prefill`, the message as the final assistant turn. Later gets `continue_postfix`. | No "continue this message" generation. | missing | C, A, T | Continue on "He opened the". |
| Assistant prefill: **Claude only**; not on background requests; not with continue-prefill; trailing spaces cut. | Prefill sent as final assistant message to every provider. | partly | A `tail`, C | Gemini connection + prefill: no trailing model turn. |
| Quiet prompts last as system. | Own prompts for background tasks. | n/a | — | — |

## 4. In-chat injections and squash

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| Depth: counted over chat messages actually sent only; 0 = after the newest, 1 = before it; too large = before the first. Preset blocks outside the chat and other injections do not count. Post-history prompt always after depth-0 items. | Same counting; after trimming. | same | A | Pin the depth table (system / user / assistant, depths 0, 1, 2) with a test. |
| All texts at one depth and role (preset in-chat prompts with the same order, AN, character note, persona-at-depth, lorebook @D, plugin memory) join into one message, one line break, each trimmed. Different `order` = own message (higher nearer the next chat message). Order of roles at one depth: assistant, user, system (system nearest the following message). Preset texts first, then summary / memory, AN, character note, lorebook entries. | Same depth + order + role merged with one "\n"; roles in ST order. | done 2026-11-13 (not seen on screen) | A | Two preset prompts depth 2 orders 50 / 100 + AN depth 2; X(system) and Y(user) at depth 1 plus preset P: `…A1, Y(user), "P\nX"(sys), U2`. |
| `injection_order` imported and used within a depth (default 100). | Imported ("Порядок на глубине"). | done 2026-11-13 | SP, P, A | As above. |
| Squash (`squash_system_messages`, default off): consecutive unnamed system messages joined with one line break, injected ones inside the history included; not the new-chat line, example separator, group nudge. | Preset switch «Склеивать соседние системные сообщения» imported from ST; joined with "\n". | done 2026-11-13 (check: named messages and the three exceptions skipped) | P, SP, A | Squash on, depth-0 system entry + post-history: "E\nPHI"; off: separate. |
| Messages with empty content dropped. | Same. | same | A | — |

## 5. World info (lorebooks)

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| Scan text: last **2** messages, each "Name: text", newest first, plus AN / character note / persona-at-depth when their scan flag is on. Entry may have own scan depth. | Last 3 messages, no names. | partly | L, character_tree | Key "Alice", last message by Alice without the name. |
| Extra scan sources per entry: persona, character description, personality, character note, scenario, creator notes. | Missing. | missing | SL, R, L | Entry with "match character description". |
| Key match: case-insensitive substring by default; whole-words option (global, per entry override); `/regex/flags` keys; macros in keys expanded. | Word-stem matching (Russian / English endings), regex keys; macros in keys not expanded. | partly | R `keyword_pattern` | Key "cat", text "category". Key "{{char}}". |
| Secondary keys: AND ANY, NOT ALL, NOT ANY, AND ALL. | Same four. | same | SL, R | — |
| Book order: chat book, persona book, then (strategy, default character first) character books then global; inside each by `order` highest first (budget priority). Final text order: lowest order first. | Chat, members, global; ranking now by ST «Order» (higher kept first) for ST files. | partly / done for order (persona book and strategies not separate) | L, R | Global order 200 + char order 10, strategy evenly. |
| Budget: `world_info_budget` % (default 25) of the context, optional cap; rest dropped except "ignore budget" entries. | 25 % of the window; cut by ST «Order»; `ignoreBudget` kept. Cap not read. | done 2026-11-13 (cap partly) | L, R, SL | Big book + constant jailbreak: constant with highest Order survives. |
| Recursion (default **off**): per entry exclude / prevent / delay-until-recursion; max steps. | Chains on by default, 2 steps; exclude / prevent mapped; delay missing. | partly | L, SL, R | A mentions B's key, recursion off: only A. |
| Min activations + max depth. | Missing. | missing | R, L | Min 1, key only in message 5. |
| Probability; sticky entries do not re-roll. | `chance`; failed roll held for `sticky` replies. | same for ST files | R | — |
| Inclusion groups: sticky wins; else prioritize; else weighted random (default 100). Group scoring (default off). | Groups, main, weight; scoring missing. | partly | R | Same group, scoring on, one with 2 hits. |
| Sticky / cooldown / delay counted in chat messages. | Counted in our replies. | partly | R | Delay 3 on a chat with 2 messages. |
| Per-entry generation-type triggers; decorators `@@activate` / `@@dont_activate`; character filter by tags. | Missing (name filter only). | missing | SL, R | `@@activate\nX` with no keys. |
| Positions: 0 before / 1 after card; 2/3 top / bottom of AN (only when AN fires); 4 at depth with role; 5/6 before / after examples; 7 outlet (only through the outlet macro). | All mapped; outlet kept but not sent; AN top / bottom do not depend on AN firing. | partly / outlet done 2026-11-13 | A, SL, M | AN interval 2, entry at AN top on a skipped turn. |
| Before / after texts: all joined with one "\n" into one text, wrapped by `wi_format` (default "{0}"), one system message at the marker. Entry text bare. Depth entries bare too (no wrapper). | Entries bare (no «[Книга › Запись]»), for depth and ST-file entries. `wi_format` not applied; before / after entries still one message each. | done for bare text 2026-11-13; `wi_format` and one-text join missing | A, SP | Two before-entries: ST "e1\ne2" in one message. |
| Role matters only for depth entries; non-depth lore is always system. | Role imported for every entry. | partly | SL | — |
| Card-embedded book at position 4 with broken depth gets 4. | Becomes "next to card". | partly | character_tree | — |
| Entry content gets regex (placement world info; depth only for @D) and macros. | `lore_rewritten`, macros. | same | RX | — |

## 6. Author's note

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| One AN per chat; position in chat at depth 4 role system; other positions after / before main; none. | Many notes (global, character, chat), each own entry; in-chat only. | partly | N, A | AN "before main". |
| Interval: fires when the count of user messages is divisible by the interval (0 = never). | `interval` = replies skipped. | partly | N | Interval 3 with 4 user messages. |
| Character AN replaces / goes before / after the chat AN, only when the AN fires. | Separate notes. | partly | N | — |
| AN may be scanned by WI (default off). | No. | missing | L | — |

## 7. Macros (expanded in every prompt text, history message, WI content and keys, regex trim strings)

| ST behaviour | Ours | Status |
|---|---|---|
| `{{user}}`, `{{char}}`, `<USER>`, `<BOT>`, `<CHAR>` | `{{user}}`, `{{char}}`; angle forms missing | partly |
| `{{charIfNotGroup}}`, `{{group}}` / `<GROUP>`, `{{groupNotMuted}}`, `{{notChar}}` | missing | missing |
| Card field macros: description, personality, scenario, persona, mesExamples (formatted and raw), card main prompt, card jailbreak, depth prompt, creator notes, version, first message, system prompt | description, personality, scenario, persona, mesExamples; the rest missing | partly |
| `{{original}}` | yes | same |
| `{{newline}}`, `{{trim}}`, `{{noop}}`, `{{// comment}}`, `{{space}}` | missing, left as raw text (comments reach the model) | missing |
| `{{random::a::b}}`, `{{pick::a::b}}` (stable per chat), `{{roll:1d6}}`, `{{reverse::x}}` | random, roll; pick, reverse missing | partly |
| Time / date macros, `timeDiff`, `idle_duration` | missing | missing |
| `{{lastMessage}}`, `{{lastUserMessage}}`, `{{lastCharMessage}}`, message ids, swipe ids, `{{lastGenerationType}}`, `{{input}}` | missing | missing |
| `{{model}}`, `{{maxContext}}`, `{{maxPrompt}}`, `{{maxResponse}}`, `{{isMobile}}` | missing | missing |
| Variables set / get / add / inc / dec / has / delete, chat-local (kept in the chat) and global (kept in settings) | set / get only, per prompt, not kept | partly |
| `{{if …}}…{{else}}…{{/if}}`, `{{outlet::name}}`, `{{banned "x"}}` | missing | missing |
| Unknown macro left as is | same | same |

Files: `core/prompt.py` (`substitute`), `core/assembly.py` (values), chat meta for variables. Test: "A{{trim}}\n\nB {{// hidden}}{{newline}}C".

## 8. Regex

| ST behaviour | Ours | Status | Files |
|---|---|---|---|
| Scripts from three scopes: global (settings.json), preset-scoped, character-scoped (only if allowed); run in that order. | All three importable; global list read from an ST `settings.json`; chip «Регекс ждёт: N» when rules wait for «Разрешить». Rules that came with a preset / card start not approved. | done 2026-11-13 (not seen on screen) | `formats/st_regex.py`, `services/regex.py`, `web/components/Regex.js` |
| Placement: user input, AI output, slash command, world info, reasoning. Flags display-only / prompt-only / neither (stored) / both (display and prompt, not stored). | when = display / prompt / both / stored. | same | — |
| Depth limits min / max counted from the newest message; WI gets depth only for @D; reasoning gets its message's depth. Counted over messages actually sent (hidden not counted). | min_depth / max_depth; counted over sent messages only. | done 2026-11-13 (continue shift n/a: no continue) | `services/regex.py` |
| Macros in the find pattern none / raw / escaped; trim strings. | Same. | same | `core/regex.py` |

## 9. Reasoning

| ST behaviour (default) | Ours | Status | Files |
|---|---|---|---|
| Past reasoning added to history: off by default; when on, at most `max_additions` (1) newest messages, wrapped as the prefix / suffix tags; not for other group members; regex placement 6 applies. | ST import sends the last reasoning back in the model's own tag; repeated lines dropped. | done 2026-11-13 (offer as a choice; settings.json holds the switch) | SP, A, T |
| Auto-parse of `<think>` in the reply (default off); request side: show thoughts (`include_reasoning` / `includeThoughts`, default on). | Always split `<thinking>` / `<think>` from chat replies. | partly (output side only) | T, chat.py |
| Reasoning effort auto / min / low / medium / high / max, mapped per service (OpenAI-compatible only for known reasoning models; Claude budget percentages and sampling removal; Gemini budget / level). | Effort list minimal / low / medium / high; Claude needs a separate `thinking_budget`. | partly | SP, gemini.py, anthropic.py, params.py |

## 10. Context size and trimming

| ST behaviour (default) | Ours | Status | Files |
|---|---|---|---|
| Budget = context size minus response length; real tokenizer. | `max_context_tokens` of the connection (0 = none), response not subtracted, 4 chars ≈ 1 token. | partly | C, A, `core/prompt.py` |
| Fixed prompts always go; if they alone overflow the request fails. | We drop members' cards, then plugin texts. | partly | A |
| History newest → oldest until the budget ends (separator, nudges, continue reserved first); depth items counted on the kept part. Examples after history in priority unless "always keep". | Oldest dropped, min 6 kept, then cards, then more history; examples never dropped. | partly | A |

## 11. Request body per service

### 11a. Custom OpenAI-compatible

| ST | Ours | Status | Files |
|---|---|---|---|
| Body: messages, model, temperature, max_tokens, stream, presence / frequency penalty, top_p always sent (even 0 / 1); top_k not sent; stop only when non-empty (max 4); logit_bias; seed ≥ 0; n > 1. | Only set connection params sent; preset sampling moved to the connection on import (asked); `stream_options.include_usage` added. With an empty connection nothing about length or temperature is sent. | partly | openai_compat.py, SP, params.py |
| `custom_include_body`, `custom_exclude_body`, `custom_include_headers`. | `extra_body` merge only. | partly | openai_compat.py, params.py, SP |
| Prompt post-processing: merge, semi-strict, strict, single user message (+ tool variants). | Missing. | missing | openai_compat.py or A |
| Messages carry `name` for named example lines. | Never. | missing | openai_compat.py, A |

### 11b. Google AI Studio (Gemini)

| ST | Ours | Status | Files |
|---|---|---|---|
| `use_sysprompt` (default **off**): off → no systemInstruction, every system message becomes a user turn; on → leading system messages to systemInstruction, one part each. | Leading system always one joined systemInstruction. | partly | gemini.py, SP |
| Other system messages → user; same-role turns merged into one text with a blank line; named messages get "Name: ". | Merged as separate parts; no names. | partly | gemini.py |
| `safetySettings` always sent, all categories OFF. | Only with the connection flag. | partly | gemini.py, params.py |
| generationConfig details (topK only if > 0, stop max 5 of 1–16 chars, seed, thinkingConfig by model and effort). | Roughly; own thinking map. | partly | gemini.py |
| No prefill outside Claude. | Prefill becomes a final model turn. | partly | A |

### 11c. Claude

| ST | Ours | Status | Files |
|---|---|---|---|
| `use_sysprompt` (default off): off → all system messages become user turns, no `system`; on → `system` as text blocks. Empty conversation → user "Let's get started.". | Leading system always → `system` string; user "." if the conversation does not start with user. | partly | anthropic.py, SP |
| Later system messages → user; names into text; same-role turns merged as content blocks. | Same merge; no names; empty → ".". | partly | anthropic.py |
| Prefill (or impersonation prefill) as the final assistant turn, trailing spaces trimmed; with thinking or on the newest models turned into a user turn. | Always as assistant turn; no trim, no flip. | partly | anthropic.py, A |
| Sampling: temperature, top_p, top_k always; limited-sampling models only one of temperature / top_p; none with thinking. | Only set ones; all removed with thinking. | partly | anthropic.py |
| Prompt caching (server config). | Missing (cost only). | missing | anthropic.py |

## 12. Groups

| ST (default) | Ours | Status | Files |
|---|---|---|---|
| Card mode swap (default) / join / join including muted; prefix / suffix. | All cards with "### Name" + "Also in the scene:". | partly | A, S |
| Activation: natural, list, manual, pooled; muted skipped. | Own orders. | partly | S |
| Group nudge, new-chat separator, group macros, names before every character line. | See sections 3 and 7. | partly | A, M |
| Other members' past reasoning never sent. | Per message regardless of speaker. | partly | A |

## 13. Where each setting lives in ST

- **Preset file** (chat completion): source, models, temperature, penalties, top_p / top_k / top_a / min_p, repetition_penalty, max context and response, `names_behavior`, `send_if_empty`, the prompt texts (`impersonation_prompt`, `new_chat_prompt`, `new_group_chat_prompt`, `new_example_chat_prompt`, `continue_nudge_prompt`, `group_nudge_prompt`), `wi_format`, `scenario_format`, `personality_format`, bias, `prompts`, `prompt_order`, `assistant_prefill`, `assistant_impersonation`, `use_sysprompt`, `squash_system_messages`, `continue_prefill`, `continue_postfix`, `show_thoughts`, `reasoning_effort`, `seed`, `n`, `custom_*` body / headers / post-processing, preset-scoped regex in `extensions`.
  **We consume**: prompts, prompt_order, `injection_order`, squash, sampling (moved to the connection), max context, reasoning effort (partly), impersonation / group nudge / prefill / formats. **Kept but unused**: names_behavior, send_if_empty, new_*_prompt, continue_*, wi_format, use_sysprompt, assistant_impersonation, custom_* body / headers, post-processing, show_thoughts, seed, n, bias.
- **settings.json**: world info settings (depth 2, budget 25 %, cap, recursion off, max steps, min activations, case, whole words, group scoring, include names, strategy), **global regex** (now read), AN defaults and character notes, persona description + position / depth / role, reasoning prefix / suffix / separator / add-to-prompts / max additions / auto-parse, custom stopping strings, example behaviour, prefer card prompt / jailbreak, bias presets.
- **Card**: description, personality, scenario, first message, examples, system prompt, post-history instructions, character note (`depth_prompt`), creator notes, embedded book, linked world, card regex, talkativeness.
- **Chat metadata**: AN text / position / depth / interval / role, chat-bound world, chat variables, scenario / system / examples overrides.
- **Server config file**: Claude caching, prompt placeholder text, Gemini API version.

---

## Ranked: likely causes of a refusal in ours for the same jailbreak / preset

The first findings (Gemini behind an OpenAI-compatible proxy) put the regex trick first. A common "jailbreak" is not a preset at all but a pair of **global regex scripts** (one replaces ordinary spaces with a blank Braille character in user messages in the prompt only; one turns them back for display only). The history then reads as Braille-spaced text. Global regexes live in the ST settings file and never travel with a preset; rules that arrive with a preset or card start unapproved here.

1. **Global regex jailbreak missing or waiting for approval.** Now fixed in two parts (done 2026-11-13): settings.json global regex import and the chip «Регекс ждёт: N». Still to check on the owner's device: «Что видела модель» must show the blank characters in user messages and earlier replies.
2. **System role delivery to Claude and Gemini.** ST default (`use_sysprompt` off) sends the preset as user turns; ours lifts leading system texts into the API system field. Fix in anthropic.py / gemini.py, keyed on the imported flag.
3. **Prefill rules.** Ours sends the preset prefill to every provider, never flips it to a user turn with thinking, and has no impersonation prefill.
4. **Macros left raw.** `{{// …}}` comments, `{{trim}}`, `{{newline}}`, `{{charIfNotGroup}}`, `{{time}}`, `{{lastUserMessage}}`, `{{if}}`, `{{outlet}}` reach the model as literal braces; comments often hold the author's notes about the jailbreak.
5. **Lorebook entry text and selection.** The title header on entries (done: bare), budget and cut order (done: 25 %, ST «Order», ignore budget), same-depth merging (done). Left: names in scan text, depth 2 vs 3, recursion default, substring vs stem matching, `wi_format`.
6. **Gemini safety settings** sent only with the connection flag; ST (direct Gemini) always sends all categories off.
7. **Missing separators and example structure.** No "[Start a new Chat]"; examples are one titled system text; our "[Start]" user turn where ST has none.
8. **Our own wording in the prompt.** "Description:" headers with empty formats, "{user} (played by the user):", "### Name", "Also in the scene:", "Example dialogue of X:".
9. **Tail order.** Group nudge after the post-history prompt; impersonation sent as user.
10. **Names behaviour.** ST prefixes every character line in groups (including the speaker).
11. **Past reasoning sent** after import (done: last one, in the model's own tag).
12. **Different history kept.** No response reserve, 4-chars estimate, our drop order.
13. **Character's Note (card depth prompt) missing**, AN before / after main missing, interval counted differently.
14. **Sampling values.** With an empty connection no temperature, penalties or max_tokens are sent, so the proxy's defaults decide (fits "answers badly" more than refusals); Claude limited-sampling rules, custom exclude-body / headers and post-processing missing.

## To confirm on the owner's device (not seen)

- «Что видела модель» for the failing chat: order of system / user / assistant, blank characters in user texts, a trailing assistant (prefill) message.
- The global regex list: are the two jailbreak rules present, enabled and approved.
- The connection's model and params (max tokens, temperature).
