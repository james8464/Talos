-- name: UpsertPresenceReading :exec
INSERT INTO presence_readings(tenant_id,human_actor_id,people,faces)
VALUES($1,$2,$3,$4)
ON CONFLICT (tenant_id,human_actor_id) DO UPDATE
SET people=EXCLUDED.people, faces=EXCLUDED.faces, captured_at=clock_timestamp();

-- name: GetPresenceReading :one
SELECT * FROM presence_readings WHERE tenant_id=$1 AND human_actor_id=$2;
