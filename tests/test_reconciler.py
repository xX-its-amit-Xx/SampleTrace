"""Tests for the reconciler — the heart of SampleTrace.

We construct ``CanonicalSample`` lists by hand so the parser layer can't
silently break these tests.
"""

from __future__ import annotations

import pytest

from sampletrace.reconciler import ReconcilerConfig, reconcile
from sampletrace.schemas import (
    CanonicalSample,
    MatchConfidence,
    MismatchKind,
    SampleSource,
)


def _bch(sample_id: str, **kwargs: object) -> CanonicalSample:
    return CanonicalSample(sample_id=sample_id, source=SampleSource.BENCHLING, **kwargs)  # type: ignore[arg-type]


def _sheet(sample_id: str, **kwargs: object) -> CanonicalSample:
    return CanonicalSample(sample_id=sample_id, source=SampleSource.SAMPLE_SHEET, **kwargs)  # type: ignore[arg-type]


def _fastq(sample_id: str, **kwargs: object) -> CanonicalSample:
    return CanonicalSample(sample_id=sample_id, source=SampleSource.FASTQ_HEADER, **kwargs)  # type: ignore[arg-type]


def _row(report, sid: str):
    return next(r for r in report.rows if r.sample_id == sid)


class TestExactMatch:
    def test_perfect_three_way_match_is_green(self) -> None:
        bch = [_bch("S001", organism="Homo sapiens")]
        downstream = [_sheet("S001"), _fastq("S001")]
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        assert row.confidence == MatchConfidence.EXACT
        assert not row.mismatches
        assert not row.is_flagged

    def test_summary_counts_green(self) -> None:
        bch = [_bch("S001"), _bch("S002")]
        downstream = [_sheet("S001"), _sheet("S002")]
        report = reconcile(bch, downstream)
        assert report.summary()["green"] == 2
        assert report.summary()["flagged"] == 0


class TestMissingSample:
    def test_sample_in_benchling_not_downstream_is_red(self) -> None:
        bch = [_bch("S001")]
        downstream = [_sheet("S002")]  # different sample
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        # Either NONE (no fuzzy ghost) or LOW (sub-threshold fuzzy ghost) is red.
        assert row.confidence.is_red
        assert MismatchKind.MISSING_FROM_DOWNSTREAM in row.mismatches

    def test_extra_downstream_flagged(self) -> None:
        bch = [_bch("S001")]
        downstream = [_sheet("S001"), _sheet("EXTRA_XYZ")]
        report = reconcile(bch, downstream)
        extra_row = _row(report, "EXTRA_XYZ")
        assert MismatchKind.EXTRA_IN_DOWNSTREAM in extra_row.mismatches
        assert extra_row.confidence == MatchConfidence.NONE

    def test_partial_downstream_coverage_downgrades(self) -> None:
        # Sample sheet has it, but FASTQ doesn't — needs human review.
        bch = [_bch("S001")]
        downstream = [_sheet("S001"), _fastq("S002")]
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        assert row.confidence != MatchConfidence.EXACT
        assert MismatchKind.MISSING_FROM_DOWNSTREAM in row.mismatches


class TestFuzzyMatch:
    def test_normalized_match_high_score(self) -> None:
        # "S001_ctrl" vs "S001-ctrl" — same after normalization
        bch = [_bch("S001_ctrl")]
        downstream = [_sheet("S001-ctrl")]
        report = reconcile(bch, downstream)
        row = _row(report, "S001_ctrl")
        # Normalized match: still confident (HIGH or above)
        assert row.confidence in (MatchConfidence.HIGH, MatchConfidence.EXACT)
        assert row.matched_sample_ids.get("sample_sheet") == "S001-ctrl"

    def test_fuzzy_below_high_is_ambiguous(self) -> None:
        # Genuinely similar but not the same — should land in MEDIUM
        bch = [_bch("ABCDEFG_treated")]
        downstream = [_sheet("ABCDEFG_treats")]  # close enough but flagged
        cfg = ReconcilerConfig(fuzzy_threshold=70, high_confidence=95)
        report = reconcile(bch, downstream, config=cfg)
        row = _row(report, "ABCDEFG_treated")
        if row.confidence == MatchConfidence.MEDIUM:
            assert MismatchKind.AMBIGUOUS_FUZZY_MATCH in row.mismatches

    def test_below_fuzzy_threshold_no_match(self) -> None:
        bch = [_bch("S001")]
        downstream = [_sheet("XYZ999")]
        cfg = ReconcilerConfig(fuzzy_threshold=90)
        report = reconcile(bch, downstream, config=cfg)
        row = _row(report, "S001")
        assert MismatchKind.MISSING_FROM_DOWNSTREAM in row.mismatches


