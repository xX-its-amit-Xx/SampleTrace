"""Parse count matrix column headers into ``CanonicalSample`` records.

Count matrices come out of every quantification step in NGS analysis
(featureCounts, RSEM, Salmon-merged, cellranger aggr, etc.). The shape is
always rows = features, columns = samples. We only need the *column labels* —
they tell us which samples actually made it through quantification.

We don't read the matrix values, just the header row. This is fast even for
multi-GB matrices.
"""

from __future__ import annotations

import csv
from pathlib import Path

from sampletrace.schemas import CanonicalSample, SampleSource

# Common non-sample columns we should skip when present at the start.
_FEATURE_COLUMNS = frozenset({
    "",  # blank corner cell
    "gene", "gene_id", "geneid", "gene_name", "genename", "symbol",
    "feature", "feature_id", "featureid",
    "transcript", "transcript_id", "transcriptid",
    "ensembl_id", "ensembl",
    "chr", "chrom", "start", "end", "strand", "length",
    "name", "id",
})


def _detect_delimiter(line: str) -> str:
    if "\t" in line:
        return "\t"
    return ","


def parse_count_matrix(path: Path | str) -> list[CanonicalSample]:
    """Read just the header row of a count matrix and emit one sample per column.

    The first few columns are typically gene/feature identifiers, not samples;
    we skip any leading column whose name matches a known feature column.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        first_line = fh.readline()
        if not first_line:
            return []
        delim = _detect_delimiter(first_line)
        reader = csv.reader([first_line], delimiter=delim)
        headers = next(reader)

    samples: list[CanonicalSample] = []
    seen_first_sample = False
    for col in headers:
        col_clean = col.strip()
        norm = col_clean.lower().replace(" ", "_")
        # Skip leading feature columns, but once we've started consuming samples
        # we trust every subsequent column is a sample (some pipelines repeat
        # ambiguous names downstream).
        if not seen_first_sample and norm in _FEATURE_COLUMNS:
            continue
        if not col_clean:
            continue
        seen_first_sample = True
        samples.append(
            CanonicalSample(
                sample_id=col_clean,
                source=SampleSource.COUNT_MATRIX,
                extra={"source_path": str(path)},
            )
        )
    return samples
