"""Tests for the Benchling client wrapper — schema mapping, config loading,
mock mode, and behavior when the optional SDK isn't installed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from sampletrace.benchling_client import (
    BenchlingClient,
    BenchlingConfig,
    _map_entity,
    load_config,
)
from sampletrace.schemas import SampleSource


class TestLoadConfig:
    def test_loads_mock_yaml(self, fixtures_dir: Path) -> None:
        cfg = load_config(fixtures_dir / "bch_mock.yml")
        assert cfg.mock is True
        assert cfg.tenant_url == "https://example.benchling.com"
        assert cfg.schema_id == "ts_mockschema001"
        assert cfg.schema_mapping["sample_id"] == "Sample ID"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "does_not_exist.yml")

    def test_env_var_overrides_api_key(
        self, fixtures_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BENCHLING_API_KEY", "from-env-12345")
        cfg = load_config(fixtures_dir / "bch_mock.yml")
        assert cfg.api_key == "from-env-12345"

    def test_no_env_var_uses_config_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BENCHLING_API_KEY", raising=False)
        path = tmp_path / "cfg.yml"
        path.write_text("benchling:\n  api_key: from-yaml\n")
        cfg = load_config(path)
        assert cfg.api_key == "from-yaml"

    def test_partial_schema_mapping_merged_with_default(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.yml"
        path.write_text(
            "benchling:\n"
            "  mock: true\n"
            "schema_mapping:\n"
            "  sample_id: 'My Custom ID'\n"
        )
        cfg = load_config(path)
        assert cfg.schema_mapping["sample_id"] == "My Custom ID"
        # Other keys keep their defaults.
        assert cfg.schema_mapping["organism"] == "Organism"


class TestMapEntity:
    def test_basic_entity_mapped(self) -> None:
        entity = {
            "id": "bfi_abc",
            "name": "S001",
            "schema_id": "ts_xyz",
            "fields": {
                "Sample ID": "S001",
                "Organism": "Homo sapiens",
                "i7 index": "ATTACTCG",
            },
        }
        mapping = {
            "sample_id": "Sample ID",
            "organism": "Organism",
            "index_i7": "i7 index",
        }
        sample = _map_entity(entity, mapping)
        assert sample is not None
        assert sample.sample_id == "S001"
        assert sample.organism == "Homo sapiens"
        assert sample.index_i7 == "ATTACTCG"
        assert sample.benchling_entity_id == "bfi_abc"
        assert sample.benchling_schema_id == "ts_xyz"

    def test_missing_sample_id_returns_none(self) -> None:
        entity = {"id": None, "name": None, "fields": {}}
        sample = _map_entity(entity, {"sample_id": "Sample ID"})
        assert sample is None

    def test_unmapped_fields_go_to_extras(self) -> None:
        entity = {
            "id": "bfi_abc",
            "name": "S001",
            "fields": {
                "Sample ID": "S001",
                "Submitter": "alice",
                "Notes": "freshly thawed",
            },
        }
        sample = _map_entity(entity, {"sample_id": "Sample ID"})
        assert sample is not None
        assert sample.extra["Submitter"] == "alice"
        assert sample.extra["Notes"] == "freshly thawed"

    def test_empty_field_values_skipped(self) -> None:
        entity = {
            "id": "bfi_abc",
            "name": "S001",
            "fields": {"Sample ID": "S001", "Organism": "", "Tissue": None},
        }
        sample = _map_entity(entity, {
            "sample_id": "Sample ID",
            "organism": "Organism",
            "tissue": "Tissue",
        })
        assert sample is not None
        assert sample.organism is None
        assert sample.tissue is None

    def test_falls_back_to_name_when_field_missing(self) -> None:
        entity = {"id": "bfi_abc", "name": "S001", "fields": {}}
        sample = _map_entity(entity, {"sample_id": "Sample ID"})
        assert sample is not None
        assert sample.sample_id == "S001"


class TestBenchlingClientMock:
    def test_default_mock_loads_bundled_data(self) -> None:
        client = BenchlingClient.mock()
        samples = client.fetch_samples()
        assert len(samples) == 4
        ids = sorted(s.sample_id for s in samples)
        assert ids == ["S001_ctrl", "S002_ctrl", "S003_trt", "S004_trt"]
        assert all(s.source == SampleSource.BENCHLING for s in samples)

    def test_custom_mock_data_path(self, fixtures_dir: Path) -> None:
        client = BenchlingClient.mock(fixtures_dir / "benchling_with_drift.json")
        samples = client.fetch_samples()
        assert len(samples) == 2
        assert samples[1].organism == "Mus musculus"

    def test_from_yaml_works(self, fixtures_dir: Path) -> None:
        client = BenchlingClient.from_yaml(fixtures_dir / "bch_mock.yml")
        assert client.is_mock
        samples = client.fetch_samples()
        assert len(samples) == 4

    def test_invalid_mock_data_format_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text('{"not_entities": "oops"}')
        client = BenchlingClient.mock(bad)
        with pytest.raises(ValueError, match="mock data must be a list"):
            client.fetch_samples()


class TestBenchlingClientReal:
    def test_real_mode_requires_credentials(self) -> None:
        cfg = BenchlingConfig(mock=False)
        client = BenchlingClient(cfg)
        with pytest.raises(ValueError, match="tenant_url and api_key"):
            client.fetch_samples()

    def test_real_mode_without_sdk_raises_helpful(self) -> None:
        cfg = BenchlingConfig(
            mock=False,
            tenant_url="https://example.benchling.com",
            api_key="fake",
        )
        client = BenchlingClient(cfg)
        # Pretend benchling_sdk is unavailable.
        sdk_modules = {
            "benchling_sdk": None,
            "benchling_sdk.auth.api_key_auth": None,
            "benchling_sdk.benchling": None,
        }
        with (
            patch.dict("sys.modules", sdk_modules),
            pytest.raises(ImportError, match="benchling-sdk not installed"),
        ):
            client.fetch_samples()


class TestConfigValidation:
    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
            BenchlingConfig(unknown_field="oops")  # type: ignore[call-arg]
