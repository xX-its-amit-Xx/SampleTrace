"""Tests for the Illumina sample sheet parser (v1 + v2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sampletrace.parsers.illumina_sample_sheet import parse_sample_sheet
from sampletrace.schemas import SampleSource


class TestSampleSheetV1:
    @pytest.fixture
    def samples(self, fixtures_dir: Path) -> list:
        return parse_sample_sheet(fixtures_dir / "SampleSheet_v1.csv")

    def test_four_samples_parsed(self, samples: list) -> None:
        assert len(samples) == 4

    def test_sample_ids_extracted(self, samples: list) -> None:
        ids = [s.sample_id for s in samples]
        assert ids == ["S001_ctrl", "S002_ctrl", "S003_trt", "S004_trt"]

    def test_source_is_sample_sheet(self, samples: list) -> None:
        assert all(s.source == SampleSource.SAMPLE_SHEET for s in samples)

    def test_indices_parsed(self, samples: list) -> None:
        assert samples[0].index_i7 == "ATTACTCG"
        assert samples[0].index_i5 == "TATAGCCT"
        assert samples[3].index_i7 == "GAGATTCC"

    def test_project_id_parsed(self, samples: list) -> None:
        assert all(s.project_id == "proj_pbmc" for s in samples)

    def test_sample_name_parsed(self, samples: list) -> None:
        assert samples[0].sample_name == "Control_A"


class TestSampleSheetV2:
    @pytest.fixture
    def samples(self, fixtures_dir: Path) -> list:
        return parse_sample_sheet(fixtures_dir / "SampleSheet_v2.csv")

    def test_four_samples_parsed(self, samples: list) -> None:
        assert len(samples) == 4

    def test_v2_indices_parsed(self, samples: list) -> None:
        # v2 uses "Index" / "Index2" headers, not v1's "index" / "index2"
        assert samples[0].index_i7 == "ATTACTCGCG"
        assert samples[0].index_i5 == "TATAGCCTAG"

    def test_v2_lane_parsed(self, samples: list) -> None:
        assert samples[0].lane == 1
        assert samples[2].lane == 2

    def test_v2_section_name_recorded(self, samples: list) -> None:
        assert samples[0].extra["section"] == "BCLConvert_Data"


class TestSampleSheetEdgeCases:
    def test_missing_data_section_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("[Header]\nfoo,bar\n")
        with pytest.raises(ValueError, match=r"no .*Data.* section"):
            parse_sample_sheet(bad)

    def test_missing_sample_id_column_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "no_sample_id.csv"
        bad.write_text("[Data]\nFoo,Bar\n1,2\n")
        with pytest.raises(ValueError, match="sample id column"):
            parse_sample_sheet(bad)

    def test_blank_rows_skipped(self, tmp_path: Path) -> None:
        sheet = tmp_path / "blanks.csv"
        sheet.write_text(
            "[Data]\nSample_ID,index\nS001,ACGT\n\n,\nS002,TGCA\n"
        )
        samples = parse_sample_sheet(sheet)
        assert [s.sample_id for s in samples] == ["S001", "S002"]

    def test_utf8_bom_handled(self, tmp_path: Path) -> None:
        sheet = tmp_path / "bom.csv"
        sheet.write_bytes(
            b"\xef\xbb\xbf[Data]\nSample_ID,index\nS001,ACGT\n"
        )
        samples = parse_sample_sheet(sheet)
        assert len(samples) == 1
        assert samples[0].sample_id == "S001"

    def test_short_rows_padded(self, tmp_path: Path) -> None:
        sheet = tmp_path / "short.csv"
        sheet.write_text("[Data]\nSample_ID,index,index2\nS001,ACGT\n")
        samples = parse_sample_sheet(sheet)
        assert samples[0].sample_id == "S001"
        assert samples[0].index_i7 == "ACGT"
        assert samples[0].index_i5 is None

    def test_non_numeric_lane_kept_in_extras(self, tmp_path: Path) -> None:
        sheet = tmp_path / "lane.csv"
        sheet.write_text("[Data]\nSample_ID,Lane\nS001,all\n")
        samples = parse_sample_sheet(sheet)
        assert samples[0].lane is None
        assert samples[0].extra["lane_raw"] == "all"
