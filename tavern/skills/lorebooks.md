---
name: lorebooks
title: Лорбуки
description: Making a NEW lorebook (a world, a city, a school, a setting for roleplay or a text RPG) or rewriting a large part of one. Not for going through an existing book entry by entry (see lorebook-work).
audience: assistant
---
A good lorebook is not an encyclopedia. It gives the chat model the RULES of the world, what the world lives on
and its vibe, so the model can PREDICT how the world reacts to anything the player does ("what if…?"), not
simulate it from a pile of facts. Compact and deep beats big and flat.

THE SHAPE (unless the user asked otherwise):
- One lorebook. Its first entry is «the core of the world» (title in the content language, e.g. «Суть мира»),
  mode "always", about 150 words: the vibe in 1–2 sentences; 3–5 laws of the world (how things work here, what
  everything costs); what this world is NOT (1–2 lines); what names sound like here.
  The laws must cover the twist whole, so the narrator never has to guess: how it is given, how it is taken by
  force, whether it can be faked or stored, and what else people pay with. Every later entry obeys these laws:
  if an entry needs an exception, the law says so.
  One money line: the coins people use and what they are worth against each other, with one everyday price
  («1 марка = 10 грошей; кружка сивухи — грош»). Later entries use only these coins.
- 5–8 top entries, mode "search": the big parts the plan chose (a place, a power, a trade, a faith, a danger,
  a way of life). Each 60–150 words.
- 15–30 sub-entries (parent_id = their top entry), mode "search", each with its OWN keywords: a district, a
  person, a custom, a creature, a law. 40–120 words each. A sub-entry is found only by its own words; the
  parent does not bring it. Use "always" for a sub-entry only for 1–2 lines that must go with the parent.
- Not bigger. More entries only if the user asked for a big one.

EVERY ENTRY (top and sub) has:
1. What it is, in concrete things you could see, smell, buy or be punished for. No "shrouded in mystery".
2. What it wants NOW and what stands in its way (a faction, a place, a person). This gives play without a
   ready plot.
3. 2–4 lines "If … → …": the price, the limit and how the world reacts. Example: "If someone sells ichor
   without a stamp → the Guild does not call the guard; they take two fingers in public."
   Vary the reactions: most are not death or maiming but a fine, a debt, gossip, a ban, a favour owed,
   being remembered, a door that closes. Violence is the exception that shows how serious a rule is.
Example of an entry (English here; write yours in the content language):
  Title: The Salt Market. Keywords: salt market, salt-market, salters, Brine Row.
  "Everything in Gullmouth is paid in salt, and the Salt Market sets its weight. Stalls on stilts over the
  mud, buyers taste a pinch before they bargain, a cheat is made to eat his whole sack. The salters want the
  new pier closed: it brings cheap salt from the south. The harbour master wants it open: he gets a cut.
  If you pay with wet salt → the deal is void and everyone at the stall remembers your face.
  If you ask for credit → only with a salter as witness, who takes a tenth.
  If you cross the salters → no inn will cook for you; food here is salted at the market."

THE TWIST: a familiar base the player recognises at once (a mining town, a mage school, a northern kingdom) plus
1–2 twists of your own that change how things work (not just names). Every top entry shows how the twist
touches it.

NAMES: easy to remember from the first hearing, and each gives an image of looks at once. Familiar, common
names and plain nicknames are better than invented ones: «Рыжая Марта», «Однорукий Гоша», «Толстый Пьер»,
"Old Tom", "the Grey Widow". These only show the style: never reuse them, make new ones for this world.
Places named the way locals would: by what is there or what happened there.
Do not make every name "Dark/Shadow/Bone + noun".

KEYWORDS: 3–6 per entry, the words a player would actually type: the name, its short form, the nickname,
the thing itself. Always add the one plain word a player uses for it in a sentence: «Палата» for «Вексельная
палата», «храм» and «церковь» for «Церковь Последней Секунды», «шахта» for «Забои «Глухой клин»», «порт» for
the docks. When the world has only one place of that kind, its plain noun is the RIGHT keyword, not a common
word: a player who writes «иду в храм» means that church. A nickname that is an
ordinary word («Сухая», «Седой», «стеклянный») is never a keyword on its own: it would wake on "a dry shirt";
use it together with the name («Нелли Сухая»). Several word forms (Russian: «порт», «порту», «портовый»; or a stem the search extends).
Never common words that fire all the time («мир», «магия», «война», "city", "people"). The words of
THIS world's theme are common words too: if the world is about debt, «долг» comes up in every other message;
if magic costs memory, «магия», «чары», «память» do. Key such an entry by its own names instead («Вексельная
палата», «ростовщик»).

BANNED CLICHÉS (unless the user asked for one): an ancient evil awakens / a seal weakens; a chosen one or a
prophecy; a dark lord without his own reasons; wise ancient elves, drunk smith dwarves, dumb savage orcs
(without a twist); magic without a price; a forgotten ancient civilisation that knew everything; a secret
order that really rules everything; a thieves' guild with a code of honour; a puppet king and an evil
advisor; a church of hypocrites and witch-burning inquisitors as its only trait; starting in a tavern, a
hooded stranger; light against darkness, good against evil; an artifact that will save or doom everyone;
dragons as the oldest and strongest; necromancers evil just for being necromancers; "ancient, forgotten,
forbidden" everywhere; a faction with no inner quarrels; a world where nothing happens without the hero;
filler ("full of secrets", "hides dangers", "no one knows"); the apocalypse at the door; the villain is the
hero's father or brother; secret royal blood in a commoner.

