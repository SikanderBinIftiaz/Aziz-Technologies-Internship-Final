"""End-to-end API tests (offline demo mode: hashing embeddings, no LLM key)."""
import json


def upload(client, headers, name: str, data: bytes, content_type: str = "text/markdown"):
    return client.post("/api/documents", headers=headers, files={"file": (name, data, content_type)})


def ask(client, headers, question: str, **extra):
    return client.post("/api/chat", headers=headers, json={"question": question, **extra})


# ---------------------------------------------------------------- health and static
def test_health_reports_status(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["mode"] == "demo"
    assert body["embedding_backend"] == "hash"


def test_frontend_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "DocuMind" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


# ---------------------------------------------------------------- auth
def test_register_login_and_me(client):
    credentials = {"email": "Alice@Example.com ", "password": "supersecret1"}
    registered = client.post("/api/auth/register", json=credentials)
    assert registered.status_code == 201
    assert registered.json()["user"]["email"] == "alice@example.com"

    login = client.post("/api/auth/login", json={"email": "alice@example.com", "password": "supersecret1"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).json()["email"] == "alice@example.com"


def test_duplicate_email_is_rejected(client):
    body = {"email": "dup@example.com", "password": "supersecret1"}
    assert client.post("/api/auth/register", json=body).status_code == 201
    response = client.post("/api/auth/register", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


def test_validation_errors_use_the_standard_error_shape(client):
    response = client.post("/api/auth/register", json={"email": "not-an-email", "password": "short"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {d["field"] for d in error["details"]} == {"email", "password"}


def test_wrong_password_and_unknown_user_are_indistinguishable(client, auth):
    _, email = auth
    wrong = client.post("/api/auth/login", json={"email": email, "password": "not-the-password"})
    unknown = client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "whatever123"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_protected_routes_require_a_valid_token(client):
    for method, path in [("get", "/api/documents"), ("get", "/api/sessions"), ("get", "/api/usage")]:
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "NOT_AUTHENTICATED"
    bad = client.get("/api/documents", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_TOKEN"


# ---------------------------------------------------------------- documents
def test_upload_list_and_delete_document(client, auth, sample_bytes):
    headers, _ = auth
    response = upload(client, headers, "handbook.md", sample_bytes("northwind_employee_handbook.md"))
    assert response.status_code == 201
    doc = response.json()
    assert doc["status"] == "ready" and doc["chunks"] >= 2 and doc["words"] > 100

    listed = client.get("/api/documents", headers=headers).json()
    assert [d["id"] for d in listed] == [doc["id"]]

    assert client.delete(f"/api/documents/{doc['id']}", headers=headers).status_code == 204
    assert client.get("/api/documents", headers=headers).json() == []
    # once deleted, its content can no longer be retrieved
    answer = ask(client, headers, "How many days of annual leave?").json()
    assert answer["found"] is False


def test_duplicate_upload_is_rejected(client, auth, sample_bytes):
    headers, _ = auth
    data = sample_bytes("skyscout_x2_manual.md")
    assert upload(client, headers, "a.md", data).status_code == 201
    response = upload(client, headers, "b.md", data)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DUPLICATE_DOCUMENT"


def test_upload_rejects_bad_files(client, auth):
    headers, _ = auth
    assert upload(client, headers, "malware.exe", b"MZ...", "application/octet-stream").status_code == 415
    assert upload(client, headers, "empty.txt", b"", "text/plain").status_code == 422
    broken = upload(client, headers, "broken.pdf", b"not really a pdf", "application/pdf")
    assert broken.status_code == 422 and broken.json()["error"]["code"] == "PARSE_FAILED"
    # a failed upload stays visible in the library with its reason
    failed = [d for d in client.get("/api/documents", headers=headers).json() if d["status"] == "failed"]
    assert failed and failed[0]["error"]


def test_upload_sanitises_path_tricks(client, auth):
    headers, _ = auth
    response = upload(client, headers, "../../etc/notes.txt", b"Plain notes about leave policy and holidays.", "text/plain")
    assert response.status_code == 201
    assert response.json()["filename"] == "notes.txt"


# ---------------------------------------------------------------- chat
def test_answer_is_grounded_with_citations(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "northwind_employee_handbook.md", sample_bytes("northwind_employee_handbook.md"))
    upload(client, headers, "skyscout_x2_manual.md", sample_bytes("skyscout_x2_manual.md"))

    body = ask(client, headers, "How many days of annual leave does a full-time employee get?").json()
    assert body["found"] is True
    assert body["mode"] == "demo"
    assert body["citations"], body
    top = body["citations"][0]
    assert top["filename"] == "northwind_employee_handbook.md"
    assert "24 days" in top["snippet"]
    assert "[1]" in body["answer"]
    assert body["confidence"] in {"high", "medium", "low"}

    # The offline hashing embedder is lexical, so the question uses the document's own wording ("flight time").
    drone = ask(client, headers, "What is the flight time of the SkyScout X2 on a full battery?").json()
    assert drone["citations"][0]["filename"] == "skyscout_x2_manual.md"
    assert "32 minutes" in drone["citations"][0]["snippet"]


def test_unanswerable_question_is_refused_without_citations(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "northwind_employee_handbook.md", sample_bytes("northwind_employee_handbook.md"))
    body = ask(client, headers, "What is the capital city of Australia?").json()
    assert body["found"] is False
    assert body["confidence"] == "none"
    assert body["citations"] == []
    assert body["answer"].startswith("I could not find")


def test_chat_without_documents_explains_what_to_do(client, auth):
    headers, _ = auth
    body = ask(client, headers, "What does the policy say about leave?").json()
    assert body["found"] is False
    assert "Upload" in body["answer"]
    assert body["usage"]["input_tokens"] == 0


def test_calculator_path_in_demo_mode(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "m.md", sample_bytes("machine_learning_notes.md"))
    body = ask(client, headers, "(24 - 5) * 3").json()
    assert body["found"] is True
    assert body["answer"] == "(24 - 5) * 3 = 57"
    assert body["tools_used"] == ["calculate"]


def test_sessions_keep_history_and_can_be_deleted(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "h.md", sample_bytes("northwind_employee_handbook.md"))
    first = ask(client, headers, "How many sick leave days do employees receive?").json()
    session_id = first["session_id"]
    second = ask(client, headers, "When is a medical certificate required?", session_id=session_id).json()
    assert second["session_id"] == session_id

    sessions = client.get("/api/sessions", headers=headers).json()
    assert [s["id"] for s in sessions] == [session_id]
    assert sessions[0]["title"].startswith("How many sick leave")

    messages = client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1]["citations"]

    assistant_id = messages[1]["id"]
    assert client.post(f"/api/messages/{assistant_id}/feedback", headers=headers, json={"value": 1}).status_code == 204
    assert client.get(f"/api/sessions/{session_id}/messages", headers=headers).json()[1]["feedback"] == 1
    assert client.post(f"/api/messages/{assistant_id}/feedback", headers=headers, json={"value": 5}).status_code == 422

    assert client.delete(f"/api/sessions/{session_id}", headers=headers).status_code == 204
    assert client.get("/api/sessions", headers=headers).json() == []


def test_chat_validates_input(client, auth):
    headers, _ = auth
    assert ask(client, headers, "   ").status_code == 422
    assert ask(client, headers, "x" * 2001).status_code == 422
    assert ask(client, headers, "hello", session_id=999999).status_code == 404


def test_scope_to_selected_documents(client, auth, sample_bytes):
    headers, _ = auth
    handbook = upload(client, headers, "h.md", sample_bytes("northwind_employee_handbook.md")).json()
    manual = upload(client, headers, "d.md", sample_bytes("skyscout_x2_manual.md")).json()
    scoped = ask(client, headers, "How long does the battery last in flight?", document_ids=[manual["id"]]).json()
    assert scoped["citations"][0]["document_id"] == manual["id"]
    wrong_scope = ask(client, headers, "How long does the battery last in flight?", document_ids=[handbook["id"]]).json()
    assert wrong_scope["found"] is False


# ---------------------------------------------------------------- streaming
def test_streaming_endpoint_emits_events_and_final_answer(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "m.md", sample_bytes("northwind_security_policy.md"))
    with client.stream(
        "POST", "/api/chat/stream", headers=headers, json={"question": "How long are backups retained?"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert "token" in kinds and kinds[-1] == "final"
    final = events[-1]
    assert final["found"] is True and "35 days" in final["citations"][0]["snippet"]
    # the streamed turn was persisted
    assert client.get(f"/api/sessions/{final['session_id']}/messages", headers=headers).json()[1]["content"]


def test_streaming_reports_errors_as_events(client, auth):
    headers, _ = auth
    with client.stream(
        "POST", "/api/chat/stream", headers=headers, json={"question": "hi", "session_id": 987654}
    ) as response:
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "error" and events[-1]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------- isolation between users
def test_users_cannot_access_each_others_data(client, auth, sample_bytes):
    headers_a, _ = auth
    doc = upload(client, headers_a, "secret.md", sample_bytes("northwind_employee_handbook.md")).json()
    session_id = ask(client, headers_a, "How many days of annual leave?").json()["session_id"]

    response = client.post("/api/auth/register", json={"email": "mallory@example.com", "password": "supersecret1"})
    headers_b = {"Authorization": f"Bearer {response.json()['access_token']}"}

    assert client.get("/api/documents", headers=headers_b).json() == []
    assert client.delete(f"/api/documents/{doc['id']}", headers=headers_b).status_code == 404
    assert client.get(f"/api/sessions/{session_id}/messages", headers=headers_b).status_code == 404
    assert client.delete(f"/api/sessions/{session_id}", headers=headers_b).status_code == 404
    assert ask(client, headers_b, "hello", session_id=session_id).status_code == 404
    assert ask(client, headers_b, "annual leave", document_ids=[doc["id"]]).status_code == 404
    # B has no documents, so A's content can never be retrieved for B
    leaked = ask(client, headers_b, "How many days of annual leave does a full-time employee get?").json()
    assert leaked["found"] is False and leaked["citations"] == []


# ---------------------------------------------------------------- usage
def test_usage_summary_tracks_requests(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "h.md", sample_bytes("northwind_employee_handbook.md"))
    ask(client, headers, "How many days of annual leave?")
    ask(client, headers, "What is the home-office stipend?")
    usage = client.get("/api/usage", headers=headers).json()
    assert usage["requests"] == 2
    assert usage["documents"] == 1 and usage["chunks"] >= 2
    assert len(usage["daily"]) == 7 and sum(d["requests"] for d in usage["daily"]) == 2
    assert len(usage["recent"]) == 2
