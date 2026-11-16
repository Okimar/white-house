# Совпадение с SillyTavern (ST_PARITY)

Статус на 2026-11-16. Ниже сначала короткий русский текст для владельца, потом английские таблицы для агентов.

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
| 0 | Основа: эталоны, тест по ним, запись запроса (`capture.py`), сравнение (`compare_requests.py`), экзамен | идёт (эталоны и тест есть, все 12 совпадают; экзамена на ваших настройках нет) |
| 1 | Путь владельца: макросы, начало истории («[Start a new Chat]»), слова вокруг карточки, персоны и примеров, параметры запроса из пресета | сделано 2026-11-16 (часть значений для макросов ещё не подаётся, см. раздел 7) |
| 2 | Точность лорбука и заметок автора | сделано 2026-11-16 |
| 3 | Длинные чаты: что выпадает из окна и точный подсчёт текста как в ST (на чистом Python, с запоминанием по сообщению) | сделано 2026-11-16 |
| 4 | Прямые подключения Gemini и Claude | сделано 2026-11-16 |
| 5 | Чего нет: «продолжить это сообщение», когда срабатывает блок пресета, режимы карточек в группе | сделано 2026-11-17 (режимы карточек — наши, нарочно) |
| 6 | Текстовый режим | сделано 2026-11-17 (story string из полей карточки и позиции персоны — дальше) |

**Правила.**
- Наши функции не выкидываем. Где у нас лучше, остаётся наше; где хуже, делаем как в ST.
- Код SillyTavern не копируем и не переписываем (владелец, 2026-11-17: папку SillyTavern открывать и читать можно — и код, чтобы понять поведение, и настройки в `data/<пользователь>/`, — нельзя копировать код). Пишем своё, своими словами и своей структурой.
- Чаты и ключи владельца не читаем, пока он не попросит. Настройки (settings.json, пресеты, шаблоны) смотреть полезно: там видно, как он на самом деле пользуется ST.
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
| 3ж | Текстовый режим | частично (шаблоны, имена, остановки, «написать за меня» — сделано) |

**Журнал** (что сделано, словами пользователя; всё ниже «не проверено на экране»).

- 2026-11-17, решение владельца: чаты с несколькими персонажами остаются нашими (имена только у чужих реплик, наши настройки сцены) — у нас лучше; 
ames_behavior из пресетов ST не применяем.

- 2026-11-17, по настройкам владельца в SillyTavern: при импорте `settings.json` (Регекс → общие правила → импорт) вместе с общими регексами переносятся настройки лорбуков — глубина скана, бюджет, потолок, рекурсия (у владельца включена без ограничения шагов — у нас это 10 шагов), минимум срабатываний, имена в скане, порядок книг, групповой счёт — и включаются везде книги, отмеченные в ST как общие (по названию; каких у нас нет — названы в уведомлении). «Целые слова» и регистр не переносятся: у нас поиск по основам слов (нарочно). Стоп-строки: поле принимает и формат ST `["\n\n", "User:"]` (так можно задать перенос строки); у владельца в ST стоп-строки пустые (`[]`), невидимых символов там нет — они в регексах (U+2800).