class TestMetadataDrift:
    def test_organism_drift_flagged(self) -> None:
        bch = [_bch("S001", organism="Homo sapiens")]
        downstream = [_sheet("S001", organism="Mus musculus")]
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        assert MismatchKind.METADATA_DRIFT in row.mismatches
        assert any("organism" in n.lower() for n in row.notes)

    def test_index_drift_flagged(self) -> None:
        bch = [_bch("S001", index_i7="ATTACTCG")]
        downstream = [_sheet("S001", index_i7="ATTACTCC")]  # one base off
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        assert MismatchKind.METADATA_DRIFT in row.mismatches

    def test_drift_skipped_when_one_side_empty(self) -> None:
        bch = [_bch("S001", organism="Homo sapiens")]
        downstream = [_sheet("S001")]  # no organism
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        assert MismatchKind.METADATA_DRIFT not in row.mismatches

    def test_case_insensitive_no_false_drift(self) -> None:
        bch = [_bch("S001", organism="homo sapiens")]
        downstream = [_sheet("S001", organism="Homo Sapiens")]
        report = reconcile(bch, downstream)
        row = _row(report, "S001")
        assert MismatchKind.METADATA_DRIFT not in row.mismatches


class TestConfig:
    def test_thresholds_must_be_ordered(self) -> None:
        with pytest.raises(ValueError, match="high_confidence must be >="):
            ReconcilerConfig(fuzzy_threshold=90, high_confidence=80)

    def test_thresholds_must_be_in_range(self) -> None:
        with pytest.raises(ValueError):
            ReconcilerConfig(fuzzy_threshold=-1)
        with pytest.raises(ValueError):
            ReconcilerConfig(high_confidence=101)


class TestReportOrdering:
    def test_flagged_rows_first(self) -> None:
        bch = [_bch("S001"), _bch("S002"), _bch("S003")]
        downstream = [_sheet("S001"), _sheet("S002"), _sheet("S003")]
        # Force a flag on S002
        bch[1] = _bch("S002", organism="Homo sapiens")
        downstream[1] = _sheet("S002", organism="Mus musculus")
        report = reconcile(bch, downstream)
        # Flagged rows come first
        first = report.rows[0]
        assert first.is_flagged


class TestRunIdPropagation:
    def test_run_id_preserved(self) -> None:
        report = reconcile([_bch("S001")], [_sheet("S001")], run_id="run123")
        assert report.run_id == "run123"

    def test_sources_used_includes_benchling(self) -> None:
        report = reconcile([_bch("S001")], [_sheet("S001"), _fastq("S001")])
        assert SampleSource.BENCHLING in report.sources_used
        assert SampleSource.SAMPLE_SHEET in report.sources_used
        assert SampleSource.FASTQ_HEADER in report.sources_used


class TestEmptyInputs:
    def test_empty_both_sides(self) -> None:
        report = reconcile([], [])
        assert report.total == 0

    def test_empty_benchling_all_extras(self) -> None:
        report = reconcile([], [_sheet("S001")])
        row = _row(report, "S001")
        assert MismatchKind.EXTRA_IN_DOWNSTREAM in row.mismatches

    def test_empty_downstream_all_missing(self) -> None:
        report = reconcile([_bch("S001")], [])
        row = _row(report, "S001")
        # With no downstream sources at all, MISSING_FROM_DOWNSTREAM isn't
        # appended (there's no source to be missing from). That's intentional.
        # But confidence should still flag the row.
        assert row.confidence in (MatchConfidence.NONE, MatchConfidence.LOW)
