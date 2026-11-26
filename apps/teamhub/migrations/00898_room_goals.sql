-- +goose Up
CREATE TABLE room_goals (
    room_id text PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
    tenant_id text NOT NULL,
    created_by_actor_id text NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
    objective text NOT NULL,
    status text NOT NULL CHECK (status IN ('active','paused','cleared')),
    turns_used integer NOT NULL DEFAULT 0,
    max_turns integer NOT NULL DEFAULT 20,
    criteria text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- +goose Down
DROP TABLE room_goals;
