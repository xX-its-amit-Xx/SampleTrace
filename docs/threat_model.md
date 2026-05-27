# Threat model for metadata drift

This document is *not* a security threat model. It catalogues the categories
of **metadata-drift "threats"** SampleTrace is designed to catch — and
explicitly lists what it can't catch — so you can decide whether the tool
fits your wet-lab → bioinformatics handoff.

## In scope: what SampleTrace catches

### 1. Sample identifier mismatches

| Scenario | Detected as |
|---|---|
| Tech transposed digits in sample sheet (`S123` → `S132`) | `MISSING_FROM_DOWNSTREAM` on `S123`, `EXTRA_IN_DOWNSTREAM` on `S132` |
| Sample sheet has `S001-ctrl`, Benchling has `S001_ctrl` | Matched at HIGH confidence with note about normalization |
| Sample sheet has `S001A`, Benchling has `S001` | Fuzzy match at MEDIUM confidence — `AMBIGUOUS_FUZZY_MATCH` |
| Sample sheet has `MYCONTROL`, Benchling has `S001_ctrl` | `MISSING_FROM_DOWNSTREAM` + `EXTRA_IN_DOWNSTREAM` |

### 2. Index drift

| Scenario | Detected as |
|---|---|
| Sample sheet i7 has one base off vs Benchling | `METADATA_DRIFT` with note on the field |
| i5 missing from sheet but present in Benchling | Not flagged (one-sided drift is silent) |
| Sample sheet index_i7 has wrong adapter family | `METADATA_DRIFT` |

### 3. Organism / tissue / sample-type drift

| Scenario | Detected as |
|---|---|
| Benchling says `Homo sapiens`, sheet says `Mus musculus` | `METADATA_DRIFT` |
| Benchling says `PBMC`, sheet description says `Liver` | `METADATA_DRIFT` (if `Tissue` column is mapped) |
| Sample type changed (e.g. RNA vs DNA library) | `METADATA_DRIFT` |

### 4. Source-coverage gaps

| Scenario | Detected as |
|---|---|
| Sample registered but no FASTQ on disk | `MISSING_FROM_DOWNSTREAM` for FASTQ source |
| FASTQ on disk but never registered | `EXTRA_IN_DOWNSTREAM` row |
| Sheet has the sample but count matrix doesn't | `MISSING_FROM_DOWNSTREAM` for count matrix source |

### 5. Schema violations

| Scenario | Detected as |
|---|---|
| Sample sheet has malformed index (non-ACGTN chars) | Parse-time validation error, caller decides to halt |
| Required field absent on Benchling side | Sample skipped at ingest with log warning |

## Out of scope: what SampleTrace does **NOT** catch

This is at least as important as what it catches. Don't assume green means
"the data is right" — assume green means "the four things SampleTrace checks
agree with each other."

### Things invisible to metadata reconciliation

- **Two samples physically swapped in the same well of the same plate.**
  The sample sheet, FASTQ headers, and Benchling all say "S001 is in well A1"
  but actually the tech put S002 there. Genotype-based identity checks
  (e.g. `verifyBamID`, `somalier`) are the right tool here, not SampleTrace.
- **Cross-contamination between wells.** A different tool's job.
- **Wrong reference genome in alignment.** SampleTrace runs *before*
  alignment by design.
- **A FASTQ file that's empty, truncated, or has wrong-length reads.**
  SampleTrace reads the first header only; QC is a separate concern.
- **The Benchling registration was wrong in the first place.** If your tech
  registered `S001` as `Homo sapiens` when it was actually mouse, SampleTrace
  will happily call the run green when the sheet also says `Homo sapiens`.
  This is a wet-lab QA problem, not a reconciliation problem.

### Limits of fuzzy matching

- A fuzzy match above `high_confidence` is reported green without surfacing
  the underlying score. If your IDs are short (e.g. 4-character codes) you
  may want to set `high_confidence` higher than the default 95 because short
  strings hit token-sort-ratio thresholds easily.
- The reconciler doesn't currently consider semantic similarity (e.g.
  understanding that `S001_ctrl_rev2` is a logical rev of `S001_ctrl`). If
  you want that, add domain logic to a parser that normalizes the revision
  suffix before handing off to the reconciler.

### Things SampleTrace deliberately doesn't try to fix

- **Auto-correct ambiguous fuzzy matches.** These are surfaced for a human;
  silent auto-correction is the bug we're trying to prevent in the first
  place.
- **Halt the pipeline.** SampleTrace exits non-zero with `--fail-on-flagged`
  but doesn't try to inject a "do not align" sentinel into anything
  downstream. That's the caller's job.

## Failure modes of SampleTrace itself

| Mode | Mitigation |
|---|---|
| Benchling API down or slow | Tool will error explicitly; CI should treat this as failure, not green |
| Mock mode mistakenly enabled in prod | Banner in output ("[mock] using bundled synthetic Benchling data") |
| Schema mapping wrong → all samples look extra/missing | Run with `--verbose`, inspect the first few `_map_entity` outputs |
| Index validation rejects a sample with Ns | Adjust the regex in `schemas.CanonicalSample._validate_index` |

## Operational recommendations

1. Wire `sampletrace reconcile --fail-on-flagged` into your post-sequencing
   pipeline before any alignment step.
2. Archive `sample_provenance.json` per run alongside your sample sheet and
   sequencer metadata. It's the audit trail for what was reconciled and how.
3. Treat MEDIUM confidence as **block-on-human-review**, not as "probably
   fine." MEDIUM is what catches the sample swaps that look almost right.
4. Re-run reconciliation any time the sample sheet is edited mid-run.
   Sample sheet drift between sequencer setup and final demux is more
   common than people expect.
