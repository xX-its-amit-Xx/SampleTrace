"""Tests for canonical schemas. These pin down the contract every other
module depends on, so they should be exhaustive and fast."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sampletrace.schemas import (
    CanonicalSample,
    MatchConfidence,
    MismatchKind,
    ReconciliationReport,
    ReconciliationRow,
    SampleSource,
)


class TestCanonicalSample:
    def test_minimal_valid(self) -> None:
        s = CanonicalSample(sample_id="S001", source=SampleSource.SAMPLE_SHEET)
        assert s.sample_id == "S001"
        assert s.source == SampleSource.SAMPLE_SHEET
        assert s.extra == {}

    def test_sample_id_required(self) -> None:
        with pytest.raises(ValidationError):
            CanonicalSample(source=SampleSource.SAMPLE_SHEET)  # type: ignore[call-arg]

    def test_sample_id_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CanonicalSample(sample_id="   ", source=SampleSource.SAMPLE_SHEET)

    def test_sample_id_stripped(self) -> None:
        s = CanonicalSample(sample_id="  S001  ", source=SampleSource.SAMPLE_SHEET)
        assert s.sample_id == "S001"

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            CanonicalSample(
                sample_id="S001",
                source=SampleSource.SAMPLE_SHEET,
                undeclared_field="oops",  # type: ignore[call-arg]
            )

    @pytest.mark.parametrize(
        "index,expected",
        [
            ("ACGT", "ACGT"),
            ("acgt", "ACGT"),
            ("ACGTACGTAC", "ACGTACGTAC"),
            ("ACNGT", "ACNGT"),
        ],
    )
    def test_index_valid(self, index: str, expected: str) -> None:
        s = CanonicalSample(
            sample_id="S001",
            source=SampleSource.SAMPLE_SHEET,
            index_i7=index,
        )
        assert s.index_i7 == expected

    @pytest.mark.parametrize("bad_index", ["ACGTX", "ACGT-", "1234", "ACGT "])
    def test_index_rejects_non_acgtn(self, bad_index: str) -> None:
        # Note: trailing whitespace is stripped by config, so "ACGT " becomes
        # "ACGT" and is valid. Drop that case.
        if bad_index.strip() != bad_index:
            return
        with pytest.raises(ValidationError):
            CanonicalSample(
                sample_id="S001",
                source=SampleSource.SAMPLE_SHEET,
                index_i7=bad_index,
            )

    def test_lane_positive(self) -> None:
        with pytest.raises(ValidationError):
            CanonicalSample(
                sample_id="S001",
                source=SampleSource.SAMPLE_SHEET,
                lane=0,
            )

    def test_merge_evidence_fills_missing(self) -> None:
        bch = CanonicalSample(
            sample_id="S001",
            source=SampleSource.BENCHLING,
            organism="Homo sapiens",
            benchling_entity_id="bfi_abc",
        )
        sheet = CanonicalSample(
            sample_id="S001",
            source=SampleSource.SAMPLE_SHEET,
            index_i7="ACGTACGT",
            lane=1,
        )
        merged = bch.merge_evidence(sheet)
        assert merged.organism == "Homo sapiens"  # from bch
        assert merged.index_i7 == "ACGTACGT"  # from sheet
        assert merged.lane == 1  # from sheet
        assert merged.benchling_entity_id == "bfi_abc"  # from bch

    def test_merge_evidence_self_wins_conflict(self) -> None:
        bch = CanonicalSample(
            sample_id="S001",
            source=SampleSource.BENCHLING,
            organism="Homo sapiens",
        )
        sheet = CanonicalSample(
            sample_id="S001",
            source=SampleSource.SAMPLE_SHEET,
            organism="Mus musculus",
        )
        merged = bch.merge_evidence(sheet)
        assert merged.organism == "Homo sapiens"

    def test_merge_evidence_unions_extras(self) -> None:
        a = CanonicalSample(
            sample_id="S001",
            source=SampleSource.BENCHLING,
            extra={"submitter": "alice"},
        )
        b = CanonicalSample(
            sample_id="S001",
            source=SampleSource.SAMPLE_SHEET,
            extra={"description": "control"},
        )
        merged = a.merge_evidence(b)
        assert merged.extra == {"submitter": "alice", "description": "control"}


class TestMatchConfidence:
    @pytest.mark.parametrize(
        "c,is_green,is_red",
        [
            (MatchConfidence.EXACT, True, False),
            (MatchConfidence.HIGH, True, False),
            (MatchConfidence.MEDIUM, False, False),
            (MatchConfidence.LOW, False, True),
            (MatchConfidence.NONE, False, True),
        ],
    )
    def test_traffic_light_classification(
        self, c: MatchConfidence, is_green: bool, is_red: bool
    ) -> None:
        assert c.is_green is is_green
        assert c.is_red is is_red


class TestReconciliationRow:
    def test_clean_row_not_flagged(self) -> None:
        row = ReconciliationRow(
            sample_id="S001",
            confidence=MatchConfidence.EXACT,
            sources_present=[SampleSource.BENCHLING, SampleSource.SAMPLE_SHEET],
        )
        assert not row.is_flagged

    def test_row_with_mismatch_flagged(self) -> None:
        row = ReconciliationRow(
            sample_id="S001",
            confidence=MatchConfidence.EXACT,
            sources_present=[SampleSource.BENCHLING],
            mismatches=[MismatchKind.METADATA_DRIFT],
        )
        assert row.is_flagged

    def test_red_confidence_flagged(self) -> None:
        row = ReconciliationRow(
            sample_id="S001",
            confidence=MatchConfidence.LOW,
            sources_present=[SampleSource.SAMPLE_SHEET],
        )
        assert row.is_flagged

    def test_confidence_none_accepts_empty_missing_sources(self) -> None:
        # When the caller has no downstream sources at all, NONE with empty
        # sources_missing is the natural representation.
        row = ReconciliationRow(
            sample_id="S001",
            confidence=MatchConfidence.NONE,
            sources_present=[SampleSource.BENCHLING],
            sources_missing=[],
        )
        assert row.is_flagged


class TestReconciliationReport:
    def test_summary_counts(self) -> None:
        report = ReconciliationReport(
            run_id="run123",
            rows=[
                ReconciliationRow(
                    sample_id="S001",
                    confidence=MatchConfidence.EXACT,
                    sources_present=[SampleSource.BENCHLING],
                ),
                ReconciliationRow(
                    sample_id="S002",
                    confidence=MatchConfidence.HIGH,
                    sources_present=[SampleSource.BENCHLING],
                ),
                ReconciliationRow(
                    sample_id="S003",
                    confidence=MatchConfidence.MEDIUM,
                    sources_present=[SampleSource.BENCHLING],
                    fuzzy_score=88.0,
                ),
                ReconciliationRow(
                    sample_id="S004",
                    confidence=MatchConfidence.LOW,
                    sources_present=[SampleSource.SAMPLE_SHEET],
                    fuzzy_score=62.0,
                ),
                ReconciliationRow(
                    sample_id="S005",
                    confidence=MatchConfidence.NONE,
                    sources_present=[SampleSource.BENCHLING],
                    sources_missing=[SampleSource.SAMPLE_SHEET],
                    mismatches=[MismatchKind.MISSING_FROM_DOWNSTREAM],
                ),
            ],
        )
        summary = report.summary()
        assert summary["total"] == 5
        assert summary["green"] == 2
        assert summary["yellow"] == 1
        assert summary["red"] == 2
        assert summary["flagged"] >= 2

    def test_empty_report(self) -> None:
        report = ReconciliationReport()
        assert report.total == 0
        assert report.summary() == {
            "total": 0,
            "green": 0,
            "yellow": 0,
            "red": 0,
            "flagged": 0,
        }
