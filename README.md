<p align="center">
  <img src="https://raw.githubusercontent.com/Hotragn/schemaport/main/assets/icon.png" alt="" width="96" height="96">
</p>

<h1 align="center">Schemaport</h1>

<p align="center"><strong>Check an AI request before you send it.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/schemaport/"><img src="https://img.shields.io/pypi/v/schemaport?color=0F172A&labelColor=38BDF8" alt="PyPI"></a>
  <a href="https://pypi.org/project/schemaport/"><img src="https://img.shields.io/pypi/pyversions/schemaport" alt="Python versions"></a>
  <a href="https://github.com/Hotragn/schemaport/actions/workflows/ci.yml"><img src="https://github.com/Hotragn/schemaport/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/Hotragn/schemaport/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/schemaport" alt="Apache-2.0"></a>
</p>

When your program talks to an AI model, it sends a block of JSON describing what
it wants. That JSON has to follow rules — and the rules differ between
providers, between models, and over time. Get one wrong and you find out after
you've paid for the call.

Schemaport reads that block of JSON on your machine and tells you what's wrong
before it goes anywhere. It never sends the request, never contacts a provider,
and never asks for an API key.

```bash
pip install schemaport
schemaport check request.json --model claude-sonnet-5
```

## Two problems it catches

**A rule you didn't know about.** You can ask a model to reply in a specific
shape — "give me an object with a customer ID and a status." That shape is
written in a standard called JSON Schema, and providers only support *part* of
that standard. Use a part they don't support and the result varies: sometimes
an error, sometimes the constraint is quietly ignored, sometimes the model
returns something you didn't ask for. Which of those you get depends on the
provider and the model.

**A prompt that stopped being cheap.** Providers charge less when the beginning
of your prompt is byte-for-byte identical to last time — they cache it. Put a
timestamp or a random session ID near the top and that stops matching. Nothing
errors. Nothing looks broken. You just quietly pay full price on every call,
and you may not notice until you read the bill.

Both problems fail *quietly*, which is exactly why they survive testing and
reach production. Schemaport makes them visible while they're still free to fix.

## Why this matters more with agents

An agent builds its requests as it goes — assembling schemas, tool definitions,
and prompts at runtime. Nobody reviews those before they're sent.

So a bad request can fail thirty steps into a long task, throwing away all the
work that came before it. And because agents often route between models, a
request that's fine for one may be rejected by another.

## What it looks like

```text
error    tool.name-invalid
         at $.tools[0].name
         Tool names on this surface must match ^[a-zA-Z0-9_-]{1,64}$.
         found: tool name 'search orders' does not match '^[a-zA-Z0-9_-]{1,64}$'
         fix:   Rename the tool to letters, digits, underscores, or hyphens, at
                most 64 characters, and update every reference to the old name.
         basis: documented — provider_documentation, recorded 2026-08-15
```

Every finding tells you four things: what rule broke, exactly where in your
request, what to change, and what evidence the rule is based on.

## Using it

You need Python 3.10 or newer. There are no other dependencies.

```bash
schemaport check request.json --model claude-sonnet-5
```

The input is the request itself — the JSON you were about to send — not a log
line or a wrapper around it. `--model` is required rather than guessed, because
a rule only applies to the models it was written for. Run `schemaport profiles`
to see which models are covered.

For scripts and agents, ask for JSON output and choose what counts as a failure:

```bash
schemaport check request.json --model claude-sonnet-5 --format json --fail-on warning
```

`--format` can be `text`, `json`, or `sarif` (for code-scanning tools).
`--fail-on` sets the lowest severity that exits non-zero, which is what makes it
usable as a CI gate.

Some models are reachable through more than one API, and the request looks
different on each. Schemaport works out which one you wrote for; if that's
genuinely ambiguous it stops and asks for `--surface` rather than checking
against rules you may not be using.

| Exit code | Meaning |
| --- | --- |
| `0` | Checked it. Nothing at or above your `--fail-on` level. |
| `1` | Checked it. Found something. |
| `2` | Couldn't check it — file missing, invalid JSON, unknown model. |

`1` and `2` stay separate on purpose. "Your request has a problem" and "I
couldn't read your request" need different responses, especially from a script.

## For agents

Findings are structured and predictable, so a program can act on them without a
human in the loop: read the path, apply the fix, check again, then send.

```text
agent drafts request
        |
        v
schemaport check ──── clean ────> send to provider
        |
        └── findings: rule ID + JSON path + fix
                         |
                         v
                 agent repairs request ──> check again
```

The same check works as a CI gate, so a bad request contract fails the build
instead of reaching production.

Schemaport is not a proxy and doesn't wrap your API client. It's a separate
command that reads a file, so it runs before you send, in CI, or on a machine
with no internet at all. Adding it changes nothing about how your code sends
requests, and removing it changes nothing either.

## Where the rules come from

Rules aren't hard-coded. They live in a versioned dataset that ships with the
package, and each one records where it came from:

- `documented` — the provider says so in writing, and the rule cites the page.
- `observed` — someone ran it against the live API and saw it happen, on a
  named model, on a date.
- `experimental` — an educated guess, clearly labelled as one.

That distinction matters because provider documentation describes intent, while
the actual API enforces something — and the two don't always agree. Rather than
picking a winner, Schemaport tells you which kind of evidence each finding rests
on, so you can judge it.

The current dataset covers the Anthropic Messages API and the OpenAI Responses
and Chat Completions APIs. **It contains no `observed` rules** — that level
requires testing against a live endpoint, and this release doesn't ship any such
tests. Run `schemaport profiles` to see exactly what's covered.

## What it doesn't do

It doesn't send your request, store it, or see the response. It doesn't handle
your API keys, choose models for you, judge your prompts, or estimate costs.

It also can't promise your request will succeed. A clean report means your
request matches the rules Schemaport knows about — provider behaviour changes,
and coverage isn't complete. Keep your normal error handling.

There's no MCP server in this release. One may make sense later; nothing here
implements one today.

## From Python

```python
from schemaport import check_file

report = check_file("request.json", "claude-sonnet-5")
for finding in report.findings:
    print(finding.severity.value, finding.rule_id, finding.path)
    print("  ", finding.remediation)
```

`check()` takes an already-parsed dictionary if you have one. Neither function
sends anything or modifies your request.

## Versioning

Semantic Versioning. While on `0.x`, breaking changes are written up in the
[changelog](https://github.com/Hotragn/schemaport/blob/main/CHANGELOG.md).
Commands, flags, exit codes, and the JSON and SARIF output formats are treated
as public interfaces.

## Documentation

- [Architecture](https://github.com/Hotragn/schemaport/blob/main/docs/architecture.md) — why it stays out of your request path, and how the checker and the rules are kept separate.
- [Contract data](https://github.com/Hotragn/schemaport/blob/main/docs/contract-data.md) — what a rule must prove before it ships.
- [Agent integration](https://github.com/Hotragn/schemaport/blob/main/docs/agent-integration.md) — the check-fix-recheck loop, in detail.
- [Shell example](https://github.com/Hotragn/schemaport/blob/main/examples/agent-preflight.md) — an end-to-end script.
- [Contributing](https://github.com/Hotragn/schemaport/blob/main/CONTRIBUTING.md) — running it locally, and the bar for adding a rule.

## License

Apache-2.0. See [LICENSE](https://github.com/Hotragn/schemaport/blob/main/LICENSE)
and [NOTICE](https://github.com/Hotragn/schemaport/blob/main/NOTICE).
