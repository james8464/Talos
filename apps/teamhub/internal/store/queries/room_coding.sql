-- name: CreateRoomCodingJob :one
INSERT INTO room_coding_jobs(id,tenant_id,room_id,requester_actor_id,kind,target_path,request)
VALUES($1,$2,$3,$4,$5,$6,$7)
RETURNING *;

-- name: ListRoomCodingJobsForRoom :many
SELECT * FROM room_coding_jobs WHERE room_id=$1 ORDER BY created_at DESC LIMIT 20;

-- name: ClaimNextRoomCodingJob :one
UPDATE room_coding_jobs
SET status='claimed', claimed_by=$1, claimed_at=clock_timestamp()
WHERE id = (
  SELECT id FROM room_coding_jobs WHERE status='queued' ORDER BY created_at
  FOR UPDATE SKIP LOCKED LIMIT 1
)
RETURNING *;

-- name: CompleteRoomCodingJob :exec
UPDATE room_coding_jobs
SET status=$2, result=$3, completed_at=clock_timestamp()
WHERE id=$1 AND status='claimed';
