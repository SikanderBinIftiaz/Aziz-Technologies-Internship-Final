"""Agent tools: schemas for the model and the safe implementations that run on the server."""
import ast
import logging
import math
import operator
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai.llm import LLMClient
from app.ai.prompts import SUMMARY_CHUNK_PROMPT, SUMMARY_COMBINE_PROMPT
from app.ai.rag import Retriever, SourceRegistry, format_for_model
from app.config import Settings
from app.core.errors import AppError
from app.db.models import Document

log = logging.getLogger(__name__)

NO_RESULTS = "NO_RESULTS: no passages in the user's documents were relevant to this query."

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "search_documents",
        "description": (
            "Search the user's uploaded documents and return the most relevant passages, numbered for citation. "
            "Use this before answering any question about document content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A complete, standalone search question."},
                "document_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional: restrict the search to these document ids.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "calculate",
        "description": "Evaluate an arithmetic expression exactly. Supports + - * / // % ** ( ), sqrt, abs, round, min, max, pi, e.",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "For example: (24 - 5) * 3 / 2"}},
            "required": ["expression"],
        },
    },
    {
        "name": "document_stats",
        "description": "Return the page count, chunk count, word count and size of one document.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "summarize_document",
        "description": "Produce a bullet-point summary of one whole document. Use only when a full summary is requested.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
        },
    },
]


@dataclass
class UsageCounter:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


@dataclass
class ToolContext:
    db: Session
    user_id: int
    retriever: Retriever
    llm: LLMClient | None
    registry: SourceRegistry
    usage: UsageCounter
    settings: Settings
    scope_ids: list[int] | None = None
    extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------- safe calculator
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max}
_CONSTANTS = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 100 or abs(left) > 1e6):
            raise ValueError("That exponent is too large.")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
        and 0 < len(node.args) <= 5
    ):
        return _FUNCTIONS[node.func.id](*[_eval(arg) for arg in node.args])
    raise ValueError("Unsupported expression.")


def safe_eval(expression: str) -> float:
    """Evaluate arithmetic with an AST whitelist. Never uses eval()."""
    expression = (expression or "").strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    if not expression or len(expression) > 200:
        raise ValueError("Expression is empty or too long.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Could not parse the expression.") from exc
    return _eval(tree.body)


def format_number(value: float) -> str:
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}" if isinstance(value, float) else str(value)


# ---------------------------------------------------------------- tool implementations
def _int_list(value) -> list[int] | None:
    if not isinstance(value, list):
        return None
    ids = [int(v) for v in value if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return ids[:50] or None


def _owned_document(ctx: ToolContext, args: dict) -> Document:
    raw = args.get("document_id")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("document_id must be an integer.")
    doc = ctx.db.get(Document, int(raw))
    if doc is None or doc.owner_id != ctx.user_id or doc.status != "ready":
        raise ValueError(f"Document {int(raw)} was not found.")
    return doc


def _search(args: dict, ctx: ToolContext) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string.")
    scope = ctx.scope_ids or _int_list(args.get("document_ids"))
    hits = ctx.retriever.search(ctx.user_id, query[:500], scope)
    if not hits:
        return NO_RESULTS
    return format_for_model([ctx.registry.register(hit) for hit in hits])


def _calculate(args: dict, ctx: ToolContext) -> str:
    expression = args.get("expression")
    if not isinstance(expression, str):
        raise ValueError("expression must be a string.")
    try:
        return format_number(safe_eval(expression))
    except ZeroDivisionError as exc:
        raise ValueError("Division by zero.") from exc
    except OverflowError as exc:
        raise ValueError("The result is too large.") from exc


def _stats(args: dict, ctx: ToolContext) -> str:
    doc = _owned_document(ctx, args)
    return (
        f"Document {doc.id}: {doc.filename}\n"
        f"Pages/sections: {doc.pages}\nIndexed chunks: {doc.chunks}\nWords: {doc.words}\n"
        f"File size: {doc.size_bytes / 1024:.1f} KB\nUploaded: {doc.created_at:%Y-%m-%d}"
    )


def _summarize(args: dict, ctx: ToolContext) -> str:
    from app.ai.vectorstore import get_vectorstore

    doc = _owned_document(ctx, args)
    chunks = get_vectorstore().get_document_chunks(ctx.user_id, doc.id)
    if not chunks:
        raise ValueError("This document has no indexed text.")
    if len(chunks) > 40:  # sample evenly to bound cost on very long documents
        step = len(chunks) / 40
        chunks = [chunks[int(i * step)] for i in range(40)]
    texts = [c["text"] for c in chunks]

    if ctx.llm is None or not ctx.llm.available:
        points = [t.split(". ")[0].strip() for t in texts[:8]]
        return f"SUMMARY of {doc.filename} (extractive, no AI model configured):\n" + "\n".join(f"- {p}" for p in points)

    batches: list[str] = []
    current, size = [], 0
    for text in texts:
        if current and size + len(text) > 6000:
            batches.append("\n\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        batches.append("\n\n".join(current))

    partials = []
    for batch in batches:
        result = ctx.llm.complete(SUMMARY_CHUNK_PROMPT, [{"role": "user", "content": batch}], max_tokens=300)
        ctx.usage.add(result.input_tokens, result.output_tokens)
        partials.append(result.text.strip())
    if len(partials) == 1:
        return f"SUMMARY of {doc.filename}:\n{partials[0]}"
    combined = ctx.llm.complete(
        SUMMARY_COMBINE_PROMPT, [{"role": "user", "content": "\n\n".join(partials)}], max_tokens=500
    )
    ctx.usage.add(combined.input_tokens, combined.output_tokens)
    return f"SUMMARY of {doc.filename}:\n{combined.text.strip()}"


_TOOLS = {
    "search_documents": _search,
    "calculate": _calculate,
    "document_stats": _stats,
    "summarize_document": _summarize,
}


def execute_tool(name: str, args: dict, ctx: ToolContext) -> str:
    """Run a tool and always return text. Errors become messages the model can recover from."""
    handler = _TOOLS.get(name)
    if handler is None:
        return f"ERROR: unknown tool '{name}'."
    try:
        return handler(args or {}, ctx)
    except ValueError as exc:
        return f"ERROR: {exc}"
    except AppError as exc:
        return f"ERROR: {exc.message}"
    except Exception:
        log.exception("Tool %s failed", name)
        return "ERROR: the tool failed unexpectedly."
