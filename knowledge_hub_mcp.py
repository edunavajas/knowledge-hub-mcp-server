#!/usr/bin/env python3
"""Self-hosted stdio MCP bridge to n8n RAG webhooks and the Outline API."""
from __future__ import annotations

import json
import os
import re
import sys
import subprocess
import tempfile
from pathlib import Path

VERSION = "0.1.0"

ENV_FILE = Path(
    os.environ.get("KNOWLEDGE_HUB_ENV_FILE")
    or (Path.home() / ".config/knowledge-hub/knowledge.env")
).expanduser()
if ENV_FILE.exists():
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip('"\'')

N8N_URL = os.environ.get("N8N_URL", "").rstrip("/")
OUTLINE_URL = os.environ.get("OUTLINE_URL", "").rstrip("/")
RAG_API_KEY = os.environ.get("RAG_SEARCH_API_KEY", "")
OUTLINE_API_KEY = os.environ.get("OUTLINE_API_KEY", "")
WORKSPACE_ROOT = Path(os.environ.get("KNOWLEDGE_HUB_WORKSPACE") or Path.cwd()).expanduser().resolve()
PROJECT_SLUG = os.environ.get("KNOWLEDGE_HUB_PROJECT") or WORKSPACE_ROOT.name

REPO_EXCLUDED_DIRS = {
    ".git", ".agents", ".atl", ".codex", ".kimi-code", ".cache",
    ".dart_tool", "node_modules", "vendor", "build", "dist", "coverage",
    "tmp", "generated",
}
REPO_DOCUMENT_NAMES = {"README.md", "AGENTS.md", "DEPLOY.md"}
REPO_DOCUMENT_DIRS = {"docs", "design"}
REDACTION_PATTERNS = (
    re.compile(r"(?i)(\b(?:api[_ -]?key|access[_ -]?token|auth(?:orization)?|password|secret)\b\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\b(?:(?:ghp|github_pat|glpat)[_-]|sk-)[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)

TOOLS = [
    {
        "name": "knowledge_search",
        "description": "Semantic search over curated Outline knowledge and sanitized repository documentation using self-hosted embeddings and pgvector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language search query."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "source_type": {"type": "string", "enum": ["outline", "repo", "all"], "default": "all"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "knowledge_read",
        "description": "Read the complete Markdown of a knowledge document by its Outline document ID.",
        "inputSchema": {
            "type": "object",
            "properties": {"document_id": {"type": "string", "description": "Outline document UUID."}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "knowledge_health",
        "description": "Return safe operational counts for the knowledge index without exposing content or vectors.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "knowledge_index",
        "description": "Start an authenticated asynchronous reindex of the Outline collection.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "knowledge_index_repository",
        "description": "Scan safe Markdown documentation in the current project workspace and asynchronously index it as technical repository knowledge.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def write_message(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def result(request_id, value: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class UpstreamError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"upstream request failed ({status})")


def _redact(text: str) -> str:
    result = text
    for pattern in REDACTION_PATTERNS:
        if pattern.groups:
            result = pattern.sub(lambda match: match.group(1) + "<REDACTED>", result)
        else:
            result = pattern.sub("<REDACTED>", result)
    return result


def _is_repo_doc(path: Path) -> bool:
    relative = path.relative_to(WORKSPACE_ROOT)
    if any(part in REPO_EXCLUDED_DIRS or part.endswith("-worktrees") for part in relative.parts):
        return False
    if path.name in REPO_DOCUMENT_NAMES or path.parent.name in REPO_DOCUMENT_DIRS:
        return True
    return relative.parent == Path(".") and path.suffix.lower() == ".md"


def _repo_documents() -> list[dict]:
    documents = []
    for path in sorted(WORKSPACE_ROOT.rglob("*.md")):
        if not path.is_file() or not _is_repo_doc(path):
            continue
        size = path.stat().st_size
        if size == 0 or size > 200_000:
            continue
        relative = path.relative_to(WORKSPACE_ROOT).as_posix()
        raw = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        content = _redact(raw).strip()
        if not content:
            continue
        heading = next((line.lstrip("# ").strip() for line in content.splitlines()
                        if line.startswith("#") and line.lstrip("# ").strip()), path.stem)
        title = heading[:200]
        documents.append({
            "source_type": "repo",
            "external_id": f"workspace:{relative}",
            "title": title,
            "collection": f"{PROJECT_SLUG}-repository",
            "content": f"{title}\n\nRepository path: {relative}\n\n{content}",
            "canonical_url": f"repo://{PROJECT_SLUG}/{relative}",
            "metadata": {
                "repository": PROJECT_SLUG,
                "path": relative,
                "kind": "repository-documentation",
                "modified_at": path.stat().st_mtime,
            },
        })
    return documents


def call_json(url: str, *, method: str = "GET", headers: dict | None = None, body: dict | None = None) -> object:
    # curl is used deliberately here: its wire behavior matches the working
    # webhook probes. Keep credentials out of argv and large JSON bodies out of
    # argv too: execve has a finite argument-size limit on Linux.
    args = ["curl", "--silent", "--show-error", "--max-time", "60", "-X", method, url,
            "--write-out", "\n__MCP_HTTP_STATUS__%{http_code}"]
    config_lines = ["header = \"Accept: application/json\""]
    for key, value in (headers or {}).items():
        config_lines.append(f"header = \"{key}: {value}\"")
    if body is not None:
        config_lines.append("header = \"Content-Type: application/json\"")
    config = ("\n".join(config_lines) + "\n").encode("utf-8")
    if body is None:
        args.extend(["--config", "-"])
        stdin = config
        completed = subprocess.run(args, input=stdin, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, check=False)
    else:
        # A short-lived 0600 config file lets stdin carry the request body.
        with tempfile.NamedTemporaryFile(prefix="knowledge-hub-curl-", mode="wb", delete=True) as config_file:
            config_file.write(config)
            config_file.flush()
            args.extend(["--config", config_file.name, "--data-binary", "@-"])
            stdin = json.dumps(body, ensure_ascii=False).encode("utf-8")
            completed = subprocess.run(args, input=stdin, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, check=False)
    marker = b"\n__MCP_HTTP_STATUS__"
    raw, separator, status_bytes = completed.stdout.rpartition(marker)
    if not separator:
        raise UpstreamError(0)
    try:
        status = int(status_bytes.decode("ascii"))
    except ValueError:
        raise UpstreamError(0) from None
    if status >= 400:
        raise UpstreamError(status)
    return json.loads(raw.decode("utf-8")) if raw else {}


def tool_call(name: str, args: dict) -> object:
    if name == "knowledge_search":
        query = str(args.get("query", "")).strip()
        if not query or len(query) > 2000:
            raise ValueError("query must contain between 1 and 2000 characters")
        limit = args.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            raise ValueError("limit must be an integer between 1 and 20")
        limit = max(1, min(20, int(limit)))
        source_type = str(args.get("source_type", "all")).strip().lower() or "all"
        if source_type not in {"outline", "repo", "all"}:
            raise ValueError("source_type must be one of: outline, repo, all")
        if not N8N_URL or not RAG_API_KEY:
            raise RuntimeError("RAG bridge is not configured")
        return call_json(
            f"{N8N_URL}/webhook/rag-search",
            method="POST",
            headers={"X-RAG-API-Key": RAG_API_KEY},
            body={"query": query, "limit": limit, "source_type": source_type},
        )

    if name == "knowledge_read":
        document_id = str(args.get("document_id", "")).strip()
        if not document_id or len(document_id) > 128:
            raise ValueError("document_id is required")
        if not OUTLINE_URL or not OUTLINE_API_KEY:
            raise RuntimeError("Outline bridge is not configured")
        return call_json(
            f"{OUTLINE_URL}/api/documents.info",
            method="POST",
            headers={"Authorization": f"Bearer {OUTLINE_API_KEY}"},
            body={"id": document_id},
        )

    if name == "knowledge_health":
        if not N8N_URL or not RAG_API_KEY:
            raise RuntimeError("RAG bridge is not configured")
        return call_json(f"{N8N_URL}/webhook/rag-health", headers={"X-RAG-API-Key": RAG_API_KEY})

    if name == "knowledge_index":
        if not N8N_URL or not RAG_API_KEY:
            raise RuntimeError("RAG bridge is not configured")
        return call_json(f"{N8N_URL}/webhook/rag-index", method="POST", headers={"X-RAG-API-Key": RAG_API_KEY})

    if name == "knowledge_index_repository":
        if not N8N_URL or not RAG_API_KEY:
            raise RuntimeError("RAG bridge is not configured")
        documents = _repo_documents()
        if not documents:
            return {"accepted": 0, "source_type": "repo"}
        response = call_json(
            f"{N8N_URL}/webhook/rag-index-repo",
            method="POST",
            headers={"X-RAG-API-Key": RAG_API_KEY},
            body={"replace_all": True, "documents": documents},
        )
        return {"accepted": len(documents), "source_type": "repo", "upstream": response}

    raise ValueError(f"unknown tool: {name}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                requested = request.get("params", {}).get("protocolVersion", "2025-06-18")
                write_message(result(request_id, {
                    "protocolVersion": requested if requested in {"2025-06-18", "2024-11-05", "2024-10-07"} else "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "knowledge-hub", "version": VERSION},
                }))
            elif method in {"notifications/initialized", "notifications/cancelled"}:
                continue
            elif method == "ping":
                write_message(result(request_id, {}))
            elif method == "tools/list":
                write_message(result(request_id, {"tools": TOOLS}))
            elif method == "tools/call":
                params = request.get("params", {})
                name = params.get("name", "")
                try:
                    value = tool_call(name, params.get("arguments") or {})
                    write_message(result(request_id, {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}))
                except (ValueError, RuntimeError) as exc:
                    write_message(result(request_id, {"isError": True, "content": [{"type": "text", "text": str(exc)}]}))
                except UpstreamError as exc:
                    # Never return upstream response bodies; they may contain internal details.
                    write_message(result(request_id, {"isError": True, "content": [{"type": "text", "text": str(exc)}]}))
                except (TimeoutError, json.JSONDecodeError, OSError):
                    write_message(result(request_id, {"isError": True, "content": [{"type": "text", "text": "upstream request failed"}]}))
            else:
                if request_id is not None:
                    write_message(error(request_id, -32601, f"method not found: {method}"))
        except json.JSONDecodeError:
            continue
        except Exception as exc:  # Keep the stdio server alive for the next request.
            if "request_id" in locals() and request_id is not None:
                write_message(error(request_id, -32603, "internal MCP bridge error"))
            print(f"knowledge-hub-mcp: {type(exc).__name__}", file=sys.stderr)


def _redact_value(value: str) -> str:
    if not value:
        return "(unset)"
    # ponytail: the first pattern only anchors on an unquoted `key:` label, so
    # feed it one; values the prefix/JWT patterns alone miss still get redacted.
    return _redact("api_key: " + value).split(": ", 1)[-1]


def _print_config() -> None:
    print(f"version: {VERSION}")
    print(f"env_file: {ENV_FILE}")
    print(f"env_file_exists: {ENV_FILE.exists()}")
    print(f"workspace_root: {WORKSPACE_ROOT}")
    print(f"project_slug: {PROJECT_SLUG}")
    print(f"n8n_url: {N8N_URL or '(unset)'}")
    print(f"outline_url: {OUTLINE_URL or '(unset)'}")
    print(f"rag_search_api_key: {_redact_value(RAG_API_KEY)}")
    print(f"outline_api_key: {_redact_value(OUTLINE_API_KEY)}")


def run_cli(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"knowledge-hub-mcp {VERSION}")
        return 0
    if "--print-config" in argv or "-print-config" in argv:
        _print_config()
        return 0
    main()
    return 0


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
