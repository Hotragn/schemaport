"""Shared fixtures.

`pythonpath = ["src"]` in pyproject.toml means the tests run against the source
tree without an install step, which keeps `pytest` runnable in an environment
where nothing can be installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples" / "requests"


@pytest.fixture
def write_request(tmp_path: Path):
    """Write a request body to a temporary file and return its path."""

    def _write(body: Any, name: str = "request.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(body, indent=2), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def anthropic_request() -> dict[str, Any]:
    return {
        "model": "claude-sonnet-5",
        "max_tokens": 1024,
        "tools": [
            {
                "name": "search_orders",
                "description": "Look up orders for a customer.",
                "input_schema": {
                    "type": "object",
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"],
                },
            }
        ],
        "system": [{"type": "text", "text": "You are a support agent."}],
        "messages": [{"role": "user", "content": "where is my order"}],
    }


@pytest.fixture
def openai_request() -> dict[str, Any]:
    """A clean Chat Completions request. Shape markers keep detection unambiguous."""
    return {
        "model": "gpt-5.6-sol",
        "messages": [{"role": "user", "content": "extract the order"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "order",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"],
                },
            },
        },
    }
