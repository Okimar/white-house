---
name: lorebook-work
title: Работа с готовым лорбуком
description: Working through an EXISTING lorebook or character entry by entry - read all its entries, edit or move many of them, make links between entries, take characters (people) out of a lorebook into Characters.
audience: assistant
---
The material is already in the app. Never ask the user to send, paste or upload a lorebook or a card: find
it in the list "IN THE APP RIGHT NOW" or with list_lorebooks / list_characters. If the user named one, take
that one; if the request says "the existing one" and there are several, ask WHICH one (a question about
content), never to send it. Only if you truly find nothing, say plainly what you looked at and that nothing
was found.

A real book has 100 or more entries. You cannot hold them all, and you must not try: a job "over all entries"
is a SWEEP. Tavern lists every entry itself, hands them to you in batches with their full text, counts them,
and does not let the item finish until each entry is handled or reported as skipped. The report then says
"N of N". So: never page through the book yourself, never say "all" unless Tavern's count says so, and give
every entry of a batch an answer ("handled" or "skipped" with a reason).

THE KINDS OF JOBS
- READ and summarise: per batch put in "notes" what matters (names, facts, who is who). Nothing is changed.
- EDIT many: do exactly what the user asked to each entry and nothing else. Keep the title, the keywords and the
  meaning unless the user asked to change them. Do not rewrite the style of an entry that needs no change.
  Report an unchanged entry as handled.
- MOVE / regroup: update_bubble with parent_id. The parent must exist and must not be the entry's own
  descendant. Read the book's structure first (list_bubbles pages).
- TAKE CHARACTERS OUT of a lorebook into Characters: see below.
- LINKS between entries: see below.

TAKING CHARACTERS OUT
- A character is a named person or being with a will and a role: someone who speaks, wants, owns or does.
  Not a place, an object, a custom, a faction as a whole, a law. A faction becomes a character only if the
  user said so.
- One card per person. A person met in several entries gets ONE card that gathers what all those entries say;
  your "notes" and the list "Already made in this task" tell you who already has a card: update that card
  (update_character), never make a second one.
- Use only what the entries say. Do not invent a past, a body or a voice the book does not give; where the
  book is silent, leave it out or make a small plausible detail and keep it short. Keep every name, rank,
  place and fate exactly.
- A card is a voice, not a dossier: follow the "characters" skill for the card (the core goes into
  "description", first_mes is a greeting in the person's voice). For an extraction the core may be short
  (150-400 words) when the book says little.
- Leave the lorebook as it is. Do not delete entries after extracting unless the user asked.
- Entries with no person in them are handled with the note "no character", not skipped silently.

LINKS (read this before you link anything)
A link makes the search bring the partner every time one of the two is found. Each link adds text to many
replies, so the right number of links is SMALL. Link two entries only when one cannot be understood without
the other (a rank that is explained only in the other entry, a place and the one rule that makes it make
sense, a person and the oath that defines them). NOT for "related", "same faction", "same place", "mentions".
Parent and child in the tree are already together: NEVER link an entry with its direct parent (or child), even if the
user asks for more links; Tavern refuses it, then pick another pair or stop. Tavern limits links per request
(about one per eight entries, at most two per entry) and the tool refuses beyond it: if it refuses, stop and
say so; do not look for another way. Only the user can ask for more ("make 30 links", "as many as
possible"). Give every link a reason of one sentence that names what is not understood without the other.
Before linking, read list_links to see what exists and how many are still allowed.

## Plan
No questions about how it is done. Ask only what the user's request leaves open about CONTENT, with a
default: for extraction - which kinds of people count (only named people / also groups), and what to do with
people who are only mentioned in one line (make a short card / leave them out); for edits - the one thing
that should change if the request is loose; for links - nothing, unless the user did not say how many (then
use the usual limit and say the number in the plan).

The plan, in this order:
1. The item that goes through the book: set "over" to the book's id (from "IN THE APP RIGHT NOW"). Its
   "detail" says exactly what is done WITH each entry. One sweep item per kind of job; do not plan one item
   per entry or per batch.
2. Only if needed, a small item after it that works on the result (for example: merge duplicate cards, make
   the links, test keywords with try_phrase).
Decisions: which book (name and id), what counts as a character or what changes, the link limit if links
are asked for, what is left untouched. At most 4 items.

## Write
- In a sweep: for each entry of the batch do the item, with tools, then answer with "actions": [] and
  "handled" (numbers), "skipped" ([{"n", "reason"}]) and "notes". Entries you do not list count as not done
  and you are asked again.
- Names and ids come from the batch text. Read the rest of a cut entry with read_bubble.
- Links: list_links first; make a link only for a pair that passes the test above, with a one-sentence reason.
  After the refusal "Link limit reached" make no more.
- Extraction: before creating a card check "Already made in this task" and your notes for the same person.
- Do not touch entries outside the batch.

## Check
- The count: the FACTS say "N of N" entries handled; fail if any entry is NOT handled or if skipped ones have
  no real reason (name them).
- Nothing the user did not ask for was changed: pick three entries that needed no change and check they are
  as they were.
- Characters (if the job was extraction): no person has two cards (list_characters), no card is a place or
  an object, every name and fate matches the entries, and the lorebook still has all its entries.
- Links (if the job made links): the number is within the limit the FACTS give; each link has a reason; for
  three links, read both entries and say whether one really cannot be understood without the other. Fail every
  link that only joins "related" entries (name it).
- Moved entries: each has a parent that exists, and no entry is under itself.
