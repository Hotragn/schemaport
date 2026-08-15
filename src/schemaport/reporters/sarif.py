"""SARIF 2.1.0 output.

For code-scanning UIs that already know how to render findings. Schemaport has
no line numbers to offer — a finding points at a node in a JSON document, not a
source span — so the JSON path travels as a logical location and the request
file as the physical one.
"""

from __future__ import annotations

import json
from typing import Any

from schemaport.model import Report, Severity

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

_LEVELS = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.INFO: "note",
}


def build(report: Report) -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {}
    rule_order: list[str] = []
    results: list[dict[str, Any]] = []

    for finding in report.findings:
        if finding.rule_id not in rules:
            rule_order.append(finding.rule_id)
            rules[finding.rule_id] = {
                "id": finding.rule_id,
                "shortDescription": {"text": finding.message},
                "fullDescription": {"text": finding.message},
                "help": {"text": finding.remediation},
                "defaultConfiguration": {"level": _LEVELS[finding.severity]},
                "properties": {
                    "confidence": finding.confidence.value,
                    "provenance": finding.provenance.to_dict(),
                    "scope": finding.scope.to_dict(),
                    "contractDatasetVersion": finding.dataset_version,
                },
            }

        message = finding.message if not finding.detail else f"{finding.message} ({finding.detail})"
        result: dict[str, Any] = {
            "ruleId": finding.rule_id,
            "ruleIndex": rule_order.index(finding.rule_id),
            "level": _LEVELS[finding.severity],
            "message": {"text": f"{message} Remediation: {finding.remediation}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": report.request_source or "request.json"}
                    },
                    "logicalLocations": [{"fullyQualifiedName": finding.path, "kind": "member"}],
                }
            ],
            "properties": {
                "jsonPath": finding.path,
                "confidence": finding.confidence.value,
            },
        }
        results.append(result)

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "schemaport",
                        "version": report.schemaport_version,
                        "informationUri": "https://pypi.org/project/schemaport/",
                        "rules": [rules[rule_id] for rule_id in rule_order],
                    }
                },
                "properties": {
                    "contractDatasetVersion": report.dataset_version,
                    "profile": report.target.profile_id,
                    "model": report.target.model,
                },
                "results": results,
            }
        ],
    }


def render_sarif(report: Report) -> str:
    return json.dumps(build(report), indent=2, ensure_ascii=False) + "\n"
