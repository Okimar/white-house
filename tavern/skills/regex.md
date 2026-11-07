---
name: regex
title: Регекс-правила
description: Making, changing, trying out or explaining regex rules - hiding or reformatting status blocks, cleaning model output, stripping thinking from the history, showing a tracker as a styled panel. Also importing rules from SillyTavern files.
audience: assistant
---
A regex rule finds a piece of text and replaces it. It works in one of four places ("when"): "display" (only
what the person sees; the saved message and the model are untouched), "prompt" (only what the model gets; the
chat looks as before), "both" (screen and model, not the saved text), "stored" (the saved message itself,
once, when it is saved). Rules live in a list: global ("for everyone"), a character's, or a preset's. They run
in that order: global, the chat's preset, the chat's characters; inside a list from top to bottom.

The pattern is written the JavaScript way: `/pattern/flags`, for example `/\[status:.*?\]/gs` (g = every match,
i = ignore case, s = the dot also matches a line break, m = ^ and $ work per line, y = sticky). A plain word
without slashes also works but replaces only the first match. In the replacement: `$1`, `$2` = the parts in
brackets, `$<name>` = a named part, `$&` or `{{match}}` = everything found, empty = delete. `{{char}}` and
`{{user}}` may stand in the replacement; in the pattern only when "macros" is "raw" or "escaped".

What this engine does NOT do (say so plainly instead of trying): no `\p{L}` Unicode properties (write the letters
out, `[а-яА-ЯёЁa-zA-Z]`); lookbehind `(?<=…)` must have a fixed length; no `(?i)` inline flags; `\d`, `\w` and `\b`
are ASCII only, exactly as in JavaScript, so `\bслово\b` never matches Russian text (use `(^|[^а-яё])слово(?![а-яё])`
with the i flag). Slash commands are not touched. A rule that runs too long (like `(a+)+$` on a long line) is stopped
and skipped, and the person is told which one.

Tools: app_call on these routes. GET /regex = every rule; GET /regex/rules?scope=global (or character / preset
with &owner=<id>); POST /regex/rules {scope, owner, name, find, replace, when, places, min_depth, max_depth,
render_html, macros, trim, run_on_edit}; PUT /regex/rules/<id> (any fields); DELETE /regex/rules/<id>;
PUT /regex/order {scope, owner, ids}; POST /regex/try {rule: {find, replace, ...}, text} tries a rule on a text
and changes nothing. `places` is a list of "user" (the person's messages), "assistant" (the model's replies),
"lore" (lorebook entries, only for the model). Allowing rules that came with an imported card or preset
(/regex/approve) is the person's own decision: never do it, tell the person where the button is (the
character's "Регекс" tab, or Settings → Регекс).

RECIPES (fix the details to the person's text):
- Hide a status block from the screen and keep it for the model: when "display", find `/\[status:[\s\S]*?\]/g`,
  replace empty. Hide it from the model but keep it on the screen: when "prompt", the same pattern.
- Remove thinking from old messages for the model: when "prompt", places ["assistant"], find
  `/<(think|thinking)>[\s\S]*?<\/\1>/gi`, replace empty (the app already cuts a thinking block out of new replies).
- Clean a habit of the model (for example "As an AI,"): when "stored" so it is gone for good, or "both" to try first.
- Tracker as a panel: when "display", render_html true, find `/\[hp=(\d+)\/(\d+)\]/g`, replace
  `<div style="display:flex;gap:8px;padding:4px 8px;border-radius:6px;background:#223;color:#fff">HP $1 / $2<progress value="$1" max="$2"></progress></div>`.
  Only plain tags and inline styles survive; scripts, forms, frames and outside pictures are cut.
- Only old messages: min_depth 3 (0 is the newest message, 1 the one before).

## Plan
Ask only what the request leaves open about the TEXT (an example of the text to catch is the best answer). Do
not ask how regex works. Plan: one item per rule (a rule does one thing; do not pack two jobs into one pattern),
and a last item that tries each rule on the example text. Say in the plan which place each rule works in
(screen / model / saved text), because the person usually means one of them.

## Write
For each rule: first write the pattern and the replacement, then ALWAYS run POST /regex/try with the person's
real example text (or the closest you have) and read the answer before saving: `ok` must be true, `matches`
must contain what you meant and nothing else, `result` must be the text you expect. If `ok` is false, the `error`
is in Russian and names the problem; fix the pattern, do not save it. Only then POST /regex/rules. Choose
`places` deliberately (a rule for the model's habits is "assistant" only). Name the rule in plain Russian
words the person will recognise. Never turn on render_html for a rule that does not write HTML. Keep patterns
simple and anchored; avoid nested repeats like `(.+)+`.

## Check
- Every rule was tried with POST /regex/try on an example before it was saved, and the example text is shown to
  the person with the result.
- The place (display / prompt / both / stored) matches what the person asked: "hide from me" is display, "the
  model should not see it" is prompt, "remove it for good" is stored; fail a rule in the wrong place.
- No pattern uses what this engine cannot do (\p{...}, variable lookbehind, inline flags, \b with Russian words).
- The rule list order still makes sense (read GET /regex/rules): a rule that depends on another comes after it.
- The report says plainly what the rules do NOT cover (for example slash commands, or messages written before a
  "display" rule are only changed on the screen) and where to turn a rule off.
