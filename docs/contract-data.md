# Contract data

Schemaport's checker is deliberately small. What makes a finding worth acting on
is the record behind it: a scoped, cited claim about what a specific provider
enforces for a specific model. This document defines what those records must
carry, how much weight each one deserves, and the rules a claim has to satisfy
before it belongs in the dataset.

The dataset ships bundled and versioned inside the distribution, under
`src/schemaport/data/`, so every check runs offline. Every finding names the
dataset version it came from.

## Documentation is not behavior

Provider documentation describes intent. A provider endpoint enforces something,
and those two are not the same artifact.

Documentation is written once and updated on its own schedule. Enforcement is
implemented per model, changes with API versions and model revisions, and
sometimes differs between models in the same family released weeks apart. A
keyword described as supported may be honored on one model, silently dropped on
another, and rejected outright on a third. A documented limit may be enforced
more tightly than published, or not enforced at all.

Neither source is authoritative on its own:

- Documentation is a strong signal about intended contracts and is often the
  only public statement of a limit. It can also lag the deployed behavior, cover
  a family rather than a model, or omit constraints that exist in practice.
- Observation captures what a specific model did on a specific date. It is
  precise and it is narrow — it says nothing about a model you did not test, a
  version you did not test, or behavior after the date you tested it.

Schemaport does not resolve this by picking a winner. It records which kind of
evidence a claim rests on, and reports that alongside every finding, so the
consumer can weigh it. That is what the confidence levels are for.

## Confidence levels

Every contract record carries exactly one confidence level. The level describes
the strength of the evidence, not the severity of the finding — a `documented`
rule can be advisory and an `experimental` rule can flag something serious.

### `documented`

The constraint is stated in the provider's own published material, and the
record cites where.

Use it when a provider documents a supported subset, an explicit limit, or an
unsupported construct. This is the strongest basis for a claim about intent, and
usually the most stable over time.

What it does not license: assuming the documented behavior is what the endpoint
actually enforces for every model in scope, or that a limit documented for one
model family applies to another. A `documented` record says "the provider
published this," not "we ran it and watched it happen" — that is `observed`. If
documentation and observation disagree, that is a finding about the dataset:
record both and scope them, rather than overwriting one with the other.

### `observed`

The constraint was reproduced against a real provider endpoint, and the record
describes the observation precisely enough for someone else to repeat it.

Use it when behavior was verified directly: a request shape that was rejected, a
keyword demonstrably ignored, a limit that took effect at a measured threshold.
An `observed` record must name the model identifier and the date, because that is
the entire scope of what was verified.

What it does not license: generalizing from one model to a family, or treating
an old observation as current. Observations age. A record whose observation
predates a known model or API revision should be re-verified or marked stale.

**The 0.1.0 dataset contains no `observed` records.** The level is defined, the
loader accepts it, and the test suite enforces its provenance rules — but
promoting a record to `observed` requires a reproducible probe artifact, and
this release ships none. The manifest states this in its `evidence_position`
field, and a test fails if a record claims the level without one.

Every `documented` record in this release cites the provider page and section it
was transcribed from, with the date it was recorded. Those citations are the
thing to check first when a finding looks wrong: the record may be right and
stale, which is a different problem from the record being wrong.

### `experimental`

The constraint is inferred, provisional, or heuristic, and has not been
confirmed by documentation or by a reproducible observation.

Use it for behavior inferred from error messages, patterns reported by users but
not reproduced, constraints extrapolated from a related model, thresholds that
are conservative guards rather than published values, and for cache-safety
heuristics whose estimates are proxies rather than measurements. An
`experimental` record must state plainly what remains unverified.

What it does not license: presenting the claim as settled. Consumers should
expect `experimental` findings to change or disappear between dataset versions,
and automated repair loops should treat them more cautiously than the other two
levels. See [agent-integration.md](agent-integration.md).

Every cache-safety rule in this release is `experimental`, and every one of them
says in its provenance what the estimate rests on and where it will be wrong.

## Provenance requirements

Confidence says how strong the evidence is. Provenance says what the evidence
actually was. Every record must carry both — a confidence level without a
traceable source is an assertion, not a contract claim.

Each record must include:

- **Source kind** (`source_kind`). `inference`, `provider_documentation`,
  `published_artifact`, or `observation` — listed weakest to strongest. It has
  to be coherent with the confidence level: an `observed` record backed by
  `inference` is a contradiction, and the test suite rejects it.
- **Reference** (`reference`). For documented claims, enough to find the
  statement again: the document and the section. For observed claims, a
  reproducible description — model identifier, API version where applicable, the
  request shape exercised, and the outcome. For inferred claims, the reasoning,
  the signal it was drawn from, and what remains unverified.
- **Evidence date** (`evidence_date`). The date the evidence was recorded into
  the dataset from its source. Not the date someone last edited the record.
- **Scope** (`scope`). The provider, the model or models, and the API surface the
  claim applies to. See the scope rule below. A record inherits its profile's
  scope and may narrow it; it may not widen it.
