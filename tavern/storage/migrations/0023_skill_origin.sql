-- Stage 10ж: where a skill came from. "" = written by the person in the tab; "assistant" = written by the
-- assistant; "url:<link>" / "file:<name>" = imported (someone else's text, shown with its source and
-- read by the assistant with a reminder that it cannot widen the assistant's powers).
ALTER TABLE skills ADD COLUMN origin TEXT NOT NULL DEFAULT '';
