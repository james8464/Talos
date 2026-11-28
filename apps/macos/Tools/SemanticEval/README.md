# Semantic search evaluation

A fixed 20-query regression set for the Talos workspace. It is intentionally
inside `Tools/SemanticEval`, which the index skips so the evaluation fixture
cannot rank itself for its own queries.

Run it from the repository root:

```sh
make semantic-eval
```

The harness builds the production `SemanticIndex`, runs each query against the
real workspace, and checks whether the first result belongs to the expected
file set. Exit code is non-zero if top-1 falls below 20/20.

## Query set

| # | Query | Relevant files |
| ---: | --- | --- |
| 1 | where are tool approvals resolved | AppServer, AppModel, TalosCLI |
| 2 | persist sessions to disk | SessionPersistence |
| 3 | MCP OAuth discovery and PKCE | MCP |
| 4 | discover local Ollama models | OllamaClient, AppModel |
| 5 | compact long conversations automatically | AppModel, SettingsView |
| 6 | store provider API keys in the keychain | Keychain, SettingsStore |
| 7 | run hooks before a tool executes | Hooks, AppModel |
| 8 | native messaging host for the browser extension | TalosNativeHost, AppModel, install-native-host.sh |
| 9 | create a GitHub pull request | GitHubService, GitHubPanel |
| 10 | language server diagnostics and code actions | LSPService, DiagnosticsPanel, Tools |
| 11 | cache semantic embeddings on disk | SemanticIndex |
| 12 | usage chart of daily tokens | SettingsView, UsageStore |
| 13 | merge a worktree branch | Git, AppModel, WorktreePanel |
| 14 | menu bar status panel | MenuBarController |
| 15 | dictation using speech recognition | DictationController |
| 16 | remote daemon HTTP API | RemoteDaemon |
| 17 | verify plugin signatures | Plugins |
| 18 | queue cloud tasks in a worktree | CloudTasks, AppModel |
| 19 | fork or edit a message branch | ConversationBranching, AppModel |
| 20 | JSON-RPC dispatch for the app server | AppServer |

## Held-out set

`HeldOut/main.swift` is a second, 15-query set written after this one and not
used to choose weights. Run it with:

```sh
make semantic-heldout
```

It records a `primary` file for each behaviour and accepts a wider set of
implementation files, because several features span a caller, a service, and a
view. It fails if any query’s top-1 is outside the accepted set, or if the
primary file wins fewer than 80% of queries.

## Results

Word-embedding hybrid with document penalty, IDF, file-level coverage,
declaration boost, and one-result-per-file aggregation:

```text
authored:  TOP1 20/20 · TOP3 20/20 · MRR 1.000
held-out:  acceptable TOP1 15/15 · primary 12/15 · TOP3 15/15 · MRR 1.000
```

Previous sentence-vector hybrid, re-scored with the same penalty, basename
boost, and file-hit aggregation:

```text
TOP1: 19/20
TOP3: 20/20
MRR: 0.975
```

Build cost in the same comparison run:

```text
WORD_BUILD_SECONDS: 6.60
SENTENCE_BUILD_SECONDS: 65.75
```

The word model therefore matches or beats sentence vectors on this set at
roughly one-tenth of the indexing cost.

## Caveats

- The relevance sets are author-defined and include the primary UI/tooling file
  for a feature as well as the implementation file, because both are useful
  search results.
- This is a regression set, not an independent benchmark; it should be extended
  when new subsystems are added. The held-out results and residual ambiguities
  are recorded in `HELDOUT_EVAL.md`.
- Scores are still relative. A query outside the set may return plausible but
  not expected files.
