"""Tests for count matrix column-header parsing."""

from __future__ import annotations

from pathlib import Path

from sampletrace.parsers.count_matrix import parse_count_matrix
from sampletrace.schemas import SampleSource


class TestParseCountMatrix:
    def test_tsv_with_gene_columns(self, fixtures_dir: Path) -> None:
        samples = parse_count_matrix(fixtures_dir / "counts.tsv")
        assert [s.sample_id for s in samples] == ["S001_ctrl", "S002_ctrl", "S003_trt", "S004_trt"]
        assert all(s.source == SampleSource.COUNT_MATRIX for s in samples)

    def test_csv_no_leading_feature_column(self, tmp_path: Path) -> None:
        f = tmp_path / "counts.csv"
        f.write_text("S001,S002,S003\n1,2,3\n4,5,6\n")
        samples = parse_count_matrix(f)
        assert [s.sample_id for s in samples] == ["S001", "S002", "S003"]

    def test_skips_multiple_feature_columns(self, tmp_path: Path) -> None:
        f = tmp_path / "counts.tsv"
        f.write_text(
            "gene_id\tgene_name\tlength\tS001\tS002\nENSG00000000003\tTSPAN6\t1234\t10\t20\n"
        )
        samples = parse_count_matrix(f)
        assert [s.sample_id for s in samples] == ["S001", "S002"]

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.csv"
        f.write_text("")
        assert parse_count_matrix(f) == []

    def test_blank_columns_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "counts.csv"
        f.write_text(",S001,,S002\n")
        samples = parse_count_matrix(f)
        assert [s.sample_id for s in samples] == ["S001", "S002"]

    def test_utf8_bom_handled(self, tmp_path: Path) -> None:
        f = tmp_path / "counts.csv"
        f.write_bytes(b"\xef\xbb\xbfgene,S001,S002\nfoo,1,2\n")
        samples = parse_count_matrix(f)
        assert [s.sample_id for s in samples] == ["S001", "S002"]

    def test_source_path_in_extras(self, tmp_path: Path) -> None:
        f = tmp_path / "counts.csv"
        f.write_text("S001,S002\n1,2\n")
        samples = parse_count_matrix(f)
        assert str(f) in samples[0].extra["source_path"]
