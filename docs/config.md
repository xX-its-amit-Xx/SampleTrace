# Configuration schema

SampleTrace is configured via a single YAML file (conventionally `bch.yml`)
plus environment variables for secrets. The YAML has three top-level
sections — only `benchling` is strictly required.

## Full example

```yaml
benchling:
  tenant_url: https://yourtenant.benchling.com
  api_key: null                            # read from BENCHLING_API_KEY instead
  schema_id: ts_xxxxxxxxxxxx               # custom-entity schema to query
  project_id: src_xxxxxxxxxxxx             # optional: restrict to one project
  mock: false                              # set true to use bundled mock data
  mock_data_path: null                     # optional: custom JSON for mock mode

schema_mapping:
  # canonical_field_name: "Benchling field name as shown in the schema"
  sample_id: "Sample ID"
  library_id: "Library ID"
  index_i7: "i7 index"
  index_i5: "i5 index"
  organism: "Organism"
  tissue: "Tissue"
  sample_type: "Sample Type"
  project_id: "Sample Project"

reconciliation:
  fuzzy_threshold: 80          # below this score: no match (default 80)
  high_confidence: 95          # at or above: green (default 95)
  require_index_match: true    # treat index drift as a flag (default true)
```

## `benchling:` section

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_url` | str | unless mock | Full URL, e.g. `https://example.benchling.com` |
| `api_key` | str | unless mock | Prefer env var `BENCHLING_API_KEY` |
| `schema_id` | str | recommended | Benchling schema ID for sample registrations |
| `project_id` | str | optional | Restrict to one Benchling project |
| `mock` | bool | optional | If true, skip Benchling and use bundled synthetic data |
| `mock_data_path` | path | optional | Custom JSON fixture (see [mock data format](#mock-data-format)) |

**Secrets:** the `BENCHLING_API_KEY` environment variable always wins over
`api_key:` in the YAML. Keep secrets out of version control.

## `schema_mapping:` section

A dict mapping our canonical field names to your Benchling schema's field
names. Defaults are sensible for tenants that follow Benchling's standard
NGS library template; override any keys for your specific schema.

Canonical fields available for mapping:

| Canonical | Type | Used by reconciler? |
|---|---|---|
| `sample_id` | str | yes (matching key) |
| `library_id` | str | drift check |
| `index_i7` / `index_i5` | str (ACGTN) | drift check |
| `organism` | str | drift check |
| `tissue` | str | drift check |
| `sample_type` | str | drift check |
| `project_id` | str | drift check |
| `sample_name` | str | metadata only |

Any unmapped Benchling fields are preserved in the per-sample `extra` dict
so they appear in the JSON provenance file for auditing.

## `reconciliation:` section

Controls the matching / drift thresholds.

| Field | Default | Meaning |
|---|---|---|
| `fuzzy_threshold` | 80 | rapidfuzz token_sort_ratio below this is treated as no match |
| `high_confidence` | 95 | At or above, fuzzy match is treated as `HIGH` (green) |
| `require_index_match` | true | If true, index drift counts as a `METADATA_DRIFT` flag |

Tuning guidance:

- Lab using strict sample-ID schemes (e.g. `S001_ctrl`): defaults are fine.
- Lab with informal IDs (`SampleA-rev2`, `sampleA_rev2`): consider lowering
  `fuzzy_threshold` to 75 and raising `high_confidence` to 97 — accept more
  fuzzy matches but make sure they're really good ones.

## Mock data format

Default mock data lives at `src/sampletrace/_mock_data/benchling_entities.json`.
Custom mocks must follow the same shape:

```json
{
  "schema_id": "ts_yourmock001",
  "entities": [
    {
      "id": "bfi_synthetic0001",
      "name": "S001_ctrl",
      "schema_id": "ts_yourmock001",
      "fields": {
        "Sample ID": "S001_ctrl",
        "Library ID": "LIB_001",
        "Organism": "Homo sapiens",
        "...": "..."
      }
    }
  ]
}
```

The `fields` keys should match the Benchling-side names in your
`schema_mapping`. The top-level `entities` list is required; everything else
is informational.

## Extending for custom registration schemas

If your Benchling schema has fields outside our canonical set (e.g.
`Donor ID`, `Passage Number`), simply add them in `schema_mapping`:

```yaml
schema_mapping:
  donor_id: "Donor ID"          # extra fields land in the canonical extras bag
  passage_number: "Passage Number"
```

These will appear under each sample's `extra` dict in the JSON provenance
output, and you can reference them in your own downstream code via
`sample.extra["Donor ID"]`. They won't be drift-checked unless you fork the
reconciler to add them to `ReconcilerConfig.drift_fields`.
