#!/usr/bin/env python3
"""Test contract claims against live endpoints, recording dated artifacts. Opt-in and billable.

The dataset ships `documented` records — transcribed from provider
documentation with a citation. Documentation states intent. An endpoint
enforces something, and the two are not always the same artifact. This is how a
claim moves from `documented` to `observed`: send a synthetic request that
isolates one construct, record what actually came back, and keep the artifact.

Like the verifier, this is deliberately not part of the package. `schemaport
check` stays offline and nothing importable touches the network.

WHAT IT COSTS

  Rejection probes are free. A request that fails validation is rejected before
  the model runs, so no tokens are generated and nothing is billed.

  Controls are not free. A control is the same request with the construct under
  test removed — it is expected to succeed, which means it generates tokens.
  Each one is capped at one output token, so the cost is a fraction of a cent,
  but it is not zero.

  Nothing is sent without --execute. The default prints the plan and exits.

WHY CONTROLS ARE NOT OPTIONAL

  A 400 on its own proves nothing: the request might have been rejected for a
  reason unrelated to the construct being tested. Only a matched pair isolates
  the cause — probe rejected, control accepted, the two differing in exactly
  one construct. A probe run without controls is recorded as `inconclusive`,
  and inconclusive artifacts do not justify promoting a record to `observed`.

WHAT IS RECORDED

  The synthetic request, the response status, and the provider's error type and
  message. Requests are built here and contain no user content. Credentials,
  auth headers, organisation identifiers, and account-linked request IDs are
  never written to an artifact.

Exit codes:
    0  every probe behaved as the record predicts
    1  at least one probe contradicts its record
    2  the run could not be completed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from schemaport.contracts import ContractDataset, Profile, load_dataset

TIMEOUT_SECONDS = 60
USER_AGENT = "schemaport-contract-prober"
ARTIFACT_ROOT = Path(__file__).resolve().parents[1] / "probes" / "artifacts"

EXIT_OK = 0
EXIT_CONTRADICTED = 1
EXIT_FAILED = 2

# Where each request shape is sent, and which environment variable carries the
# credential. The key is read at send time and never stored, logged, or written.
ENDPOINTS = {
    "openai.responses": ("https://api.openai.com/v1/responses", "OPENAI_API_KEY"),
    "openai.chat_completions": (
        "https://api.openai.com/v1/chat/completions",
        "OPENAI_API_KEY",
    ),
    "anthropic.messages": ("https://api.anthropic.com/v1/messages", "ANTHROPIC_API_KEY"),
}

# Anything matching these never reaches an artifact, whatever a provider echoes
# back in an error body.
_REDACT = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\borg-[A-Za-z0-9]{6,}\b"),
    re.compile(r"\b(?:req|request)_[A-Za-z0-9]{8,}\b", re.I),
    re.compile(r"Bearer\s+\S+", re.I),
]

CONFIRMED, REFUTED, INCONCLUSIVE = "confirmed", "refuted", "inconclusive"


@dataclass(frozen=True)
class Probe:
    """One rule's probe definition, resolved against the profile that carries it."""

    rule_id: str
    profile: Profile
    model: str
    expectation: str
    request: dict[str, Any]
    control: dict[str, Any] | None

    @property
    def endpoint(self) -> tuple[str, str]:
        return ENDPOINTS[self.profile.request_shape]

    @property
    def billable_calls(self) -> int:
        # Only the control is expected to reach the model.
        return 1 if self.control else 0


@dataclass
class Outcome:
    """What actually happened, ready to be written as an artifact."""

    probe: Probe
    probe_status: int | None = None
    probe_error: dict[str, Any] | None = None
    control_status: int | None = None
    control_error: dict[str, Any] | None = None
    control_ran: bool = False
    transport_error: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.transport_error or self.probe_status is None:
            return INCONCLUSIVE
        rejected = self.probe_status == 400
        if not rejected:
            # The record says this construct is refused. It was not.
            return REFUTED if self.probe_status < 300 else INCONCLUSIVE
        if not self.control_ran:
            return INCONCLUSIVE
        if self.control_status is not None and self.control_status < 300:
            return CONFIRMED
        # Both failed: the rejection is not attributable to the construct.
        return INCONCLUSIVE

    @property
    def summary(self) -> str:
        if self.transport_error:
            return f"transport failed: {self.transport_error}"
        parts = [f"probe HTTP {self.probe_status}"]
        if self.control_ran:
            parts.append(f"control HTTP {self.control_status}")
        else:
            parts.append("control not run")
        return ", ".join(parts)

    def to_artifact(self, tool_version: str) -> dict[str, Any]:
        return {
            "rule_id": self.probe.rule_id,
            "profile": self.probe.profile.profile_id,
            "model": self.probe.model,
            "api_surface": self.probe.profile.api_surface,
            "observed_at": date.today().isoformat(),
            "verdict": self.verdict,
            "expectation": self.probe.expectation,
            "probe": {
                "request": self.probe.request,
                "status": self.probe_status,
                "error": self.probe_error,
            },
            "control": {
                "request": self.probe.control,
                "status": self.control_status,
                "error": self.control_error,
                "ran": self.control_ran,
            },
            "notes": self.notes,
            "recorded_by": tool_version,
        }


def redact(value: Any) -> Any:
    """Strip anything credential- or account-shaped before it is written down."""
    if isinstance(value, str):
        for pattern in _REDACT:
            value = pattern.sub("[redacted]", value)
        return value
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if k.lower() in _SECRET_KEYS else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


_SECRET_KEYS = {"authorization", "x-api-key", "api-key", "api_key", "organization"}


