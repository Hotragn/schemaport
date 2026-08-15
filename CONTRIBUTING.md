# Contributing

Two kinds of change land here, and they have different bars.

**Engine changes** — traversal, path construction, report rendering, CLI
behaviour — are ordinary code review. Keep the engine free of provider
knowledge; if a change requires knowing what a particular provider enforces,
that knowledge belongs in the dataset.

**Contract-data changes** are reviewed as claims, not as code. The bar is in
[docs/contract-data.md](docs/contract-data.md), and it is not negotiable: a
record states its scope and cites its evidence, or it does not ship.

## Local development

No network access is required to develop or test this package.

```bash
python -m pip install -e ".[dev]"
```

The test suite also runs against the source tree without an install, because
`pyproject.toml` puts `src` on the path for pytest:

```bash
python -m pytest
```

Lint, format, and type checks:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src/schemaport
```

Try the CLI against the shipped examples:

```bash
python -m schemaport check examples/requests/openai-structured-output.json --model gpt-5.6-sol
```

## Adding a contract record

A rule is a JSON object, either in a profile under
`src/schemaport/data/profiles/` or — when several profiles share it — in a rule
set under `src/schemaport/data/rule-sets/`. It needs:

- `rule_id` — stable, namespaced, and never reused for a different constraint.
  Consumers branch on it and suppress on it.
- `check` — the name of an analyzer that implements the traversal. If none fits,
  the analyzer is a separate code change; the claim still lives in the data.
- `severity` — `info`, `warning`, or `error`. How much it matters, not how sure
  you are.
- `confidence` — `documented`, `observed`, or `experimental`. How sure you are,
  not how much it matters.
- `message` — what the rule requires.
- `remediation` — the edit to make. Agents act on this field, so write an
  instruction, not a diagnosis.
- `provenance` — `source_kind`, `reference`, `evidence_date`. The reference has
  to be specific enough for someone else to find or reproduce the claim.
- `params` — thresholds, keyword lists, patterns. Anything provider-specific
  goes here rather than into the analyzer.

Optional: `scope` to narrow the profile's scope (never to widen it),
`applies_to_kinds` to restrict the rule to particular schema locations, and
`requires_strict` for rules that only apply under an opt-in strict mode.

A profile pulls in a rule set with `extends`, and adjusts what it inherits with
`overrides` — a map from `rule_id` to the fields to replace. That is how the
Anthropic profiles share one tool contract while carrying different cache
thresholds. An override naming a rule the profile does not inherit is an error,
so a renamed rule cannot leave a silently dead override behind.

The test suite enforces the mechanical parts — scope is named, provenance is
present and dated, `source_kind` is coherent with `confidence`, no rule claims a
model outside its profile, `check` names an analyzer that exists. Run
`python -m pytest tests/test_contract_data.py` after editing the dataset.

Bump `dataset_version` in `src/schemaport/data/dataset.json` when the dataset
changes in a way that could change a report. Every finding carries that version,
which is what makes an old report interpretable.

### Citing a pinned artifact

When the provider publishes something machine-readable — an OpenAPI document,
generated SDK types — cite that instead of prose. Set the source kind to
`published_artifact` and pin it:

```json
"artifact": {
  "url": "https://.../{revision}/openapi.yaml",
  "revision": "<full commit sha>",
  "tracks": "master",
  "expect": ["the", "literal", "strings", "the claim depends on"]
}
```

`expect` is what makes the claim re-checkable, so it has to name strings that
would actually disappear if the claim stopped holding. The loader rejects a URL
without `{revision}`, an empty `expect`, and a `verified` model the `expect`
list does not cover.

Then run the verifier before you commit — it is the only thing here that uses
the network, and it is not part of the package:

```bash
python tools/verify_contract_data.py
```

A weekly workflow runs the same script and opens an issue on drift. When it
fires, re-read the source before editing: a vanished string can mean the
contract changed, or only that the document was reorganised. Correct the record
and bump `dataset_version` — do not repin without reading.

### Promoting a record to `observed`

`observed` requires a reproducible probe artifact: the model identifier, the API
version where applicable, the request shape exercised, the outcome, and the
date. Built from synthetic request material — never from real user prompts, tool
outputs, or production request bodies. Provenance describes what was observed,
not how the endpoint was reached; keys, tokens, endpoints, and account
identifiers never enter the dataset.

There is a test asserting this release ships no `observed` records. If you land
a genuine probe artifact, update that test and the manifest's
`evidence_position` in the same change.

## Adding a provider surface

A new request layout needs one adapter in `src/schemaport/shapes.py` — a
function that locates schemas, tool definitions, and ordered content segments in
that layout and returns a `RequestView`. Register it in `_ADAPTERS`, then write
profiles that name it in `request_shape`. No analyzer should need to change.

## Things that are out of scope

The boundary in [docs/architecture.md](docs/architecture.md) is a design
decision, not a backlog. Proposals that make Schemaport send requests, proxy
traffic, wrap an SDK, observe responses, or require a network call for a check
are not going to be accepted, because every one of them breaks the property the
CI and agent-loop use cases depend on.

## Before a release

Follow the release checklist in the publishing instructions. Two items are
specific to this repository and easy to miss:

- Re-read the accuracy claims in `README.md` and `docs/` against what the
  dataset actually contains. The confidence-level coverage statements are
  specific, and they go stale the moment the dataset changes — in particular
  the claim that this release ships no `observed` records.
- Bump `dataset_version` if the dataset changed, and check that the manifest's
  `evidence_position` still describes what is actually in it.
