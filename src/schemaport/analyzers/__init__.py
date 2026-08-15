"""Analyzer registry.

An analyzer is a pure function from `(context, rule)` to findings. It owns
traversal; the rule owns the claim — the threshold, the keyword list, the
message, the remediation, and the provenance. That split is what lets a
contract change ship as a dataset edit instead of a code change.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from schemaport.contracts import Profile, Rule
from schemaport.errors import ContractDataError
from schemaport.model import Finding
from schemaport.shapes import RequestView


@dataclass(frozen=True)
class AnalysisContext:
    """Everything an analyzer is allowed to see."""

    request: Mapping[str, Any]
    view: RequestView
    profile: Profile
    dataset_version: str

    def finding(self, rule: Rule, path: str, detail: str | None = None) -> Finding:
        """Build a finding that carries the rule's claim and this location."""
        return Finding(
            rule_id=rule.rule_id,
            severity=rule.severity,
            path=path,
            message=rule.message,
            remediation=rule.remediation,
            confidence=rule.confidence,
            provenance=rule.provenance,
            scope=rule.scope,
            dataset_version=self.dataset_version,
            detail=detail,
        )


Analyzer = Callable[[AnalysisContext, Rule], Iterator[Finding]]

_REGISTRY: dict[str, Analyzer] = {}


def register(name: str) -> Callable[[Analyzer], Analyzer]:
    """Register an analyzer under the name rules reference in `check`."""

    def decorate(fn: Analyzer) -> Analyzer:
        if name in _REGISTRY:  # pragma: no cover - import-time guard
            raise RuntimeError(f"analyzer {name!r} is already registered")
        _REGISTRY[name] = fn
        return fn

    return decorate


def get(name: str) -> Analyzer:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise ContractDataError(
            f"rule references unknown check {name!r}; known checks: {', '.join(sorted(_REGISTRY))}"
        ) from exc


def known_checks() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# Importing the modules is what populates the registry. Kept at the bottom so
# the decorator exists by the time they run.
from schemaport.analyzers import cache_safety, structured_output  # noqa: E402,F401
