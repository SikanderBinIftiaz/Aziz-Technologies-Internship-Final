# DocuMind AI: Master Project Plan (Day 1)

**Author:** Sikander Bin Iftiaz
**Track:** Python Programming and AI Development, Week 6 (Advanced AI Engineering)
**Project type:** Intermediate-level AI application (RAG + Agent + API + Web UI)

---

## 1. Problem Statement

Organizations and students accumulate large volumes of unstructured documents (reports, policies, manuals, research papers, course notes). Finding a specific answer means opening many files and searching manually. Generic chatbots cannot help because they have not seen these private documents, and they invent ("hallucinate") answers when they do not know.

**DocuMind AI** lets a user upload their own documents and ask questions in natural language. Every answer is grounded in the uploaded content, carries **source citations** (file and page), and the system says "I could not find this in your documents" instead of guessing.

### Success criteria
| Criterion | Target |
|---|---|
| Answers grounded in retrieved context | >= 90% on the evaluation set |
| Retrieval hit rate (correct chunk in top-4) | >= 85% |
| Unanswerable questions correctly refused | >= 90% |
| Median end-to-end latency (question to answer) | < 6 seconds |
| Every endpoint covered by an automated test | 100% |

---

## 2. Project Scope

### In scope
- Upload and process PDF, DOCX, TXT and Markdown files
- Chunking, embedding and storage in a vector database
- Retrieval-Augmented Generation (RAG) with citations
- An agent that can choose tools (document search, calculator, document statistics, summarizer)
- Conversation memory per session
- REST API (FastAPI) with validation, JWT authentication and rate limiting
- Professional single-page web interface served by the backend
- Evaluation harness (retrieval metrics, hallucination checks, prompt tests)
- Structured logging, error handling, token/cost tracking
- Deployment preparation (Docker, environment variables, README)

### Out of scope (listed as future improvements)
- Multi-tenant organization management
- OCR for scanned images
- Fine-tuning models
- Real-time collaborative editing

---

## 3. Feature List

| # | Feature | Priority |
|---|---|---|
| F1 | User registration and login (JWT) | Must |
| F2 | Document upload with validation (type, size) | Must |
| F3 | Automatic parsing, chunking, embedding and indexing | Must |
| F4 | Question answering with citations | Must |
| F5 | "I don't know" behavior when context is insufficient | Must |
| F6 | Streaming responses in the UI | Should |
| F7 | Agent tool use (calculator, doc stats, summarize) | Must |
| F8 | Chat history per session | Should |
| F9 | Document library (list, delete, re-index) | Must |
| F10 | Usage dashboard (tokens, cost estimate, latency) | Should |
| F11 | Evaluation suite runnable from the CLI | Must |
| F12 | Dark/light theme and responsive layout | Could |

---

## 4. User Flow

```
Register / Login
      |
      v
Upload documents  --->  Backend validates, parses, chunks, embeds, indexes
      |                         |
      v                         v
Document library shows "Ready" status
      |
      v
Ask a question in chat
      |
      v
Agent decides: answer from documents? calculate? summarize? statistics?
      |
      v
Retrieve top-k chunks  --->  Build grounded prompt  --->  LLM generates answer
      |
      v
Answer streamed to UI with citations (file, page) + confidence note
      |
      v
User opens a citation to view the source passage / gives feedback
```

---

## 5. AI Architecture

