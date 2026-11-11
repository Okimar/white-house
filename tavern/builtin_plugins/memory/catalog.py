"""What the assistant is told about each action of the memory (ADR 0019 point J): `api.action(name, fn, describe=,
reads=, confirm=)`. `describe` is one English line for a model (what it takes, what it gives); an action WITHOUT a line
is not shown to the assistant (it can still be called by name, as the page does). `reads` = it changes nothing (no
journal, no waiting for replies). `confirm` = the assistant must ask the user first with that question.

Every action takes `chat_id` (the chat's id) unless its line says otherwise. Chapters are named by `chapter_id`
(from `chapters`), messages by their NUMBER in the chat (`number`) or `message_id`.
"""

from __future__ import annotations

# name -> (describe, reads, confirm)
ACTIONS: dict[str, tuple[str, bool, str]] = {
    # chapters
    "state": ("{chat_id} -> memory on/off in the chat, counts of pieces / vectors / chapters, the background job", True, ""),
    "chapters": ("{chat_id} -> the chapters of the open branch: id, number, first_no..last_no, title, state, summary, "
                 "locked, plus the plot, members' flags and the job", True, ""),
    "split": ("{chat_id, number} -> a new chapter starts at that message number", False, ""),
    "merge": ("{chat_id, chapter_id} -> joins the chapter with the one before it", False, ""),
    "move_border": ("{chat_id, chapter_id, number} -> the chapter now starts at that message number", False, ""),
    "remove": ("{chat_id, chapter_id} -> deletes a chapter border (the chapter can be restored)", False, ""),
    "restore": ("{chat_id, chapter_id} -> brings a deleted chapter back", False, ""),
    "accept": ("{chat_id, chapter_id} -> accepts the changed text of a stale chapter", False, ""),
    "edit": ("{chat_id, chapter_id, title?, whole?} -> rename a chapter, or send it whole when found", False, ""),
    # summaries and the plot
    "summary_edit": ("{chat_id, chapter_id, text} -> your own summary of a chapter, locked against automatic work",
                     False, ""),
    "summary_lock": ("{chat_id, chapter_id, locked} -> lock or unlock a chapter's summary", False, ""),
    "summary_restore": ("{chat_id, chapter_id, text_id} -> an older version of a summary becomes the current one",
                        False, ""),
    "summary_versions": ("{chat_id, chapter_id} -> every version of a chapter's summary", True, ""),
    "plot_edit": ("{chat_id, text} -> your own «Сюжет до сих пор» (the plot so far), locked", False, ""),
    "plot_lock": ("{chat_id, locked} -> lock or unlock the plot", False, ""),
    "plot_restore": ("{chat_id, text_id} -> an older plot becomes the current one", False, ""),
    "plot_versions": ("{chat_id} -> every version of the plot on this branch", True, ""),
    "resummarize": ("{chat_id, chapter_id} -> asks the memory to write the chapter's summary again (one model "
                    "request, in the background)", False, ""),
    "plot_rebuild": ("{chat_id} -> asks to build the plot again from the summaries (background, a model request)",
                     False, ""),
    "member_set": ("{chat_id, character_id, remembers_all?, no_layer?} -> a member remembers every chapter / gets no "
                   "development layer", False, ""),
    "set_enabled": ("{chat_id, enabled} -> switch the memory on or off in this chat", False, ""),
    "vectorize": ("{chat_id} -> press «Векторизовать»: wake the background job (vectors, chapters, summaries)", False, ""),
    # development layers and «Сейчас:» lines
    "layers": ("{chat_id} -> development layers per character / persona / face and the «Сейчас:» lines of lorebook "
               "bubbles", True, ""),
    "layer_versions": ("{chat_id, target} -> versions of one character's development layer", True, ""),
    "layer_edit": ("{chat_id, target, text} -> your own development layer of a character, locked", False, ""),
    "layer_lock": ("{chat_id, target, locked} -> lock or unlock a layer", False, ""),
    "layer_restore": ("{chat_id, target, text_id} -> an older layer becomes the current one", False, ""),
    "layer_chats": ("{chat_id, target} -> other chats a layer can be copied to", True, ""),
    "layer_transfer": ("{chat_id, target, to_chat_id} -> copy a layer to another chat", False, ""),
    "now_edit": ("{chat_id, block_id, text} -> your own «Сейчас:» line for a lorebook bubble in this chat", False, ""),
    "now_delete": ("{chat_id, block_id} -> remove a «Сейчас:» line", False, ""),
    "now_write": ("{chat_id, block_id} -> write the line into the lorebook for good (changes the lorebook)", False, ""),
    # «Слово и нити»
    "threads": ("{chat_id} -> promises, vows, claims, debts, secrets and quests with their quotes and statuses", True, ""),
    "thread_edit": ("{chat_id, thread_id, ...fields} -> change a record by hand (locked)", False, ""),
    "thread_status": ("{chat_id, thread_id, status: active|kept|broken|dropped} -> a record's status on this branch",
                      False, ""),
    "thread_lock": ("{chat_id, thread_id, locked} -> lock or unlock a record", False, ""),
    "thread_delete": ("{chat_id, thread_id} -> delete a record (it can be restored)", False, ""),
    "thread_restore": ("{chat_id, thread_id} -> bring a deleted record back", False, ""),
    "thread_add": ("{chat_id, kind, who, quote, ...} -> add a record by hand with an exact quote", False, ""),
    # faces (mini-cards)
    "faces": ("{chat_id, all?, query?} -> mini-cards of side characters of this chat (all = of every chat)", True, ""),
    "face_versions": ("{chat_id, face_id} -> versions of a mini-card", True, ""),
    "face_edit": ("{chat_id, face_id, name?, aliases?, card?} -> change a mini-card by hand (locked)", False, ""),
    "face_lock": ("{chat_id, face_id, locked}", False, ""),
    "face_favourite": ("{chat_id, face_id, favourite} -> a favourite is used in every chat where it is named", False, ""),
    "face_use": ("{chat_id, face_id, in_use} -> use or do not use the mini-card in this chat", False, ""),
    "face_restore": ("{chat_id, face_id, text_id} -> an older version of a mini-card", False, ""),
    "face_delete": ("{chat_id, face_id} -> delete a mini-card (it can be brought back)", False, ""),
    "face_undelete": ("{chat_id, face_id}", False, ""),
    "face_merge": ("{chat_id, face_id, into_id} -> two mini-cards of one person become one", False, ""),
    "face_add": ("{chat_id, name, card, aliases?} -> a NEW mini-card for a side character, linked to this chat", False,
                 "Создать мини-карточку второстепенного персонажа?"),
    "face_make_real": ("{chat_id, face_id} -> turn a mini-card into a real character card", False,
                       "Сделать из мини-карточки настоящего персонажа?"),
    # the whole chat
    "bulk_estimate": ("{chat_id} -> the price and size of going through the whole chat (requests, tokens, money)",
                      True, ""),
    "bulk_state": ("{chat_id} -> progress of the whole-chat run and what it really cost so far", True, ""),
    "bulk_start": ("{chat_id, confirmed: true} -> go through the whole chat in the background (spends money: read "
                   "bulk_estimate first and tell the user the price)", False, "Разобрать весь чат? Это тратит деньги."),
    "bulk_stop": ("{chat_id} -> stop the whole-chat run (the work done stays)", False, ""),
    # documents
    "docs": ("{chat_id} -> every document of the memory with: attached to this chat or not, state, chapters", True, ""),
    "doc_view": ("{doc_id} -> a document's chapters with their summaries", True, ""),
    "doc_estimate": ("{doc_id, with_summaries?} -> the price of vectors and summaries for a document", True, ""),
    "doc_attach": ("{chat_id, doc_id} -> the chat may now find this document", False, ""),
    "doc_detach": ("{chat_id, doc_id} -> the chat no longer finds this document", False, ""),
    "doc_start": ("{doc_id, confirmed: true, with_summaries?} -> start vectors and summaries for a document (spends "
                  "money: tell the user the price from doc_estimate)", False, "Начать разбор документа? Это тратит деньги."),
    "doc_delete": ("{doc_id} -> delete a document with all its work", False, "Удалить документ из памяти?"),
}
