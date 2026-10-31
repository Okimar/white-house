-- Stage 6а: which catalog service a connection was made from, and what we learned about its models.

ALTER TABLE connections ADD COLUMN service TEXT NOT NULL DEFAULT '';

-- Model facts per connection: from the service's own list (`info`) and from trial requests
-- (`probe`: setting -> "ok" | "rejected"). Refreshed by "Проверить".
CREATE TABLE model_info (
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    model         TEXT NOT NULL,
    info          TEXT NOT NULL DEFAULT '{}',
    probe         TEXT NOT NULL DEFAULT '{}',
    checked_at    TEXT NOT NULL,
    PRIMARY KEY (connection_id, model)
) STRICT;