```
+--------------------------- Web UI (HTML/CSS/JS) ----------------------------+
|  Login | Document Library | Chat (streaming + citations) | Usage Dashboard   |
+------------------------------------+---------------------------------------+
                                     | HTTPS / JSON / SSE
+------------------------------------v---------------------------------------+
|                           FastAPI Application                               |
|  Routers: auth | documents | chat | admin/usage | health                    |
|  Middleware: CORS, rate limit, request-id logging, error handler            |
+--------+------------------+-------------------+-----------------+-----------+
         |                  |                   |                 |
+--------v------+  +--------v--------+  +-------v-------+  +------v---------+
| Ingestion     |  | Agent Orchestr. |  | RAG Engine    |  | SQL Database   |
| parse/chunk/  |  | tool selection, |  | retrieve,     |  | users, docs,   |
| embed/index   |  | tool execution  |  | rerank, build |  | sessions,      |
|               |  |                 |  | prompt        |  | messages, usage|
+--------+------+  +--------+--------+  +-------+-------+  +----------------+
         |                  |                   |
         |                  +---------+---------+
         |                            |
+--------v---------+        +---------v----------+
| Embedding model  |        | LLM (Anthropic     |
| (local, MiniLM)  |        | Claude API)        |
+--------+---------+        +--------------------+
         |
+--------v---------+
| ChromaDB (vector |
| store, persisted)|
+------------------+
```

### Request pipeline (RAG)
1. Question arrives with the session ID and JWT.
2. Query is rewritten using the last turns of history so follow-ups are self-contained.
3. Query embedding is computed locally.
4. Top-k (k=6) chunks retrieved from ChromaDB, filtered to the user's own documents.
5. Chunks below a similarity threshold are discarded. If none remain, the system returns a "not found" answer without calling the LLM, which saves cost and avoids hallucination.
6. A grounded prompt is built (system rules, numbered context blocks, question).
7. The LLM returns a structured JSON answer: `answer`, `citations[]`, `confidence`.
8. The output is validated with Pydantic, logged, and streamed to the client.

### Agent design
The agent uses native tool calling. Tools:

| Tool | Purpose |
|---|---|
| `search_documents(query, document_ids?)` | RAG retrieval |
| `calculate(expression)` | Safe arithmetic (AST-based, no `eval`) |
| `document_stats(document_id)` | Pages, chunks, word count |
| `summarize_document(document_id)` | Map-reduce summary |

A hard limit of 4 tool-call iterations prevents loops.

---

## 6. Technology Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11 | Course language, best AI ecosystem |
| Backend framework | FastAPI | Async, automatic validation, OpenAPI docs |
| Validation | Pydantic v2 | Typed request/response and structured LLM output |
| LLM | Anthropic Claude API | Strong instruction following, tool calling, streaming |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local) | Free, fast, no API cost, good quality for English |
| Vector DB | ChromaDB (persistent) | Simple, embedded, ideal for single-node projects |
| Relational DB | SQLite via SQLAlchemy | Zero setup, easily swappable for PostgreSQL |
| Document parsing | pypdf, python-docx | Reliable text extraction |
| Authentication | JWT (python-jose) + bcrypt (passlib) | Standard and stateless |
| Rate limiting | slowapi | Protects cost and availability |
| Frontend | HTML5, CSS3, vanilla JavaScript | No build step, easy to demo |
| Testing | pytest, httpx | API and unit tests |
| Packaging | Docker | Reproducible deployment |
| Version control | Git + GitHub | Required deliverable |

---

## 7. Model Selection

### Approach comparison
| Approach | Fit for this project? | Reason |
|---|---|---|
| Traditional ML | No | Needs labeled data; cannot generate answers |
| Deep learning (train own model) | No | Needs large data and compute; unnecessary |
| **LLM + RAG** | **Yes** | Private knowledge, citations, no training needed |

### API vs local model
| Option | Pros | Cons | Decision |
|---|---|---|---|
| Hosted LLM API | Best quality, tool calling, no GPU | Per-token cost, internet needed | **Generation** |
| Local LLM (Ollama) | Free, private | Weaker quality, needs RAM/GPU | Future option |
| Local embeddings | Free, fast, private | Slightly lower quality than large API models | **Embeddings** |

**Decision:** hosted Claude model for generation and agent reasoning, local MiniLM for embeddings. The model name is set by an environment variable (`LLM_MODEL`), so it can be changed without code changes.

---

## 8. Data Requirements

