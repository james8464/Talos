# Semantic index

Talos ships a local semantic code search used by the `search_codebase` tool and
the inspector’s index status. This document covers its data flow, privacy,
performance, fallback behavior, and limitations.

## Data flow

1. **Candidate discovery.** The index walks the workspace, skipping `.git`,
   `.build`, `node_modules`, `DerivedData`, package caches, vendored
   dependencies, and similar generated directories. It accepts source, markup,
   config, and documentation extensions listed in `SemanticIndex.swift`, stops
   after 700 files, and resolves symlinks so paths are stable.
2. **Read and filter.** Files larger than 300 KB, binary files containing NUL
   bytes, non-UTF-8 files, and empty files are skipped.
3. **Chunking.** Text is split into 40-line windows with a 10-line overlap, up
   to 90 chunks per file. Chunks shorter than 40 meaningful characters or
   without a letter are skipped. Each chunk keeps its path and start/end line.
4. **Embedding.** The chunk is embedded locally with
   `NLEmbedding.sentenceEmbedding(for: .english)` (512 dimensions). The embedded
   text is the relative path plus the first 1,500 characters of the chunk, so
   filenames can contribute to relevance. A stemmed lexical token set is stored
   alongside the vector.
5. **Caching.** Vectors live in an actor-isolated, in-memory index keyed by the
   resolved workspace path. File size and modification time are recorded for
   stale detection. Nothing is written to disk.
6. **Query.** The query is embedded with the same model, then every chunk is
   scored:

   ```text
   score = 0.45 × cosine(embedding) + 0.50 × lexical overlap + 0.05 × path match
   ```

   Lexical overlap uses lowercased, camel-case-split, stopped, stemmed tokens
   with prefix matching. The tool returns the top-ranked chunks with paths,
   line ranges, scores, and text.
7. **Invalidation.** Each search/status call re-enumerates candidates and
   compares file count, sizes, and modification times. A change triggers a full
   rebuild; there is no per-file incremental vector update.

## Privacy

- File contents and embeddings stay in the app process. `SemanticIndex` imports
  only `Foundation` and `NaturalLanguage` and makes no network requests.
- Embeddings and chunk text are not persisted, logged, exported in the audit
  log, or written to the CLI.
- Vectors are discarded when the app quits.
- Search results returned to the model become part of the prompt, so the
  selected provider still receives the chunk text. The index does not change
  the app’s existing provider/privacy boundary.
- Keys and sessions remain outside the index.

## Performance

Measured with an optimized standalone build of `SemanticIndex.swift` on this
Mac (`/usr/bin/time -l`):

| Workspace | Files | Chunks | First build | Warm query | Status check | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Small sample | 3 | 3 | 60 ms | 47 ms | <1 ms | 50 MB |
| Talos repository | 90 | 953 | 62.9 s | 19 ms | 3 ms | 185 MB |

- Embedding dominates the first build: roughly 65 ms per chunk in the Talos
  repository. Warm queries are milliseconds; stale detection still enumerates
  and stats the workspace.
- The build runs on a `SemanticIndex` actor, so it does not block the UI thread,
  but a tool call waits for the build before returning its first result.
- The inspector shows “Rebuilding…” while an explicit rebuild runs.
- Indexing the same workspace again in the same process is free; relaunching
  the app rebuilds because the cache is memory-only.

## Fallback behavior

- If `NLEmbedding.sentenceEmbedding` is unavailable, status reports
  `unavailable`, search returns “On-device sentence embeddings are unavailable.
  Use search_files instead.”, and no index is built.
- If no supported files are found, status reports 0 chunks and search returns
  “No semantic matches”.
- Files that fail to read, are binary, are too large, or are not UTF-8 are
  skipped silently rather than failing the whole index.
- Disabling semantic search in Settings removes `search_codebase` from the tool
  prompt and returns a clear disabled error.
- Exact strings, symbols, and paths should still use `search_files` or the LSP
  tools; semantic search is complementary.

## Limitations

- The sentence model is English and general-purpose, not trained on code.
  Identifiers and comments work better than dense syntax; the lexical and path
  terms compensate but do not make it a compiler-aware index.
- Fixed 40-line windows can split functions, types, or markdown sections. The
  embedded text is truncated to 1,500 characters.
- Caps: 700 files, 300 KB per file, 90 chunks per file. Very large monorepos are
  sampled, not fully indexed.
- No disk persistence or cross-workspace sharing; each launch starts cold.
- Stale detection compares file count/size/mtime. A same-size edit within the
  timestamp tolerance (0.5 s) may not trigger a rebuild until a later change.
- Scores are relative ranking values, not calibrated confidence. A “no match”
  threshold is not applied; the top hits are always returned.
- Only workspace text files are indexed. Attachments, PDFs, MCP resources,
  remote repositories, and binary assets are out of scope.
- Rebuilds are all-or-nothing; one changed file re-embeds the workspace.
- Query cost includes a full candidate enumeration for stale detection on every
  search.
