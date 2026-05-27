"""Parsers that turn raw NGS artifacts into ``CanonicalSample`` lists.

Each parser is independent and returns ``list[CanonicalSample]`` so the
reconciler can treat them uniformly regardless of source.
"""

from sampletrace.parsers.count_matrix import parse_count_matrix
from sampletrace.parsers.fastq_header import parse_fastq_directory, parse_fastq_header
from sampletrace.parsers.illumina_sample_sheet import parse_sample_sheet

__all__ = [
    "parse_count_matrix",
    "parse_fastq_directory",
    "parse_fastq_header",
    "parse_sample_sheet",
]
