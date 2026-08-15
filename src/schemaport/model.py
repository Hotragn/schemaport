"""Value types shared by the engine, the analyzers, and the reporters.

Everything here is immutable and JSON-serialisable. A `Finding` carries enough
on its own for an agent to act without consulting a second table: what is wrong
(`rule_id`), where (`path`), what to change (`remediation`), and how much the
claim is worth (`confidence` plus `provenance`).
"""

from __future__ import annotations

import enum
import functools
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from schemaport.errors import ContractDataError


@functools.total_ordering
class Severity(enum.Enum):
    """How much a finding matters. Ordered; `--fail-on` compares against it."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self.value]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.rank < other.rank

    @classmethod
    def parse(cls, value: str) -> Severity:
        try:
            return cls(value)
        except ValueError as exc:
            allowed = ", ".join(s.value for s in cls)
            raise ContractDataError(
                f"unknown severity {value!r}; expected one of {allowed}"
            ) from exc


_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}

SEVERITY_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)


class Confidence(enum.Enum):
    """Strength of the evidence behind a contract claim.

    This is orthogonal to severity: a `DOCUMENTED` rule can be advisory and an
    `EXPERIMENTAL` one can flag something serious. See docs/contract-data.md.
    """

    DOCUMENTED = "documented"
    OBSERVED = "observed"
    EXPERIMENTAL = "experimental"

    @classmethod
    def parse(cls, value: str) -> Confidence:
        try:
            return cls(value)
        except ValueError as exc:
            allowed = ", ".join(c.value for c in cls)
            raise ContractDataError(
                f"unknown confidence level {value!r}; expected one of {allowed}"
            ) from exc


@dataclass(frozen=True)
class Artifact:
    """A published, machine-readable document pinned to an exact revision.

    A prose citation rots quietly: the page is edited, the claim it supported
    changes, and nothing in the dataset notices. An artifact citation does not.
    It names a document, the revision it was read at, and the literal strings
    the claim depends on — which makes the claim re-checkable by a script and
    makes provider drift a detectable event rather than a discovery.

    `tracks` is the moving ref the pin was taken from, so a verifier can fetch
    both and diff them. Nothing here is read during a check; see
    tools/verify_contract_data.py.
    """

    url: str
    revision: str
    tracks: str
    expect: tuple[str, ...]

    def url_at(self, revision: str) -> str:
        return self.url.format(revision=revision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "revision": self.revision,
            "tracks": self.tracks,
            "expect": list(self.expect),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, label: str) -> Artifact:
        missing = [f for f in ("url", "revision", "tracks", "expect") if not raw.get(f)]
        if missing:
            raise ContractDataError(
                f"{label}: artifact is missing required field(s): {', '.join(missing)}"
            )
        url = str(raw["url"])
        if "{revision}" not in url:
            raise ContractDataError(
                f"{label}: artifact url must contain '{{revision}}' so the pin and the "
                f"tracked ref can both be fetched"
            )
        expect = raw["expect"]
        if not isinstance(expect, (list, tuple)) or not expect:
            raise ContractDataError(
                f"{label}: artifact must list the literal strings the claim depends on "
                f"in 'expect', or there is nothing to re-check"
            )
        return cls(
            url=url,
            revision=str(raw["revision"]),
            tracks=str(raw["tracks"]),
            expect=tuple(str(item) for item in expect),
        )


# Evidence kinds, weakest to strongest for a claim of the same confidence.
# `published_artifact` outranks prose documentation because it is what the
# provider's own SDKs are generated from, and because it can be re-checked.
SOURCE_KINDS = ("inference", "provider_documentation", "published_artifact", "observation")


@dataclass(frozen=True)
class Provenance:
    """What a contract claim rests on.

    `evidence_date` is the date the evidence was recorded into the dataset from
    its source, not the date someone last edited the record.
    """

    source_kind: str
    reference: str
    evidence_date: str
    artifact: Artifact | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "reference": self.reference,
            "evidence_date": self.evidence_date,
            "artifact": self.artifact.to_dict() if self.artifact else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, rule_id: str) -> Provenance:
        missing = [f for f in ("source_kind", "reference", "evidence_date") if not raw.get(f)]
        if missing:
            raise ContractDataError(
                f"rule {rule_id!r} provenance is missing required field(s): {', '.join(missing)}"
            )
        source_kind = str(raw["source_kind"])
        artifact_raw = raw.get("artifact")
        artifact = (
            Artifact.from_dict(artifact_raw, label=f"rule {rule_id!r}")
            if isinstance(artifact_raw, Mapping)
            else None
        )
        if source_kind == "published_artifact" and artifact is None:
            raise ContractDataError(f"rule {rule_id!r} claims a published artifact but pins none")
        return cls(
            source_kind=source_kind,
            reference=str(raw["reference"]),
            evidence_date=str(raw["evidence_date"]),
            artifact=artifact,
        )


@dataclass(frozen=True)
class Scope:
    """The boundary of a contract claim.

    A record that cannot name its provider and models does not belong in the
    dataset; the loader enforces that rather than trusting the author.
    """

    provider: str
    models: tuple[str, ...]
    api_surface: str
    applies_to: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "models": list(self.models),
            "api_surface": self.api_surface,
            "applies_to": self.applies_to,
        }


@dataclass(frozen=True)
class Finding:
    """One non-conformance or risk, located in the request that was checked."""

    rule_id: str
    severity: Severity
    path: str
    message: str
    remediation: str
    confidence: Confidence
    provenance: Provenance
    scope: Scope
    dataset_version: str
    detail: str | None = None

    @property
    def sort_key(self) -> tuple[int, str, str]:
        # Most severe first, then stable by rule and location so two runs over
        # the same input produce byte-identical reports.
        return (-self.severity.rank, self.rule_id, self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "path": self.path,
            "message": self.message,
            "detail": self.detail,
            "remediation": self.remediation,
            "confidence": self.confidence.value,
            "provenance": self.provenance.to_dict(),
            "scope": self.scope.to_dict(),
            "dataset_version": self.dataset_version,
        }


@dataclass(frozen=True)
class Target:
    """The profile the request was checked against.

    `model_scope` is why this profile claims this model at all. It travels with
    the report so a consumer can tell a model the evidence names outright from
    one covered by a broader version range.
    """

    provider: str
    model: str
    profile_id: str
    request_shape: str
    model_scope: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "profile": self.profile_id,
            "request_shape": self.request_shape,
            "model_scope": self.model_scope,
        }


@dataclass(frozen=True)
class Report:
    """The result of one check: a target, a dataset version, and findings."""

    schemaport_version: str
    dataset_version: str
    target: Target
    findings: tuple[Finding, ...]
    request_source: str | None = None

    def counts(self) -> dict[str, int]:
        counts = {severity.value: 0 for severity in SEVERITY_ORDER}
        for finding in self.findings:
            counts[finding.severity.value] += 1
        return counts

    def has_at_or_above(self, threshold: Severity) -> bool:
        return any(finding.severity >= threshold for finding in self.findings)
