"""The prober, exercised without sending anything.

Everything worth testing here is decided before a socket is opened: which
probes exist, what gets sent, what a result means, and what never reaches an
artifact. The network call itself is one `urlopen`; the judgement around it is
the part that can be wrong.

The prober lives in tools/ rather than in the package, so it is imported by
path. That separation is the point — nothing importable from `schemaport`
touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from schemaport import load_dataset
from schemaport.errors import ContractDataError

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load_prober():
    spec = importlib.util.spec_from_file_location("prober", TOOLS / "probe_contract_data.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because dataclasses resolves a class's
    # annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prober = _load_prober()


@pytest.fixture(scope="module")
def found():
    return list(prober.probes(load_dataset()))


class TestDiscovery:
    def test_the_dataset_defines_probes(self, found) -> None:
        assert found, "no probe definitions found; the harness has nothing to run"

    def test_every_probe_targets_a_known_endpoint(self, found) -> None:
        for probe in found:
            assert probe.profile.request_shape in prober.ENDPOINTS

    def test_the_model_comes_from_the_profile_not_the_definition(self, found) -> None:
        """A pinned model would silently probe a target the record does not cover."""
        for probe in found:
            assert probe.request["model"] in probe.profile.models
            assert probe.control["model"] == probe.request["model"]

    def test_a_rule_filter_narrows_the_run(self) -> None:
        one = list(prober.probes(load_dataset(), only="tool.name-invalid"))
        assert one
        assert {p.rule_id for p in one} == {"tool.name-invalid"}

    def test_probe_and_control_differ(self, found) -> None:
        for probe in found:
            assert probe.request != probe.control, (
                f"{probe.rule_id}: control is identical to the probe, so it isolates nothing"
            )

    def test_controls_cap_their_output(self, found) -> None:
        """A control reaches the model. It should generate as little as possible."""
        for probe in found:
            capped = probe.control.get("max_tokens") or probe.control.get("max_output_tokens")
            assert capped is not None and capped <= 16, (
                f"{probe.rule_id}: control does not cap output tokens"
            )

    def test_only_controls_are_counted_billable(self, found) -> None:
        for probe in found:
            assert probe.billable_calls == 1


class TestVerdicts:
    """A rejection only means something next to a control that succeeded."""

    def _outcome(self, **kw: Any):
        first = next(iter(prober.probes(load_dataset())))
        return prober.Outcome(probe=first, **kw)

    def test_rejected_probe_with_accepted_control_confirms(self) -> None:
        o = self._outcome(probe_status=400, control_status=200, control_ran=True)
        assert o.verdict == prober.CONFIRMED

    def test_rejected_probe_without_a_control_is_inconclusive(self) -> None:
        o = self._outcome(probe_status=400, control_ran=False)
        assert o.verdict == prober.INCONCLUSIVE

    def test_both_rejected_is_inconclusive(self) -> None:
        """If the control fails too, the rejection is not about the construct."""
        o = self._outcome(probe_status=400, control_status=400, control_ran=True)
        assert o.verdict == prober.INCONCLUSIVE

    def test_accepted_probe_refutes_the_record(self) -> None:
        o = self._outcome(probe_status=200, control_status=200, control_ran=True)
        assert o.verdict == prober.REFUTED

    def test_transport_failure_never_counts_as_evidence(self) -> None:
        o = self._outcome(transport_error="connection reset")
        assert o.verdict == prober.INCONCLUSIVE

    def test_a_server_error_is_not_a_refutation(self) -> None:
        o = self._outcome(probe_status=503, control_ran=True, control_status=503)
        assert o.verdict == prober.INCONCLUSIVE


class TestRedaction:
    @pytest.mark.parametrize(
        "raw",
        [
            "your key sk-abcd1234efgh5678 is invalid",
            "Bearer sk-live-0000111122223333",
            "org-A1B2C3D4E5 exceeded quota",
            "request req_9f8e7d6c5b4a3210 failed",
        ],
    )
    def test_credential_shapes_never_survive(self, raw: str) -> None:
        out = prober.redact(raw)
        assert "[redacted]" in out
        for leak in ("sk-abcd1234efgh5678", "sk-live-0000111122223333", "org-A1B2C3D4E5"):
            assert leak not in out

    def test_secret_keys_are_dropped_by_name(self) -> None:
        out = prober.redact({"Authorization": "Bearer abc", "x-api-key": "xyz", "type": "error"})
        assert out["Authorization"] == "[redacted]"
        assert out["x-api-key"] == "[redacted]"
        assert out["type"] == "error"

    def test_redaction_reaches_into_nested_bodies(self) -> None:
        out = prober.redact({"error": {"message": "bad key sk-deadbeefdeadbeef", "code": 400}})
        assert "sk-deadbeef" not in json.dumps(out)


class TestArtifacts:
    def test_an_artifact_carries_scope_and_date(self, found) -> None:
        outcome = prober.Outcome(
            probe=found[0], probe_status=400, control_status=200, control_ran=True
        )
        art = outcome.to_artifact("probe/test")
        for field in ("rule_id", "model", "api_surface", "observed_at", "verdict", "expectation"):
            assert art[field], f"artifact is missing {field}"
        assert art["verdict"] == prober.CONFIRMED

    def test_an_artifact_contains_no_credentials(self, found) -> None:
        outcome = prober.Outcome(
            probe=found[0],
            probe_status=400,
            probe_error=prober.redact({"error": {"message": "key sk-abcdefgh12345678 bad"}}),
            control_ran=True,
            control_status=200,
        )
        blob = json.dumps(outcome.to_artifact("probe/test"))
        assert "sk-abcdefgh12345678" not in blob

    def test_promotion_is_offered_only_with_evidence(self, found) -> None:
        outcome = prober.Outcome(
            probe=found[0], probe_status=400, control_status=200, control_ran=True
        )
        block = json.loads(prober.promotion_block(outcome, Path("probes/x.json")))
        assert block["confidence"] == "observed"
        assert block["provenance"]["source_kind"] == "observation"
        assert "control" in block["provenance"]["reference"]


class TestSafetyGates:
    def test_the_default_run_sends_nothing(self, capsys) -> None:
        assert prober.main([]) == prober.EXIT_OK
        out = capsys.readouterr().out
        assert "Dry run. Nothing was sent." in out

    def test_the_plan_separates_free_from_billable(self, capsys) -> None:
        prober.main(["--with-controls"])
        out = capsys.readouterr().out
        assert "free" in out and "billable" in out

    def test_without_controls_the_plan_says_results_are_unusable(self, capsys) -> None:
        prober.main([])
        assert "recorded as inconclusive" in capsys.readouterr().out

    def test_execute_without_a_key_refuses_rather_than_sending(
        self, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert prober.main(["--execute"]) == prober.EXIT_FAILED
        assert "not set" in capsys.readouterr().err


class TestDefinitionValidation:
    """Malformed probes fail at load, not against a live endpoint."""

    def _rule(self, probe: Any) -> None:
        from schemaport.contracts import _parse_probe

        _parse_probe(probe, rule_id="test.rule", source="test.json")

    def _shaped(self, **pair: Any) -> dict[str, Any]:
        return {"expectation": "x", "requests": {"anthropic.messages": pair}}

    def test_a_probe_without_a_control_is_rejected(self) -> None:
        with pytest.raises(ContractDataError, match="no control"):
            self._rule(self._shaped(request={"a": 1}))

    def test_a_probe_that_pins_a_model_is_rejected(self) -> None:
        with pytest.raises(ContractDataError, match="pins a model"):
            self._rule(self._shaped(request={"model": "gpt-5.6-sol"}, control={"a": 1}))

    def test_a_control_identical_to_the_request_is_rejected(self) -> None:
        """It would isolate nothing, and every result would be a false confirm."""
        with pytest.raises(ContractDataError, match="isolates nothing"):
            self._rule(self._shaped(request={"a": 1}, control={"a": 1}))

    def test_a_probe_with_no_shaped_bodies_is_rejected(self) -> None:
        with pytest.raises(ContractDataError, match="keyed by request shape"):
            self._rule({"expectation": "x", "request": {"a": 1}, "control": {"a": 2}})

    def test_a_well_formed_probe_loads(self) -> None:
        self._rule(self._shaped(request={"a": 1}, control={"a": 2}))

    def test_no_probe_is_fine(self) -> None:
        self._rule(None)
