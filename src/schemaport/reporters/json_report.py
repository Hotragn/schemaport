"""The JSON report.

This is the machine-facing contract: an agent locates `path`, reads `rule_id`,
applies `remediation`, and checks again. Field names here are treated as public
interface — see the compatibility note in the README.
"""

from __future__ import annotations

import json
from typing import Any

from schemaport.model import Report


def build(report: Report) -> dict[str, Any]:
    """The report as plain data, ready to serialise."""
    return {
        "schemaport_version": report.schemaport_version,
        "contract_dataset_version": report.dataset_version,
        "target": report.target.to_dict(),
        "request": {"source": report.request_source},
        "summary": report.counts(),
        "findings": [finding.to_dict() for finding in report.findings],
    }


def render_json(report: Report) -> str:
    return json.dumps(build(report), indent=2, ensure_ascii=False) + "\n"
