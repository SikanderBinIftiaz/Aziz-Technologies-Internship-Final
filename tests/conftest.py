"""Test configuration. Environment variables are set BEFORE the app is imported so every test runs
against a throw-away database and vector store, with offline hashing embeddings and no LLM key."""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="documind-tests-")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{_TMP}/test.db",
        "CHROMA_PATH": f"{_TMP}/chroma",
        "UPLOAD_DIR": f"{_TMP}/uploads",
        "EMBEDDING_BACKEND": "hash",
        "ANTHROPIC_API_KEY": "",
        "JWT_SECRET": "test-secret-key-for-unit-tests-only",
        "APP_ENV": "test",
        "RATE_LIMIT_AUTH": "1000/minute",
        "RATE_LIMIT_CHAT": "1000/minute",
        "RATE_LIMIT_UPLOAD": "1000/minute",
        "LOG_LEVEL": "WARNING",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


_counter = {"n": 0}


@pytest.fixture
def auth(client):
    """A freshly registered user: returns (headers, email)."""
    _counter["n"] += 1
    email = f"user{_counter['n']}@example.com"
    response = client.post("/api/auth/register", json={"email": email, "password": "correct-horse-battery"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, email


@pytest.fixture
def sample_bytes():
    def load(name: str) -> bytes:
        return (SAMPLES / name).read_bytes()

    return load
