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
