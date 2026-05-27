"""Tests for the credential-related CLI commands: configure, verify-auth, keyring-info."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sampletrace.cli import main


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCHLING_API_KEY", raising=False)


class TestVerifyAuth:
    def test_dry_run_missing_key_exits_1(self, fixtures_dir: Path) -> None:
        runner = CliRunner()
        with patch("sampletrace.credentials._read_keyring", return_value=None):
            result = runner.invoke(
                main,
                [
                    "verify-auth",
                    "--benchling-config",
                    str(fixtures_dir / "bch_mock.yml"),
                    "--dry-run",
                ],
            )
        assert result.exit_code == 1
        assert "missing" in result.output
        assert "sampletrace configure" in result.output

    def test_dry_run_with_env_key_exits_0(
        self, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BENCHLING_API_KEY", "sk_test_xyz1234")
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "verify-auth",
                "--benchling-config",
                str(fixtures_dir / "bch_mock.yml"),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "key source    : env" in result.output
        assert "***1234" in result.output
        # Critically: the full key must not appear.
        assert "sk_test_xyz1234" not in result.output
        assert "[dry-run]" in result.output

    def test_yaml_key_triggers_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "bch.yml"
        cfg.write_text(
            "benchling:\n"
            "  tenant_url: https://example.benchling.com\n"
            "  api_key: sk_in_yaml_dontdothis\n"
        )
        runner = CliRunner()
        with patch("sampletrace.credentials._read_keyring", return_value=None):
            result = runner.invoke(
                main, ["verify-auth", "--benchling-config", str(cfg), "--dry-run"]
            )
        assert result.exit_code == 0, result.output
        assert "key source    : yaml" in result.output
        assert "WARNING" in result.output
        # No full key in output.
        assert "sk_in_yaml_dontdothis" not in result.output

    def test_requires_config_or_tenant_url(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["verify-auth"])
        assert result.exit_code != 0
        assert "benchling-config" in result.output.lower()


class TestConfigure:
    def test_configure_stores_in_keyring_and_writes_template(self, tmp_path: Path) -> None:
        runner = CliRunner()
        cfg_path = tmp_path / "bch.yml"
        with patch("keyring.set_password") as set_pw:
            result = runner.invoke(
                main,
                [
                    "configure",
                    "--tenant-url",
                    "https://example.benchling.com",
                    "--config",
                    str(cfg_path),
                ],
                input="sk_user_typed_this\nsk_user_typed_this\n",
            )
        assert result.exit_code == 0, result.output
        set_pw.assert_called_once()
        args = set_pw.call_args.args
        assert args[1] == "https://example.benchling.com"
        assert args[2] == "sk_user_typed_this"

        # Key MUST NOT be echoed back.
        assert "sk_user_typed_this" not in result.output

        # YAML template should exist and NOT contain the key.
        assert cfg_path.exists()
        content = cfg_path.read_text()
        assert "sk_user_typed_this" not in content
        assert "api_key: null" in content
        assert "example.benchling.com" in content

    def test_configure_delete_removes_keyring_entry(self) -> None:
        runner = CliRunner()
        with patch("keyring.delete_password") as del_pw:
            result = runner.invoke(
                main,
                [
                    "configure",
                    "--tenant-url",
                    "https://example.benchling.com",
                    "--delete",
                ],
            )
        assert result.exit_code == 0, result.output
        del_pw.assert_called_once()
        assert "removed" in result.output.lower()

    def test_configure_does_not_overwrite_existing_config(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "bch.yml"
        cfg_path.write_text("benchling:\n  tenant_url: https://existing\n")
        original = cfg_path.read_text()
        runner = CliRunner()
        with patch("keyring.set_password"):
            result = runner.invoke(
                main,
                [
                    "configure",
                    "--tenant-url",
                    "https://new.benchling.com",
                    "--config",
                    str(cfg_path),
                ],
                input="sk_key\nsk_key\n",
            )
        assert result.exit_code == 0, result.output
        assert cfg_path.read_text() == original
        assert "already exists" in result.output

    def test_configure_no_backend_surfaces_error(self) -> None:
        runner = CliRunner()
        # No keyring module at all.
        with patch.dict("sys.modules", {"keyring": None, "keyring.errors": None}):
            result = runner.invoke(
                main,
                [
                    "configure",
                    "--tenant-url",
                    "https://example.benchling.com",
                ],
                input="sk_key\nsk_key\n",
            )
        assert result.exit_code != 0
        assert "keyring is not installed" in result.output


class TestKeyringInfo:
    def test_shows_backend_name(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["keyring-info"])
        # Whatever backend is installed (Windows vault, macOS, file, null),
        # this should exit 0 and print *something*.
        assert result.exit_code == 0
        assert "keyring backend" in result.output
        assert "sampletrace.benchling" in result.output


class TestReconcileWithCredentialResolution:
    """End-to-end: reconcile should still work with mock mode after the
    credential refactor (no regressions)."""

    def test_mock_reconcile_still_works(self, fixtures_dir: Path, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "reconcile",
                "--mock",
                "--sample-sheet",
                str(fixtures_dir / "SampleSheet_v1.csv"),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "reconciliation_report.html").exists()
