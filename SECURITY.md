# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's private reporting:
<https://github.com/Hotragn/schemaport/security/advisories/new>. Do not open a
public issue for a security report.

Please include what you tried, what happened, and what you expected. If it
involves a request body, send a synthetic one: do not include real prompts,
tool outputs, credentials, or production request content in a report.

Expect an acknowledgement while the report is triaged, a fix or an explanation
of why it is not one, and a coordinated disclosure once a release is available.

## Supported versions

While on `0.y.z`, only the latest released version is supported. Fixes ship as a
new release; a published artifact is never replaced.

## What Schemaport does with your data

Worth stating plainly, because the tool reads request bodies that frequently
contain sensitive content:

- **It makes no network connection.** No code path opens a socket. There is no
  telemetry, no update check, no analytics, and no provider call. The test suite
  asserts this by disabling sockets and running a check.
- **It does not need credentials.** No API key, token, or provider account is
  read from the environment, a config file, or anywhere else.
- **It does not persist anything.** The request file is read and never written
  back. No cache, log, temporary copy, or state directory is created.
- **The report contains excerpts of your request.** Findings quote short
  fragments of the content they matched — a tool name, a timestamp, a property
  name — so that they are actionable. Treat a report with the same care as the
  request it describes, especially before attaching one to a bug report or
  uploading SARIF to a code-scanning service.

## Reporting a contract-data error

An incorrect contract record is a correctness bug rather than a vulnerability,
and it belongs in a normal issue. Include the `rule_id`, the
`contract_dataset_version` from the report, the model you targeted, and what the
provider actually does. A record that cannot be reproduced is corrected or
removed with the dataset version bumped.
