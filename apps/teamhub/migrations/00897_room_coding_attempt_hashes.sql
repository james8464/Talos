-- +goose Up
ALTER TABLE room_coding_jobs ADD COLUMN attempt_hashes text[] NOT NULL DEFAULT '{}';

-- +goose Down
ALTER TABLE room_coding_jobs DROP COLUMN attempt_hashes;
