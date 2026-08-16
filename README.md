# Schemaport

**Static request conformance for the agent–provider boundary.**

Schemaport checks a fully rendered LLM request before your application or agent
sends it. It reads a request JSON file and a target model, resolves a bundled,
versioned provider-contract profile, and emits deterministic findings with
stable rule IDs, JSON paths, severity, remediation, confidence, and
provenance — in text, JSON, or SARIF.

It runs locally: no SDK, API key, telemetry, proxy, or network call. Stable exit
codes make it usable in CI, air-gapped evaluation harnesses, and agent repair
loops. Apache-2.0 licensed.

## Install

```bash
pip install schemaport
```

Python 3.10+. No runtime dependencies, no provider SDK, no credentials.

## Quick start

Point it at a request body you have already rendered, and the model you intend
to send it to:

```bash
schemaport check request.json --model claude-sonnet-5
```

The input is the request body itself — the JSON you would hand to the provider —
not a wrapper, log line, or SDK call. `--model` is required rather than
inferred, because a profile's claims apply only to the models it names; run
`schemaport profiles` to see them.

For machine consumption, ask for JSON and set the severity that should fail:

```bash
schemaport check request.json --model claude-sonnet-5 --format json --fail-on warning
```

A model is often reachable on more than one API, and the request is shaped
differently on each. Schemaport reads the request to work out which surface it
was written for; when that is ambiguous it stops and asks for `--surface`
rather than checking against a contract you may not be using.

Schemaport reads the file and writes a report. It does not send the request,
contact the provider, or modify the input.

| Exit | Meaning |
| --- | --- |
| `0` | Ran; nothing at or above `--fail-on`. |
| `1` | Ran; found something at or above `--fail-on`. |
| `2` | Could not use the invocation or input — unreadable file, invalid JSON, unknown model. |

`1` and `2` stay distinct on purpose: "your request has a problem" and "I could
not check your request" call for different handling.

## The problem agents created

Agentic systems build schemas, tool definitions, messages, and cache boundaries
at runtime. Many of those request bodies are never reviewed by a human before
they reach a provider.

An unsupported structured-output construct can fail late in a long trajectory,
wasting the context and tool work that came before it. Routing multiplies the
risk: a request valid for one model family may be rejected, transformed, or only
partially supported by another. Agent loops also create cache-risk patterns that
ordinary prompt review misses — timestamps, UUIDs, and changing tool definitions
can alter an otherwise stable prefix between turns, reducing cache reuse without
producing an obvious application error.

## Agent-native preflight

Findings are structured, deterministic, and machine-readable, so an agent can
inspect its own pending request, locate the affected path, apply a known
remediation, and check again before it sends anything.

```text
agent drafts request
        |
        v
schemaport check ──── clean ────> send to provider
        |
        └── findings: rule ID + JSON path + remediation
                         |
                         v
                 agent repairs request ──> check again
```

Which makes it useful in two places: as a CI gate that stops invalid request
contracts reaching deployment, and as a preflight call an agent or orchestrator
makes before dispatch.

Schemaport is not a runtime proxy or SDK replacement. It is an independent,
side-effect-free check, deliberately outside the execution path, so it can run
before dispatch, in CI, or offline with no provider access at all. Adopting it
does not change how your application sends requests.

## What it checks

**Structured-output conformance.** Providers support different subsets and
limits of JSON Schema. Schemaport analyzes the schema actually present in the
request and reports constructs the selected profile marks as unsupported,
model-specific, or beyond a documented limit — unsupported keywords, nesting
depth, property counts, enum size, schema size.

**Cache-safety heuristics.** It also reports likely cache-risk patterns:
volatile content inside a cached prefix, cache markers past the point they
protect, prefixes below a cacheability threshold. It does not claim an actual
cache hit, token count, or bill — only static risk from request shape, with the
assumptions stated.

Both classes fail quietly, which is why they are worth encoding. Depending on
provider and model, an unsupported keyword may be rejected, partially supported,
silently transformed, or handled inconsistently across versions. Reduced cache
reuse is usually silent at the application level and may surface only later, in
provider metrics or costs.

```text
error    tool.name-invalid
         at $.tools[0].name
         Tool names on this surface must match ^[a-zA-Z0-9_-]{1,64}$.
         found: tool name 'search orders' does not match '^[a-zA-Z0-9_-]{1,64}$'
         fix:   Rename the tool to letters, digits, underscores, or hyphens, at
                most 64 characters, and update every reference to the old name.
         basis: documented — provider_documentation, recorded 2026-08-15
```

## The contract dataset

Provider documentation states intent; a provider endpoint enforces something,
and the two are not the same artifact. Schemaport keeps the checking engine
separate from the contract dataset so every rule carries a dataset version, the
provider and models it applies to, a confidence level, a provenance record, and
a date.

Confidence is about evidence, not severity:

- `documented` — stated in the provider's published material, and cited.
- `observed` — reproduced against a live endpoint, dated and model-scoped.
- `experimental` — inferred, provisional, or heuristic.

Dataset 0.1.0 covers the Anthropic Messages API and the OpenAI Responses and
Chat Completions APIs. **It contains no `observed` records** — that level needs
a reproducible probe against a live endpoint, and this release ships none.

## Non-goals

Schemaport does not send requests, proxy traffic, wrap an SDK, store prompts,
cache responses, reconcile bills, select models, evaluate outputs, or judge
prompt quality. It does not guarantee provider acceptance or cache behavior. A
clean report means the request conforms to the resolved profile as far as the
bundled data goes — keep your normal error handling on the provider call.

There is no MCP server in this release. An adapter is a plausible future
addition; nothing here describes shipped MCP functionality.

## Library use

```python
from schemaport import check_file

report = check_file("request.json", "claude-sonnet-5")
for finding in report.findings:
    print(finding.severity.value, finding.rule_id, finding.path)
    print("  ", finding.remediation)
```

`check` takes an already-parsed request mapping. Neither sends anything or
mutates its input.

## Compatibility

Semantic Versioning. While on `0.y.z`, breaking changes are documented in the
[changelog](https://github.com/Hotragn/schemaport/blob/main/CHANGELOG.md). CLI
commands, flags, exit codes, output formats, the JSON and SARIF report schemas,
and the dataset's provenance fields are public interfaces once published.

## Documentation

- [Architecture](https://github.com/Hotragn/schemaport/blob/main/docs/architecture.md) — why it sits outside the execution path, and how checker and data are separated.
- [Contract data](https://github.com/Hotragn/schemaport/blob/main/docs/contract-data.md) — confidence levels, provenance, and scoping rules.
- [Agent integration](https://github.com/Hotragn/schemaport/blob/main/docs/agent-integration.md) — the preflight loop and the JSON findings contract.
- [Shell example](https://github.com/Hotragn/schemaport/blob/main/examples/agent-preflight.md) — an orchestrator flow, end to end.
- [Contributing](https://github.com/Hotragn/schemaport/blob/main/CONTRIBUTING.md) — local development, and the bar a contract record must clear.

## License

Apache-2.0. See [LICENSE](https://github.com/Hotragn/schemaport/blob/main/LICENSE)
and [NOTICE](https://github.com/Hotragn/schemaport/blob/main/NOTICE).
