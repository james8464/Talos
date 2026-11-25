# Bounded coding jobs

`talos codejob` runs one small source change in the background without opening
apps, capturing media, or producing audio.

```sh
python3 -m talos_core.cli codejob start "Clarify this helper" \
    --file talos_core/runtime/text_stream.py --verify-only
python3 -m talos_core.cli codejob run --id JOB_ID --model qwen2.5:1.5b
python3 -m talos_core.cli codejob status
```

Each job records one request, one target file, a wall-clock budget (5–3600 s)
and whether writes are allowed. The runner asks the local model for a minimal
`before`/`after`/`regression_test`, rejects ambiguous or non-applying candidates,
then verifies the diff in the existing ephemeral sandbox. With `--verify-only`
the source tree is never modified; without it, an applied change keeps a
rollback snapshot and is reverted if verification fails. Artifacts live in
`state/coding-jobs/<id>/`: `job.json`, `report.json`, `change.diff` and
`regression.py`.

`run_due` holds a non-blocking lock so a worker and an interactive run cannot
claim the same job. A failed model reply or deadline is recorded as `declined`
or `deferred`, not raised.

`start --detach` launches `run` in a new session and forwards the same state,
root and model. A detached qwen3:4b job was observed going `queued` to
`verified` with the sandbox tests passing and the target hash unchanged in
verify-only mode. A model server failure (for example an Ollama token repeat
limit) is recorded as `error` with the server message and leaves the source
untouched.

## Dependency-ordered plans

```sh
python3 -m talos_core.cli codeplan start --file plan.json
python3 -m talos_core.cli codeplan run PLAN_ID --model qwen3:4b
python3 -m talos_core.cli codeplan status PLAN_ID
```

`plan.json` holds up to eight steps, each with `id`, a `target` or a `targets`
list (2–4 files), `request` and optional `needs`:

```json
{"steps": [
  {"id": "first", "target": "talos_core/example.py", "request": "Clarify the helper."},
  {"id": "second", "targets": ["talos_core/other.py", "talos_core/caller.py"],
   "request": "Update the caller and its call site.", "needs": ["first"]}
]}
```

Plans are verify-only unless `start --apply` is given. Steps run in a stable
topological order; a step whose dependency did not end `verified` or `applied`
is recorded as `blocked` and never starts. The plan report lives at
`state/coding-plans/<id>/report.json`. Cycles, unknown dependencies, duplicate
ids and unsafe targets are rejected before any model call.

Multi-file steps ask the model for one before/after per target plus a single
regression test. Every target must appear exactly once; a partial or extra
patch is declined before verification. The combined diff is applied and tested
as one change, so a step cannot leave half of its files edited.

## Retry on a failed test

When the sandbox unit test fails, the runner makes one revised attempt with the
failure output appended to the prompt. The same wall-clock budget applies,
`attempts` is recorded in `job.json` and `report.json`, and a second failure is
reported as `declined` rather than retried indefinitely. Invalid JSON, an
abstained patch, or a candidate that does not apply stops immediately without a
retry.

## From the room

Typed messages can run the same verify-only path without a terminal:

```text
coding job talos_core/runtime/text_stream.py: Add one clarifying sentence to the module docstring.
coding plan state/plan.json
coding plan: make the helper clearer and update its caller
coding jobs
```

The room queues the work, runs it with the local model, and replies with the
job or plan status, per-step statuses for plans, and whether every target stayed
unchanged. A `coding plan:` request without a JSON file generates a plan from
the source catalog: at most three steps, three targets per step, validated for
unsafe paths, cycles and unknown dependencies before anything runs. Voice input is not routed to these commands, and a room request can
never apply a patch.
