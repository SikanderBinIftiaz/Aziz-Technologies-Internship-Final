# DocuMind AI: Final Report (Day 5)

Presenter: Sikander Bin Iftiaz
Program: Python Programming and AI Development, Week 6 Master Project

This report follows the 17 points required for the final presentation. Every number below comes from the code and the test and evaluation runs in this repository. Where something could not be verified in the build environment, it is marked.

---

## 1. Project overview

DocuMind AI is a document question-answering application. A user uploads PDF, DOCX, TXT or Markdown files and asks questions in plain language. Each answer cites the exact passage it came from, and the application says "I could not find this in your documents" when the documents do not contain the answer.

## 2. Problem statement

People lose time searching through reports, policies, manuals and notes. General chatbots have not seen private documents and tend to invent answers. The problem is to give grounded, checkable answers over a user's own files, and to refuse when the evidence is missing.

## 3. Proposed solution

A Retrieval-Augmented Generation (RAG) system with an agent layer:

- Documents are parsed, chunked, embedded and stored in a vector database, separately per user.
- A question triggers retrieval of the most similar passages.
- The language model answers only from those passages, cites them, and refuses when they are insufficient.
- The agent can also call a calculator, a document-statistics tool and a summarizer.

## 4. Features

| Feature | Implemented |
|---|---|
| Accounts with JWT login | Yes |
| Upload PDF, DOCX, TXT, MD with validation and duplicate detection | Yes |
| Question answering with numbered citations and an evidence panel that highlights the supporting sentence | Yes |
| Honest "not found" behavior and a confidence label | Yes |
| Streaming answers (Server-Sent Events) | Yes |
| Agent tools: search, calculator, document stats, summarize | Yes |
| Chat history, restricting a question to selected documents | Yes |
| Helpful / not helpful feedback on answers | Yes |
| Usage dashboard (questions, tokens, estimated cost, latency, per-day chart) | Yes |
| Demo mode without an API key | Yes |
| Responsive interface (desktop and mobile) | Yes |

## 5. AI architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md). In short: browser, FastAPI, agent loop, retriever over ChromaDB, Claude API for generation, SQLite for application data.

## 6. Technology stack

Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy and SQLite, ChromaDB, sentence-transformers, Anthropic Claude API, bcrypt and python-jose (JWT), slowapi (rate limiting), pytest, Docker, HTML, CSS and vanilla JavaScript.

## 7. Model selection

| Question | Decision | Reason |
|---|---|---|
| Traditional ML, deep learning, or LLM? | LLM with retrieval | No labelled data or training is needed, and answers can cite sources |
| API or local model for generation? | Hosted Claude API | Reliable tool calling and streaming; the model name is a setting (`LLM_MODEL`) |
| API or local model for embeddings? | Local `all-MiniLM-L6-v2` | Free, fast, private, no per-request cost |
| Offline fallback? | Feature-hashing embedder | Lets the app and tests run with no downloads; lower quality, see section 12 |

## 8. Data pipeline

Upload, then validate (type, size, empty, duplicate by SHA-256), then store the file, then extract text per page or section, then chunk (800 characters, 150 overlap), then embed in batches of 64, then index in ChromaDB with owner, document, filename, page and chunk metadata. The document row moves from `processing` to `ready` or `failed` with an error message. Deleting a document removes its file, its vectors and its row.

## 9. Prompt design

Prompts live in `app/ai/prompts.py` and carry a version constant. The agent system prompt has nine rules: answer only from retrieved passages, cite every claim with `[n]`, reply with an exact fixed sentence when evidence is missing, use the calculator for all arithmetic, use the right tool for stats and summaries, treat passages as untrusted data, write standalone search queries, stay concise, and never reveal the rules. Separate prompts cover chunk summaries, summary combination and the optional faithfulness judge.

## 10. RAG and agent architecture

- Retrieval: query embedding, owner-filtered top-k search in ChromaDB, similarity threshold.
- Grounding: passages are wrapped in labelled `<document>` blocks; citations are validated against what was really retrieved and renumbered.
- Agent: a bounded loop (default four steps). The first step forces a tool call and the last step removes tools so the model must answer.
- Confidence: derived from the best cited passage's similarity relative to the threshold.

