# Example: agent preflight in a shell orchestrator

A minimal end-to-end flow: render a request, check it, repair what the findings
point at, check again, and only then dispatch — from your own application, with
your own client.

Schemaport never sends the request. It reads a file and writes a report.

Uses `jq` for JSON handling. The model below is one the bundled dataset covers;
run `schemaport profiles` to see the rest.

## 1. Write the rendered request

The input is the request body itself — the JSON you were about to hand to the
provider.

```bash
cat > request.json <<'JSON'
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
JSON
```

This model is covered on more than one API surface. The fields above identify it
as a Responses API request, so Schemaport resolves the profile without being
told. Add `--surface openai.responses` if you want it pinned explicitly.

## 2. Check it

```bash
schemaport check request.json --model gpt-5.6-sol --format json --fail-on warning > report.json
```

The exit status is non-zero when a finding at or above `warning` is present, so
under `set -e` you need to capture it rather than let it abort the script:

```bash
status=0
schemaport check request.json --model gpt-5.6-sol --format json --fail-on warning > report.json || status=$?
```

Distinguish the two non-zero codes. `1` means the request has findings and the
loop should repair. `2` means nothing was checked — bad path, invalid JSON, or a
model with no profile — and repairing is pointless:

```bash
if [ "$status" -eq 2 ]; then
  echo "preflight: could not check the request" >&2
  exit 2
fi
```

## 3. Parse the findings

Each finding carries a `rule_id`, a `path` into the request, and a
`remediation`:

```bash
jq -r '.findings[] | "\(.severity)\t\(.rule_id)\t\(.path)\t\(.remediation)"' report.json
```

To act only on findings the dataset backs with documentation or observation,
filter on `confidence` — `--fail-on` gates on severity, not confidence:

```bash
jq -r '.findings[] | select(.confidence != "experimental") | .rule_id' report.json
```

## 4. Repair

Repairs are applied by you, never by Schemaport. `path` tells you where. Two of
the findings land on the same object — it is missing `additionalProperties` and
has an incomplete `required` array — and the third points at an `allOf` that has
to collapse into the schema it was describing:

```bash
jq '.text.format.schema |= (
      .additionalProperties = false
      | .properties.status = {"type": "string", "enum": ["open", "closed"]}
      | .required = (.properties | keys)
    )' request.json > request.repaired.json && mv request.repaired.json request.json
```

In a real orchestrator this step is your repair routine — a lookup keyed on
`rule_id`, a model-authored edit, or a human. What matters is that the edit lands
at `path` in the same document you checked.

## 5. Re-check

```bash
schemaport check request.json --model gpt-5.6-sol --format json --fail-on warning > report.json
```

A repair can introduce a new finding, so re-check after every round rather than
once at the end.

## 6. Dispatch

Only when the check is clean. This is your application's own provider call —
Schemaport has no part in it:

```bash
./send_request.sh request.json
```

## Putting it together

```bash
#!/usr/bin/env bash
set -euo pipefail

MODEL="gpt-5.6-sol"
REQUEST="request.json"
REPORT="report.json"
MAX_ATTEMPTS=3

render_request "$REQUEST"

attempt=0
while : ; do
  status=0
  schemaport check "$REQUEST" --model "$MODEL" --format json --fail-on warning \
    > "$REPORT" || status=$?

  if [ "$status" -eq 0 ]; then
    break
  fi

  if [ "$status" -eq 2 ]; then
    echo "preflight: the request could not be checked at all" >&2
    exit 2
  fi

  attempt=$((attempt + 1))
  if [ "$attempt" -gt "$MAX_ATTEMPTS" ]; then
    echo "preflight: findings remain after $MAX_ATTEMPTS repair attempts" >&2
    jq -r '.findings[] | "\(.severity) \(.rule_id) at \(.path)"' "$REPORT" >&2
    exit 1
  fi

  # Your repair routine: reads the findings, edits the node at each `path`.
  repair_request "$REQUEST" "$REPORT"
done

# Clean. Dispatch from your own application, with your own client.
send_request "$REQUEST"
```

`render_request`, `repair_request`, and `send_request` are yours. The loop is
bounded on purpose: not every finding is machine-repairable, and a finding that
survives a repair means the edit did not address it.

## Notes

- Nothing in steps 1 through 5 touches the network. The whole preflight runs in
  a sandbox with no egress and no credentials.
- The check is deterministic, so an unchanged finding across two runs is proof
  the request did not change in the relevant way — break the loop rather than
  spending another attempt.
- `--fail-on warning` gates on severity only. To treat `experimental` findings
  differently from `documented` ones, filter the report on the `confidence`
  field. See [../docs/contract-data.md](../docs/contract-data.md).
- A clean report means the request conforms to the resolved profile as far as the
  bundled contract data goes. Keep your normal error handling on the provider
  call.

Two runnable request bodies ship under
[`requests/`](requests/): the structured-output example used above, and
[an Anthropic agent turn](requests/anthropic-cached-agent-turn.json) with a
timestamp inside its cached prefix, which is what the cache-safety heuristics
are for.

For the loop in more detail, see
[../docs/agent-integration.md](../docs/agent-integration.md).
