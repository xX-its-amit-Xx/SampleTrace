"""Click-based CLI — the production entry point.

The default ``reconcile`` command is what you wire into CI: pull from
Benchling, parse the run artifacts, write the four reports, exit non-zero
if anything's flagged so the build fails.

``--mock`` skips Benchling entirely and uses bundled synthetic data — handy
for demos, smoke tests, and developing without a tenant.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

from sampletrace import __version__
from sampletrace.benchling_client import BenchlingClient, load_config
from sampletrace.parsers import (
    parse_count_matrix,
    parse_fastq_directory,
    parse_sample_sheet,
)
from sampletrace.reconciler import ReconcilerConfig, reconcile
from sampletrace.report import write_all
from sampletrace.schemas import CanonicalSample, ReconciliationReport

console = Console()
logger = logging.getLogger(__name__)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="sampletrace")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """SampleTrace — Benchling-linked NGS metadata reconciliation."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _load_reconciler_config(config_path: Path | None) -> ReconcilerConfig:
    """Pull the ``reconciliation:`` section out of the YAML config, if present."""
    if config_path is None or not config_path.exists():
        return ReconcilerConfig()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    section = raw.get("reconciliation", {}) or {}
    kwargs: dict[str, Any] = {}
    for key in ("fuzzy_threshold", "high_confidence", "require_index_match"):
        if key in section:
            kwargs[key] = section[key]
    return ReconcilerConfig(**kwargs)


def _fetch_benchling(config: Path | None, mock: bool) -> list[CanonicalSample]:
    if mock:
        click.echo("[mock] using bundled synthetic Benchling data")
        return BenchlingClient.mock().fetch_samples()
    if config is None:
        raise click.UsageError("--benchling-config is required unless --mock is set")
    cfg = load_config(config)
    if cfg.mock:
        click.echo(f"[mock] config {config} has mock=true; using mock data")
    return BenchlingClient(cfg).fetch_samples()


def _print_summary(report: ReconciliationReport) -> None:
    summary = report.summary()
    table = Table(title=f"Reconciliation: {report.run_id or 'unnamed run'}")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    table.add_row("[green]Green[/green]", str(summary["green"]))
    table.add_row("[yellow]Yellow[/yellow]", str(summary["yellow"]))
    table.add_row("[red]Red[/red]", str(summary["red"]))
    table.add_row("[bold]Total[/bold]", str(summary["total"]))
    table.add_row("[bold red]Flagged[/bold red]", str(summary["flagged"]))
    console.print(table)


@main.command()
@click.option(
    "--benchling-config",
    "-b",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to bch.yml. Required unless --mock.",
)
@click.option(
    "--sample-sheet",
    "-s",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Illumina sample sheet (v1 or v2).",
)
@click.option(
    "--fastq-dir",
    "-f",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of FASTQ files to scan (recursively).",
)
@click.option(
    "--count-matrix",
    "-c",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Count matrix TSV/CSV; only column headers are read.",
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("reports"),
    show_default=True,
    help="Directory to write the four output files into.",
)
@click.option(
    "--run-id",
    type=str,
    help="Optional run identifier embedded in reports.",
)
@click.option(
    "--mock",
    is_flag=True,
    help="Skip Benchling and use bundled synthetic data — for demos / dev.",
)
@click.option(
    "--fail-on-flagged",
    is_flag=True,
    help="Exit non-zero if any sample is flagged (for CI gates).",
)
def reconcile_cmd(
    benchling_config: Path | None,
    sample_sheet: Path | None,
    fastq_dir: Path | None,
    count_matrix: Path | None,
    output_dir: Path,
    run_id: str | None,
    mock: bool,
    fail_on_flagged: bool,
) -> None:
    """Reconcile Benchling registrations against NGS run artifacts."""
    if not any([sample_sheet, fastq_dir, count_matrix]):
        raise click.UsageError(
            "at least one of --sample-sheet, --fastq-dir, --count-matrix is required"
        )

    benchling_samples = _fetch_benchling(benchling_config, mock)
    click.echo(f"Loaded {len(benchling_samples)} Benchling samples")

    downstream: list[CanonicalSample] = []
    if sample_sheet:
        sheet_samples = parse_sample_sheet(sample_sheet)
        click.echo(f"  parsed {len(sheet_samples)} samples from {sample_sheet.name}")
        downstream.extend(sheet_samples)
    if fastq_dir:
        fastq_samples = parse_fastq_directory(fastq_dir)
        click.echo(f"  parsed {len(fastq_samples)} samples from FASTQs in {fastq_dir}")
        downstream.extend(fastq_samples)
    if count_matrix:
        matrix_samples = parse_count_matrix(count_matrix)
        click.echo(f"  parsed {len(matrix_samples)} samples from {count_matrix.name}")
        downstream.extend(matrix_samples)

    rec_cfg = _load_reconciler_config(benchling_config)
    report = reconcile(benchling_samples, downstream, config=rec_cfg, run_id=run_id)

    paths = write_all(report, output_dir)
    click.echo("")
    _print_summary(report)
    click.echo("")
    click.echo("Wrote:")
    for kind, path in paths.items():
        click.echo(f"  {kind:>10}: {path}")

    if fail_on_flagged and report.flagged:
        click.echo(f"\n[FAIL] {len(report.flagged)} samples flagged; exiting non-zero.", err=True)
        sys.exit(2)


# Register the command under both ``reconcile`` (the documented name) and
# ``reconcile-cmd`` (internal). Click uses the function name by default; we
# override so the README is accurate.
main.add_command(reconcile_cmd, name="reconcile")


@main.command()
@click.option(
    "--report",
    "-r",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to sample_provenance.json from a prior reconcile run.",
)
@click.option(
    "--port",
    "-p",
    type=int,
    default=8501,
    show_default=True,
)
def dashboard(report: Path, port: int) -> None:
    """Launch the Streamlit dashboard for interactive triage."""
    try:
        import streamlit.web.cli as stcli
    except ImportError:
        raise click.ClickException(
            "streamlit not installed. Install with: pip install 'sampletrace[dashboard]'"
        ) from None
    dashboard_module = Path(__file__).parent / "dashboard.py"
    sys.argv = [
        "streamlit",
        "run",
        str(dashboard_module),
        "--server.port",
        str(port),
        "--",
        "--report",
        str(report),
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
