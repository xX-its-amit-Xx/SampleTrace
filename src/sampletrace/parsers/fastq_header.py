"""Parse Illumina FASTQ headers and filenames into ``CanonicalSample`` records.

Standard Illumina FASTQ header (CASAVA 1.8+):

    @<instrument>:<run>:<flowcell>:<lane>:<tile>:<x>:<y> <read>:<filtered>:<control>:<index>

The sample identifier is *not* in the header itself — it's encoded in the
filename, e.g. ``S001_S1_L001_R1_001.fastq.gz`` where ``S001`` is the sample
name from the sample sheet. We pull both: filename gives sample ID + read
metadata; header gives flowcell, lane, and observed index.
"""

from __future__ import annotations

import gzip
import io
import re
from pathlib import Path
from typing import IO

from sampletrace.schemas import CanonicalSample, SampleSource

# Illumina filename convention: <SampleName>_S<n>_L<lane>_R<read>_001.fastq[.gz]
# Some pipelines drop the _L and _R parts; allow that too.
_FILENAME_RE = re.compile(
    r"^(?P<sample>.+?)_S(?P<snum>\d+)"
    r"(?:_L(?P<lane>\d{3}))?"
    r"(?:_R(?P<read>[12]))?"
    r"(?:_001)?"
    r"\.fastq(?:\.gz)?$",
    re.IGNORECASE,
)

# CASAVA 1.8+ header. Groups: instrument, run, flowcell, lane, tile, x, y,
# read, filtered, control, index (index may be dual: "ACGT+ACGT").
_HEADER_RE = re.compile(
    r"^@(?P<instrument>[^:]+)"
    r":(?P<run>\d+)"
    r":(?P<flowcell>[^:]+)"
    r":(?P<lane>\d+)"
    r":(?P<tile>\d+)"
    r":(?P<x>\d+)"
    r":(?P<y>\d+)"
    r"(?:\s+(?P<read>[12])"
    r":(?P<filtered>[YN])"
    r":(?P<control>\d+)"
    r":(?P<index>[ACGTN+]*))?$"
)


def _open_fastq(path: Path) -> IO[str]:
    """Open a fastq or fastq.gz file as text."""
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="ascii", errors="replace")
    return path.open("r", encoding="ascii", errors="replace")


def _parse_filename(path: Path) -> dict[str, str | int | None]:
    """Extract sample id, S-number, lane, read from an Illumina-style filename."""
    m = _FILENAME_RE.match(path.name)
    if not m:
        return {"sample_id": path.stem.split(".")[0], "s_number": None, "lane": None, "read": None}
    return {
        "sample_id": m["sample"],
        "s_number": int(m["snum"]),
        "lane": int(m["lane"]) if m["lane"] else None,
        "read": int(m["read"]) if m["read"] else None,
    }


def _parse_header_line(line: str) -> dict[str, str | int | None]:
    """Extract flowcell, lane, indices from one CASAVA 1.8 header line.

    Returns empty dict for headers we don't recognize so the caller can still
    produce a sample record with just filename-derived metadata.
    """
    m = _HEADER_RE.match(line.rstrip("\n"))
    if not m:
        return {}
    out: dict[str, str | int | None] = {
        "instrument": m["instrument"],
        "run_id": m["run"],
        "flowcell_id": m["flowcell"],
        "lane": int(m["lane"]),
    }
    index = m["index"]
    if index:
        if "+" in index:
            i7, i5 = index.split("+", 1)
            out["index_i7"] = i7
            out["index_i5"] = i5
        else:
            out["index_i7"] = index
    return out


def parse_fastq_header(path: Path) -> CanonicalSample:
    """Parse a single FASTQ file's first header + filename into a sample.

    Only reads the first read; we don't need more than that to identify the
    sample, and FASTQs can be huge.
    """
    path = Path(path)
    fname_info = _parse_filename(path)
    with _open_fastq(path) as fh:
        first = fh.readline()
    header_info = _parse_header_line(first)

    sample_id = str(fname_info["sample_id"])
    extra: dict[str, str | int | None] = {}
    if fname_info["s_number"] is not None:
        extra["s_number"] = fname_info["s_number"]
    if fname_info["read"] is not None:
        extra["read"] = fname_info["read"]
    if "instrument" in header_info:
        extra["instrument"] = header_info["instrument"]
    extra["source_path"] = str(path)

    return CanonicalSample(
        sample_id=sample_id,
        source=SampleSource.FASTQ_HEADER,
        lane=header_info.get("lane") if header_info.get("lane") is not None else fname_info.get("lane"),  # type: ignore[arg-type]
        flowcell_id=header_info.get("flowcell_id"),  # type: ignore[arg-type]
        run_id=header_info.get("run_id"),  # type: ignore[arg-type]
        index_i7=header_info.get("index_i7"),  # type: ignore[arg-type]
        index_i5=header_info.get("index_i5"),  # type: ignore[arg-type]
        extra=extra,
    )


def parse_fastq_directory(directory: Path) -> list[CanonicalSample]:
    """Walk a directory of FASTQs and parse one record per sample.

    Multiple FASTQs per sample (R1+R2, multiple lanes) are collapsed: we keep
    one ``CanonicalSample`` per unique ``sample_id`` and let the first one win.
    The reconciler doesn't need duplicates and the report would be noisy.
    """
    directory = Path(directory)
    seen: dict[str, CanonicalSample] = {}
    # Sort so output is deterministic across platforms.
    paths = sorted(
        p
        for p in directory.rglob("*.fastq*")
        if p.suffix in (".fastq", ".gz") and p.is_file()
    )
    for p in paths:
        try:
            sample = parse_fastq_header(p)
        except OSError:
            continue
        seen.setdefault(sample.sample_id, sample)
    return list(seen.values())
