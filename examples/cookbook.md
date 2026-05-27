# SampleTrace cookbook

Four end-to-end use cases you can run today. Every one of them works
**without a real Benchling tenant** — they use mock mode by default and
include a "real Benchling" toggle at the bottom of each recipe.

## Setup (once)

```bash
git clone https://github.com/yourorg/SampleTrace.git
cd SampleTrace
pip install -e ".[benchling,dashboard,dev]"
```

If you want to point at a real Benchling tenant for any of these, set:

```bash
export BENCHLING_API_KEY="sk_..."
```

and edit the `--mock` flag out / `--benchling-config` flag in.

> **Note on Benchling credentials:** Benchling does not currently offer
> public free-tier API access. The recipes below ship synthetic data so you
> can prove the workflows end-to-end without credentials. To run against
> real Benchling, you need either (1) an existing Benchling tenant your
> organization administers (typical case), or (2) coordinate with Benchling
> sales for a sandbox tenant. Set `BENCHLING_API_KEY` and adjust the
> `--mock` / `--benchling-config` flags as noted in each recipe.

---

## Recipe 1 — "Did anything go wrong on last night's NovaSeq run?"

**You are a bioinformatician at 9am. The run finished overnight. You want to
know in under 60 seconds whether you can start alignment or whether to walk
to the wet lab.**

```bash
sampletrace reconcile \
    --mock \
    --sample-sheet tests/fixtures/SampleSheet_v1.csv \
    --output-dir reports/lastnight/ \
    --run-id novaseq_2026_05_25 \
    --fail-on-flagged
echo "exit: $?"
```

Expected behavior:

- The mock Benchling data matches the v1 sample sheet exactly (same IDs,
  same indices) so this run is clean. Exit code 0.
- `reports/lastnight/reconciliation_report.html` is a single file you can
  open in a browser or attach to a JIRA ticket.

