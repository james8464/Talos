-- +goose Up
ALTER TABLE room_coding_jobs ADD COLUMN attempts integer NOT NULL DEFAULT 0;
ALTER TABLE room_coding_jobs ADD COLUMN lease_expires_at timestamptz;
CREATE INDEX room_coding_jobs_lease ON room_coding_jobs(status, lease_expires_at)
    WHERE status = 'claimed';

-- +goose Down
DROP INDEX room_coding_jobs_lease;
ALTER TABLE room_coding_jobs DROP COLUMN lease_expires_at;
ALTER TABLE room_coding_jobs DROP COLUMN attempts;
