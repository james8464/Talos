-- +goose Up
ALTER TABLE room_goals DROP CONSTRAINT room_goals_status_check;
ALTER TABLE room_goals ADD CONSTRAINT room_goals_status_check
    CHECK (status IN ('active','paused','cleared','done','blocked'));
ALTER TABLE room_goals ADD COLUMN last_verdict text NOT NULL DEFAULT '';
ALTER TABLE room_goals ADD COLUMN last_reason text NOT NULL DEFAULT '';

-- +goose Down
ALTER TABLE room_goals DROP COLUMN last_reason;
ALTER TABLE room_goals DROP COLUMN last_verdict;
ALTER TABLE room_goals DROP CONSTRAINT room_goals_status_check;
ALTER TABLE room_goals ADD CONSTRAINT room_goals_status_check
    CHECK (status IN ('active','paused','cleared'));