- **Remediation** (`remediation`). What the consumer should change in the
  request. This is the field agents act on, so it must describe a concrete edit,
  not a diagnosis.

Two constraints on how evidence is captured:

- **No customer content.** Observations, fixtures, and examples must be built
  from synthetic request material. Never record, distribute, or cite real user
  prompts, tool outputs, or production request bodies as evidence.
- **No credentials or artifacts of access.** Provenance records describe what was
  observed, not how the endpoint was reached. Keys, tokens, endpoints, and
  account identifiers never appear in the dataset.

A shipped record, trimmed to the fields under discussion:

```json
{
  "rule_id": "cache.breakpoint-limit-exceeded",
  "check": "cache.breakpoint_limit",
  "severity": "error",
  "confidence": "documented",
  "params": { "limit": 4 },
  "message": "The request defines more cache_control breakpoints than this surface accepts.",
  "remediation": "Reduce the request to at most four cache_control markers. Keep the ones at the boundaries of content that genuinely stays stable across turns — normally the end of the tool definitions and the end of the system prompt.",
  "provenance": {
    "source_kind": "provider_documentation",
    "reference": "Anthropic \"Prompt caching\": \"You can define up to 4 cache breakpoints\" per request. https://platform.claude.com/docs/en/build-with-claude/prompt-caching",
    "evidence_date": "2026-08-15"
  }
}
```

The `scope` block is inherited from the profile — provider, models, and API
surface — and appears in full on every finding the rule produces.

## Pinned artifacts, and why they beat prose

A prose citation rots quietly. The page gets edited, the claim it supported
changes, and nothing in the dataset notices — which is the freshness problem an
offline tool cannot otherwise solve, since it never contacts the provider.

Some provider material is not prose. An OpenAPI document, or a generated SDK's
type definitions, is machine-readable, versioned, and pinnable to an exact
revision. A record can cite one of those instead, with `source_kind` or `basis`
set to `published_artifact`:

```json
"artifact": {
  "url": "https://raw.githubusercontent.com/openai/openai-openapi/{revision}/openapi.yaml",
  "revision": "2186421dca0cca7c1e67caa7739005e8b1ccc4dd",
  "tracks": "master",
  "expect": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
}
```

This ranks above `provider_documentation` for three reasons:

- **It is closer to the contract.** An OpenAPI document is what the provider's
  own SDKs are generated from, not a human summary of the behaviour.
- **The citation cannot rot.** The revision is exact, so the reader sees what
  the author saw, however much the document has moved since.
- **It is re-checkable.** `expect` names the literal strings the claim depends
  on, so a script can confirm they are still there.

That last point is what turns staleness from a discovery into an event.
`tools/verify_contract_data.py` fetches each artifact at its pinned revision and
again at the ref it tracks, and reports anything that has disappeared. It is
opt-in, it is the only thing in the repository that reaches the network, and it
is not part of the installed package — `schemaport check` remains offline, and
a test asserts no module in `src/` imports a network library. A scheduled
workflow runs it weekly and opens an issue on drift.

Drift is reported, never auto-corrected. A vanished string can mean the provider
changed the contract, or only that the document was reorganised, and telling
those apart is a judgement call.

What an artifact does **not** establish is runtime behaviour. It shows what the
provider published, not what the endpoint enforces. That is still `observed`,
and it still needs a probe.

## The model list is a claim too

Every rule cites its evidence. The profile's model list has to as well, and for
the same reason: it decides which requests each of those rules is allowed to
speak about. An unsourced list widens every finding in the profile without any
single record looking wrong.

So a profile carries a `model_scope` block, and the loader refuses a profile
without one:

```json
"model_scope": {
  "basis": "provider_documentation",
  "reference": "…the page and section, with a URL…",
  "evidence_date": "2026-08-15",
  "verified": ["claude-sonnet-5", "claude-opus-4-8"]
}
```

`verified` is the part that does the work. Provider documentation states model
support two different ways, and they are not equally strong:

- **Named.** The source names this exact model identifier — an enumerated table,
  a per-model capability statement. The model goes in `verified`.
- **Covered by range.** The source says something like "this snapshot and
  later." The model is inside the documented range but the source never names
  it. It stays out of `verified`.

Both are `documented`; the second is weaker, and the dataset says which is
which rather than flattening them. Every finding reports
`target.model_scope.named_in_evidence`, and `schemaport profiles` prints a note
for any model covered only by a range.

In this release every model is named. The Anthropic profiles rest on the
cache-limitations table, which lists each model by name. The OpenAI profiles
rest on a pinned artifact: the Structured Outputs guide states support only as a
version range ("gpt-4o-2024-08-06 and later") and the models page carries no
capability flag, so prose alone left those identifiers range-covered — but all
three appear in OpenAI's published OpenAPI document, and pinning that closed the
gap without a single API call.

Note what the pin does and does not establish. It shows these models exist on
this API, which is what prose left unconfirmed. The Structured Outputs contract
itself still comes from the guide, and the guide still scopes support by version
range. Confirming that a specific model enforces a specific constraint is a
different claim, and it needs `observed`.

## Rule sets and shared records