**Try the failing case:** swap to the v2 sample sheet — indices are longer
(10bp vs the mock's 8bp), which the reconciler flags as drift:

```bash
sampletrace reconcile --mock \
    --sample-sheet tests/fixtures/SampleSheet_v2.csv \
    --output-dir reports/lastnight_v2/ \
    --fail-on-flagged ; echo "exit: $?"
```

Exit code 2; `reports/lastnight_v2/mismatches.csv` lists every flagged sample.

**To use real Benchling:** drop `--mock`, add `-b your_tenant.yml`.

---

## Recipe 2 — "Reconcile a full run: Benchling + sheet + FASTQs + counts"

**End-to-end reconciliation across every available data source. Use this in
your pipeline definition once you have a FASTQ directory and quantification
output.**

```bash
# Simulate a FASTQ directory for the demo. Real runs will get these from
# BCL Convert; we hand-craft headers here so the indices match what the
# mock Benchling registration says.
mkdir -p /tmp/run123_fastq
write_fastq () {
  local sample="$1" idx7="$2" idx5="$3"
  printf '@NB551234:123:HWNCMBGXC:1:11101:1234:5678 1:N:0:%s+%s\nACGT\n+\nIIII\n' \
    "$idx7" "$idx5" \
    > /tmp/run123_fastq/${sample}_S1_L001_R1_001.fastq
}
write_fastq S001_ctrl ATTACTCG TATAGCCT
write_fastq S002_ctrl TCCGGAGA ATAGAGGC
write_fastq S003_trt  CGCTCATT CCTATCCT
write_fastq S004_trt  GAGATTCC GGCTCTGA

sampletrace reconcile \
    --mock \
    --sample-sheet tests/fixtures/SampleSheet_v1.csv \
    --fastq-dir /tmp/run123_fastq \
    --count-matrix tests/fixtures/counts.tsv \
    --output-dir reports/full_run/ \
    --run-id run123
```

What this produces:

- `reports/full_run/reconciliation_report.html` — green for all 4 samples
  because the mock, sheet, FASTQs, and count matrix all agree.
- `reports/full_run/sample_provenance.json` — full audit. Look at any one
  sample's `extra` field: you'll see the Benchling field values, the
  parsed sample-sheet section name, and the FASTQ source path.

**To use real Benchling:** drop `--mock`, point `--benchling-config` at your
`bch.yml`. Everything else stays identical.

---

## Recipe 3 — "Catch a sample swap before alignment"

**Simulate the exact failure SampleTrace is built to prevent: a sample whose
metadata has drifted between wet-lab registration and the run output.**

The bundled `tests/fixtures/benchling_with_drift.json` has two samples:
`S001_ctrl` (matches the sheet) and `S002_swap` (different organism + missing
from sheet).

```bash
# Drop the drift fixture into a temporary config that points at it
cat > /tmp/bch_drift.yml <<'YAML'
benchling:
  mock: true
  mock_data_path: tests/fixtures/benchling_with_drift.json
schema_mapping:
  sample_id: "Sample ID"
  library_id: "Library ID"
  index_i7: "i7 index"
  organism: "Organism"
  tissue: "Tissue"
YAML

sampletrace reconcile \
    --benchling-config /tmp/bch_drift.yml \
    --sample-sheet tests/fixtures/SampleSheet_v1.csv \
    --output-dir reports/swap_demo/ \
    --run-id swap_demo \
    --fail-on-flagged ; echo "exit: $?"
```

You'll see exit code 2 and a `mismatches.csv` containing:

- `S002_swap` — missing from sample sheet entirely
- `S002_ctrl`, `S003_trt`, `S004_trt` — present in sheet but unknown to
  Benchling (because the drift fixture has different samples)

Open `reports/swap_demo/reconciliation_report.html` to see the traffic-light
view; the notes column will show *why* each row is flagged.

**Where this catches a real swap:** if a tech accidentally swapped two
samples in the registration UI before the run, the Benchling-side ID and
the sample-sheet ID will differ. SampleTrace fails the run *before* you
spend 6 CPU-hours on alignment.

**To use real Benchling:** swap `mock: true` for your tenant config and
ensure your registered schema has a sample that's been changed since the
sheet was generated.

---

## Recipe 4 — "Nightly CI: fail the build if anything's flagged"

**A GitHub Actions step you can drop into the workflow that runs whenever a
new sequencing run lands in your data lake.**

`.github/workflows/post-sequencing.yml`:

```yaml
name: post-sequencing reconciliation
on:
  workflow_dispatch:
    inputs:
      run_id:
        required: true
      sheet_path:
        required: true
      fastq_dir:
        required: true

jobs:
  reconcile:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install sampletrace
      - name: Reconcile
        env:
          BENCHLING_API_KEY: ${{ secrets.BENCHLING_API_KEY }}
        run: |
          sampletrace reconcile \
            --benchling-config config/bch.yml \
            --sample-sheet ${{ github.event.inputs.sheet_path }} \
            --fastq-dir ${{ github.event.inputs.fastq_dir }} \
            --output-dir reports/${{ github.event.inputs.run_id }} \
            --run-id ${{ github.event.inputs.run_id }} \
            --fail-on-flagged
      - name: Upload reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: reconciliation-${{ github.event.inputs.run_id }}
          path: reports/${{ github.event.inputs.run_id }}/
```

To test the recipe locally without secrets, mock-mode it:

```bash
sampletrace reconcile --mock \
    --sample-sheet tests/fixtures/SampleSheet_v1.csv \
    --output-dir reports/ci_demo/ \
    --fail-on-flagged
```

The `--fail-on-flagged` flag is the contract between SampleTrace and your CI
system: exit 2 means at least one row needed human attention, and the build
should not proceed to expensive alignment.

---

## Interactive triage with the dashboard

After any of the above, launch the dashboard to drill into individual rows:

```bash
sampletrace dashboard --report reports/full_run/sample_provenance.json
# opens http://localhost:8501
```

Filter to "flagged only" in the sidebar, pick any sample from the drilldown
dropdown, and inspect the raw JSON for that row — including the matched
sample IDs from each source and any drift notes.
