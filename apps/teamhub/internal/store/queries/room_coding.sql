-- name: CreateRoomCodingJob :one
INSERT INTO room_coding_jobs(id,tenant_id,room_id,requester_actor_id,kind,target_path,request)
VALUES($1,$2,$3,$4,$5,$6,$7)
RETURNING *;

-- name: ListRoomCodingJobsForRoom :many
SELECT * FROM room_coding_jobs WHERE room_id=$1 ORDER BY created_at DESC LIMIT 20;

-- name: ClaimNextRoomCodingJob :one
UPDATE room_coding_jobs
SET status='claimed', claimed_by=sqlc.arg(claimed_by), claimed_at=clock_timestamp(),
    lease_expires_at=clock_timestamp() + make_interval(secs => sqlc.arg(lease_seconds)::int),
    attempts=attempts+1
WHERE id = (
  SELECT id FROM room_coding_jobs
  WHERE status='queued'
     OR (status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < clock_timestamp())
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED LIMIT 1
)
RETURNING *;

-- name: RequeueRoomCodingJob :execrows
UPDATE room_coding_jobs
SET status='queued', claimed_by='', claimed_at=NULL, lease_expires_at=NULL, result=$2
WHERE id=$1 AND status='claimed';

-- name: CompleteRoomCodingJob :exec
UPDATE room_coding_jobs
SET status=$2, result=$3, completed_at=clock_timestamp(), lease_expires_at=NULL
WHERE id=$1 AND status='claimed';
