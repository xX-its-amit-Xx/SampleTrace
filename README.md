# SampleTrace

> Sample swaps are the most embarrassing way to lose three months of work. SampleTrace catches them at intake.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**SampleTrace** is a Benchling-linked NGS metadata reconciliation tool. It pulls
sample registrations from Benchling, parses your sequencing run artifacts
(Illumina sample sheets, FASTQ headers, count matrices), and reconciles them
against the Benchling source of truth — producing a single-file HTML report
with traffic-light per-sample status.

If something is mis-labeled, swapped, or drifted between wet-lab registration
and the sequencer output, you find out *before* you've burned a week of
alignment compute and slotted the wrong data into a slide.

---

## Why this exists

Sample-level metadata is the connective tissue of an NGS run. It also rots
silently: a tech transposes two digits in a sample sheet, an index pair gets
copy-pasted off-by-one, a project ID gets relabeled mid-run because someone
"fixed" it in their local copy. None of those errors crash anything. They all
surface three months later when a reviewer asks why the control sample looks
exactly like the treated one.

SampleTrace runs at intake — between the sequencer dropping FASTQs and the
aligner picking them up — and refuses to let mismatched metadata flow
downstream silently.

## Quickstart

```bash
pip install -e ".[benchling,dashboard]"

sampletrace reconcile \
    --benchling-config bch.yml \
    --sample-sheet SampleSheet.csv \
    --fastq-dir /data/run123/ \
    --output-dir reports/
```

Outputs land in `reports/`:

- `reconciliation_report.html` — traffic-light dashboard, one self-contained file
- `reconciliation_report.md` — same content, Markdown for PR/Slack
- `mismatches.csv` — flagged samples only, for triage
- `sample_provenance.json` — full audit trail

### Mock mode (no Benchling credentials required)

```bash
sampletrace reconcile \
    --mock \
    --sample-sheet examples/data/SampleSheet_v2.csv \
    --fastq-dir examples/data/fastq/ \
    --output-dir /tmp/demo-report/
```

Mock mode loads synthetic Benchling responses from
`src/sampletrace/_mock_data/`. Useful for CI, demos, and local development
before you have a Benchling tenant wired up.

## Configuration

The fastest way to get going with a real Benchling tenant:

```bash
sampletrace configure --tenant-url https://yourtenant.benchling.com
# stores your API key in the OS keyring; writes a sanitized bch.yml template

sampletrace verify-auth --benchling-config bch.yml
# confirms the key + tenant + schema_id all work without ingesting data
```

The key is read at runtime via the documented precedence
(`BENCHLING_API_KEY` env var → OS keyring → `.env` → YAML). It's never
echoed back, never logged, never written to any output file.
See [docs/credentials.md](docs/credentials.md) for the full credential
story across laptop / CI / Docker / k8s.

Sample `bch.yml`:

```yaml
benchling:
  tenant_url: https://yourtenant.benchling.com
  api_key: null                     # resolved at runtime; DO NOT commit a key here
  schema_id: ts_xxxxxxxxxxxx        # Sample registration schema
  project_id: src_xxxxxxxxxxxx      # Optional: filter to one project

# Map Benchling schema field names -> canonical SampleTrace field names.
schema_mapping:
  sample_id: "Sample ID"
  library_id: "Library ID"
  index_i7: "i7 index"
  index_i5: "i5 index"
  organism: "Organism"
  tissue: "Tissue"
  sample_type: "Sample Type"

reconciliation:
  fuzzy_threshold: 80      # below this, treat as no match
  high_confidence: 95      # at or above, treat as green
  require_index_match: true
```

Required schema fields: only `sample_id`. Everything else is optional and only
checked when present on both sides.

See [docs/config.md](docs/config.md) for the full schema and how to extend
for custom registration schemas.

## Outputs

### `reconciliation_report.md` (excerpt)

```
# Reconciliation report — run123
Generated: 2026-05-26 19:14 UTC
Total samples: 48 — 44 green, 2 yellow, 2 red, 4 flagged

| Sample      | Confidence | Sources                      | Issues               |
|-------------|------------|------------------------------|----------------------|
| S001_ctrl   | exact      | benchling, sheet, fastq      | —                    |
| S002_ctrl   | exact      | benchling, sheet, fastq      | —                    |
| S047_trt    | low        | sheet, fastq                 | missing_from_benchling |
| S048_trt    | medium     | benchling, sheet (S048-trt)  | ambiguous_fuzzy_match |
```

### `mismatches.csv` (sketch)

```csv
sample_id,confidence,mismatches,sources_present,fuzzy_score,notes
S047_trt,none,missing_from_downstream,"benchling",,sample sheet missing this Benchling-registered sample
S048_trt,medium,ambiguous_fuzzy_match,"benchling,sample_sheet",87.0,"sheet has S048-trt, matched to Benchling S048_trt"
```

## CLI vs dashboard

| Use case | Tool |
|---|---|
| CI / nightly automation, fail-the-build-on-mismatch | `sampletrace reconcile` |
| Interactive triage after a run finishes | `sampletrace dashboard` |
| Embedding in your own pipeline | `from sampletrace import ...` |

The dashboard is optional; install with `pip install -e ".[dashboard]"` and run
`sampletrace dashboard --report reports/reconciliation_report.json`.

## Development

```bash
pip install -e ".[dev]"

pytest                     # full suite, no credentials needed
ruff check .               # lint
mypy                       # types
pytest --cov               # coverage report
```

All tests use synthetic fixtures and mock-mode Benchling responses — you can
run the suite on a fresh laptop without ever touching a real Benchling tenant.

## Docker

```bash
docker compose up reconcile
```

See [docker-compose.yml](docker-compose.yml) and [docker/Dockerfile](docker/Dockerfile).

## Documentation

- [docs/config.md](docs/config.md) — full configuration schema
- [docs/credentials.md](docs/credentials.md) — where to put your Benchling API key (laptop, CI, Docker, k8s) and what NOT to do
- [docs/benchling_permissions.md](docs/benchling_permissions.md) — what Benchling permissions SampleTrace needs and why
- [docs/threat_model.md](docs/threat_model.md) — categories of metadata drift this tool is designed to catch (and what it can't catch)
- [examples/cookbook.md](examples/cookbook.md) — runnable end-to-end use cases (including a real-Benchling laptop setup walkthrough)

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
