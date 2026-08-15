"""The human-readable report.

No colour and no terminal detection: the output is the same whether it is on a
tty, in a CI log, or in a file. Findings read top to bottom as what, where,
why it matters, and what to change.
"""

from __future__ import annotations

from schemaport.model import Report, Severity

_LABEL_WIDTH = max(len(severity.value) for severity in Severity)


def render_text(report: Report) -> str:
    lines = [
        f"schemaport {report.schemaport_version} — contract dataset {report.dataset_version}",
        f"target:  {report.target.profile_id} (model {report.target.model})",
    ]
    if report.request_source:
        lines.append(f"request: {report.request_source}")
    lines.append("")

    if not report.findings:
        lines.append("No findings. The request conforms to this profile as far as the")
        lines.append("bundled contract data goes; that is not a guarantee of acceptance.")
        lines.append("")
        return "\n".join(lines)

    for finding in report.findings:
        label = finding.severity.value.ljust(_LABEL_WIDTH)
        lines.append(f"{label}  {finding.rule_id}")
        lines.append(f"{' ' * _LABEL_WIDTH}  at {finding.path}")
        lines.append(f"{' ' * _LABEL_WIDTH}  {finding.message}")
        if finding.detail:
            lines.append(f"{' ' * _LABEL_WIDTH}  found: {finding.detail}")
        lines.append(f"{' ' * _LABEL_WIDTH}  fix:   {finding.remediation}")
        lines.append(
            f"{' ' * _LABEL_WIDTH}  basis: {finding.confidence.value}"
            f" — {finding.provenance.source_kind},"
            f" recorded {finding.provenance.evidence_date}"
        )
        lines.append("")

    counts = report.counts()
    lines.append(f"{counts['error']} error, {counts['warning']} warning, {counts['info']} info")
    lines.append("")
    return "\n".join(lines)
