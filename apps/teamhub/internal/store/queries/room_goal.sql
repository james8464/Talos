-- name: UpsertRoomGoal :one
INSERT INTO room_goals(room_id,tenant_id,created_by_actor_id,objective,status,turns_used,max_turns,criteria)
VALUES($1,$2,$3,$4,'active',0,$5,$6)
ON CONFLICT (room_id) DO UPDATE
SET tenant_id=EXCLUDED.tenant_id, created_by_actor_id=EXCLUDED.created_by_actor_id,
    objective=EXCLUDED.objective, status='active', turns_used=0,
    max_turns=EXCLUDED.max_turns, criteria=EXCLUDED.criteria, updated_at=clock_timestamp()
RETURNING *;

-- name: GetRoomGoal :one
SELECT * FROM room_goals WHERE room_id=$1;

-- name: SetRoomGoalStatus :one
UPDATE room_goals SET status=$2, updated_at=clock_timestamp()
WHERE room_id=$1 RETURNING *;

-- name: NextActiveRoomGoal :one
SELECT * FROM room_goals
WHERE status='active' AND turns_used < max_turns
ORDER BY updated_at LIMIT 1;

-- name: IncrementRoomGoalTurns :one
UPDATE room_goals SET turns_used=turns_used+1, updated_at=clock_timestamp()
WHERE room_id=$1 AND status='active' RETURNING *;

-- name: RecordRoomGoalVerdict :one
UPDATE room_goals SET last_verdict=$2, last_reason=$3, updated_at=clock_timestamp()
WHERE room_id=$1 RETURNING *;

-- name: LatestRoomConversationBody :one
SELECT body FROM messages
WHERE room_id=$1 AND message_kind='conversation'
ORDER BY room_seq DESC LIMIT 1;