def probes(dataset: ContractDataset, only: str | None = None) -> Iterator[Probe]:
    """Every probe the dataset defines, one per rule per model it applies to."""
    for profile in dataset.profiles:
        if profile.request_shape not in ENDPOINTS:
            continue
        for rule in profile.rules:
            definition = rule.probe
            if definition is None or (only and rule.rule_id != only):
                continue
            # A rule set spans surfaces; only probe the one this profile is for.
            pair = definition["requests"].get(profile.request_shape)
            if pair is None:
                continue
            request = pair["request"]
            # One model per rule is enough to establish the observation, and the
            # record's scope names which. Take the first the profile lists.
            model = profile.models[0]
            yield Probe(
                rule_id=rule.rule_id,
                profile=profile,
                model=model,
                expectation=str(definition["expectation"]),
                request={**request, "model": model},
                control={**pair["control"], "model": model},
            )


def send(url: str, body: dict[str, Any], key: str, shape: str) -> tuple[int, dict[str, Any]]:
    """POST one synthetic request. Returns the status and a redacted body."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if shape.startswith("anthropic"):
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["Authorization"] = f"Bearer {key}"

    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:4000]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, redact(parsed)


def run(probe: Probe, *, with_controls: bool) -> Outcome:
    url, env_var = probe.endpoint
    key = os.environ.get(env_var, "")
    outcome = Outcome(probe=probe)
    if not key:
        outcome.transport_error = f"{env_var} is not set"
        return outcome

    shape = probe.profile.request_shape
    try:
        outcome.probe_status, outcome.probe_error = send(url, probe.request, key, shape)
        if probe.control and with_controls:
            outcome.control_status, outcome.control_error = send(url, probe.control, key, shape)
            outcome.control_ran = True
        elif probe.control:
            outcome.notes.append(
                "Control not run, so the rejection is not attributable to the construct "
                "under test. Re-run with --with-controls before promoting this record."
            )
        else:
            outcome.notes.append("This probe defines no control and cannot isolate a cause.")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        outcome.transport_error = str(exc)
    return outcome


def plan(found: list[Probe], with_controls: bool) -> str:
    billable = sum(p.billable_calls for p in found) if with_controls else 0
    lines = [
        f"{len(found)} probe(s) across {len({p.profile.profile_id for p in found})} profile(s).",
        "",
        f"  free      {len(found)} rejection request(s) — refused before the model runs",
        f"  billable  {billable} control request(s) — capped at one output token each",
        "",
    ]
    for probe in found:
        control = "with control" if (probe.control and with_controls) else "NO CONTROL"
        lines.append(f"  {probe.rule_id:52s} {probe.model:26s} {control}")
    if not with_controls and any(p.control for p in found):
        lines += [
            "",
            "Controls are disabled. Every result will be recorded as inconclusive,",
            "because a rejection with no matched control does not isolate a cause.",
            "Pass --with-controls to make these observations usable.",
        ]
    return "\n".join(lines)


def promotion_block(outcome: Outcome, artifact_path: Path) -> str:
    """The provenance a confirmed observation earns. Printed, never auto-applied."""
    return json.dumps(
        {
            "confidence": "observed",
            "provenance": {
                "source_kind": "observation",
                "reference": (
                    f"Synthetic request isolating this construct was rejected with HTTP "
                    f"{outcome.probe_status} on {outcome.probe.model}; the matched control, "
                    f"differing only in that construct, was accepted. Artifact: "
                    f"{artifact_path.as_posix()}"
                ),
                "evidence_date": date.today().isoformat(),
            },
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_contract_data",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--execute", action="store_true", help="actually send requests")
    parser.add_argument(
        "--with-controls",
        action="store_true",
        help="also send the matched control requests, which are billable",
    )
    parser.add_argument("--rule", help="probe a single rule_id")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=ARTIFACT_ROOT,
        help="where to write artifacts",
    )
    args = parser.parse_args(argv)

    dataset = load_dataset()
    found = list(probes(dataset, only=args.rule))
    if not found:
        print("No probe definitions found in the dataset.", file=sys.stderr)
        return EXIT_FAILED

    print(plan(found, args.with_controls))

    if not args.execute:
        print("\nDry run. Nothing was sent. Pass --execute to run these.")
        return EXIT_OK

    needed = {p.endpoint[1] for p in found}
    missing = sorted(var for var in needed if not os.environ.get(var))
    if missing:
        print(f"\nCannot execute: {', '.join(missing)} not set.", file=sys.stderr)
        return EXIT_FAILED

    print("\nSending. Rejection requests are free; controls cost a fraction of a cent.\n")
    tool_version = f"probe_contract_data/{dataset.version}"
    contradicted = 0

    for probe in found:
        outcome = run(probe, with_controls=args.with_controls)
        path = args.artifacts / probe.profile.provider / probe.model / f"{probe.rule_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(outcome.to_artifact(tool_version), indent=2) + "\n", encoding="utf-8"
        )

        mark = {CONFIRMED: "ok", REFUTED: "CONTRADICTED", INCONCLUSIVE: "inconclusive"}
        print(f"  [{mark[outcome.verdict]:13s}] {probe.rule_id}")
        print(f"                  {outcome.summary}")
        if outcome.verdict == REFUTED:
            contradicted += 1
            print("                  The endpoint accepted what this record says it refuses.")
        if outcome.verdict == CONFIRMED:
            print(f"                  promote with:\n{_indent(promotion_block(outcome, path))}")

    print(f"\nArtifacts under {args.artifacts}")
    if contradicted:
        print(f"{contradicted} record(s) contradicted by the endpoint.", file=sys.stderr)
        return EXIT_CONTRADICTED
    return EXIT_OK


def _indent(text: str, prefix: str = " " * 18) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
