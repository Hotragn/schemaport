# Agent integration

Schemaport is a deterministic, offline function from a rendered request and a
target model to a set of findings. That makes it callable by an autonomous
process: an agent can check its own pending request, repair it, and check again
before spending a token on a provider call.

This document describes that loop and the findings contract it depends on.

## The loop

```text
1. render     the agent builds the full request body it intends to send
2. check      schemaport check request.json --model MODEL --format json
3. repair     for each finding: locate `path`, read `rule_id`, apply `remediation`
4. re-check   run the same command again on the repaired request
5. dispatch   only when the report is clean, the agent's own client sends it
```

Steps 3 and 4 repeat under a bounded attempt limit. Step 5 is the agent's own
code: Schemaport never sends anything, and the loop is complete before any
provider is contacted.

Three properties of the checker make this work. It is **deterministic**, so a
finding that disappears after a repair disappeared because the repair worked.
It is **offline**, so the loop runs at full speed in a sandbox with no egress and
no credentials. It is **side-effect-free**, so running it fifty times in a repair
loop costs nothing and changes nothing. See
[architecture.md](architecture.md) for why the v0.1 boundary is drawn there.

## Calling it

Invoke the CLI as a subprocess and read JSON from standard output:

```bash
schemaport check request.json --model gpt-5.6-sol --format json --fail-on warning
```

`--fail-on` sets the lowest severity that exits non-zero. In an agent loop it is
convenient as a fast "is there anything to do?" signal — a zero exit means
nothing at or above the threshold was found, so the agent can skip parsing and
dispatch. Below that threshold, findings are still present in the report; parse
them if you want to log or act on advisory results.

Exit code 2 means something different and must not be treated as findings: the
invocation or its input could not be used at all — unreadable file, invalid
JSON, or a model the bundled dataset has no profile for. An agent that collapses
1 and 2 will loop forever trying to repair a request that was never checked.

Standard output carries only the report, so it stays pipeable into `jq`. The
severity summary goes to standard error.

`--format sarif` exists for code-scanning tools. For agent consumption, use
`json`.

## The findings contract

A JSON report describes the target it resolved, the dataset version it used, and
a list of findings. Each finding is self-contained — an agent should never need
to consult a separate table to act on one.

The fields an agent depends on:

| Field | Purpose |
| --- | --- |
| `rule_id` | Stable identifier for the constraint. Safe to branch on, suppress, or count across runs. |
| `path` | JSON path to the offending node in the request that was checked. This is where the repair applies. |
| `severity` | How much this matters. Interacts with `--fail-on`. |
| `message` | What the rule requires, in prose. |
| `detail` | What was actually found at `path`, when the analyzer can be specific. May be `null`. |
| `remediation` | The concrete edit to make. This is the field an automated repair acts on. |
| `confidence` | `documented`, `observed`, or `experimental`. See [contract-data.md](contract-data.md). |
| `provenance` | What the claim rests on, and when the evidence was recorded. |
| `scope` | The provider, models, and API surface the claim covers. |

The report also carries the resolved provider and model profile and the contract
dataset version, so a finding can be attributed to a specific claim in a specific
dataset release.

Paths use a JSONPath-style syntax: dotted segments for identifier-like keys,
numeric brackets for array indices, and single-quoted brackets for keys that are
neither — `$.tools[0].input_schema.properties['order id']`.

## Worked example

This one reproduces. The request is
[`examples/requests/openai-structured-output.json`](../examples/requests/openai-structured-output.json);
the findings are what the shipped 0.1.0 dataset produces for it.

The agent has rendered a request with a dynamically constructed response schema:

```json
{
  "model": "gpt-5.6-sol",
  "instructions": "You extract structured order data from support tickets.",
  "input": [
    { "role": "user", "content": "Ticket 4412: the customer says their order never arrived." }
  ],
  "text": {
    "format": {
      "type": "json_schema",
      "name": "order_lookup",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "customer_id": { "type": "string" },
          "status": { "allOf": [{ "type": "string" }, { "enum": ["open", "closed"] }] },
          "note": { "type": "string" }
        },
        "required": ["customer_id"]
      }
    }
  }
}
```

It runs the check:

```bash
schemaport check request.json --model gpt-5.6-sol --format json --fail-on warning
```

and gets back a report. Abridged to the fields under discussion — the full
report also carries `scope`, `provenance`, and `dataset_version` on every
finding:

```json
{
  "schemaport_version": "0.1.0",
  "contract_dataset_version": "0.1.0",
  "target": {
    "provider": "openai",
    "model": "gpt-5.6-sol",
    "profile": "openai/responses",
    "request_shape": "openai.responses"
  },
  "summary": { "error": 3, "warning": 0, "info": 0 },
  "findings": [
    {
      "rule_id": "structured-output.additional-properties-not-false",
      "severity": "error",
      "path": "$.text.format.schema",
      "message": "Every object in a strict schema must set \"additionalProperties\": false.",
      "detail": "object schema does not set additionalProperties",
      "remediation": "Add \"additionalProperties\": false to the object schema at this path. Strict mode requires it on every object, not only the root.",
      "confidence": "documented"
    },
    {
      "rule_id": "structured-output.property-not-required",
      "severity": "error",
      "path": "$.text.format.schema",
      "message": "Every property declared on an object in a strict schema must appear in that object's \"required\" array.",
      "detail": "2 properties not in 'required': 'status', 'note'",
      "remediation": "Add the missing property names to this object's \"required\" array. For a field that is genuinely optional, keep it required and widen its type to a union that includes \"null\" rather than leaving it out.",
      "confidence": "documented"
    },
    {
      "rule_id": "structured-output.unsupported-keyword",
      "severity": "error",
      "path": "$.text.format.schema.properties.status.allOf",
      "message": "This composition keyword is outside the JSON Schema subset accepted under strict mode.",
      "detail": "keyword 'allOf' is present in schema 'order_lookup'",
      "remediation": "Remove the keyword. Use \"anyOf\" in place of \"allOf\", merge the combined constraints into a single schema, and replace conditional keywords with an \"anyOf\" over the concrete shapes you accept.",
      "confidence": "documented"
    }
  ]
}
```

