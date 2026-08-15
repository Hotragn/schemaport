"""The CLI contract: exit codes, formats, and the errors a caller has to tell apart.

Exit codes are public interface. A CI gate and an agent loop both branch on
them, so "found a problem in your request" (1) and "could not check your
request" (2) must not collapse into each other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from schemaport.cli import EXIT_FINDINGS, EXIT_OK, EXIT_USAGE, main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "requests"


class TestExitCodes:
    def test_clean_request_exits_zero(self, write_request, openai_request, capsys) -> None:
        path = write_request(openai_request)
        assert main(["check", str(path), "--model", "gpt-5.6-sol"]) == EXIT_OK
        assert "No findings" in capsys.readouterr().out

    def test_findings_at_the_threshold_exit_one(self, write_request, openai_request) -> None:
        openai_request["response_format"]["json_schema"]["schema"]["additionalProperties"] = True
        path = write_request(openai_request)
        code = main(["check", str(path), "--model", "gpt-5.6-sol"])
        assert code == EXIT_FINDINGS

    def test_findings_below_the_threshold_exit_zero(self, write_request, openai_request) -> None:
        schema = openai_request["response_format"]["json_schema"]["schema"]
        schema["properties"]["customer_id"]["pattern"] = "^C[0-9]+$"
        path = write_request(openai_request)
        # The only finding is info; the default threshold is error.
        assert main(["check", str(path), "--model", "gpt-5.6-sol"]) == EXIT_OK

    def test_fail_on_lowers_the_threshold(self, write_request, openai_request) -> None:
        schema = openai_request["response_format"]["json_schema"]["schema"]
        schema["properties"]["customer_id"]["pattern"] = "^C[0-9]+$"
        path = write_request(openai_request)
        code = main(["check", str(path), "--model", "gpt-5.6-sol", "--fail-on", "info"])
        assert code == EXIT_FINDINGS


class TestUsageErrors:
    def test_unknown_model_exits_two_and_points_at_profiles(
        self, write_request, openai_request, capsys
    ) -> None:
        path = write_request(openai_request)
        assert main(["check", str(path), "--model", "gpt-4o"]) == EXIT_USAGE
        err = capsys.readouterr().err
        assert "no bundled contract profile covers model 'gpt-4o'" in err
        assert "schemaport profiles" in err

    def test_missing_file_exits_two(self, tmp_path: Path, capsys) -> None:
        missing = tmp_path / "nope.json"
        assert main(["check", str(missing), "--model", "claude-sonnet-5"]) == EXIT_USAGE
        assert "request file not found" in capsys.readouterr().err

    def test_invalid_json_exits_two_with_a_position(self, tmp_path: Path, capsys) -> None:
        path = tmp_path / "broken.json"
        path.write_text('{"model": ', encoding="utf-8")
        assert main(["check", str(path), "--model", "claude-sonnet-5"]) == EXIT_USAGE
        err = capsys.readouterr().err
        assert "not valid JSON" in err
        assert "line 1" in err

    def test_a_byte_order_mark_is_tolerated(self, tmp_path: Path, openai_request) -> None:
        """Windows toolchains emit one; it is not the caller's problem."""
        path = tmp_path / "bom.json"
        path.write_text(json.dumps(openai_request), encoding="utf-8-sig")
        assert main(["check", str(path), "--model", "gpt-5.6-sol"]) == EXIT_OK

    def test_a_json_array_is_refused(self, write_request, capsys) -> None:
        path = write_request([{"model": "claude-sonnet-5"}])
        assert main(["check", str(path), "--model", "claude-sonnet-5"]) == EXIT_USAGE
        assert "must contain a JSON object" in capsys.readouterr().err

    def test_no_command_prints_help_and_exits_two(self, capsys) -> None:
        assert main([]) == EXIT_USAGE
        assert "usage:" in capsys.readouterr().out


