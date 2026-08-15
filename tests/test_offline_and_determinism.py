"""The three properties the agent integration depends on.

Deterministic, offline, side-effect-free is not a slogan here — an agent repair
loop breaks if any one of them is false. Determinism is what lets a caller
conclude that a finding disappeared because the repair worked; offline is what
lets the loop run in a sandbox with no egress; side-effect-freedom is what makes
running it fifty times cost nothing.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from schemaport import check, check_file, load_dataset
from schemaport.engine import analyze


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any attempt to open a socket fail loudly."""

    def refuse(*args: Any, **kwargs: Any):
        raise AssertionError("schemaport attempted a network operation")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.mark.usefixtures("no_network")
def test_a_check_runs_with_sockets_disabled(write_request, anthropic_request) -> None:
    path = write_request(anthropic_request)
    report = check_file(path, "claude-sonnet-5")
    assert report.target.model == "claude-sonnet-5"


@pytest.mark.usefixtures("no_network")
def test_loading_the_dataset_needs_no_network() -> None:
    assert load_dataset().profiles


def test_the_same_input_produces_the_same_findings(anthropic_request) -> None:
    first = check(anthropic_request, "claude-sonnet-5")
    second = check(anthropic_request, "claude-sonnet-5")
    assert [f.to_dict() for f in first.findings] == [f.to_dict() for f in second.findings]


def test_findings_are_ordered_most_severe_first(write_request) -> None:
    request = {
        "model": "claude-sonnet-5",
        "tools": [
            {
                "name": "search orders",
                "input_schema": {"type": "object", "properties": {}},
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "system": [{"type": "text", "text": "Started 2026-08-15T09:14:02Z."}],
        "messages": [{"role": "user", "content": "hello"}],
    }
    report = check(request, "claude-sonnet-5")
    ranks = [finding.severity.rank for finding in report.findings]
    assert ranks == sorted(ranks, reverse=True)


def test_ties_are_broken_by_rule_then_path(anthropic_request) -> None:
    """Two runs must agree on the order of equally severe findings."""
    anthropic_request["tools"].append(
        {
            "name": "another tool",
            "input_schema": {"type": "object", "properties": {}},
        }
    )
    anthropic_request["tools"][0]["name"] = "search orders"
    report = check(anthropic_request, "claude-sonnet-5")
    keys = [(f.rule_id, f.path) for f in report.findings if f.rule_id == "tool.name-invalid"]
    assert keys == sorted(keys)


def test_the_request_mapping_is_not_mutated(anthropic_request) -> None:
    import copy

    before = copy.deepcopy(anthropic_request)
    profile = load_dataset().resolve("claude-sonnet-5")
    analyze(anthropic_request, profile, dataset_version="test")
    assert anthropic_request == before


def test_a_repair_removes_exactly_the_finding_it_addressed(anthropic_request) -> None:
    """The loop in docs/agent-integration.md relies on this being true."""
    anthropic_request["tools"][0]["name"] = "search orders"
    before = check(anthropic_request, "claude-sonnet-5")
    assert "tool.name-invalid" in {f.rule_id for f in before.findings}

    anthropic_request["tools"][0]["name"] = "search_orders"
    after = check(anthropic_request, "claude-sonnet-5")
    assert "tool.name-invalid" not in {f.rule_id for f in after.findings}
    assert len(after.findings) == len(before.findings) - 1
