"""Unit tests for the building blocks: chunking, calculator, security, embeddings, citations."""
import math

import anthropic
import httpx
import pytest

from app.ai.embeddings import HashingEmbedder
from app.ai.rag import Hit, SourceRegistry, confidence_level, validate_citations
from app.ai.tools import execute_tool, safe_eval
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.core.errors import AppError
from app.ingestion.chunker import chunk_pages
from app.ingestion.parsers import ParsedPage, parse_file
from app.ai.llm import _map_error


# ---------------------------------------------------------------- chunker
def _pages(text: str) -> list[ParsedPage]:
    return [ParsedPage(1, text)]


def test_chunks_respect_size_and_have_sequential_indexes():
    text = " ".join(f"Sentence number {i} talks about topic {i % 7}." for i in range(120))
    chunks = chunk_pages(_pages(text), size=300, overlap=60)
    assert len(chunks) > 3
    assert all(len(c.text) <= 300 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_overlap_between_neighbours():
    text = " ".join(f"Fact {i} is stated here clearly." for i in range(60))
    chunks = chunk_pages(_pages(text), size=250, overlap=80)
    shared = [set(a.text.split(". ")) & set(b.text.split(". ")) for a, b in zip(chunks, chunks[1:])]
    assert any(shared)


def test_chunker_splits_unbroken_strings():
    chunks = chunk_pages(_pages("x" * 2500), size=800, overlap=100)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 800 for c in chunks)


def test_chunker_keeps_page_numbers():
    pages = [ParsedPage(1, "First page content."), ParsedPage(2, "Second page content.")]
    chunks = chunk_pages(pages, size=800, overlap=100)
    assert [c.page for c in chunks] == [1, 2]


def test_markdown_headings_start_new_chunks():
    filler = " ".join(f"This sentence adds some detail about the topic number {i}." for i in range(6))
    text = f"## Leave\n\n{filler}\n\n## Sick\n\n{filler}\n\n## Remote\n\n{filler}"
    chunks = chunk_pages(_pages(text), size=800, overlap=100)
    assert len(chunks) == 3
    assert chunks[0].text.startswith("## Leave") and "Sick" not in chunks[0].text
    assert chunks[1].text.startswith("## Sick") and "Remote" not in chunks[1].text
    assert chunks[2].text.startswith("## Remote")


def test_tiny_fragments_are_merged_into_the_next_chunk():
    text = "# Title\n\nShort intro.\n\n## Details\n\n" + " ".join(f"Detail sentence {i} is here." for i in range(8))
    chunks = chunk_pages(_pages(text), size=800, overlap=100)
    assert len(chunks) == 1
    assert chunks[0].text.startswith("# Title") and "Detail sentence 7" in chunks[0].text


def test_chunker_rejects_bad_parameters():
    with pytest.raises(ValueError):
        chunk_pages(_pages("hello"), size=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_pages(_pages("hello"), size=0, overlap=0)


def test_parser_rejects_binary_garbage(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(AppError) as exc:
        parse_file(bad)
    assert exc.value.code == "PARSE_FAILED"


def test_parser_rejects_empty_text(tmp_path):
    empty = tmp_path / "blank.txt"
    empty.write_text("   \n\n  ")
    with pytest.raises(AppError) as exc:
        parse_file(empty)
    assert exc.value.code == "NO_TEXT"


# ---------------------------------------------------------------- calculator
@pytest.mark.parametrize(
    "expression, expected",
    [
        ("2 + 3 * 4", 14),
        ("(24 - 5) * 3", 57),
        ("2 ** 10", 1024),
        ("2 ^ 3", 8),
        ("10 / 4", 2.5),
        ("7 // 2", 3),
        ("-5 + 8", 3),
        ("sqrt(144)", 12),
        ("round(3.14159, 2)", 3.14),
        ("max(3, 9, 4)", 9),
    ],
)
def test_calculator_evaluates_valid_expressions(expression, expected):
    assert math.isclose(safe_eval(expression), expected)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo hacked')",
        "open('/etc/passwd')",
        "().__class__.__bases__",
        "lambda: 1",
        "[1, 2, 3]",
        "'a' * 5",
        "x + 1",
        "2 ** 100000",
        "",
        "1 +",
    ],
)
def test_calculator_rejects_unsafe_or_invalid_input(expression):
    with pytest.raises(ValueError):
        safe_eval(expression)


