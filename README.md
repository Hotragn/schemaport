# Schemaport

**Static request conformance for the agent–provider boundary.**

Schemaport checks a fully rendered LLM request before your application or agent
sends it. It reads a request JSON file and a target model, resolves a bundled,
versioned provider-contract profile, and emits deterministic findings with
stable rule IDs, JSON paths, severity, remediation, confidence, and
provenance — in human-readable text, JSON, or SARIF.

It runs locally: no SDK, API key, telemetry, proxy, or network call. Stable exit
codes make it usable in CI, air-gapped evaluation harnesses, and agent repair
loops. Apache-2.0 licensed.

## Install

```bash
pip install schemaport
```

Requires Python 3.10 or newer. Schemaport has no runtime dependencies, does not
depend on a provider SDK, and never asks for credentials.

## Quick start

Point it at a request body you have already rendered, and the model you intend
to send it to:

```bash
schemaport check request.json --model claude-sonnet-5
```

The input is the request body itself — the JSON you would hand to the provider —
not a wrapper, log line, or SDK call. `--model` must be a model identifier the
bundled dataset carries a profile for; run `schemaport profiles` to see them.
It is required rather than inferred, because a profile's claims apply only to
the models it names.

For machine consumption, ask for JSON and set the severity at which the command
should fail:

```bash
schemaport check request.json --model claude-sonnet-5 --format json --fail-on warning
```

`--format` selects the report shape: `text` for humans, `json` for agents and
scripts, `sarif` for code-scanning tools that already understand SARIF.
`--fail-on` sets the lowest severity that exits non-zero, which is what makes
the command usable as a CI gate.

A model is often reachable on more than one API, and the request is shaped
differently on each. Schemaport reads the request to work out which surface it
was written for; when that is ambiguous it stops and asks for `--surface`
rather than checking against a contract you may not be using.

Schemaport reads the file and writes a report. It does not send the request,
contact the provider, or modify the input.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The check ran; nothing at or above `--fail-on` was found. |
| `1` | The check ran; something at or above `--fail-on` was found. |
| `2` | The invocation or its input could not be used — unreadable file, invalid JSON, unknown model. |

A caller needs 1 and 2 to stay distinct: "your request has a problem" and "I
could not check your request" call for different handling.

## The problem agents created

Agentic systems dynamically construct schemas, tool definitions, messages, and
cache boundaries at runtime. Many of these request bodies are never reviewed by
a human before they reach a provider.

A malformed tool schema or unsupported structured-output construct can fail late
in a long trajectory, wasting the context, tool work, and model calls that came
before it. Routing multiplies this risk: a request valid for one model family
may be rejected, transformed, or only partially supported by another.

Agent loops also create cache-risk patterns that ordinary prompt review misses.
Volatile values — timestamps, UUIDs, reordered JSON keys, changing tool
definitions — can alter an otherwise stable prefix between turns. Depending on
provider behavior and configuration, this can reduce cache reuse without
producing an obvious application error.

Schemaport makes those request-level risks visible before a provider call. It is
designed for environments where egress is unavailable or undesired, including
CI, sandboxes, and locked-down evaluation systems.

## Agent-native preflight

Schemaport produces structured, deterministic, machine-readable findings. An
agent can inspect its own pending request, identify the exact affected path,
apply a known remediation, and validate again before it sends anything.

```text
agent drafts request
        |
        v
schemaport check
        |
        +--> clean --> send to provider
        |
        +--> findings: rule ID + JSON path + remediation
                         |
                         v
                 agent repairs request
                         |
                         +--> schemaport check again
```

This makes Schemaport useful in two places:

- As a CI gate that prevents invalid request contracts from reaching deployment.
- As a preflight tool that an agent, orchestrator, or future MCP adapter can
  call before dispatching a provider request.

Schemaport is not a runtime proxy or SDK replacement. It is an independent,
side-effect-free contract check, deliberately outside the execution path, so it
can run before dispatch, in CI, or in an offline environment with no provider
access at all. Adopting it does not change how your application sends requests.

See [docs/agent-integration.md](docs/agent-integration.md) for the full
check–repair–recheck loop, and
[examples/agent-preflight.md](examples/agent-preflight.md) for a shell walkthrough.

## What it checks

### Structured-output conformance

Providers support different subsets and limits of JSON Schema. Schemaport
analyzes the schema actually present in the rendered request and reports
constructs that the selected provider profile marks as unsupported, risky,
model-specific, or beyond a documented or observed limit.

