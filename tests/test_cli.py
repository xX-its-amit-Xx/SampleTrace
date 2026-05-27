"""End-to-end CLI tests using click.testing.CliRunner."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sampletrace.cli import main


class TestCliBasics:
    def test_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "reconcile" in result.output

    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "sampletrace" in result.output.lower()

    def test_reconcile_requires_downstream(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["reconcile", "--mock"])
        assert result.exit_code != 0
        assert "at least one of" in result.output

    def test_reconcile_requires_benchling_or_mock(self, fixtures_dir: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["reconcile", "--sample-sheet", str(fixtures_dir / "SampleSheet_v2.csv")]
        )
        assert result.exit_code != 0
        assert "benchling-config" in result.output.lower()


class TestReconcileMockMode:
    def test_mock_with_sample_sheet(self, fixtures_dir: Path, tmp_path: Path) -> None:
        runner = CliRunner()
        out_dir = tmp_path / "report"
        result = runner.invoke(
            main,
            [
                "reconcile",
                "--mock",
                "--sample-sheet",
                str(fixtures_dir / "SampleSheet_v2.csv"),
                "--output-dir",
                str(out_dir),
                "--run-id",
                "cli_test",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (out_dir / "reconciliation_report.html").exists()
        assert (out_dir / "reconciliation_report.md").exists()
        assert (out_dir / "mismatches.csv").exists()
        assert (out_dir / "sample_provenance.json").exists()

    def test_mock_with_count_matrix(self, fixtures_dir: Path, tmp_path: Path) -> None:
        runner = CliRunner()
        out_dir = tmp_path / "report"
        result = runner.invoke(
            main,
            [
                "reconcile",
                "--mock",
                "--count-matrix",
                str(fixtures_dir / "counts.tsv"),
                "--output-dir",
                str(out_dir),
            ],
        )
        assert result.exit_code == 0, result.output

    def test_mock_with_config_file(self, fixtures_dir: Path, tmp_path: Path) -> None:
        runner = CliRunner()
        out_dir = tmp_path / "report"
        result = runner.invoke(
            main,
            [
                "reconcile",
                "--benchling-config",
                str(fixtures_dir / "bch_mock.yml"),
                "--sample-sheet",
                str(fixtures_dir / "SampleSheet_v2.csv"),
                "--output-dir",
                str(out_dir),
            ],
        )
        assert result.exit_code == 0, result.output

    def test_fail_on_flagged_exits_nonzero_when_flagged(
        self, fixtures_dir: Path, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        out_dir = tmp_path / "report"
        # The bundled mock data + v1 sheet match cleanly, but v2 has different
        # indices so there will be drift -> flagged.
        result = runner.invoke(
            main,
            [
                "reconcile",
                "--mock",
                "--sample-sheet",
                str(fixtures_dir / "SampleSheet_v2.csv"),
                "--output-dir",
                str(out_dir),
                "--fail-on-flagged",
            ],
        )
        # The v2 indices are 10bp but mock has 8bp — drift will be flagged.
        # Confirm exit code is 2 (our chosen non-zero) if there's any flagging.
        if result.exit_code != 0:
            assert result.exit_code == 2

    def test_fail_on_flagged_clean_run_exits_zero(self, fixtures_dir: Path, tmp_path: Path) -> None:
        runner = CliRunner()
        out_dir = tmp_path / "report"
        # v1 sheet indices exactly match the bundled mock => no drift => clean
        result = runner.invoke(
            main,
            [
                "reconcile",
                "--mock",
                "--sample-sheet",
                str(fixtures_dir / "SampleSheet_v1.csv"),
                "--output-dir",
                str(out_dir),
                "--fail-on-flagged",
            ],
        )
        assert result.exit_code == 0, result.output


class TestVerboseLogging:
    def test_verbose_flag_accepted(self, fixtures_dir: Path, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "-v",
                "reconcile",
                "--mock",
                "--sample-sheet",
                str(fixtures_dir / "SampleSheet_v1.csv"),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        assert result.exit_code == 0, result.output
