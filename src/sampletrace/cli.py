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
from sampletrace.benchling_client import BenchlingClient, BenchlingConfig, load_config
from sampletrace.credentials import (
    ENV_VAR,
    KEYRING_SERVICE,
    KeySource,
    delete_from_keyring,
    resolve_api_key,
    store_in_keyring,
)
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


@main.command()
@click.option(
    "--tenant-url",
    "-t",
    type=str,
    required=True,
    help="Your Benchling tenant URL, e.g. https://acme.benchling.com",
)
@click.option(
    "--config",
    "-c",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("bch.yml"),
    show_default=True,
    help="Where to write a sanitized YAML template (api_key NOT included).",
)
@click.option(
    "--delete",
    is_flag=True,
    help="Remove the stored key for this tenant from the OS keyring.",
)
def configure(tenant_url: str, config: Path, delete: bool) -> None:
    """Store a Benchling API key safely in the OS keyring.

    The key never touches disk in plaintext and is never written to the
    YAML config — that file gets a placeholder + a comment pointing at the
    keyring. The key is read on subsequent runs via the documented
    credential precedence.
    """
    if delete:
        removed = delete_from_keyring(tenant_url=tenant_url)
        if removed:
            click.echo(f"removed keyring entry for {tenant_url}")
        else:
            click.echo(f"no keyring entry found for {tenant_url}")
        return

    click.echo(f"Storing Benchling API key for {tenant_url} in the OS keyring.")
    click.echo("(The key will not be echoed back and will not be written to any file.)")
    api_key = click.prompt(
        "Benchling API key",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        store_in_keyring(tenant_url=tenant_url, api_key=api_key)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    # Write a sanitized YAML template so the user has a config file to point
    # the CLI at — but with NO key in it.
    if not config.exists():
        template = (
            "benchling:\n"
            f"  tenant_url: {tenant_url}\n"
            "  api_key: null     # resolved from OS keyring at runtime — DO NOT commit a key here\n"
            "  schema_id: ts_xxxxxxxxxxxx    # your sample registration schema\n"
            "  # project_id: src_xxxxxxxxxxxx  # uncomment to restrict to one project\n"
            "  mock: false\n"
            "\n"
            "# Override schema_mapping if your Benchling schema uses different field names.\n"
            "schema_mapping:\n"
            '  sample_id: "Sample ID"\n'
            "\n"
            "reconciliation:\n"
            "  fuzzy_threshold: 80\n"
            "  high_confidence: 95\n"
        )
        config.write_text(template, encoding="utf-8")
        click.echo(f"wrote config template -> {config}")
    else:
        click.echo(f"config {config} already exists — not overwriting")

    click.echo(f"\nDone. Verify with:\n  sampletrace verify-auth --benchling-config {config}")


@main.command()
@click.option(
    "--benchling-config",
    "-b",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to bch.yml.",
)
@click.option(
    "--tenant-url",
    "-t",
    type=str,
    help="Override tenant URL (skip the YAML).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Don't call Benchling; just report where the key would come from.",
)
def verify_auth(benchling_config: Path | None, tenant_url: str | None, dry_run: bool) -> None:
    """Confirm Benchling credentials work without ingesting any data.

    Resolves the API key via the documented precedence, prints *where* it
    came from (never the key itself), and — unless --dry-run — makes one
    cheap API call to confirm the tenant + schema are reachable.
    """
    if benchling_config is None and tenant_url is None:
        raise click.UsageError("--benchling-config or --tenant-url is required")

    if benchling_config:
        cfg = load_config(benchling_config, resolve_credentials=False)
        if tenant_url:
            cfg = cfg.model_copy(update={"tenant_url": tenant_url})
    else:
        cfg = BenchlingConfig(tenant_url=tenant_url)

    resolution = resolve_api_key(
        tenant_url=cfg.tenant_url,
        yaml_value=cfg.api_key,
    )
    click.echo(f"tenant URL    : {cfg.tenant_url or '<unset>'}")
    click.echo(f"schema_id     : {cfg.schema_id or '<unset>'}")
    click.echo(f"key source    : {resolution.source.value}")
    click.echo(f"key (redacted): {resolution.redacted}")

    if resolution.source == KeySource.MISSING:
        click.echo("")
        click.echo("No API key found. Pick one:")
        click.echo(f"  1. sampletrace configure --tenant-url {cfg.tenant_url or '<your-tenant>'}")
        click.echo(f"  2. export {ENV_VAR}=<your-key>")
        click.echo(
            "  3. (last resort) set benchling.api_key in your YAML — see docs/credentials.md"
        )
        sys.exit(1)

    if resolution.source == KeySource.YAML:
        click.echo("")
        click.echo(
            "WARNING: key is in YAML. Prefer `sampletrace configure` (OS "
            "keyring) or the env var for production."
        )

    if dry_run:
        click.echo("\n[dry-run] not contacting Benchling")
        return

    # Real call.
    cfg_with_key = cfg.model_copy(update={"api_key": resolution.api_key})
    client = BenchlingClient(cfg_with_key)
    try:
        result = client.verify_auth()
    except (ValueError, ImportError) as e:
        raise click.ClickException(str(e)) from e
    except Exception as e:
        click.echo(f"\nFAIL: {type(e).__name__}: {e}", err=True)
        click.echo(
            "Common causes: bad api_key, wrong tenant_url, schema_id not "
            "visible to this key, or network/firewall blocking benchling.com.",
            err=True,
        )
        sys.exit(2)

    click.echo("")
    click.echo("OK — Benchling reachable")
    click.echo(f"  mode             : {result['mode']}")
    click.echo(f"  first page count : {result.get('first_page_count', 'n/a')}")


main.add_command(verify_auth, name="verify-auth")


@main.command(name="keyring-info")
def keyring_info() -> None:
    """Show the OS keyring backend that would be used (for troubleshooting)."""
    try:
        import keyring
    except ImportError:
        click.echo("keyring is not installed. Install with: pip install 'sampletrace[benchling]'")
        sys.exit(1)
    backend = keyring.get_keyring()
    click.echo(f"keyring backend : {backend.__class__.__name__}")
    click.echo(f"priority        : {getattr(backend, 'priority', 'n/a')}")
    click.echo(f"service id      : {KEYRING_SERVICE}")
    click.echo(
        "\nKey lookups are scoped per-tenant (account = your tenant URL),"
        " so one machine can hold credentials for multiple Benchling tenants."
    )


if __name__ == "__main__":
    main()