Findings can cover unsupported keywords, nesting depth, property counts, enum
size, schema size, and other contract constraints as the dataset supports them.

### Cache-safety heuristics

Schemaport also analyzes request shape for likely cache-risk patterns, including
unstable prefix content, cache markers placed after volatile content, and
request prefixes estimated to be below a configured cacheability threshold.

It does not claim an actual cache hit, token count, provider bill, or runtime
result. It identifies static risk from the request shape and explains the
assumptions behind each result.

### Why these two

Both classes share a property that makes them worth encoding: they tend to fail
quietly. Depending on the provider and model, an unsupported schema keyword may
be rejected outright, partially supported, silently transformed, or handled
inconsistently across versions — you cannot assume a single behavior. Reduced
cache reuse is usually silent at the application level and may surface only
later, in provider metrics or costs. These are the defects that survive testing
and reach production.

### What a finding looks like

```text
error    tool.name-invalid
         at $.tools[0].name
         Tool names on this surface must match ^[a-zA-Z0-9_-]{1,64}$.
         found: tool name 'search orders' does not match '^[a-zA-Z0-9_-]{1,64}$'
         fix:   Rename the tool to letters, digits, underscores, or hyphens, at
                most 64 characters — replace spaces and punctuation with
                underscores — and update every reference to the old name in the
                request.
         basis: documented — provider_documentation, recorded 2026-08-15
```

Two runnable examples ship with the source, under `examples/requests/`: an
OpenAI structured-output request with repairable schema defects, and an
Anthropic agent turn carrying an unsupported keyword in a strict tool schema
and a session timestamp inside its cached prefix.

## The contract dataset

Provider documentation is useful, but production behavior is model-specific and
changes over time. Schemaport separates the checking engine from the
provider-contract dataset so that every rule can carry:

- A versioned contract dataset identifier.
- The provider and model profile it applies to.
- A confidence level: `documented`, `observed`, or `experimental`.
- A provenance record pointing to the source or reproducible observation.
- A timestamp and clear scope for the claim.

The checker is intentionally simple and offline. The long-term value is a
contract dataset that makes provider behavior reproducible, auditable, and
reviewable rather than implicit in application code.

Dataset 0.1.0 covers the Anthropic Messages API and the OpenAI Responses and
Chat Completions APIs: tool-definition contracts, the strict schema subsets and
their documented limits, and prompt-cache prefix shape. Every `documented`
record cites the provider page and section it was transcribed from, with the
date it was recorded. There are no `observed` records — that level requires a
reproducible probe against a live endpoint, and this release ships none.

Run `schemaport profiles` to see the models each profile covers.
[docs/contract-data.md](docs/contract-data.md) documents the confidence levels,
the provenance requirements, and the scoping rules a contract record must
satisfy.

## Non-goals

Schemaport does not send provider requests, proxy traffic, wrap an SDK, store
prompts, cache responses, reconcile bills, select models, evaluate outputs, or
judge prompt quality. It does not guarantee provider acceptance or cache
behavior.

It reads one rendered request and reports whether that request appears to
conform to the selected contract profile — and where documented, observed, or
heuristic risk remains.

There is no MCP server in this release. An adapter is a plausible future
addition; nothing here should be read as describing shipped MCP functionality.

## Library use

The CLI is the supported integration surface, but the same check is importable:

```python
from schemaport import check_file

report = check_file("request.json", "claude-sonnet-5")
for finding in report.findings:
    print(finding.severity.value, finding.rule_id, finding.path)
    print("  ", finding.remediation)
```

`check` takes an already-parsed request mapping if you have one in hand.
Neither function sends anything or mutates its input.

## Compatibility

Schemaport follows Semantic Versioning. While on `0.y.z`, breaking changes are
documented in the changelog. The CLI commands, flags, exit codes, output
formats, JSON and SARIF report schemas, and the contract dataset's provenance
fields are treated as public interfaces once published.

## Documentation

- [docs/architecture.md](docs/architecture.md) — why Schemaport sits outside the
  execution path, and how the checker and contract data are separated.
- [docs/contract-data.md](docs/contract-data.md) — confidence levels, provenance,
  and scoping rules for contract records.
- [docs/agent-integration.md](docs/agent-integration.md) — the preflight loop and
  the JSON findings contract.
- [examples/agent-preflight.md](examples/agent-preflight.md) — an orchestrator
  shell flow, end to end.
- [CONTRIBUTING.md](CONTRIBUTING.md) — local development, and what a contract
  record needs before it is accepted.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