TWO USES: a chat with one character (the world as the character meets it: the lorebook backs a 1-on-1 chat)
or a text RPG (a narrator runs the world for the player). The shape is the same; for an RPG put more weight
on "If … → …" lines and on what each side wants now.

## Plan
Questions (before the plan), in this order, each with a default:
1. What it is for: a text RPG / a chat with a character. Default: text RPG if the request says RPG, game or
   adventure; otherwise a chat with a character.
2. The familiar base and tone: 3 concrete options, each a different kind of place and mood (for example a
   mountain mining town, a mage school, a desert caravan road, a swamp principality, a capital in a long
   siege). Do not default to a port city: pick the default from what the request suggests.
3. The twist: 3 options that change how the world WORKS, not just how it looks.
4. Only if the request leaves it open: the focus (the whole world / one city / one school or guild).
Do not ask about the size: it is compact unless the user said otherwise.

The plan shows the skeleton so the user's "go" approves it:
- "decisions": the use (RPG / chat), the base, the tone, the twist(s); the core of the world in 3–5 short
  laws; the name style with 2–3 new sample names (not the ones from this skill); "size: 5–8 top entries, 15–30 sub-entries, 60–150 words each";
  "no clichés from the banned list"; and "in the final report, offer to make a narrator and a preset for the
  game" (for an RPG).
- "items", in this order:
  1. Create the lorebook and write «the core of the world» (always).
  2…N. One item per top entry: its title, one line of what it is and what it wants now, and the 2–4
     sub-entries to make under it (their titles).
  Last. "Test with player phrases": try 4–5 messages a player would write, fix keywords that miss or fire
     where they should not.
- At most 10 items in all.

## Write
- Create the lorebook once (item 1) and reuse its id from the earlier summaries; never create a second one.
- For a top-entry item: first create the top entry, then its sub-entries with parent_id = the top entry's id
  (you get it from the result). Every sub-entry has its own keywords.
- Check each entry before you send it: concrete things? what it wants now and what blocks it? 2–4 "If … →"
  lines? 60–150 words (sub-entries 40–120)? memorable names that show looks? nothing from the banned list?
- Links between entries: none by default. Make one (link_bubbles, with a reason) only when one entry cannot
  be understood without the other; Tavern limits them and refuses beyond the limit. Never link an entry with its
  direct parent or child (they already go together), even if the user asks for more links.
- Do not repeat the core of the world inside other entries; refer to its laws through concrete effects.
- Before each entry, reread the core laws (they are in the summary of item 1): nothing in the entry may break
  one (forgeries where the law says none exist, coins where the law says there is one currency). Numbers must
  add up with the ones already written (a wage that "does not cover" a bed must be smaller than the bed's
  price).
- The test item: call try_phrase with 6–8 different player messages written the way a player writes: short,
  with the plain word, never the full title («иду в Палату», «захожу в храм», «ищу Нелли»), plus 2 messages
  that are NOT about any entry but use an ordinary word from a nickname or title («надеваю сухую рубаху») —
  nothing should come for those. Also one message per top entry with only its plain word («иду в храм»,
  «спускаюсь в шахту»): each must bring its entry. If an entry that should come stays in "not_found",
  add the words the player used to its keywords (update_bubble); if a wrong one comes, remove the word that
  woke it. In "say", list each phrase and what it brought.

## Check
- «The core of the world» exists, is "always", about 150 words or less, and names 3–5 laws of the world and
  what the world is not.
- The size: 5–8 top entries and 15–30 sub-entries (unless the user asked otherwise); entries are not over
  150 words (quote the longest).
- Every entry says what it wants now and what stands in its way, and has 2–4 "If … → …" lines with a price,
  a limit or a reaction (quote one entry that lacks them, if any).
- The twist changes how things work and shows up in the top entries, not only in the core.
- Nothing from the banned cliché list (name the cliché and the entry if found).
- Names are easy to remember and give an image of looks; not every name is "Dark/Shadow/Bone + noun".
- Sub-entries have their own keywords; no keyword is a common word that would fire all the time. Name the
  2–3 words of this world's theme (what the twist is about: debt, magic, salt…) and fail if any entry has one
  of them, or a form of it, as a keyword. The plain noun of a place the world has only one of («храм»,
  «шахта», «кабак») is right: never ask to remove it. Fail instead if a top entry LACKS its plain word.
- Money: the core names the coins and their worth; fail if an entry uses a coin the core does not name.
- Reactions vary: count the "If … →" lines ending in death or maiming; fail if they are more than a third.
- No name from the skill's examples (Рыжая Марта, Однорукий Гоша, Толстый Пьер, Old Tom, the Grey Widow).
- Keywords work: try 3 short player messages yourself with try_phrase, using plain words, not full titles
  (one about a place, one about a person, one with an ordinary word from a nickname that should bring
  nothing); say which phrase brought what.
- The core laws hold: take each law of «the core of the world» and look for an entry that breaks it (quote the
  law and the entry). Check numbers that compare (prices, wages, ages) for sense.
- The core says how the twist is given, taken by force, faked or stored, and what else people pay with, if
  entries use any of these.
- Links: few or none. Each link has a reason and joins two entries where one cannot be understood without the
  other (not merely related ones); fail every link that does not pass.
