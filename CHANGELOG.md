# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While on
`0.y.z`, breaking changes are documented here even before 1.0.

## Unreleased

Nothing yet.

## 0.1.0 — unreleased

Not yet tagged or published. Dates go in when the release is cut.

First public release.

### Added

- `schemaport check REQUEST --model MODEL` — reads a rendered request body and
  reports where it does not conform to the contract profile for that model.
- `schemaport profiles` — lists the bundled profiles, the models each covers,
  and the scope note for each.
- Report formats: `text`, `json`, and SARIF 2.1.0.
- `--fail-on {info,warning,error}` to choose the severity that exits non-zero.
- `--surface` to pick the API surface when a model is covered on more than one.
  Left unset, the request's own fields decide; an ambiguous request stops with a
  usage error rather than being checked against the wrong contract.
- Exit codes: `0` clean, `1` findings at or above the threshold, `2` the
  invocation or input could not be used.
- Request shape adapters for the Anthropic Messages API and the OpenAI Responses
  and Chat Completions APIs.
- Structured-output analyzers: root type, root-level forbidden keywords,
  `additionalProperties`, `required` completeness, unsupported keywords, nesting
  depth, whole-schema property and enum totals, combined identifier and literal
  string length, large-enum string length, enum value types, keyword value sets,
  external `$ref`, recursive schemas, strict-mode opt-in, and tool-name
  constraints.
- Cache-safety analyzers: volatile content inside a cached prefix, cache
  breakpoints beyond the accepted count, a prefix below the cacheability
  threshold, and a substantial stable prefix with no cache marker.
- Bundled contract dataset 0.1.0: six profiles over fifteen model identifiers,
  covering the Anthropic Messages API (tool contract, the strict tool-use schema
  subset, and four documented cache tiers) and the OpenAI Responses and Chat
  Completions APIs (the strict Structured Outputs subset and its limits). Every
  record names its provider, models, and API surface, and carries a dated
  provenance block citing the page it was transcribed from.
- Rule sets: profiles sharing a contract `extend` a common record set and
  `override` only what differs, so a claim has one place to be corrected.
- `model_scope` on every profile: the model list cites its own evidence, and
  reports whether each model is named in that evidence or covered by a version
  range. Surfaced per finding as `target.model_scope.named_in_evidence` and in
  `schemaport profiles`.
- `published_artifact` evidence kind: a record can cite a machine-readable
  provider document pinned to an exact revision, with the literal strings the
  claim depends on. `tools/verify_contract_data.py` re-fetches each pin and the
  ref it tracks and reports drift; a weekly workflow runs it and opens an issue.
  The tool is opt-in, networked, and outside the installed package.
- Python API: `check`, `check_file`, `analyze`, `load_dataset`, and the
  `Finding` / `Report` value types.
- Documentation: architecture, contract-data rules, agent integration, and a
  shell orchestrator example.
- CI running lint, format, types, and tests on Python 3.10–3.13, a job that
  drops egress and reruns the suite to enforce the offline guarantee, and a
  packaging job asserting the contract dataset ships inside the wheel. Release
  publishes on a version tag through PyPI Trusted Publishing.

### Notes on this release

- No `observed` contract records. The confidence level is defined and enforced,
  but promoting a record to it requires a reproducible probe artifact, and this
  release ships none. Every shipped record is `documented` — transcribed from
  provider documentation with a citation — or `experimental`, which here means a
  heuristic built on a documented mechanism, with the inference stated.
- No MCP server, adapter, or transport. An adapter is a plausible future
  addition; nothing in this release implements one.
- No runtime dependencies, and no network access under any code path.
