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
   resolved workspace path. They are also persisted per file in a binary plist
   at `~/Library/Application Support/Talos/semantic-index/<sha256>.plist`, where
   the hash is derived from the workspace path. File size and modification time
   validate each entry on the next launch. Only files with chunks are cached.
6. **Query.** The query is embedded with the same model, then every chunk is
   scored:

   ```text
   score = 0.45 × cosine(embedding) + 0.50 × lexical overlap + 0.05 × path match
   ```

   Lexical overlap uses lowercased, camel-case-split, stopped, stemmed tokens
   with prefix matching. The tool returns the top-ranked chunks with paths,
   line ranges, scores, and text.
7. **Invalidation.** Each search/status call re-enumerates candidates and
   compares file count, sizes, and modification times. Unchanged files are
   loaded from the disk cache and only changed/new files are re-embedded. The
   inspector's **Rebuild Index** action ignores the cache and re-embeds
   everything, then replaces the cache.

## Privacy

- `SemanticIndex` makes no network requests. It uses `Foundation`,
  `NaturalLanguage`, and `CryptoKit` locally.
- Embeddings and chunk text are persisted only in the local cache at
  `~/Library/Application Support/Talos/semantic-index/`. The cache is not
  logged, not included in the audit export, and not exposed by the CLI.
- The cache directory is created with user-only permissions (0700) and each
  file with 0600.
- Deleting the `semantic-index` directory removes every cached vector and chunk;
  the next search rebuilds from the workspace.
- Sessions, tokens, and provider keys are outside the cache.
- Search results returned to the model become part of the prompt, so the
  selected provider still receives the chunk text. The index does not change
  the app’s existing provider/privacy boundary.
- Keys and sessions remain outside the index.

## Performance

Measured with an optimized standalone build of `SemanticIndex.swift` on this
Mac (`/usr/bin/time -l`):

| Workspace / state | Files | Chunks | Startup | Warm query | Cached files | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Small sample, first run | 3 | 3 | 142 ms | 17 ms | 0 | 54 MB |
| Small sample, cached launch | 3 | 3 | 17 ms | 17 ms | 3 | 40 MB |
| Talos repository, first run | 93 | 972 | 66.4 s | 19 ms | 0 | 185 MB |
| Talos repository, cached launch | 93 | 972 | 0.9 s | 19 ms | 93 | 118 MB |
| Talos repository, one file changed | 93 | 972 | 1.3 s | 19 ms | 92 | 167 MB |

- Embedding dominates the first-ever build: roughly 68 ms per chunk in the
  Talos repository. The cache turns a 66.4 s launch into a 0.9 s launch and a
  one-file edit into 1.3 s.
- The Talos cache is 6.9 MB for 93 files / 972 chunks.
- The build runs on a `SemanticIndex` actor, so it does not block the UI thread,
  but a tool call waits for the build before returning its first result.
- The inspector shows “Rebuilding…” during an explicit rebuild and reports the
  number of files reused from cache.
- Cache writes are best-effort; failure to read or write it falls back to
  embedding from source.

## Fallback behavior

- If `NLEmbedding.sentenceEmbedding` is unavailable, status reports
  `unavailable`, search returns “On-device sentence embeddings are unavailable.
  Use search_files instead.”, and no index is built.
- If no supported files are found, status reports 0 chunks and search returns
  “No semantic matches”.
- Files that fail to read, are binary, are too large, or are not UTF-8 are
  skipped silently rather than failing the whole index.
- A missing, corrupt, or older-version cache is ignored and rebuilt. Cache
  write failures do not fail a search.
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
- The first-ever index for a workspace still costs the full embedding time;
  persistence accelerates later launches and changed-file updates only.
- The cache is local per workspace, not shared across workspaces, and grows
  with the indexed text. It is not compressed.
- Stale detection compares file count/size/mtime. A same-size edit within the
  timestamp tolerance (0.5 s) may not reuse or rebuild the expected entry until
  a later change.
- Cache format changes require a version bump; older caches are discarded.
- Scores are relative ranking values, not calibrated confidence. A “no match”
  threshold is not applied; the top hits are always returned.
- Only workspace text files are indexed. Attachments, PDFs, MCP resources,
  remote repositories, and binary assets are out of scope.
- An explicit Rebuild Index is all-or-nothing (force re-embed); normal searches
  reuse unchanged files.
- Query cost includes a full candidate enumeration for stale detection on every
  search.
