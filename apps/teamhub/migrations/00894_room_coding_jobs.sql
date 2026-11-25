-- +goose Up
CREATE TABLE room_coding_jobs (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    room_id text NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    requester_actor_id text NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('job','plan')),
    target_path text NOT NULL DEFAULT '',
    request text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','claimed','done','failed','cancelled')),
    result text NOT NULL DEFAULT '',
    claimed_by text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    claimed_at timestamptz,
    completed_at timestamptz
);
CREATE INDEX room_coding_jobs_room_created ON room_coding_jobs(room_id, created_at DESC);
CREATE INDEX room_coding_jobs_status_created ON room_coding_jobs(status, created_at);

-- +goose Down
DROP TABLE room_coding_jobs;
