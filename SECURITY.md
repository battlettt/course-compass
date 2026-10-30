# Security

## Bring your own key (BYOK)

Course Compass never ships, requests, prints, or stores an API key.

- LLM mode reads `COURSE_COMPASS_API_KEY` from the environment at the moment a command runs. It is held in memory only for that request.
- The key is sent only as an `Authorization: Bearer` header to the base URL you configure (`COURSE_COMPASS_BASE_URL`).
- `LLMConfig.__repr__` masks the key, and HTTP error messages report only the status code, so the key does not end up in tracebacks or logs.
- The base URL must be `https://`. Plain `http://` is accepted only for `localhost` / `127.0.0.1` / `::1`, so you can point it at a local model server.
- Without a key, everything runs locally and makes no network calls at all.

Keep keys in your shell profile or a `.env` file that is never committed (`.env` is in `.gitignore`).

## What leaves your machine

- **Local mode (`--local`, or no key set):** nothing.
- **LLM mode:** your question plus the top retrieved note chunks (up to 4 for `ask`, up to 8 for `quiz`) and a list of defined terms from your notes go to your chosen LLM provider. Do not use LLM mode on notes you are not allowed to share with that provider.

## Local storage

All state (chunks, quiz items, review cards, attempts) is one JSON file, by default `.course-compass/db.json`. It is written atomically (temp file + rename) and is git-ignored. There is no server, account, or telemetry.

## Hardening in the code

| Risk | Guard |
|---|---|
| Path traversal on ingest | `..` path components are refused; resolved paths, including symlink targets, must sit inside an `--allow-dir` root (default: the current directory). |
| Huge inputs | 200 KB per note, 2 MB total, 200 files, 500-character questions, 20 quiz items, 1 MB LLM response cap. |
| Hung network | 30 s timeout on every LLM HTTP call. |
| Model output | Answers and quiz JSON are schema-checked; cited chunk ids must exist; distractors must come from your notes. Invalid output is repaired once, then rejected with an error. It is never shown silently. |
| Corrupt database | Detected on load with a clear error; never half-written thanks to atomic saves. |
| Demo page | Fully static: no external scripts, styles, fonts or network calls. Pasted notes are rendered as text nodes, never parsed as HTML. |

## Reporting

This is a small student project. If you find a problem, open an issue describing it without posting real keys or private notes.
