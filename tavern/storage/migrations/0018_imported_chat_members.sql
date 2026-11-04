-- Chats imported from a file used to get no members, so the model never saw the card.
-- A chat's main character is always a member (services/cast.py keeps it so): give them that back.
INSERT INTO chat_members (chat_id, character_id)
SELECT c.id, c.character_id FROM chats c
WHERE c.character_id IS NOT NULL
  AND c.character_id IN (SELECT id FROM characters)
  AND NOT EXISTS (SELECT 1 FROM chat_members m WHERE m.chat_id = c.id AND m.character_id = c.character_id);
