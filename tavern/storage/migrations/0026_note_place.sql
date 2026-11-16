-- Wave 2 of the SillyTavern parity plan: where an author's note goes, as SillyTavern's author's note positions.
--   depth       : inside the chat, `depth` messages from the end (what every note did so far);
--   before_main : right before the preset's main prompt;
--   after_main  : right after the preset's main prompt.
-- Existing notes keep going inside the chat.
ALTER TABLE author_notes ADD COLUMN place TEXT NOT NULL DEFAULT 'depth'
    CHECK (place IN ('depth', 'before_main', 'after_main'));
