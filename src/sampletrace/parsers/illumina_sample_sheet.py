"""Parse Illumina sample sheets — both v1 (bcl2fastq) and v2 (BCL Convert).

v1 layout:

    [Header]
    IEMFileVersion,4
    ...
    [Reads]
    151
    151
    [Settings]
    ...
    [Data]
    Sample_ID,Sample_Name,index,index2,Sample_Project,...
    S001,Sample001,ACGTACGT,TGCATGCA,proj1,...

v2 layout (BCL Convert):

    [Header]
    FileFormatVersion,2
    ...
    [BCLConvert_Settings]
    ...
    [BCLConvert_Data]
    Sample_ID,Index,Index2,...
    S001,ACGTACGT,TGCATGCA,...

We auto-detect the format by which ``[*_Data]`` section we find. Column
naming varies even within a "version" (Sample_ID vs SampleID; index vs Index
vs I7_Index_ID + its actual sequence in a separate column), so we normalize
case-insensitively and fall back gracefully.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from sampletrace.schemas import CanonicalSample, SampleSource

# Aliases for the column names we care about. Keys are the canonical field
# names we'll set on CanonicalSample; values are the column-header strings
# we'll accept (matched case-insensitively, whitespace/underscore stripped).
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "sample_id": ("sample_id", "sampleid", "sample"),
    "sample_name": ("sample_name", "samplename"),
    "project_id": ("sampleproject", "project", "projectid", "sampleprojectid"),
    "index_i7": ("index", "index1", "i7_index", "i7indexsequence", "indexsequence"),
    "index_i5": ("index2", "i5_index", "i5indexsequence"),
    "lane": ("lane",),
}

_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*$")


def _norm(s: str) -> str:
    return s.strip().lower().replace("_", "").replace(" ", "")


def _find_data_section(lines: list[str]) -> tuple[int, str] | None:
    """Return (line index of first data row, section name) or None."""
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m and m.group(1).lower().endswith("data"):
            return i + 1, m.group(1)
    return None


def _build_column_map(header: list[str]) -> dict[str, int]:
    """Map canonical field name -> column index in this sheet.

    Both sides are passed through ``_norm`` so the alias table can be written
    however is most readable; matching is always case-insensitive and ignores
    underscores/whitespace.
    """
    norm_headers = [_norm(h) for h in header]
    result: dict[str, int] = {}
    for canonical, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            norm_alias = _norm(alias)
            if norm_alias in norm_headers:
                result[canonical] = norm_headers.index(norm_alias)
                break
    return result


def parse_sample_sheet(path: Path | str) -> list[CanonicalSample]:
    """Parse an Illumina sample sheet (v1 or v2) into canonical samples.

    Raises:
        ValueError: if no recognizable Data section or no sample_id column.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    return _parse_sample_sheet_text(text, source_path=str(path))


def _parse_sample_sheet_text(text: str, source_path: str = "<inline>") -> list[CanonicalSample]:
    lines = text.splitlines()
    section = _find_data_section(lines)
    if section is None:
        raise ValueError("no [*_Data] section found — is this an Illumina sample sheet?")

    data_start, section_name = section
    # Header row is the first non-empty line of the data section.
    header_idx = data_start
    while header_idx < len(lines) and not lines[header_idx].strip():
        header_idx += 1
    if header_idx >= len(lines):
        raise ValueError(f"[{section_name}] section has no header row")

    reader = csv.reader(io.StringIO("\n".join(lines[header_idx:])))
    rows = list(reader)
    if not rows:
        return []
    header_row = rows[0]
    col_map = _build_column_map(header_row)
    if "sample_id" not in col_map:
        raise ValueError(f"could not find sample id column in {section_name} headers: {header_row}")

    samples: list[CanonicalSample] = []
    for row in rows[1:]:
        if not row or not any(c.strip() for c in row):
            continue
        # Stop at the next section marker (rare but happens).
        if _SECTION_RE.match(row[0]):
            break
        # Pad short rows so the index lookups don't crash.
        if len(row) < len(header_row):
            row = row + [""] * (len(header_row) - len(row))

        extras: dict[str, Any] = {
            "section": section_name,
            "source_path": source_path,
        }
        kwargs: dict[str, Any] = {
            "source": SampleSource.SAMPLE_SHEET,
            "extra": extras,
        }
        for field, idx in col_map.items():
            raw = row[idx].strip() if idx < len(row) else ""
            if not raw:
                continue
            if field == "lane":
                try:
                    kwargs[field] = int(raw)
                except ValueError:
                    # Lane column with "all" or similar; record in extras.
                    extras["lane_raw"] = raw
                continue
            kwargs[field] = raw

        # sample_id is required by the schema; we asserted col_map has it.
        if "sample_id" not in kwargs or not kwargs["sample_id"]:
            continue

        samples.append(CanonicalSample(**kwargs))
    return samples
