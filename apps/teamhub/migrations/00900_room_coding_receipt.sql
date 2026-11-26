-- +goose Up
ALTER TABLE room_coding_jobs ADD COLUMN receipt jsonb;

-- +goose Down
ALTER TABLE room_coding_jobs DROP COLUMN receipt;
