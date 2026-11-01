-- Stage 7а: who speaks. Every reply knows which character wrote it; members get the settings
-- that decide who speaks (talkativeness, muted) and a short description others may see.

-- The character who wrote this message (assistant replies); NULL for the user, system and
-- messages from before this stage that could not be matched by name.
ALTER TABLE messages ADD COLUMN character_id TEXT REFERENCES characters(id) ON DELETE SET NULL;

UPDATE messages SET character_id = (
    SELECT cm.character_id FROM chat_members cm JOIN characters c ON c.id = cm.character_id
    WHERE cm.chat_id = messages.chat_id AND cm.is_me = 0 AND c.name = messages.name
    ORDER BY cm.position LIMIT 1
) WHERE role = 'assistant';

-- talkativeness: 0..100, how readily the member speaks when nobody is named.
-- muted: does not speak and is left out of the prompt until switched back on.
-- blurb: one or two lines about the member, shown to the model when another member speaks.
ALTER TABLE chat_members ADD COLUMN talkativeness INTEGER NOT NULL DEFAULT 50
    CHECK (talkativeness BETWEEN 0 AND 100);
ALTER TABLE chat_members ADD COLUMN muted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chat_members ADD COLUMN blurb TEXT NOT NULL DEFAULT '';