- 2026-11-17, волна 6: заметка персонажа из карточки (Character's Note, `depth_prompt`) теперь уходит модели в каждом ответе на своей глубине — раньше она терялась. Текстовый режим: шаблон из ST импортируется правильно (перенос строки только после метки, без лишнего переноса перед концом реплики), есть отдельные метки для первого и последнего сообщения, внутри меток работают {{user}}, {{char}} и {{name}}, ответ обрывается на «Имя:» пользователя и на метках, во встроенных шаблонах нет лишнего BOS (его добавляет сервер), «Написать за меня» просто открывает вашу реплику без текста-инструкции. Оставлено на потом: story string из полей карточки (у нас карточку собирает пресет) и позиции персоны.

- 2026-11-17, волна 5: в меню последнего сообщения есть «Дописать» — модель продолжает это же сообщение, текст растёт на месте, новой версии нет. Как в ST: сообщение уходит в конец запроса, за ним строка «[Continue your last message…]»; в пресете можно поменять эту строку, выбрать, чем соединять старый и новый текст (пробел, ничего, перенос, пустая строка), и включить «сообщение как начало ответа». У каждого блока пресета есть «Только для»: обычный ответ, «Дописать», «Написать за меня», другой вариант (в ST — «триггеры»). Всё это переносится при импорте и экспорте пресетов ST. Режимы карточек в группе (swap / join) не повторяли: у нас для этого настройка «что модель знает о других» в «Участниках». Проверено на экране (телефон и ПК).

- 2026-11-16, волна 2 (заметки автора): у заметки есть «Где» — в чате на глубине (как было), перед главным промптом или после него, как в ST. Записи лорбука «у заметки сверху / снизу» идут вместе с заметкой; записи из файлов ST — только когда заметка сработала, как там. В настройках лорбуков есть «Искать ключи и в заметках автора» (по умолчанию выключено, как в ST). При импорте чата из ST его заметка автора становится заметкой чата (текст, глубина, место, роль, частота). Оставлено наше: несколько заметок (общие, персонажа, чата) и «Пропускать ответов».

- 2026-11-16, волна 3 (длинные чаты): под ответ модели теперь оставляется место — из окна подключения вычитается «Максимум токенов ответа», как в ST. Токены считаются по-новому: русский текст стоит дороже английского (раньше считалось «4 буквы = 1 токен», и длинные русские чаты не влезали в окно). После каждого ответа сервис сообщает, сколько токенов было на самом деле, и приложение подстраивает счёт под эту модель — точнее, чем у ST для Gemini и Claude. Примеры диалогов, как в ST, идут только если после переписки осталось место (в блоке «Примеры диалогов» можно выбрать «всегда» или «никогда»). В «Что видела модель» видно, сколько оставлено под ответ и какие примеры не влезли.

- 2026-11-16, волна 1 (начало истории и слова): перед первым сообщением чата стоит системная строка «[Start a new Chat]» (в группе «[Start a new group chat. Group members: …]»). Подсказка группе «[Write the next reply only as …]» идёт сразу после последнего сообщения, до пост-истории. «Написать за меня» уходит последним системным сообщением, как в ST. Префилл («начало ответа») уходит только в Claude и в текстовый режим, Gemini и OpenAI его больше не получают. Пресет из ST шлёт карточку, персону и примеры голыми, без наших заголовков («Description:», «(played by the user)»), а примеры — отдельными сообщениями «[Example Chat]» с именами. Наш собственный пресет оставил свои заголовки. Есть «Если чат кончается ответом модели» (send_if_empty), по умолчанию пусто.
- 2026-11-16, волна 1 (макросы): `{{// комментарии}}`, `{{trim}}`, `{{newline}}`, `{{random}}`, `{{pick}}`, `{{roll}}`, время и дата, `{{if}}…{{else}}…{{/if}}`, переменные, `{{group}}` и длинные имена полей карточки раскрываются как в ST.
- 2026-11-16, волна 2 (лорбук): скан по последним 2 сообщениям с именами («Аня: текст»), рекурсия по умолчанию выключена (как в ST; включается в настройках), порядок книг: чат, персона, персонажи, общие. Новое у записей из ST: «искать ещё и в описании / сценарии / персоне», «только после шага цепочки», групповой счёт по числу ключей, срабатывание только для выбранного вида генерации (обычный ответ, другой вариант, за меня), `@@activate` / `@@dont_activate`, таймеры в сообщениях чата. Макросы в ключах (`{{char}}`) раскрываются.
- 2026-11-16, волна 4 (Gemini и Claude напрямую): тело запроса как в ST. Если пресет из ST, его флаг «системный промпт в отдельное поле» (`use_sysprompt`) теперь соблюдается; без флага в файле системные тексты идут как сообщения пользователя, как в ST. Фильтры Gemini выключены по умолчанию, штрафов за повторы в Gemini нет, остановок не больше 5. У Claude от 4.6 размышление «adaptive», префилл там становится текстом пользователя. Для OpenAI-совместимых из пресета ST работают постобработка (merge / semi / strict / single), свои поля тела, убираемые поля и свои заголовки (кроме ключа, хоста и куки).
- 2026-11-16, помощник: сайты открываются и через VPN в режиме «fake IP» (Clash, sing-box): раньше все они считались «адресом внутри локальной сети».

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
All twelve cases (C0 baseline, C1–C5 @D at depths 0–2 in each role, C6 two @D orders 50 / 100, C7 before char,
C8 after char, C9 squash on, C10 preset in-chat d1 + lore d1, C11 keyword `river`) **match** since wave 1a
(2026-11-16): `WAVES` and `UNDECIDED` are empty.

Differences in every case:

| Difference | Verdict |
|---|---|
| ST's system «[Start a new Chat]» before the first history message | done (wave 1a) |
| We add `stream_options.include_usage: true` | allowed difference: real token counts for spending |
| ST sends `presence_penalty: 0`, `frequency_penalty: 0`; our ST import drops zeros as «off» | allowed difference (lead, 2026-11-14): same effect, some Gemini routes reject the fields |

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
| Per-prompt generation-type trigger list (`injection_trigger`: normal, continue, impersonate, swipe, regenerate, quiet); empty = always. | Block `triggers` («Только для»), imported / exported; the chat passes normal / continue / impersonate / swipe (`PromptInput.generation_kind`). | done 2026-11-17 | SP, P, A, C | `tests/unit/test_continue_and_triggers.py` |
| Card's system prompt replaces main; card's post-history instructions replace jailbreak; `{{original}}` = preset text. Only with "prefer card prompt" (default on) and prompt on and not `forbid_overrides`. | `prefer_card_prompts` and `{{original}}` handled; `forbid_overrides` ignored. | partly | SP, P, A | Main with `forbid_overrides: true` + card system prompt: ST keeps preset text. |
| Chat-level overrides of system prompt / scenario / examples. | None. | missing (rare) | C, A | — |
| `enhanceDefinitions` and `nsfw` are ordinary text prompts. | Imported as text blocks. | same | SP | — |
| Main default text uses `{{charIfNotGroup}}`. | Ours says `{{char}}`; imported text kept, macro not expanded. | partly | P, M | Group chat, import default ST preset. |

## 2. Card, persona, examples

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| Description sent raw (macros expanded). Personality through `personality_format`, scenario through `scenario_format` (default just the field); format used only when the field is non-empty. | Preset from ST: each part bare, its own system message (wave 1a). Our own presets keep "Description:\n…" headers with empty formats. | done 2026-11-16 for ST presets; ours **отличается нарочно** (headers help weak models) | A `card_text` | `personality_format: ""`: bare text, no header. |
| Persona: one bare system text at the persona marker. Other positions (per persona): top / bottom of AN, in chat at depth with role, none. | Preset from ST: the bare persona description (wave 1a); ours keeps "{user} (played by the user):". Marker position only. | done for the text 2026-11-16; positions partly | A | Persona "Tall man." → exactly "Tall man.". Persona at depth 2 role user. |
| Character's Note (card `extensions.depth_prompt`: prompt, depth default 4, role default system) injected in chat every reply. | The speaking character's, every reply, macros, joined after the author's note at its depth (`assembly.character_note`, `CAT_CHAR_NOTE`). | done 2026-11-17 | cards.py, A, C | Card with depth_prompt, depth 1: before the last message. |
| Example dialogues: `mes_example` split by `<START>`; each block preceded by a system separator `new_example_chat_prompt` (default "[Example Chat]"); each line group is its own system message named example_user / example_assistant, leading "Name:" removed. | Preset from ST: the same structure (`example_dialogues`, `new_example_chat` text, `example_user` / `example_assistant` names, never squashed). Ours keeps one "Example dialogue of X:" text. | done 2026-11-16 for ST presets | A `examples`, `example_dialogues` | `<START>\n{{user}}: hi\n{{char}}: yo` → [sys "[Example Chat]", sys(name example_user) "hi", sys(name example_assistant) "yo"]. |
| Named example messages reach each API differently: Claude / Gemini / merge modes put "Name: " back in front and drop the name; plain OpenAI-compatible sends the `name` field. Named system messages never squashed. | `ChatMessage.name` / `speaker`: OpenAI-compatible sends `name`; Gemini, Claude and text templates put "Name: " in front (`spoken()`). | done 2026-11-16 | providers, A | `test_named_lines_reach_each_service` |
| Example behaviour (settings.json): normal = only if room after history; always keep; never = stripped. Whole blocks dropped together. | Per preset: the examples block's «Когда отправлять примеры» (room / always / never); a member's examples dropped whole. | done 2026-11-16 | A, P | `tests/unit/test_long_chats.py` |
| WI entries "before / after examples" are parsed like example text and put at the start / end of the example blocks. | Plain text around the examples block. | partly | A `place_lore("examples_*")` | Position 5 with `<START>` content. |
| Groups, join mode: fields of all members wrapped with join prefix / suffix; muted left out unless "join, include muted". Default is **swap**: only the speaker's card. | All cards always, each "### Name". | partly | A `cast`, S | A and B, A speaks, swap: only A's description. |

## 3. Chat history, names, separators, nudges, continue, impersonate, prefill

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| History starts with a system message `new_chat_prompt` ("[Start a new Chat]"; groups: `new_group_chat_prompt`), never squashed. | Same: `new_chat` / `new_group_chat` texts (ST defaults, also for our presets), alone, kept by the budget. Our own preset with the text emptied still gets a user "[Start]" when the chat has no user turn; ST presets never. | done 2026-11-16 | A, P/SP | `tests/unit/test_st_framing.py` |
| Hidden messages skipped; narrator / system-type messages go as system; carriage returns removed; macros expanded in every history message. | Hidden skipped, macros expanded. | same (check `\r`) | A | "a\r\nb" → "a\nb". |
| Names (`names_behavior`): default = in group chats every non-user message gets "Name: " (including the current speaker); "content" = every message incl. user; "completion" = `name` field (sanitised); "none". | Groups: only other characters' messages get "Name: ". No setting. | Groups: only other characters' messages get "Name: " (the speaker's own lines stay bare), with our own scene / «что модель знает о других» settings. | **отличается нарочно** (owner, 2026-11-17: our multi-character chat is better; all the owner's presets use the default 0) | Group A, B; history [user, A, B], A speaks: ST "A: …", "B: …". |
| `send_if_empty`: when the last history message is from the assistant, a user message with this text is appended. | Same (`send_if_empty`, empty by default; counts for depths, notes land on the real message). | done 2026-11-16 | A, SP | "[continue]" + last message assistant. |
| Group nudge: system message right after the last history message, before post-history prompts; not when writing for the user; never squashed. | Same; ST's default text (old default texts upgraded once, own texts kept). | done 2026-11-16 | A `tail`, P | ST order …last msg, nudge, jailbreak. |
| Impersonation prompt: a **system** message at the very end. Claude uses `assistant_impersonation` as prefill. | Same; `impersonation_prefill` for Claude / text mode. | done 2026-11-16 | P, SP, anthropic | Last message role system. |
| Continue (append to the last message): last message moved to the end; then a system nudge `continue_nudge_prompt` or, with `continue_prefill`, the message as the final assistant turn. Later gets `continue_postfix`. | «Дописать» (`chat.start_continue_message`, POST /api/chats/{id}/continue-message): the message goes last (depth 0 before it), then `prompts.continue_nudge` or nothing with `continue_prefill`; saved as old + `continue_postfix` + new in the same version. | done 2026-11-17 | C, A | `test_continue_message_api.py` |
| Assistant prefill: **Claude only**; not on background requests; not with continue-prefill; trailing spaces cut. | Same: only for the connection kinds anthropic and text mode (`PromptInput.provider_kind`, `chat.prompt_kind`), trailing spaces cut. | done 2026-11-16 | A `tail`, C | Gemini connection + prefill: no trailing model turn. |
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
| Scan text: last **2** messages, each "Name: text", newest first, plus AN / character note / persona-at-depth when their scan flag is on. Entry may have own scan depth. | Lorebooks: own `scan_messages` (2) and `include_names` (on), "Name: text" (`lorebooks.scan_lines`). Character bubbles keep 3 messages without names (ours). | done 2026-11-16 | L, character_tree | `test_lore_parity.py` |
| Extra scan sources per entry: persona, character description, personality, character note, scenario, creator notes. | Rule `match` (imported from both world files and card books). | done 2026-11-16 | SL, R, L | Entry with "match scenario". |
| Key match: case-insensitive substring by default; whole-words option (global, per entry override); `/regex/flags` keys; macros in keys expanded. | Word-stem matching (Russian / English endings), regex keys; macros in keys expanded (bubble stays as written). | stems **отличается нарочно** (owner, 2026-11-14: Russian endings); macros done 2026-11-16 | R `keyword_pattern`, L | Key "{{char}}". |
| Secondary keys: AND ANY, NOT ALL, NOT ANY, AND ALL. | Same four. | same | SL, R | — |
| Book order: chat book, persona book, then (strategy, default character first) character books then global; inside each by `order` highest first (budget priority). Final text order: lowest order first. | Same: tiers chat 0, persona 1, characters / global by `strategy` (character_first, global_first, evenly), inside a tier by «Order»; found list lowest order first. | done 2026-11-16 | L `book_tiers`, R | `test_lore_parity.py`, `test_lorebooks.py` |
| Budget: `world_info_budget` % (default 25) of the context, optional cap; rest dropped except "ignore budget" entries. | Same, `budget_cap` setting. | done 2026-11-16 | L, R, SL | Big book + constant jailbreak: constant with highest Order survives. |
| Recursion (default **off**): per entry exclude / prevent / delay-until-recursion; max steps. | Same default (off; the setting turns chains on); exclude / prevent / `after_chain` (delay until level N). | done 2026-11-16 | L, SL, R | A mentions B's key, recursion off: only A. |
| Min activations + max depth. | `min_activations`, `min_depth_max`. | done 2026-11-16 | R, L | Min 1, key only in an older message. |
| Probability; sticky entries do not re-roll. | `chance`; failed roll held for `sticky` replies. | same for ST files | R | — |
| Inclusion groups: sticky wins; else prioritize; else weighted random (default 100). Group scoring (default off). | Groups, main, weight; group scoring (setting `group_scoring`, rule `scoring` per entry). | done 2026-11-16 | R | Same group, scoring on, one with 2 hits. |
| Sticky / cooldown / delay counted in chat messages. | Entries from ST files: chat messages; our own bubbles: replies. | done 2026-11-16 (ours **отличается нарочно**) | R, L | Sticky 2 by message count. |
| Per-entry generation-type triggers; decorators `@@activate` / `@@dont_activate`; character filter by tags. | Triggers (rule `triggers`; the chat passes normal / swipe / impersonate) and decorators done; tag filter: ST tag ids cannot be mapped (name filter only). | done 2026-11-16 except tags | SL, R, C | `@@activate\nX` with no keys. |
| Positions: 0 before / 1 after card; 2/3 top / bottom of AN (only when AN fires); 4 at depth with role; 5/6 before / after examples; 7 outlet (only through the outlet macro). | All mapped; outlet kept but not sent; AN top / bottom do not depend on AN firing. | partly / outlet done 2026-11-13 | A, SL, M | AN interval 2, entry at AN top on a skipped turn. |
| Before / after texts: all joined with one "\n" into one text, wrapped by `wi_format` (default "{0}"), one system message at the marker. Entry text bare. Depth entries bare too (no wrapper). | Entries bare (no «[Книга › Запись]»), for depth and ST-file entries. `wi_format` not applied; before / after entries still one message each. | done for bare text 2026-11-13; `wi_format` and one-text join missing | A, SP | Two before-entries: ST "e1\ne2" in one message. |
| Role matters only for depth entries; non-depth lore is always system. | Role imported for every entry. | partly | SL | — |
| Card-embedded book at position 4 with broken depth gets 4. | Becomes "next to card". | partly | character_tree | — |
| Entry content gets regex (placement world info; depth only for @D) and macros. | `lore_rewritten`, macros. | same | RX | — |

## 6. Author's note

| ST behaviour (default) | Ours | Status | Files | Test idea |
|---|---|---|---|---|
| One AN per chat; position in chat at depth 4 role system; other positions after / before main; none. | Many notes (global, character, chat); each has `place` depth / before_main / after_main (migration 0026); outside the chat never kept on a message; «none» = switched off. ST chat import: `chat_metadata.note_*` → a chat note. | done 2026-11-16; many notes **отличается нарочно** | N, A, `formats/st_chat.py` | `tests/unit/test_author_notes_st.py` |
| Interval: fires when the count of user messages is divisible by the interval (0 = never). | `interval` = replies skipped (import: N − 1, 0 = note off). | **отличается нарочно** (counts replies, also in group chats and «пусть поговорят») | N, `st_chat` | — |
| Character AN replaces / goes before / after the chat AN, only when the AN fires. | Separate notes, each with its own place. | **отличается нарочно** | N | — |
| AN may be scanned by WI (default off). | Lorebook setting `scan_notes` (off): fired notes join the scan text. | done 2026-11-16 | L, C | `test_author_notes_st_api.py` |
| WI «at the AN top / bottom» goes only when the AN fires, inside its text. | Rides with the first fired note (its depth / place / role); ST-file entries only with a fired note, ours always. | done 2026-11-16 | A | — |

## 7. Macros (expanded in every prompt text, history message, WI content and keys, regex trim strings)

| ST behaviour | Ours | Status |
|---|---|---|
| `{{user}}`, `{{char}}`, `<USER>`, `<BOT>`, `<CHAR>` | All; names without case. Angle forms only in upper case (lower-case `<user>` is often an XML-like tag in presets). | same / angle case **отличается нарочно** |
| `{{charIfNotGroup}}`, `{{group}}` / `<GROUP>`, `{{groupNotMuted}}`, `{{notChar}}` | Engine done (2026-11-14): values `group`, `groupNotMuted`, `notChar`; without them the character / the user (ST's one-character result). Group names wait for the assembly to pass them. | partly (wiring) |
| Card field macros: description, personality, scenario, persona, mesExamples (formatted and raw), card main prompt, card jailbreak, depth prompt, creator notes, version, first message, system prompt | All names and long forms (`charDescription`, `charJailbreak`, `greeting` …); macros inside a card text are expanded too. Values beyond description / personality / scenario / persona / mesExamples wait for the assembly (until then empty). | partly (wiring) |
| `{{original}}` | yes | same |
| `{{newline}}`, `{{trim}}`, `{{noop}}`, `{{// comment}}`, `{{space}}` | Done. Comments go first and whole (also multi-line; nothing inside runs); trim last (eats line breaks around it, also ones `{{newline}}` made). | same |
| `{{random::a::b}}`, `{{random:a,b}}`, `{{pick::a::b}}` (stable per chat), `{{roll:1d6}}`, `{{reverse::x}}` | Done; pick seeded by value `chatId` + text + place; roll takes `NdM±K` and a bare `N`. | same (pick's exact choice differs from ST's) |
| Time / date macros, `timeDiff`, `idle_duration` | Done, English formats (`3:05 PM`, `November 14, 2026`); `datetimeformat` takes the common date tokens; `idle_duration` from value `lastUserMessageTime`, else "just now". | same (ST follows the UI language) |
| `{{lastMessage}}`, `{{lastUserMessage}}`, `{{lastCharMessage}}`, message ids, swipe ids, `{{lastGenerationType}}`, `{{input}}` | Engine done (values by those names, empty / "normal" without them); wait for the assembly to pass them. | partly (wiring) |
| `{{model}}`, `{{maxContext}}`, `{{maxPrompt}}`, `{{maxResponse}}`, `{{isMobile}}`, `{{hasExtension}}` | Engine done (values, empty / "false" without); `hasExtension` always "false". | partly (wiring) |
| Variables set / get / add / inc / dec / has / delete, chat-local (kept in the chat) and global (kept in settings) | All operations and global forms; shared by every text of one request; not kept between replies yet (needs a place in the chat / settings). | partly |
| `{{if …}}…{{else}}…{{/if}}`, `{{outlet::name}}`, `{{banned "x"}}` | if / else (nested, `!` negation, a macro name or `{{…}}` as condition; false = empty, false, 0, off, no; only the chosen branch runs); outlet from value `outlet::name` (empty when none); banned removed (no banned-token list). | partly (outlet wiring) |
| Unknown macro left as is | same (inner macros expanded) | same |

Files: `core/macros.py` (engine), `core/prompt.py` (`substitute`), `core/assembly.py` (values), chat meta for variables. Test: "A{{trim}}\n\nB {{// hidden}}{{newline}}C" → "AB \nC" (`tests/unit/test_macros.py`).

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
| Budget = context size minus response length; real tokenizer. | Window − the connection's `max_tokens` (at most ¾ of the window; none when unset) — same. Counting: `core/tokens.py` by script (pure Python, cached per text), times a ratio learned per connection + model from the service's real prompt count (`token_ratio`); the window is turned into estimated units (`tokens.window`). | done 2026-11-16; counting **отличается нарочно** (learns every model, ST guesses for Gemini / Claude) | C `response_reserve`, `learn_ratio`, `core/tokens.py` |
| Fixed prompts always go; if they alone overflow the request fails. | We drop members' cards, then plugin texts. | **отличается нарочно** (a reply instead of an error) | A |
| History newest → oldest until the budget ends (separator, nudges, continue reserved first); depth items counted on the kept part. Examples after history in priority unless "always keep". | Examples block `examples_mode`: room (default, ST normal: a member's examples go whole before any message), always, never. Then oldest messages, min 6 kept, then cards, then more history. | done 2026-11-16 for examples; min 6 kept **отличается нарочно** | A, P |

## 11. Request body per service

### 11a. Custom OpenAI-compatible

| ST | Ours | Status | Files |
|---|---|---|---|
| Body: messages, model, temperature, max_tokens, stream, presence / frequency penalty, top_p always sent (even 0 / 1); top_k not sent; stop only when non-empty (max 4); logit_bias; seed ≥ 0; n > 1. | Only set connection params sent; preset sampling moved to the connection on import (asked); `stream_options.include_usage` added. With an empty connection nothing about length or temperature is sent. | partly | openai_compat.py, SP, params.py |
| `custom_include_body`, `custom_exclude_body`, `custom_include_headers`. | Same, from an ST preset (`core.presets.provider_extras` → `request.extra`); headers never auth / key / cookie / host / length. `seed` ≥ 0 only, `n` never. | done 2026-11-16 | openai_compat.py, params.py, presets.py, chat.py |
| Prompt post-processing: merge, semi-strict, strict, single user message (+ tool variants). | Same (`post_process`). | done 2026-11-16 | openai_compat.py |
| Messages carry `name` for named example lines. | Same. | done 2026-11-16 | openai_compat.py, A |

### 11b. Google AI Studio (Gemini)

| ST | Ours | Status | Files |
|---|---|---|---|
| `use_sysprompt` (default **off**): off → no systemInstruction, every system message becomes a user turn; on → leading system messages to systemInstruction, one part each. | Same for a preset from ST (flag read from the file, off when absent); our own presets keep it on (system field), **отличается нарочно**. Gemma / picture models never. | done 2026-11-16 | gemini.py, presets.py |
| Same. | done 2026-11-16 | gemini.py |
| Same (all OFF by default; the connection may still set its own). | done 2026-11-16 | gemini.py, params.py |
| Same; no penalties for Gemini; candidateCount 1; includeThoughts on; 2.5 budget, 3 level (max = high). | done 2026-11-16 | gemini.py |
| No prefill outside Claude. | Same (assembly `provider_kind`). | done 2026-11-16 | A |

### 11c. Claude

| ST | Ours | Status | Files |
|---|---|---|---|
| `use_sysprompt` (default off): off → all system messages become user turns, no `system`; on → `system` as text blocks. Empty conversation → user "Let's get started.". | Same for a preset from ST (off when absent); our presets: on. "Let's get started." placeholder, zero-width space for empty text. | done 2026-11-16 | anthropic.py, presets.py |
| Same; names into text; pictures of the model go to the next user turn. | done 2026-11-16 | anthropic.py |
| Same (trimmed; a user turn with thinking or from 4.6 on). | done 2026-11-16 | anthropic.py, A |
| Same rules for limited / newest / thinking models; effort → budget share before 4.6, adaptive + `output_config.effort` from 4.6. | done 2026-11-16 | anthropic.py |
| Prompt caching (server config). | Missing (cost only). | missing | anthropic.py |

## 12. Groups

| ST (default) | Ours | Status | Files |
|---|---|---|---|
| Card mode swap (default) / join / join including muted; prefix / suffix. | All cards with "### Name" + "Also in the scene:", tuned by the chat's «что модель знает о других» (`speakers` `others`). | **отличается нарочно** (finer control per chat) | A, S |
| Activation: natural, list, manual, pooled; muted skipped. | Own orders. | partly | S |
| Group nudge, new-chat separator, group macros, names before every character line. | See sections 3 and 7. | partly | A, M |
| Other members' past reasoning never sent. | Per message regardless of speaker. | partly | A |

## 13. Where each setting lives in ST

- **Preset file** (chat completion): source, models, temperature, penalties, top_p / top_k / top_a / min_p, repetition_penalty, max context and response, `names_behavior`, `send_if_empty`, the prompt texts (`impersonation_prompt`, `new_chat_prompt`, `new_group_chat_prompt`, `new_example_chat_prompt`, `continue_nudge_prompt`, `group_nudge_prompt`), `wi_format`, `scenario_format`, `personality_format`, bias, `prompts`, `prompt_order`, `assistant_prefill`, `assistant_impersonation`, `use_sysprompt`, `squash_system_messages`, `continue_prefill`, `continue_postfix`, `show_thoughts`, `reasoning_effort`, `seed`, `n`, `custom_*` body / headers / post-processing, preset-scoped regex in `extensions`.
  **We consume**: prompts, prompt_order, `injection_order`, squash, sampling (moved to the connection), max context, reasoning effort (partly), impersonation / group nudge / prefill / formats, send_if_empty, new_*_prompt, assistant_impersonation (2026-11-16), use_sysprompt, custom_* body / headers, post-processing (2026-11-16, `core.presets.provider_extras`). **Kept but unused**: names_behavior, continue_*, wi_format, show_thoughts, seed, n, bias.
- **settings.json**: world info settings (depth 2, budget 25 %, cap, recursion off, max steps, min activations, case, whole words, group scoring, include names, strategy), **global regex** (now read), AN defaults and character notes, persona description + position / depth / role, reasoning prefix / suffix / separator / add-to-prompts / max additions / auto-parse, custom stopping strings, example behaviour, prefer card prompt / jailbreak, bias presets.
- **Card**: description, personality, scenario, first message, examples, system prompt, post-history instructions, character note (`depth_prompt`), creator notes, embedded book, linked world, card regex, talkativeness.
- **Chat metadata**: AN text / position / depth / interval / role, chat-bound world, chat variables, scenario / system / examples overrides.
- **Server config file**: Claude caching, prompt placeholder text, Gemini API version.

---

## Ranked: likely causes of a refusal in ours for the same jailbreak / preset

The first findings (Gemini behind an OpenAI-compatible proxy) put the regex trick first. A common "jailbreak" is not a preset at all but a pair of **global regex scripts** (one replaces ordinary spaces with a blank Braille character in user messages in the prompt only; one turns them back for display only). The history then reads as Braille-spaced text. Global regexes live in the ST settings file and never travel with a preset; rules that arrive with a preset or card start unapproved here.

1. **Global regex jailbreak missing or waiting for approval.** Now fixed in two parts (done 2026-11-13): settings.json global regex import and the chip «Регекс ждёт: N». Still to check on the owner's device: «Что видела модель» must show the blank characters in user messages and earlier replies.
2. **System role delivery to Claude and Gemini.** Done 2026-11-16: a preset from ST follows its `use_sysprompt` (off when the file does not say).
3. **Prefill rules.** Done 2026-11-16: Claude / text mode only, trimmed, flipped to a user turn with thinking or on 4.6+, impersonation prefill.
4. **Macros left raw.** Done 2026-11-16 (engine); some values (`lastUserMessage`, `model`, outlets) still wait for the assembly to pass them.
5. **Lorebook entry text and selection.** Done 2026-11-16 except `wi_format` and stem matching (ours on purpose).
6. **Gemini safety settings.** Done 2026-11-16: all off by default.
7. **Missing separators and example structure.** Done 2026-11-16.
8. **Our own wording in the prompt.** Done 2026-11-16 for presets from ST; left: "### Name" and "Also in the scene:" in group chats (section 12).
9. **Tail order.** Done 2026-11-16.
10. **Names behaviour.** ST prefixes every character line in groups (including the speaker).
11. **Past reasoning sent** after import (done: last one, in the model's own tag).
12. **Different history kept.** Done 2026-11-16: response reserve, counting by script with the learned ratio, examples only while room is left; our min 6 messages stays.
13. **Character's Note, AN positions.** Done 2026-11-16 / 17; the interval stays ours on purpose.
14. **Sampling values.** With an empty connection no temperature, penalties or max_tokens are sent, so the proxy's defaults decide (fits "answers badly" more than refusals); Claude limited-sampling rules, custom exclude-body / headers and post-processing missing.

## To confirm on the owner's device (not seen)

- «Что видела модель» for the failing chat: order of system / user / assistant, blank characters in user texts, a trailing assistant (prefill) message.
- The global regex list: are the two jailbreak rules present, enabled and approved.
- The connection's model and params (max tokens, temperature).