class TestFormats:
    def test_json_report_carries_the_documented_fields(
        self, write_request, openai_request, capsys
    ) -> None:
        openai_request["response_format"]["json_schema"]["schema"]["additionalProperties"] = True
        path = write_request(openai_request)
        main(["check", str(path), "--model", "gpt-5.6-sol", "--format", "json"])
        report = json.loads(capsys.readouterr().out)

        assert report["schemaport_version"]
        assert report["contract_dataset_version"]
        assert report["target"]["profile"] == "openai/chat-completions"
        assert set(report["summary"]) == {"error", "warning", "info"}

        finding = report["findings"][0]
        for field in ("rule_id", "path", "severity", "remediation", "confidence", "provenance"):
            assert field in finding, f"agents depend on {field!r} being present"

    def test_sarif_report_is_well_formed(self, write_request, openai_request, capsys) -> None:
        openai_request["response_format"]["json_schema"]["schema"]["additionalProperties"] = True
        path = write_request(openai_request)
        main(["check", str(path), "--model", "gpt-5.6-sol", "--format", "sarif"])
        sarif = json.loads(capsys.readouterr().out)

        assert sarif["version"] == "2.1.0"
        run = sarif["runs"][0]
        assert run["tool"]["driver"]["name"] == "schemaport"
        result = run["results"][0]
        assert result["level"] in {"error", "warning", "note"}
        assert result["ruleIndex"] < len(run["tool"]["driver"]["rules"])
        assert result["properties"]["jsonPath"].startswith("$")

    def test_machine_formats_write_the_summary_to_stderr(
        self, write_request, openai_request, capsys
    ) -> None:
        """stdout stays parseable when a caller pipes it into jq."""
        openai_request["response_format"]["json_schema"]["schema"]["additionalProperties"] = True
        path = write_request(openai_request)
        main(["check", str(path), "--model", "gpt-5.6-sol", "--format", "json"])
        captured = capsys.readouterr()
        json.loads(captured.out)
        assert "failing at or above" in captured.err


class TestProfilesCommand:
    def test_lists_models_and_rule_counts(self, capsys) -> None:
        assert main(["profiles"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "anthropic/messages-1024-token-cache" in out
        assert "claude-sonnet-5" in out
        assert "does not" in out  # the note about unlisted models


class TestVersion:
    def test_version_flag(self, capsys) -> None:
        with pytest.raises(SystemExit) as raised:
            main(["--version"])
        assert raised.value.code == 0
        assert "schemaport" in capsys.readouterr().out


class TestSideEffects:
    def test_the_request_file_is_not_modified(self, write_request, openai_request) -> None:
        openai_request["response_format"]["json_schema"]["schema"]["additionalProperties"] = True
        path = write_request(openai_request)
        before = path.read_bytes()
        main(["check", str(path), "--model", "gpt-5.6-sol", "--format", "json"])
        assert path.read_bytes() == before

    def test_repeated_runs_produce_identical_output(
        self, write_request, openai_request, capsys
    ) -> None:
        openai_request["response_format"]["json_schema"]["schema"]["additionalProperties"] = True
        path = write_request(openai_request)
        argv = ["check", str(path), "--model", "gpt-5.6-sol", "--format", "json"]

        main(argv)
        first = capsys.readouterr().out
        main(argv)
        second = capsys.readouterr().out
        assert first == second


class TestShippedExamples:
    """The example requests in the repository are checked, not just written."""

    @pytest.mark.parametrize(
        ("filename", "model"),
        [
            ("openai-structured-output.json", "gpt-5.6-sol"),
            ("anthropic-cached-agent-turn.json", "claude-sonnet-5"),
        ],
    )
    def test_example_produces_the_findings_the_docs_describe(
        self, filename: str, model: str, capsys
    ) -> None:
        path = EXAMPLES / filename
        code = main(["check", str(path), "--model", model, "--format", "json"])
        assert code == EXIT_FINDINGS
        report: dict[str, Any] = json.loads(capsys.readouterr().out)
        assert report["findings"], "an example that produces nothing teaches nothing"

    def test_openai_example_shows_a_repairable_structural_defect(self, capsys) -> None:
        path = EXAMPLES / "openai-structured-output.json"
        main(["check", str(path), "--model", "gpt-5.6-sol", "--format", "json"])
        report = json.loads(capsys.readouterr().out)
        rule_ids = {finding["rule_id"] for finding in report["findings"]}
        assert "structured-output.additional-properties-not-false" in rule_ids
        assert "structured-output.property-not-required" in rule_ids

    def test_anthropic_example_shows_a_cache_prefix_defect(self, capsys) -> None:
        path = EXAMPLES / "anthropic-cached-agent-turn.json"
        main(["check", str(path), "--model", "claude-sonnet-5", "--format", "json"])
        report = json.loads(capsys.readouterr().out)
        rule_ids = {finding["rule_id"] for finding in report["findings"]}
        assert "cache.volatile-prefix-content" in rule_ids
        assert "tool.name-invalid" in rule_ids