| Item | Detail |
|---|---|
| Input formats | PDF, DOCX, TXT, MD |
| Max file size | 15 MB per file |
| Chunking | 800 characters, 150 overlap, split on paragraph/sentence boundaries |
| Chunk metadata | document_id, filename, page, chunk_index, owner_id |
| Evaluation dataset | `data/eval/eval_set.json`: 30 questions (20 answerable with expected source, 10 unanswerable) over a sample document pack |
| Sample documents | `data/samples/`: 3 to 5 public-domain or self-written documents for demo and tests |

### Data pipeline
```
Upload -> validate (type/size/hash dedupe) -> extract text per page
       -> clean (whitespace, headers) -> chunk with overlap
       -> embed in batches -> upsert to ChromaDB with metadata
       -> update document status in SQL (processing -> ready | failed)
```

---

## 9. Prompt Plan

| Prompt | Purpose | Key rules |
|---|---|---|
| System prompt (RAG) | Ground answers | Use only provided context; cite as `[n]`; say "not found" if unsupported |
| Query rewrite prompt | Make follow-ups standalone | Output only the rewritten question |
| Agent system prompt | Tool policy | Prefer `search_documents`; use `calculate` for any arithmetic; max 4 steps |
| Summarize prompt | Map-reduce summary | Chunk summaries, then combined summary |
| Judge prompt (evaluation) | Faithfulness scoring | Given context + answer, return supported / unsupported |

All prompts live in `app/ai/prompts.py` as versioned constants so they can be tested and compared.

---

## 10. API Requirements

### Endpoints
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/health` | No | Liveness and dependency check |
| POST | `/api/auth/register` | No | Create account |
| POST | `/api/auth/login` | No | Get JWT |
| GET | `/api/auth/me` | Yes | Current user |
| POST | `/api/documents` | Yes | Upload and index a document |
| GET | `/api/documents` | Yes | List documents |
| DELETE | `/api/documents/{id}` | Yes | Delete document and vectors |
| POST | `/api/chat` | Yes | Ask a question (JSON response) |
| POST | `/api/chat/stream` | Yes | Ask a question (SSE streaming) |
| GET | `/api/sessions` | Yes | List chat sessions |
| GET | `/api/sessions/{id}/messages` | Yes | Session history |
| GET | `/api/usage` | Yes | Token, cost and latency statistics |

### External APIs
- Anthropic Messages API (generation, tools, streaming)

### Cross-cutting rules
Pydantic validation on every body, consistent error format `{ "error": { "code", "message" } }`, request-ID in logs, rate limit of 30 requests/minute per user on chat endpoints.

---

## 11. Database and Vector Database Requirements

### SQL tables (SQLite)
| Table | Columns |
|---|---|
| `users` | id, email (unique), password_hash, created_at |
| `documents` | id, owner_id, filename, content_hash, pages, chunks, status, error, created_at |
| `sessions` | id, owner_id, title, created_at |
| `messages` | id, session_id, role, content, citations_json, created_at |
| `usage_logs` | id, owner_id, endpoint, input_tokens, output_tokens, cost_usd, latency_ms, created_at |

### Vector store (ChromaDB)
- One collection: `documind_chunks`
- Cosine distance
- Metadata filter on `owner_id` (users can never retrieve each other's data)
- Persisted in `data/chroma/`

---

## 12. Project Structure

```
documind-ai/
├── app/
│   ├── main.py                 # FastAPI app factory, middleware, static files
│   ├── config.py               # Settings from environment
│   ├── logging_config.py       # Structured logging
│   ├── api/
│   │   ├── deps.py             # DB session, current user dependency
│   │   └── routes/
│   │       ├── auth.py
│   │       ├── documents.py
│   │       ├── chat.py
│   │       ├── sessions.py
│   │       ├── usage.py
│   │       └── health.py
│   ├── ai/
│   │   ├── llm.py              # LLM client wrapper, retries, token accounting
│   │   ├── prompts.py          # Versioned prompts
│   │   ├── embeddings.py       # Local embedding model
│   │   ├── vectorstore.py      # ChromaDB wrapper
│   │   ├── rag.py              # Retrieval + grounded generation
│   │   ├── agent.py            # Tool-calling loop
│   │   └── tools.py            # Tool schemas and implementations
│   ├── ingestion/
│   │   ├── parsers.py          # PDF/DOCX/TXT/MD extraction
│   │   ├── chunker.py
│   │   └── pipeline.py
│   ├── db/
│   │   ├── database.py
│   │   └── models.py
│   ├── schemas/                # Pydantic request/response models
│   ├── core/
│   │   ├── security.py         # Hashing, JWT
│   │   └── errors.py           # Exceptions and handlers
│   └── static/                 # Frontend (index.html, css, js)
├── tests/                      # pytest suite
├── evaluation/
│   ├── run_eval.py             # Retrieval + hallucination evaluation
│   └── reports/
├── data/
│   ├── samples/
│   ├── eval/
│   ├── uploads/
│   └── chroma/
├── docs/
│   ├── PROJECT_PLAN.md
│   └── ARCHITECTURE.md
├── .env.example
├── .gitignore
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## 13. Environment and GitHub Plan

