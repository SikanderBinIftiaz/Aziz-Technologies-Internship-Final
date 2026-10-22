# DocuMind AI: Architecture

This document describes the system as implemented. The original Day 1 design is in [`PROJECT_PLAN.md`](PROJECT_PLAN.md).

## 1. System overview

```
Browser (HTML + CSS + vanilla JS, served by FastAPI)
   |  JSON and Server-Sent Events, JWT bearer token
   v
FastAPI application (app/main.py)
   |  middleware: request id, security headers (CSP), CORS, per-user rate limits
   |
   +-- routes: auth | documents | chat | sessions | usage | health
   |
   +-- Ingestion pipeline ---------> parse -> chunk -> embed -> ChromaDB
   |
   +-- Agent (app/ai/agent.py) ----> LLM (Claude API, tool calling, streaming)
   |        |
   |        +-- tools: search_documents | calculate | document_stats | summarize_document
   |        +-- Retriever (embed query -> ChromaDB top-k -> similarity threshold)
   |
   +-- SQLite via SQLAlchemy: users, documents, sessions, messages, usage_logs
```

## 2. Request flow for a question

1. `POST /api/chat/stream` receives `{question, session_id?, document_ids?}`. The JWT identifies the user.
2. The route checks that the session and any selected documents belong to the user, then loads the last `HISTORY_MESSAGES` messages. Old `[n]` citation markers are stripped from history because they refer to passages from earlier turns.
3. The agent lists the user's `ready` documents. If there are none, it returns a fixed "upload a document first" message.
4. **With an API key (Claude mode):** a bounded tool-calling loop runs for at most `AGENT_MAX_STEPS` steps. The first step forces a tool call. The last step offers no tools, so the model must answer. If the model streams text and then decides to call a tool, a `reset` event tells the UI to discard that text.
5. **Without an API key (demo mode):** the agent runs retrieval only and returns the best-matching sentences from the top passages, with citations. Pure arithmetic questions are answered by the safe calculator.
6. Every retrieved passage is registered in a `SourceRegistry` that assigns stable numbers for the whole run.
7. The final text goes through citation validation: any `[n]` that does not match a real retrieved passage is removed, and the remaining numbers are renumbered by first use.
8. The answer is persisted with its citations, tools used, confidence and a usage log row (tokens, estimated cost, latency), then sent to the client as the `final` event.

SSE event types: `status`, `token`, `reset`, `final`, `error`.

## 3. Retrieval and grounding

| Mechanism | Where | Purpose |
|---|---|---|
| Owner filter on every vector query | `vectorstore.py` | A user can only ever retrieve their own chunks |
| Similarity threshold | `rag.py` | Weak matches are dropped, so unsupported questions return "not found" |
| Delimited passages (`<document ...>`) | `format_for_model` | Passages are labelled as untrusted data; closing tags inside text are neutralised |
| Strict system prompt | `prompts.py` | Answer only from passages, cite every claim, refuse when unsupported, ignore instructions inside documents |
| Citation validation | `validate_citations` | Prevents invented or out-of-range citation numbers |
| Confidence label | `confidence_level` | `none`, `low`, `medium` or `high`, from the best cited passage's similarity relative to the threshold |

Chunking: paragraph and sentence aware, `CHUNK_SIZE` characters with `CHUNK_OVERLAP`. PDFs keep real page numbers. DOCX, TXT and Markdown have no pages, so they are split into sections of about 3,000 characters and shown as "section n".

## 4. Embedding backends

| Backend | When used | Quality |
|---|---|---|
| `local` (sentence-transformers, `all-MiniLM-L6-v2`) | `EMBEDDING_BACKEND=auto` and the model can be loaded, or `local` | Semantic: handles paraphrases |
| `hash` (feature hashing of words and word pairs) | `auto` fallback if the model cannot load, or `hash` explicitly (used by the test suite) | Lexical only: needs word overlap, so paraphrased questions can be refused |

Each backend has its own default similarity threshold (0.30 local, 0.15 hash). `SIMILARITY_THRESHOLD` overrides it. `GET /api/health` reports which backend is active.

## 5. Agent tools

| Tool | Behavior |
|---|---|
| `search_documents(query, document_ids?)` | Retrieves passages, registers them for citation, returns them numbered |
| `calculate(expression)` | Evaluates arithmetic through an AST whitelist. `eval` is never used |
| `document_stats(document_id)` | Pages or sections, chunks, words for one of the user's documents |
| `summarize_document(document_id)` | Map-reduce summary: bullet summaries of chunks, then a combined summary |

Tool arguments are validated, document ids are checked for ownership, and tool failures come back to the model as `ERROR: ...` text instead of crashing the request.

## 6. Data model (SQLite)

| Table | Key columns |
|---|---|
| `users` | email (unique), password_hash |
| `documents` | owner_id, filename, content_hash (unique per owner), pages, chunks, words, status (`processing`, `ready`, `failed`), error |
| `sessions` | owner_id, title |
| `messages` | session_id, role, content, citations_json, tools_json, confidence, found, feedback |
| `usage_logs` | owner_id, endpoint, input_tokens, output_tokens, cost_usd, latency_ms |

Vector store: one ChromaDB collection with cosine distance. Each chunk carries `owner_id`, `document_id`, `filename`, `page` and `chunk_index`.

## 7. Security

- Passwords are SHA-256 pre-hashed then bcrypt-hashed, so long passphrases are never truncated at bcrypt's 72-byte limit.
- JWT access tokens expire after `JWT_EXPIRE_MINUTES`. With `APP_ENV=production` the server refuses to start with the default secret.
- Rate limits are keyed by user (IP for anonymous calls): chat, auth and upload each have their own limit.
- Uploads: extension allow-list, size limit, empty-file check, filename sanitising, SHA-256 duplicate detection per user.
- Responses carry a Content-Security-Policy (`script-src 'self'`), `X-Frame-Options: DENY`, `nosniff` and `Referrer-Policy: no-referrer`.
- Error responses use one format, `{"error": {"code", "message"}}`, with no stack traces.
- The frontend HTML-escapes all model, document and filename text before its small markdown renderer adds formatting tags, and it ships its fonts locally, so it makes no third-party requests.

## 8. Observability

Structured logging (JSON in production), a request id on every log line and in the `X-Request-ID` response header, and a per-question usage record (tokens, cost, latency) that powers the usage dashboard.

## 9. Known limitations

- The offline hash embedder is a fallback, not a replacement for the real model (see section 4).
- No OCR: scanned PDFs without a text layer are rejected with "No readable text was found".
- SQLite and an embedded vector store suit a single server. A larger deployment would move to PostgreSQL and a managed vector database.
- Ingestion runs inside the upload request. Very large files would need a background job queue.