Note that `--model` alone did not decide the contract. The model is covered on
two API surfaces; `instructions`, `input`, and `text.format` identify this
request as one written for the Responses API, and the report says which profile
it resolved.

The full `target` block also carries `model_scope`, including
`named_in_evidence` — whether the source behind this profile names this exact
model or covers it through a version range. For this model it is `false`. That
does not weaken the schema rules, which are documented for the feature, but it
is the field to check before treating a profile's coverage of your model as
settled. See [contract-data.md](contract-data.md).

The agent handles all three findings mechanically:

1. **Locate.** `path` is `$.text.format.schema` for the first two and
   `$.text.format.schema.properties.status.allOf` for the third. The agent
   resolves those paths against the request document it just rendered — the same
   document it passed to the checker, so they resolve exactly.
2. **Classify.** `rule_id` is what the agent branches on. If it keeps a repair
   table, this is the key it looks up. If it does not, the rule ID is still what
   it logs, counts, and deduplicates on.
3. **Repair.** Add `"additionalProperties": false`, add the names in `detail` to
   `required`, and collapse the `allOf` into the single schema it was
   describing:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "customer_id": { "type": "string" },
    "status": { "type": "string", "enum": ["open", "closed"] },
    "note": { "type": "string" }
  },
  "required": ["customer_id", "status", "note"]
}
```

4. **Re-check.** The agent writes the repaired request and runs the same command
   again. The report is clean, the exit status is 0, and it can dispatch.

Note what the agent did not need: no knowledge of which provider it was talking
to, no hardcoded list of supported keywords, no network call. Each finding
carried everything required to act.

Not every finding is this mechanical. The same dataset carries `experimental`
cache-safety findings whose remediation is a judgement call about whether a
value really is stable across turns — those are for a human to read, not for an
agent to act on unattended. Weigh `confidence` before repairing automatically.

## Loop discipline

A repair loop needs limits, because not every finding is machine-repairable.

**Bound the attempts.** Two or three repair rounds is usually enough. A finding
that survives repair means the agent's edit did not address it — looping harder
will not help. Stop and surface it.

**Detect non-progress.** If the same `rule_id` at the same `path` appears in
consecutive reports, the loop is stuck. Break immediately rather than burning
attempts. Because the checker is deterministic, an unchanged finding across two
runs is proof the request did not change in the relevant way.

**Re-check after every repair, not once at the end.** A repair can introduce a
new finding — replacing an unsupported construct with a supported one can push a
schema past a property or depth limit. Only the re-check tells you.

**Weigh confidence before acting automatically.** `documented` and `observed`
findings are safe inputs to an automated repair. `experimental` findings are
inferred or heuristic and may change between dataset versions; prefer to surface
them for review rather than have an agent rewrite a request on their basis. If
your policy differs by confidence level, filter on the `confidence` field —
`--fail-on` gates on severity only.

**Escalate rather than dispatching blind.** If findings remain after the attempt
limit, the right move is to stop and report, not to send the request anyway and
hope. The whole point of preflight is to catch this before the call.

**A clean report is not a guarantee.** It means the request conforms to the
resolved profile as far as the bundled contract data goes. Provider behavior can
change, and coverage for a given model can be incomplete. Keep your normal error
handling on the provider call — preflight reduces the failure rate, it does not
remove the failure mode.

## Calling it in-process

Shelling out is the supported integration, and it is what the examples use. If
the agent is already Python, the same check is importable and avoids the
subprocess:

```python
from schemaport import UnknownModelError, check

try:
    report = check(request_body, "gpt-5.6-sol")
except UnknownModelError as exc:
    ...  # the equivalent of exit code 2: nothing was checked

for finding in report.findings:
    apply_repair(request_body, finding.path, finding.rule_id, finding.remediation)
```

`check` takes a mapping and does not mutate it. `check_file` takes a path.
Neither sends anything.

## What Schemaport does not do in this loop

It does not send the request, hold credentials, choose a model, rewrite the
request, or observe the response. Repairs are applied by the caller. Dispatch is
performed by the caller's own client, from the caller's own application, after
the loop has finished.

## On MCP

**Schemaport v0.1 does not include an MCP server.** No MCP server, adapter, or
transport is implemented in this repository.

An MCP adapter is a plausible future addition — it would let the same process
that authored a request call the check without shelling out — but it is not part
of v0.1 and nothing in this documentation should be read as describing shipped
MCP functionality. Until such an adapter exists, the supported integration
surfaces are the CLI (`schemaport check --format json`, parsed from standard
output) and the Python API above.
