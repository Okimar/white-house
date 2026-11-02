-- Stage 10а: the assistant's engine. A conversation carries the task it is working on (questions,
-- plan with item states, messages that came in meanwhile, critic rounds) as JSON, so the work
-- survives closing the window and goes on in the next request.
ALTER TABLE assistant_chats ADD COLUMN task TEXT NOT NULL DEFAULT '{}';

-- «Что я о вас знаю»: the user's tastes the assistant remembered (or the user wrote), shown and
-- edited in its settings, given to every role. chat_id: the conversation it was learned in.
CREATE TABLE assistant_memory (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL,
    chat_id    TEXT REFERENCES assistant_chats(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
) STRICT;
