"""Tests for FASTQ header + filename parsing."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from sampletrace.parsers.fastq_header import parse_fastq_directory, parse_fastq_header
from sampletrace.schemas import SampleSource


def _fastq_content(
    instrument: str = "NB551234",
    run: int = 123,
    flowcell: str = "HWNCMBGXC",
    lane: int = 1,
    i7: str = "ATTACTCG",
    i5: str = "TATAGCCT",
) -> str:
    header = (
        f"@{instrument}:{run}:{flowcell}:{lane}:11101:1234:5678 "
        f"1:N:0:{i7}+{i5}\n"
    )
    return header + "ACGTACGTACGTACGT\n+\nIIIIIIIIIIIIIIII\n"


def _write_fastq(path: Path, content: str, gz: bool = False) -> Path:
    if gz:
        full = path.with_suffix(path.suffix + ".gz")
        with gzip.open(full, "wb") as fh:
            fh.write(content.encode("ascii"))
        return full
    path.write_text(content, encoding="ascii")
    return path


class TestParseFastqHeader:
    def test_plain_fastq(self, tmp_path: Path) -> None:
        path = _write_fastq(
            tmp_path / "S001_ctrl_S1_L001_R1_001.fastq",
            _fastq_content(),
        )
        sample = parse_fastq_header(path)
        assert sample.sample_id == "S001_ctrl"
        assert sample.source == SampleSource.FASTQ_HEADER
        assert sample.lane == 1
        assert sample.flowcell_id == "HWNCMBGXC"
        assert sample.index_i7 == "ATTACTCG"
        assert sample.index_i5 == "TATAGCCT"
        assert sample.run_id == "123"

    def test_gzipped_fastq(self, tmp_path: Path) -> None:
        path = _write_fastq(
            tmp_path / "S002_S2_L002_R1_001.fastq",
            _fastq_content(lane=2, i7="TCCGGAGA", i5="ATAGAGGC"),
            gz=True,
        )
        sample = parse_fastq_header(path)
        assert sample.sample_id == "S002"
        assert sample.lane == 2
        assert sample.index_i7 == "TCCGGAGA"

    def test_filename_without_lane(self, tmp_path: Path) -> None:
        path = _write_fastq(
            tmp_path / "S003_S3_R1_001.fastq",
            _fastq_content(lane=3),
        )
        sample = parse_fastq_header(path)
        assert sample.sample_id == "S003"
        # Lane should come from the header since filename has none
        assert sample.lane == 3

    def test_single_index(self, tmp_path: Path) -> None:
        path = tmp_path / "S004_S4_L001_R1_001.fastq"
        path.write_text(
            "@NB551234:123:HWNCMBGXC:1:11101:1:1 1:N:0:ATTACTCG\n"
            "ACGT\n+\nIIII\n"
        )
        sample = parse_fastq_header(path)
        assert sample.index_i7 == "ATTACTCG"
        assert sample.index_i5 is None

    def test_unrecognized_header_still_returns_sample(self, tmp_path: Path) -> None:
        path = tmp_path / "S005_S5_L001_R1_001.fastq"
        path.write_text("@some-non-illumina-header here\nACGT\n+\nIIII\n")
        sample = parse_fastq_header(path)
        assert sample.sample_id == "S005"
        assert sample.lane == 1  # from filename
        assert sample.flowcell_id is None

    def test_non_illumina_filename_uses_stem(self, tmp_path: Path) -> None:
        path = tmp_path / "myreads.fastq"
        path.write_text("@x:1:fc:1:1:1:1 1:N:0:A\nA\n+\nI\n")
        sample = parse_fastq_header(path)
        assert sample.sample_id == "myreads"


class TestParseFastqDirectory:
    def test_collapses_r1_r2_per_sample(self, tmp_path: Path) -> None:
        _write_fastq(tmp_path / "S001_S1_L001_R1_001.fastq", _fastq_content())
        _write_fastq(tmp_path / "S001_S1_L001_R2_001.fastq", _fastq_content())
        _write_fastq(tmp_path / "S002_S2_L001_R1_001.fastq", _fastq_content(i7="TCCGGAGA"))
        samples = parse_fastq_directory(tmp_path)
        ids = sorted(s.sample_id for s in samples)
        assert ids == ["S001", "S002"]

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert parse_fastq_directory(tmp_path) == []

    def test_walks_subdirectories(self, tmp_path: Path) -> None:
        sub = tmp_path / "fastqs"
        sub.mkdir()
        _write_fastq(sub / "S001_S1_L001_R1_001.fastq", _fastq_content())
        samples = parse_fastq_directory(tmp_path)
        assert len(samples) == 1

    def test_unreadable_file_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "S001_S1_L001_R1_001.fastq.gz"
        # write something invalid for gzip
        path.write_bytes(b"not gzip data")
        samples = parse_fastq_directory(tmp_path)
        assert samples == []