- Virtual environment: `python -m venv .venv`
- Secrets only in `.env` (never committed); `.env.example` documents all variables
- Branching: `main` (stable) and `dev`; small commits with conventional messages (`feat:`, `fix:`, `test:`, `docs:`)
- Planned commit milestones: scaffold, ingestion, vector store, RAG, agent, API, UI, tests, evaluation, docs, deployment

---

## 14. Development Roadmap

| Day | Focus | Deliverables |
|---|---|---|
| **Day 1** | Planning and architecture | This plan, scaffold, README, GitHub repo |
| **Day 2** | AI core | Config, logging, LLM wrapper, prompts, embeddings, ingestion pipeline, structured outputs, error handling |
| **Day 3** | RAG, agent and API | Vector store, RAG engine, agent tools, FastAPI routes, auth, database, streaming, **web interface** |
| **Day 4** | Testing and deployment | pytest suite, evaluation harness, hallucination tests, cost optimization, security review, Dockerfile, final README |
| **Day 5** | Presentation | Live demo, report (17 points), GitHub link, future improvements |

---

## 15. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated answers | Similarity threshold, strict grounding prompt, citation validation, "not found" path, faithfulness evaluation |
| API cost growth | Token budgets, top-k limit, caching of identical questions, usage dashboard |
| Prompt injection inside documents | Context is delimited and labeled as untrusted data; system rules take priority; tool outputs are length-limited |
| Slow responses | Local embeddings, streaming, batch indexing |
| Data leakage between users | Mandatory `owner_id` filter on every vector query, tested explicitly |
| API outage | Retries with backoff, clear user-facing error message |

---

## 16. Final Report Mapping (Day 5)

Overview, problem, solution, features, architecture, stack, model selection, data pipeline, prompt design, RAG/agent design, API design, challenges, solutions, testing and evaluation, live demo, GitHub repository, future improvements. Sections 1 to 15 of this document and the evaluation reports feed directly into these slides.

---

## 17. Implementation notes: where the build differs from this plan

This plan was written on Day 1. The finished system follows it closely, with these deliberate changes. [`ARCHITECTURE.md`](ARCHITECTURE.md) describes the system as built.

| Plan | As built | Why |
|---|---|---|
| passlib + bcrypt | bcrypt directly, with SHA-256 pre-hashing | One dependency fewer, and bcrypt's 72-byte truncation no longer applies |
| Separate query-rewrite step | The agent writes standalone search queries (system prompt rule 7) | The model resolves pronouns from the conversation when it forms the search query |
| Below-threshold search skips the LLM | The model still runs and answers "not found" from an empty tool result | Keeps the refusal wording and tool trace consistent |
| MiniLM embeddings only | Plus a hashing-embedder fallback and a demo mode without an API key | Lets the app and test suite run offline |
| Similarity threshold fixed at 0.30 | Default depends on the embedding backend (0.30 local, 0.15 hash), overridable | The two backends produce different score ranges |
| Usage dashboard (F10) | Dashboard, plus helpful / not helpful ratings per answer (not in the plan) | Added during the build |
