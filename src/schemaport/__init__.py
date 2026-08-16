"""Static request conformance for the agent-provider boundary.

Schemaport reads a fully rendered LLM request, resolves a bundled versioned
contract profile for the model you intend to send it to, and reports where the
request does not conform — with a stable rule ID, a JSON path, a severity, a
remediation, a confidence level, and provenance for the claim behind it.

Everything runs locally. No SDK, API key, telemetry, proxy, or network call.
The request is read, never sent and never modified.

    from schemaport import check_file

    report = check_file("request.json", "claude-sonnet-5")
    for finding in report.findings:
        print(finding.rule_id, finding.path, finding.remediation)
"""

from __future__ import annotations

__version__ = "0.1.1"

from schemaport.contracts import ContractDataset, Profile, Rule, load_dataset
from schemaport.engine import analyze, check, check_file
from schemaport.errors import (
    ContractDataError,
    SchemaportError,
    UnknownModelError,
    UsageError,
)
from schemaport.model import (
    Confidence,
    Finding,
    Provenance,
    Report,
    Scope,
    Severity,
    Target,
)

__all__ = [
    "Confidence",
    "ContractDataError",
    "ContractDataset",
    "Finding",
    "Profile",
    "Provenance",
    "Report",
    "Rule",
    "SchemaportError",
    "Scope",
    "Severity",
    "Target",
    "UnknownModelError",
    "UsageError",
    "__version__",
    "analyze",
    "check",
    "check_file",
    "load_dataset",
]
