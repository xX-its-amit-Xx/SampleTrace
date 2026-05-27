"""Tests for the report writer — every output format gets exercised."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from sampletrace.reconciler import reconcile
from sampletrace.report import (
    render_html,
    render_markdown,
    write_all,
    write_mismatches_csv,
    write_provenance_json,
)
from sampletrace.schemas import CanonicalSample, SampleSource


def _bch(sid: str, **kw: object) -> CanonicalSample:
    return CanonicalSample(sample_id=sid, source=SampleSource.BENCHLING, **kw)  # type: ignore[arg-type]


def _sheet(sid: str, **kw: object) -> CanonicalSample:
    return CanonicalSample(sample_id=sid, source=SampleSource.SAMPLE_SHEET, **kw)  # type: ignore[arg-type]


@pytest.fixture
def sample_report():
    bch = [_bch("S001"), _bch("S002", organism="Homo sapiens"), _bch("S003")]
    downstream = [
        _sheet("S001"),
        _sheet("S002", organism="Mus musculus"),  # drift
        _sheet("EXTRA_xyz"),  # extra
    ]
    return reconcile(bch, downstream, run_id="testrun")


class TestRenderHtml:
    def test_contains_run_id(self, sample_report) -> None:
        html = render_html(sample_report)
        assert "testrun" in html

    def test_contains_all_sample_ids(self, sample_report) -> None:
        html = render_html(sample_report)
        for sid in ("S001", "S002", "S003", "EXTRA_xyz"):
            assert sid in html

    def test_contains_traffic_light_classes(self, sample_report) -> None:
        html = render_html(sample_report)
        assert "green" in html
        assert "red" in html

    def test_self_contained_no_external_assets(self, sample_report) -> None:
        html = render_html(sample_report)
        # No <link>, <script src=>, or @import — should render in any inbox.
        assert "<link" not in html.lower()
        assert "script src=" not in html.lower()
        assert "@import" not in html.lower()

    def test_html_escapes_user_content(self) -> None:
        bch = [_bch("S001<script>alert(1)</script>")]
        report = reconcile(bch, [])
        html = render_html(report)
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;" in html


class TestRenderMarkdown:
    def test_contains_summary_line(self, sample_report) -> None:
        md = render_markdown(sample_report)
        assert "Total samples:" in md

    def test_traffic_light_emojis(self, sample_report) -> None:
        md = render_markdown(sample_report)
        # Should contain at least one of each
        assert any(e in md for e in ("🟢", "🟡", "🔴"))

    def test_flagged_details_section(self, sample_report) -> None:
        md = render_markdown(sample_report)
        assert "Flagged samples" in md


class TestWriteMismatchesCsv:
    def test_only_flagged_rows_written(self, sample_report, tmp_path: Path) -> None:
        path = tmp_path / "mismatches.csv"
        write_mismatches_csv(sample_report, path)
        with path.open() as fh:
            rows = list(csv.DictReader(fh))
        # All written rows must be flagged
        assert len(rows) == len(sample_report.flagged)
        for r in rows:
            assert r["sample_id"] in {row.sample_id for row in sample_report.flagged}

    def test_header_present(self, sample_report, tmp_path: Path) -> None:
        path = tmp_path / "mismatches.csv"
        write_mismatches_csv(sample_report, path)
        with path.open() as fh:
            reader = csv.reader(fh)
            header = next(reader)
        for col in ("sample_id", "confidence", "mismatches", "notes"):
            assert col in header


class TestWriteProvenanceJson:
    def test_full_audit_present(self, sample_report, tmp_path: Path) -> None:
        path = tmp_path / "provenance.json"
        write_provenance_json(sample_report, path)
        data = json.loads(path.read_text())
        assert data["run_id"] == "testrun"
        assert len(data["rows"]) == len(sample_report.rows)
        assert len(data["benchling_samples"]) == 3
        assert "summary" in data
        assert "generated_at" in data


class TestWriteAll:
    def test_all_four_files_written(self, sample_report, tmp_path: Path) -> None:
        out = tmp_path / "out"
        paths = write_all(sample_report, out)
        assert (out / "reconciliation_report.html").exists()
        assert (out / "reconciliation_report.md").exists()
        assert (out / "mismatches.csv").exists()
        assert (out / "sample_provenance.json").exists()
        assert set(paths.keys()) == {"html", "markdown", "csv", "json"}

    def test_creates_output_dir(self, sample_report, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "nested" / "dir"
        write_all(sample_report, out)
        assert out.exists()
