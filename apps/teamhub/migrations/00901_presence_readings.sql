-- +goose Up
CREATE TABLE presence_readings (
    tenant_id text NOT NULL,
    human_actor_id text NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
    people integer NOT NULL CHECK (people >= 0 AND people <= 100),
    faces integer NOT NULL CHECK (faces >= 0 AND faces <= 100),
    captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, human_actor_id)
);

-- +goose Down
DROP TABLE presence_readings;
