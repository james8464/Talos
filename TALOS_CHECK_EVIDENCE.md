# Full check evidence

The full repository gate ran on this branch with audio muted, from the final
`/Users/james/Developer/Talos` checkout.

```bash
TALOS_MUTE=1 LC_ALL=C NODE_OPTIONS=--max-old-space-size=8192 \
BUILTIN_SKILL_BASE_REF=talos/foundation CHECK_JOBS=2 \
make check BASE=talos/foundation
```

```text
FULL_EXIT: 0
```

## Plan

```text
Check plan: every check (.github/ISSUE_TEMPLATE/bug_report.yml)
run  check-repository
run  check-docs
run  check-contracts
run  check-packages
run  check-voice-runtime
run  check-teamhub-cli
run  check-installer
run  check-desktop
run  check-teamhub
run  check-teamhub-tooling
run  check-scripts-fast
run  check-web
run  check-node-services
run  check-computer
```

## Suite summaries

```text
Test Files  15 passed (15)
Tests  74 passed (74)
Test Files  2 passed (2)
Tests  15 passed (15)
Test Files  3 passed (3)
Tests  35 passed (35)
Test Files  7 passed (7)
Tests  45 passed (45)
Test Files  10 passed (10)
Tests  96 passed (96)
Test Files  1 passed (1)
Tests  22 passed (22)
Test Files  62 passed | 3 skipped (65)
Tests  721 passed | 21 skipped (742)
Test Files  27 passed (27)
Tests  307 passed | 6 skipped (313)
Test Files  65 passed | 3 skipped (68)
Tests  989 passed | 15 skipped (1004)
Test Files  28 passed (28)
Tests  221 passed (221)
Test Files  49 passed | 1 skipped (50)
Tests  645 passed | 1 skipped (646)
Test Files  16 passed (16)
Tests  185 passed (185)
Test Files  14 passed (14)
Tests  100 passed | 1 skipped (101)
Test Files  1 passed (1)
Tests  21 passed (21)
Test Files  10 passed (10)
Tests  178 passed | 3 skipped (181)
[tests]  Test Files  423 passed (423)
[tests]       Tests  3847 passed (3847)
Test Files  281 passed | 8 skipped (289)
Tests  2906 passed | 52 skipped (2958)
Test Files  4 passed | 1 skipped (5)
Tests  7 passed | 1 skipped (8)
Test Files  1 passed (1)
Tests  12 passed (12)
```

## Tail of the run

```text
 RUN  v4.1.9 /Users/james/Developer/Talos/apps/node-services


 Test Files  1 passed (1)
      Tests  12 passed (12)
   Start at  10:26:27
   Duration  22.79s (transform 374ms, setup 13ms, import 571ms, tests 22.14s, environment 0ms)

```
