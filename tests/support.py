"""Builders for synthetic profiles and rules.

Analyzer tests drive the engine through rules they construct here rather than
through the bundled dataset, so editing shipped contract data cannot silently
change what an analyzer test asserts. The dataset gets its own tests.
"""

from __future__ import annotations

from typing import Any

from schemaport.contracts import ModelScope, Profile, Rule
from schemaport.engine import analyze
from schemaport.model import Confidence, Finding, Provenance, Scope, Severity

PROVENANCE = Provenance(
    source_kind="provider_documentation",
    reference="synthetic record used by the test suite; not a shipped contract claim",
    evidence_date="2026-08-15",
)

SCOPE = Scope(
    provider="testing",
    models=("test-model",),
    api_surface="test surface",
    applies_to="the test model only",
)


def make_rule(check: str, **overrides: Any) -> Rule:
    fields: dict[str, Any] = {
        "rule_id": overrides.pop("rule_id", f"test.{check}"),
        "check": check,
        "severity": Severity.ERROR,
        "confidence": Confidence.DOCUMENTED,
        "message": "synthetic rule",
        "remediation": "synthetic remediation",
        "scope": SCOPE,
        "provenance": PROVENANCE,
        "params": {},
        "applies_to_kinds": (),
        "requires_strict": False,
    }
    fields.update(overrides)
    return Rule(**fields)


MODEL_SCOPE = ModelScope(
    basis="provider_documentation",
    reference="synthetic model scope used by the test suite; not a shipped claim",
    evidence_date="2026-08-15",
    verified=("test-model",),
)


def make_profile(shape: str, *rules: Rule) -> Profile:
    return Profile(
        profile_id="testing/profile",
        provider="testing",
        request_shape=shape,
        api_surface="test surface",
        models=("test-model",),
        model_scope=MODEL_SCOPE,
        summary="",
        coverage="",
        rules=rules,
    )


def run(request: dict[str, Any], shape: str, *rules: Rule) -> tuple[Finding, ...]:
    """Analyze `request` under a throwaway profile built from `rules`."""
    return analyze(request, make_profile(shape, *rules), dataset_version="test")


def openai_response_format(schema: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "hello"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": strict, "schema": schema},
        },
    }
