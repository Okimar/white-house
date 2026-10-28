-- Character tree, after the owner's review: free bubbles, chance, "через N ответов", links.

-- detached = 1: the bubble hangs free on the map, joined to nothing. It and everything under it
-- is not sent until it is joined to the card or to a joined bubble again.
ALTER TABLE character_blocks ADD COLUMN detached INTEGER NOT NULL DEFAULT 0;
-- Green bubbles: the chance (percent) that a found bubble really reaches the model. A failed
-- roll keeps it out for its "stays N replies" too, so regenerating cannot force it in.
ALTER TABLE character_blocks ADD COLUMN chance INTEGER NOT NULL DEFAULT 100 CHECK (chance BETWEEN 0 AND 100);
-- Blue bubbles: replies to skip between sendings, 0 = every reply.
ALTER TABLE character_blocks ADD COLUMN every INTEGER NOT NULL DEFAULT 0 CHECK (every BETWEEN 0 AND 1000);

-- missed = 1: the bubble was found but its chance failed at hit_gen.
ALTER TABLE chat_block_hits ADD COLUMN missed INTEGER NOT NULL DEFAULT 0;

-- A link between two bubbles of one character, usually in different groups ("Руки › Пальцы" and
-- "Ноги › Пальцы"): when one goes to the model, the other goes too (with its sub-bubbles).
-- Stored once per pair, a < b.
CREATE TABLE block_links (
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    a            TEXT NOT NULL REFERENCES character_blocks(id) ON DELETE CASCADE,
    b            TEXT NOT NULL REFERENCES character_blocks(id) ON DELETE CASCADE,
    PRIMARY KEY (a, b),
    CHECK (a < b)
) STRICT;
CREATE INDEX idx_block_links_character ON block_links(character_id);
CREATE INDEX idx_block_links_b ON block_links(b);
