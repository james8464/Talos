-- +goose Up
ALTER TABLE room_coding_jobs ADD COLUMN next_attempt_at timestamptz;
CREATE INDEX room_coding_jobs_available ON room_coding_jobs(status, next_attempt_at)
    WHERE status = 'queued';

-- +goose Down
DROP INDEX room_coding_jobs_available;
ALTER TABLE room_coding_jobs DROP COLUMN next_attempt_at;