def test_calculate_tool_returns_errors_as_text():
    assert execute_tool("calculate", {"expression": "1/0"}, None).startswith("ERROR")
    assert execute_tool("calculate", {"expression": "(24 - 5) * 3"}, None) == "57"
    assert execute_tool("nope", {}, None).startswith("ERROR")


# ---------------------------------------------------------------- security
def test_password_hash_roundtrip_and_long_passwords():
    hashed = hash_password("a-very-long-passphrase-" * 10)
    assert verify_password("a-very-long-passphrase-" * 10, hashed)
    assert not verify_password("wrong", hashed)
    assert hashed != hash_password("a-very-long-passphrase-" * 10)  # salted


def test_jwt_roundtrip_and_tamper_detection():
    token = create_access_token(42)
    assert decode_access_token(token) == 42
    with pytest.raises(AppError):
        decode_access_token(token[:-3] + "abc")
    with pytest.raises(AppError):
        decode_access_token("not-a-token")


# ---------------------------------------------------------------- embeddings
def test_hashing_embedder_is_normalised_and_deterministic():
    embedder = HashingEmbedder()
    a = embedder.embed(["annual leave policy for employees"])[0]
    b = embedder.embed(["annual leave policy for employees"])[0]
    assert a == b
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-6)


def test_hashing_embedder_ranks_related_text_higher():
    embedder = HashingEmbedder()
    query, related, unrelated = embedder.embed(
        [
            "how many days of annual leave do employees get",
            "Employees receive 24 days of paid annual leave per year.",
            "The drone camera records 4K video with a three axis gimbal.",
        ]
    )
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))
    assert dot(query, related) > dot(query, unrelated) + 0.1


# ---------------------------------------------------------------- citations
def _registry() -> SourceRegistry:
    registry = SourceRegistry()
    for i, score in enumerate([0.6, 0.4, 0.2], start=0):
        registry.register(Hit(f"1:{i}", f"text {i}", 1, "a.md", 1, i, score))
    return registry


def test_valid_citations_are_renumbered_by_first_use():
    text, cited = validate_citations("Second fact [2]. First fact [1]. Again [2].", _registry())
    assert text == "Second fact [1]. First fact [2]. Again [1]."
    assert [s.chunk_index for s in cited] == [1, 0]


def test_invented_citations_are_removed():
    text, cited = validate_citations("Real [1] and invented [7] and grouped [3, 9].", _registry())
    assert "[7]" not in text and "[9]" not in text
    assert len(cited) == 2


def test_registry_deduplicates_the_same_chunk():
    registry = SourceRegistry()
    first = registry.register(Hit("1:0", "t", 1, "a.md", 1, 0, 0.3))
    again = registry.register(Hit("1:0", "t", 1, "a.md", 1, 0, 0.5))
    assert first is again and again.score == 0.5 and len(registry) == 1


def test_confidence_levels():
    registry = _registry()
    cited = [registry.get(1)]
    assert confidence_level(False, [], 0.3) == "none"
    assert confidence_level(True, [], 0.3) == "medium"
    assert confidence_level(True, cited, 0.3) == "high"  # 0.6 >= 2 * 0.3
    assert confidence_level(True, [registry.get(3)], 0.3) == "low"


# ---------------------------------------------------------------- LLM error mapping
def test_llm_errors_map_to_friendly_app_errors():
    request = httpx.Request("POST", "https://api.example.test")
    assert _map_error(anthropic.APIConnectionError(request=request)).code == "LLM_UNAVAILABLE"
    assert _map_error(anthropic.APITimeoutError(request=request)).code == "LLM_UNAVAILABLE"
    response = httpx.Response(429, request=request)
    assert _map_error(anthropic.RateLimitError("slow down", response=response, body=None)).code == "LLM_RATE_LIMITED"
    response = httpx.Response(401, request=request)
    assert _map_error(anthropic.AuthenticationError("bad key", response=response, body=None)).code == "LLM_AUTH_FAILED"
