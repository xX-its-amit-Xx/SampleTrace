"""Tests for the credential resolver — precedence rules, redaction, and keyring stubs.

We mock keyring + dotenv heavily because their behavior depends on the host
OS and on whether a backend is registered. The tests pin down the
*precedence logic*, not the libraries themselves.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from sampletrace.credentials import (
    ENV_VAR,
    KEYRING_SERVICE,
    KeyResolution,
    KeySource,
    _redact,
    delete_from_keyring,
    resolve_api_key,
    store_in_keyring,
)


class TestRedact:
    def test_long_key_shows_last_four(self) -> None:
        assert _redact("sk_abcdefgh1234") == "***1234"

    def test_short_key_fully_redacted(self) -> None:
        assert _redact("abc") == "***"

    def test_exact_four_char_key_fully_redacted(self) -> None:
        assert _redact("abcd") == "***"


class TestKeyResolution:
    def test_missing_constructor(self) -> None:
        kr = KeyResolution.missing()
        assert kr.api_key is None
        assert kr.source == KeySource.MISSING
        assert kr.redacted == "<none>"


class TestResolveApiKeyPrecedence:
    """Pin down which source wins under each combination."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_VAR, raising=False)

    def test_env_var_wins_over_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV_VAR, "from-env-12345")
        with patch("sampletrace.credentials._read_keyring", return_value="from-keyring"):
            result = resolve_api_key(tenant_url="https://t.example", yaml_value="from-yaml")
        assert result.api_key == "from-env-12345"
        assert result.source == KeySource.ENV

    def test_keyring_used_when_no_env(self) -> None:
        with patch("sampletrace.credentials._read_keyring", return_value="from-keyring"):
            result = resolve_api_key(
                tenant_url="https://t.example",
                yaml_value="from-yaml",
                load_dotenv=False,
            )
        assert result.api_key == "from-keyring"
        assert result.source == KeySource.KEYRING

    def test_yaml_used_when_no_env_no_keyring(self) -> None:
        with patch("sampletrace.credentials._read_keyring", return_value=None):
            result = resolve_api_key(
                tenant_url="https://t.example",
                yaml_value="from-yaml",
                load_dotenv=False,
            )
        assert result.api_key == "from-yaml"
        assert result.source == KeySource.YAML

    def test_missing_when_nothing_set(self) -> None:
        with patch("sampletrace.credentials._read_keyring", return_value=None):
            result = resolve_api_key(
                tenant_url="https://t.example",
                yaml_value=None,
                load_dotenv=False,
            )
        assert result.api_key is None
        assert result.source == KeySource.MISSING

    def test_no_tenant_url_skips_keyring(self) -> None:
        with patch("sampletrace.credentials._read_keyring") as kr:
            result = resolve_api_key(
                tenant_url=None,
                yaml_value="from-yaml",
                load_dotenv=False,
            )
        kr.assert_not_called()
        assert result.source == KeySource.YAML

    def test_dotenv_loaded_when_no_env_no_keyring(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Simulate dotenv loading by patching the loader to set the env var.
        def _fake_load(path: Path | None = None) -> bool:
            monkeypatch.setenv(ENV_VAR, "from-dotenv")
            return True

        with (
            patch("sampletrace.credentials._read_keyring", return_value=None),
            patch("sampletrace.credentials._load_dotenv_if_present", side_effect=_fake_load),
        ):
            result = resolve_api_key(
                tenant_url="https://t.example",
                yaml_value=None,
                load_dotenv=True,
            )
        assert result.api_key == "from-dotenv"
        assert result.source == KeySource.DOTENV

    def test_yaml_warning_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("sampletrace.credentials._read_keyring", return_value=None),
            caplog.at_level("WARNING"),
        ):
            resolve_api_key(
                tenant_url="https://t.example",
                yaml_value="from-yaml",
                load_dotenv=False,
            )
        assert any("YAML" in r.message for r in caplog.records)

    def test_redacted_never_contains_full_key(self) -> None:
        with patch("sampletrace.credentials._read_keyring", return_value="sk_supersecret"):
            result = resolve_api_key(
                tenant_url="https://t.example",
                yaml_value=None,
                load_dotenv=False,
            )
        assert result.api_key == "sk_supersecret"
        assert "supersecret" not in result.redacted
        assert result.redacted == "***cret"


class TestStoreInKeyring:
    """keyring is installed in the test venv (via the [benchling] extras), so
    we patch its functions directly rather than replacing sys.modules."""

    def test_happy_path_calls_set_password(self) -> None:
        with patch("keyring.set_password") as set_pw:
            store_in_keyring(tenant_url="https://t.example", api_key="sk_xyz")
        set_pw.assert_called_once_with(KEYRING_SERVICE, "https://t.example", "sk_xyz")

    def test_no_keyring_module_raises_runtime(self) -> None:
        # Simulate keyring being absent by making the import fail.
        with (
            patch.dict("sys.modules", {"keyring": None, "keyring.errors": None}),
            pytest.raises(RuntimeError, match="keyring is not installed"),
        ):
            store_in_keyring(tenant_url="https://t.example", api_key="sk_xyz")

    def test_no_backend_surfaces_actionable_error(self) -> None:
        from keyring.errors import NoKeyringError

        with (
            patch("keyring.set_password", side_effect=NoKeyringError("no backend")),
            pytest.raises(RuntimeError, match="no OS keyring backend"),
        ):
            store_in_keyring(tenant_url="https://t.example", api_key="sk_xyz")


class TestDeleteFromKeyring:
    def test_happy_path(self) -> None:
        with patch("keyring.delete_password") as del_pw:
            ok = delete_from_keyring(tenant_url="https://t.example")
        assert ok is True
        del_pw.assert_called_once_with(KEYRING_SERVICE, "https://t.example")

    def test_no_keyring_returns_false(self) -> None:
        with patch.dict("sys.modules", {"keyring": None, "keyring.errors": None}):
            assert delete_from_keyring(tenant_url="https://t.example") is False

    def test_missing_entry_returns_false(self) -> None:
        from keyring.errors import PasswordDeleteError

        with patch("keyring.delete_password", side_effect=PasswordDeleteError("nope")):
            assert delete_from_keyring(tenant_url="https://t.example") is False