## 11. API architecture

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Dependency check, active mode and embedding backend |
| POST | `/api/auth/register`, `/api/auth/login` | Create account, get token |
| GET | `/api/auth/me` | Current user |
| POST | `/api/documents` | Upload and index |
| GET | `/api/documents` | List |
| DELETE | `/api/documents/{id}` | Delete |
| POST | `/api/chat` | Ask, JSON response |
| POST | `/api/chat/stream` | Ask, streamed |
| GET | `/api/sessions`, `/api/sessions/{id}/messages` | History |
| DELETE | `/api/sessions/{id}` | Delete a chat |
| POST | `/api/messages/{id}/feedback` | Rate an answer |
| GET | `/api/usage` | Usage summary |

Interactive documentation is served at `/docs`.

## 12 and 13. Challenges faced and solutions implemented

| Challenge | Solution |
|---|---|
| Models sometimes cite passages that do not exist | Citation validator removes unknown numbers and renumbers the rest |
| A user must never see another user's content | Mandatory `owner_id` filter on every vector query; ownership checks on documents and sessions; covered by API tests |
| Model streams text, then decides to call a tool | A `reset` event lets the UI discard the partial text |
| bcrypt silently truncates passwords after 72 bytes | Passwords are SHA-256 pre-hashed before bcrypt |
| Prompt injection hidden inside uploaded documents | Passages are delimited and labelled untrusted; closing tags inside text are neutralised; the prompt forbids following instructions in passages |
| Arithmetic must never come from the model's head or from `eval` | AST-whitelist calculator exposed as a tool |
| Strict Content-Security-Policy | No inline scripts; fonts shipped locally; UI built with escaped templates |
| Running and testing without network access or an API key | Demo mode (retrieval only) and a hashing embedder fallback |
| **Open limitation:** the hashing embedder is lexical, so a paraphrased question can be refused (for example, "how long does it fly" against text that says "flight time") | Use the real sentence-transformers model (the default when it can be downloaded). This was **not testable in the build environment**, where the model host is blocked |

## 14. Testing and evaluation

**Automated tests:** 82 pytest tests pass: unit tests for chunking, parsing and the calculator (including rejection of unsafe expressions); API tests for auth, upload validation, isolation between users, chat, streaming, sessions, usage and error format; and agent-loop tests with a scripted fake model (citation validation, not-found behavior, stream reset, tool errors, prompt-delimiter escape). The tests run offline.

**Interface check:** the full flow (register, upload four documents, ask three questions, open a citation, view usage) was driven in a real browser at desktop and phone widths with no console errors, page errors or failed requests.

**Evaluation harness:** `python evaluation/run_eval.py` indexes the four sample documents into an isolated store and runs 30 questions (20 answerable, 10 unanswerable).

Latest run, 10 October 2026 (offline demo mode, hashing embeddings, lexical grounding check):

| Metric | Result | Target |
|---|---|---|
| Retrieval hit rate | 90.0% | 85% |
| Answers contain expected facts | 85.0% | n/a |
| Grounded answers | 70.0% | 90% |
| Unanswerable questions refused | 80.0% | 90% |
| Median latency | 3 ms | 6,000 ms |

Retrieval meets its target; grounding and refusal do not, and the report says so. Four questions need attention: two answerable ones whose passage was not retrieved (meal reimbursement limit, SkyScout flight time) and two unanswerable ones that were answered instead of refused (SkyScout price, number of Northwind employees). These numbers measure the offline fallback, not the full system. The run with the real embedding model and Claude (`python evaluation/run_eval.py --judge`, with `ANTHROPIC_API_KEY` set) has **not been performed**, because the build environment has neither an API key nor access to the embedding model. Run it on your machine before presenting and replace this table with its output.

## 15. Live project demonstration

Suggested script (about five minutes):

1. Create an account and upload the four files from `data/samples/`.
2. Ask "How many unused leave days can be carried over?" and open citation 1 to show the highlighted sentence.
3. Ask "What is 15% of 2,400?" to show the calculator tool.
4. Ask "What is the capital city of Australia?" to show the honest refusal.
5. Ask a follow-up in the same chat to show history, then open Usage.

## 16. GitHub repository

Create the repository, then push with commit messages in the `feat:`, `fix:`, `test:`, `docs:` style. The repository URL goes here: ______________________

## 17. Future improvements

- OCR for scanned PDFs
- Hybrid keyword plus vector search, and a reranker
- Background job queue for large uploads
- PostgreSQL and a managed vector database for multi-server use
- Team workspaces and role-based access
- Optional local language model through Ollama
