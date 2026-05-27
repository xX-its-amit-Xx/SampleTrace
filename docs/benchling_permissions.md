# Benchling permissions model

SampleTrace is **read-only** with respect to Benchling. It never writes,
updates, or deletes anything in your tenant. The reconciliation report lives
entirely outside Benchling — on disk, in your CI artifacts, in your
dashboard. This document tells you exactly which Benchling permissions the
tool needs and why.

## Required permissions

For the API key SampleTrace uses, the bound user (or service account) needs:

| Permission | Why |
|---|---|
| `READ` on the **sample registration schema** (custom-entity schema) | To call `custom_entities.list()` |
| `READ` on the **project(s)** containing your samples | To filter by `project_id` if you set one |
| **No write permissions** | SampleTrace never mutates |

That's it. SampleTrace does not need admin, schema-editor, or any project-write
permissions.

## Recommended setup

1. **Create a service account in Benchling** rather than using a human user's
   key. This keeps the audit trail clean (`benchling_entity_id` in the
   provenance JSON ties back to who pulled the data) and avoids credential
   sprawl when the human leaves the team.
2. **Scope the service account to a single Benchling project** containing only
   the schema you want SampleTrace to read. If you have multiple NGS projects,
   one service account per project is cleaner than one with broad access.
3. **Rotate the API key** on the same cadence as the rest of your tenant
   secrets (typically quarterly).

## How SampleTrace handles the key

- The key is read from the `BENCHLING_API_KEY` environment variable in
  preference to the YAML config. This keeps it out of version control by
  default.
- The key is **never logged**, even with `--verbose`. Log lines reference the
  tenant URL and entity counts only.
- The key is **never written** to any output file (HTML report, Markdown
  report, CSV, JSON provenance). Inspect `sample_provenance.json` after a run
  if you want to verify.
- The key is **never sent** anywhere besides the configured `tenant_url`. The
  `benchling-sdk` is the only HTTP client used and goes only to Benchling.

## What ends up in the report

The provenance JSON contains the following Benchling-derived data per sample:

- The entity ID (`bfi_...`)
- The schema ID (`ts_...`)
- All fields you mapped in `schema_mapping` (sample_id, library_id, indices,
  organism, etc.)
- Any unmapped fields, captured into the per-sample `extra` dict

If your Benchling schema has fields you do **not** want appearing in the
provenance file (e.g. PHI, contractual confidential metadata), either:

- Drop those fields from your Benchling schema, or
- Fork `benchling_client._map_entity` to strip them at ingest, or
- Restrict the API key so it can't read them in the first place (preferred).

## API rate limits

SampleTrace makes one paginated `list` call per reconciliation run. For a
typical NovaSeq run of ~200 samples, expect 1-3 API calls total. This is
well below any Benchling rate limit and you can run it in tight CI loops
without throttling.

## Audit trail on the Benchling side

The bound user/service account will appear in Benchling's audit log as having
performed `READ` operations on the custom-entity schema each time SampleTrace
is invoked. The number of audit entries equals the number of paginated API
calls (not the number of samples). This is intentional — your Benchling
admins should be able to see when SampleTrace ran without digging.
