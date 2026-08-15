"""Cache-safety heuristics.

These rules are estimates and the dataset says so, but the estimate still has
to be the one it claims to be: the prefix is the span through the last
breakpoint, volatile content after that span is not a finding, and nothing here
asserts anything about an actual cache hit.
"""

from __future__ import annotations

from typing import Any

from tests.support import make_rule, run

TIMESTAMP_PATTERNS = [
    {"name": "an ISO 8601 timestamp", "regex": r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?"},
    {
        "name": "a UUID",
        "regex": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    },
]


def anthropic(system_blocks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": "test-model",
        "system": system_blocks,
        "messages": [{"role": "user", "content": "hello"}],
    }
    request.update(extra)
    return request


def cached(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def plain(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class TestVolatilePrefix:
    def test_volatile_content_before_the_breakpoint_is_reported(self) -> None:
        rule = make_rule("cache.volatile_prefix", params={"patterns": TIMESTAMP_PATTERNS})
        request = anthropic([cached("Session started at 2026-08-15T09:14:02Z.")])
        findings = run(request, "anthropic.messages", rule)
        assert [f.path for f in findings] == ["$.system[0]"]
        assert "an ISO 8601 timestamp" in (findings[0].detail or "")

    def test_volatile_content_after_the_breakpoint_is_not_a_finding(self) -> None:
        """Content past the last marker is outside the span that must stay stable."""
        rule = make_rule("cache.volatile_prefix", params={"patterns": TIMESTAMP_PATTERNS})
        request = anthropic([cached("Stable instructions.")])
        request["messages"] = [
            {"role": "user", "content": "as of 2026-08-15T09:14:02Z, where is my order"}
        ]
        assert not run(request, "anthropic.messages", rule)

    def test_no_breakpoint_means_no_prefix_to_protect(self) -> None:
        rule = make_rule("cache.volatile_prefix", params={"patterns": TIMESTAMP_PATTERNS})
        request = anthropic([plain("Session 2026-08-15T09:14:02Z.")])
        assert not run(request, "anthropic.messages", rule)

    def test_one_finding_per_segment_even_with_several_matches(self) -> None:
        rule = make_rule("cache.volatile_prefix", params={"patterns": TIMESTAMP_PATTERNS})
        text = "Started 2026-08-15T09:14:02Z, session 3f2b8c14-59a7-4d61-9f0e-72c5ab3d8e10."
        findings = run(anthropic([cached(text)]), "anthropic.messages", rule)
        assert len(findings) == 1

    def test_a_tool_definition_in_the_prefix_is_scanned(self) -> None:
        rule = make_rule("cache.volatile_prefix", params={"patterns": TIMESTAMP_PATTERNS})
        request = anthropic(
            [plain("Instructions.")],
            tools=[
                {
                    "name": "lookup",
                    "description": "Snapshot taken 2026-08-15T09:14:02Z.",
                    "input_schema": {"type": "object"},
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        )
        findings = run(request, "anthropic.messages", rule)
        assert [f.path for f in findings] == ["$.tools[0]"]

    def test_patterns_missing_from_the_rule_disable_it(self) -> None:
        rule = make_rule("cache.volatile_prefix", params={})
        request = anthropic([cached("Session started at 2026-08-15T09:14:02Z.")])
        assert not run(request, "anthropic.messages", rule)

    def test_an_automatic_surface_scans_a_leading_window(self) -> None:
        rule = make_rule(
            "cache.volatile_prefix",
            params={"patterns": TIMESTAMP_PATTERNS, "prefix_scan_chars": 40},
        )
        request = {
            "messages": [
                {"role": "system", "content": "x" * 60},
                {"role": "user", "content": "at 2026-08-15T09:14:02Z"},
            ]
        }
        # The first message already exhausts the window, so the second is out of scope.
        assert not run(request, "openai.chat_completions", rule)

    def test_an_automatic_surface_reports_inside_the_window(self) -> None:
        rule = make_rule(
            "cache.volatile_prefix",
            params={"patterns": TIMESTAMP_PATTERNS, "prefix_scan_chars": 4000},
        )
        request = {"messages": [{"role": "system", "content": "at 2026-08-15T09:14:02Z"}]}
        findings = run(request, "openai.chat_completions", rule)
        assert "in the request prefix" in (findings[0].detail or "")


class TestBreakpointLimit:
    def test_extra_breakpoints_are_each_reported(self) -> None:
        rule = make_rule("cache.breakpoint_limit", params={"limit": 2})
        request = anthropic([cached("a"), cached("b"), cached("c"), cached("d")])
        findings = run(request, "anthropic.messages", rule)
        assert [f.path for f in findings] == ["$.system[2]", "$.system[3]"]

    def test_at_the_limit_is_not_a_finding(self) -> None:
        rule = make_rule("cache.breakpoint_limit", params={"limit": 2})
        request = anthropic([cached("a"), cached("b")])
        assert not run(request, "anthropic.messages", rule)

    def test_a_surface_without_explicit_markers_is_skipped(self) -> None:
        rule = make_rule("cache.breakpoint_limit", params={"limit": 0})
        request = {"messages": [{"role": "user", "content": "hello"}]}
        assert not run(request, "openai.chat_completions", rule)


class TestPrefixBelowMinimum:
    def test_a_short_marked_prefix_is_reported(self) -> None:
        rule = make_rule("cache.prefix_below_minimum", params={"min_prefix_chars": 100})
        findings = run(anthropic([cached("short")]), "anthropic.messages", rule)
        assert [f.path for f in findings] == ["$.system[0]"]
        assert "5 characters" in (findings[0].detail or "")

    def test_a_long_enough_prefix_is_silent(self) -> None:
        rule = make_rule("cache.prefix_below_minimum", params={"min_prefix_chars": 100})
        assert not run(anthropic([cached("x" * 200)]), "anthropic.messages", rule)

    def test_an_unmarked_request_is_left_to_the_no_breakpoint_rule(self) -> None:
        rule = make_rule("cache.prefix_below_minimum", params={"min_prefix_chars": 100})
        assert not run(anthropic([plain("short")]), "anthropic.messages", rule)


class TestNoBreakpoint:
    def test_a_large_unmarked_prefix_is_reported_at_its_last_block(self) -> None:
        rule = make_rule("cache.no_breakpoint", params={"min_prefix_chars": 100})
        request = anthropic([plain("x" * 60), plain("y" * 60)])
        findings = run(request, "anthropic.messages", rule)
        assert [f.path for f in findings] == ["$.system[1]"]

    def test_a_small_prefix_is_not_worth_a_marker(self) -> None:
        rule = make_rule("cache.no_breakpoint", params={"min_prefix_chars": 1000})
        assert not run(anthropic([plain("short")]), "anthropic.messages", rule)

    def test_an_existing_marker_silences_the_rule(self) -> None:
        rule = make_rule("cache.no_breakpoint", params={"min_prefix_chars": 10})
        assert not run(anthropic([cached("x" * 100)]), "anthropic.messages", rule)

    def test_message_content_does_not_count_toward_the_stable_prefix(self) -> None:
        rule = make_rule("cache.no_breakpoint", params={"min_prefix_chars": 100})
        request = anthropic([plain("short")])
        request["messages"] = [{"role": "user", "content": "x" * 500}]
        assert not run(request, "anthropic.messages", rule)
