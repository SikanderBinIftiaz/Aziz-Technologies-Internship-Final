"""Regression tests for fixes made while building the web interface."""
import pytest

from app.ai.agent import _arithmetic_question
from app.ingestion.parsers import parse_file
from tests.test_api import ask, upload


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What is 15% of 2,400?", "15/100*2400"),
        ("what's 12.5% of 80", "12.5/100*80"),
        ("Calculate 18 * 24", "18 * 24"),
        ("12*(3+4)", "12*(3+4)"),
        ("How much is 1,200 + 300?", "1200 + 300"),
        ("How many days of annual leave do I get?", None),
        ("What is the home-office stipend?", None),
        ("What is 2024?", None),
    ],
)
def test_demo_calculator_understands_natural_phrasing(question, expected):
    assert _arithmetic_question(question) == expected


def test_percent_question_is_answered_through_the_api(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "northwind_employee_handbook.md", sample_bytes("northwind_employee_handbook.md"))
    body = ask(client, headers, "What is 15% of 2,400?").json()
    assert body["found"] is True
    assert "360" in body["answer"]
    assert body["tools_used"] == ["calculate"]


def test_markdown_headings_become_plain_sentences(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Title\n\n## Annual Leave\n\nEmployees get **24 days** of `paid` leave.\n", encoding="utf-8")
    text = parse_file(path)[0].text
    assert "##" not in text and "**" not in text and "`" not in text
    assert "Annual Leave." in text
    assert "24 days of paid leave" in text


def test_text_files_keep_hash_characters(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("Ticket #42 is open.\n\n## not a heading in plain text\n", encoding="utf-8")
    assert "## not a heading" in parse_file(path)[0].text


def test_long_questions_get_a_clean_session_title(client, auth, sample_bytes):
    headers, _ = auth
    upload(client, headers, "northwind_employee_handbook.md", sample_bytes("northwind_employee_handbook.md"))
    question = "How many unused leave days can be carried over to the next year according to the handbook?"
    ask(client, headers, question)
    title = client.get("/api/sessions", headers=headers).json()[0]["title"]
    assert len(title) <= 60
    assert title.endswith("…")
    assert question.startswith(title[:-1])
    assert not title[:-1].endswith(" ")