Several profiles usually share a contract and differ on one value. The four
Anthropic profiles in this release carry an identical tool and strict-tool-use
contract and differ only in the minimum cacheable prompt length, which the
provider publishes per model.

Duplicating every record four times would mean four places to correct a claim.
Instead a profile can `extend` a rule set — a shared file of records — and
`override` named fields on what it inherits:

```json
{
  "profile_id": "anthropic/messages-1024-token-cache",
  "models": ["claude-sonnet-5", "claude-opus-4-8", "..."],
  "extends": ["anthropic-messages"],
  "overrides": {
    "cache.prefix-below-minimum": {
      "params": { "min_prefix_chars": 4096 },
      "provenance": { "...": "the citation for these models' figure" }
    }
  }
}
```

Nothing about scope is inherited loosely. A profile always names its own models,
an inherited rule takes the scope of the profile it lands in, and an override
that names a rule the profile does not inherit is an error rather than a
silently ignored typo.

## One model, several surfaces

A model is often reachable through more than one API, and the request is shaped
differently on each — the schema sits under `response_format.json_schema` on one
surface and `text.format` on another. These are separate profiles, because a
check against one says nothing about the other.

That makes model alone insufficient to pick a profile. Schemaport resolves it by
reading the request: the fields present identify the surface it was written for.
That is inference about the request document, not about the model, so it does
not widen any contract claim. When the request does not clearly match one
surface, the check stops and asks for `--surface` rather than guessing.

## The scope rule

**A contract record may not make a universal claim.** Every record states the
provider, the model or models, and the API surface it applies to. A claim that
cannot name its scope does not go in the dataset, and the loader refuses to load
a profile that omits any of the three.

This is the rule that keeps the dataset honest as it grows, and it rules out the
phrasings that are easiest to reach for:

| Not allowed | Why | Instead |
| --- | --- | --- |
| "Providers do not support this keyword." | No provider, no model, no version. | Name the profile the claim was established against. |
| "This limit applies to all models from PROVIDER." | Family-wide claim from model-specific evidence. | Record it for the models actually covered. |
| "Deeply nested schemas fail." | No threshold, no scope, not checkable. | State the depth, the model, and the evidence for it. |
| "This behavior changed recently." | No version boundary, no date. | Give the version range and the evidence date. |

Practical consequences, all of them visible in the shipped dataset:

- **Extending a rule to another model requires evidence for that model.**
  Copying a record to a new profile without new evidence lowers its confidence
  to `experimental` at best, and the record must say so.
- **Profiles are resolved per model, by exact match.** `--model` is required
  rather than inferred, and an unlisted model is refused rather than resolved to
  a neighbouring profile — fuzzy resolution would silently widen the scope of
  every finding it produced. `schemaport profiles` lists what is covered.
- **A model-specific threshold gets its own profile.** The four Anthropic
  profiles share one tool contract and differ only in the minimum cacheable
  prompt length, which the provider publishes per model. Merging them would mean
  asserting one model's threshold for another; the profile names say which tier
  they encode.
- **A limit is implemented as it is written.** The documented property and enum
  caps on the OpenAI surface are totals across a whole schema, not per-object
  caps, and the analyzers sum them accordingly. Reading a total as a per-object
  limit would let a schema pass a check the provider would reject.
- **A finding's applicability is only as wide as the record's scope.**
  Schemaport reports the profile it resolved and the full scope block on every
  finding, so the boundary is visible in the output.

## Freshness

Records go stale quietly. Provider behavior can change without any signal to a
tool that never contacts the provider, which is a direct consequence of
Schemaport being offline by design.

The dataset handles this by making age visible rather than by guessing:

- Every record carries the date its evidence was gathered, and every report
  carries the dataset version.
- Records whose evidence predates a known model or API revision in their scope
  are candidates for re-verification and should be marked rather than silently
  trusted.
- A record that can no longer be reproduced is corrected or removed with the
  dataset version bumped, not quietly edited in place.

Treat a check as a statement about the request under a specific dataset version.
It is not a live read of provider behavior, and a clean report is not a
guarantee of provider acceptance.

## Reading confidence in findings

Every finding reports its `confidence`, its `provenance`, and the
`dataset_version` that produced it. A reasonable default reading:

- `documented` — treat as a contract requirement. Fix it.
- `observed` — treat as verified for the scope named. Fix it, and check whether
  the scope actually covers the model you are targeting.
- `experimental` — treat as a prompt to investigate. Worth surfacing in review;
  worth more caution before automated repair acts on it.

`--fail-on` gates on severity, not confidence. If you want a stricter policy for
lower-confidence findings, filter the JSON report on the `confidence` field in
your own tooling rather than assuming severity encodes it.

## Adding a record

The mechanics are in [../CONTRIBUTING.md](../CONTRIBUTING.md). The short version:
a new rule is a JSON object in a profile under `src/schemaport/data/profiles/`,
naming a `check` that an analyzer implements, with a severity, a confidence, a
message, a remediation, and a provenance block. If the constraint needs traversal
the existing analyzers cannot do, that is a code change too — but the claim
itself stays in the data.
