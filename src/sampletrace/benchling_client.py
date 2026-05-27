"""Thin wrapper around ``benchling-sdk`` with first-class mock mode.

Both modes converge on ``list[CanonicalSample]``. The schema-mapping logic
(Benchling field name -> canonical field name) is shared, so unit tests
exercise the *same* mapping code path as production. The only difference
between real and mock is *where the raw entity dicts come from*: the SDK
or a JSON file.

Why a wrapper at all?
- ``benchling-sdk`` is heavy and optional; users without Benchling access
  can still develop and test against mock data.
- The wrapper enforces our schema-mapping contract: every output is a
  validated ``CanonicalSample``, even from an unfamiliar Benchling schema.
- It centralizes config loading, env-var precedence, and error messages.
"""

from __future__ import annotations

import json
import logging
import os
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sampletrace.schemas import CanonicalSample, SampleSource

logger = logging.getLogger(__name__)


# Default mapping from Benchling-schema field names to our canonical fields.
# Tunable per-tenant via the YAML config; this is what shows up in the
# bundled mock fixtures.
_DEFAULT_SCHEMA_MAPPING: dict[str, str] = {
    "sample_id": "Sample ID",
    "library_id": "Library ID",
    "index_i7": "i7 index",
    "index_i5": "i5 index",
    "organism": "Organism",
    "tissue": "Tissue",
    "sample_type": "Sample Type",
    "project_id": "Sample Project",
}


class BenchlingConfig(BaseModel):
    """Validated Benchling configuration loaded from YAML or constructed in code."""

    model_config = ConfigDict(extra="forbid")

    tenant_url: str | None = None
    api_key: str | None = None
    schema_id: str | None = None
    project_id: str | None = None
    schema_mapping: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_SCHEMA_MAPPING))
    mock: bool = False
    mock_data_path: Path | None = None


def load_config(path: Path | str) -> BenchlingConfig:
    """Load and validate a ``bch.yml``-style config file.

    Recognized top-level keys: ``benchling:`` (BenchlingConfig fields) and
    ``schema_mapping:`` (merged into BenchlingConfig.schema_mapping). The
    ``reconciliation:`` section is read separately by the CLI and ignored
    here.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"benchling config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    bch_section: dict[str, Any] = raw.get("benchling", {}) or {}
    mapping_section: dict[str, str] = raw.get("schema_mapping", {}) or {}

    merged_mapping = dict(_DEFAULT_SCHEMA_MAPPING)
    merged_mapping.update(mapping_section)
    bch_section.setdefault("schema_mapping", merged_mapping)

    # Env var takes precedence over config file for the api_key, so
    # secrets don't accidentally end up in version control.
    env_key = os.environ.get("BENCHLING_API_KEY")
    if env_key:
        bch_section["api_key"] = env_key

    return BenchlingConfig(**bch_section)


def _map_entity(entity: dict[str, Any], mapping: dict[str, str]) -> CanonicalSample | None:
    """Convert one raw Benchling entity dict into a CanonicalSample."""
    fields = entity.get("fields", {}) or {}

    # ``mapping`` is canonical_field_name -> benchling_field_name.
    sample_id_field = mapping.get("sample_id", "Sample ID")
    sample_id = fields.get(sample_id_field) or entity.get("name") or entity.get("id")
    if not sample_id:
        return None

    kwargs: dict[str, Any] = {
        "sample_id": str(sample_id),
        "source": SampleSource.BENCHLING,
        "benchling_entity_id": entity.get("id"),
        "benchling_schema_id": entity.get("schema_id"),
    }
    extras: dict[str, Any] = {}

    canonical_to_bch = mapping
    for canonical_field, bch_field in canonical_to_bch.items():
        if canonical_field == "sample_id":
            continue
        value = fields.get(bch_field)
        if value in (None, ""):
            continue
        # Skip if the canonical field isn't on the schema (so unknown mappings
        # land in extras instead of crashing pydantic).
        if canonical_field in CanonicalSample.model_fields:
            kwargs[canonical_field] = value
        else:
            extras[bch_field] = value

    # Drop unmapped Benchling fields into extras for the audit trail.
    mapped_bch_fields = set(canonical_to_bch.values())
    for bch_field, value in fields.items():
        if bch_field not in mapped_bch_fields:
            extras[bch_field] = value

    if extras:
        kwargs["extra"] = extras
    return CanonicalSample(**kwargs)


def _load_mock_entities(path: Path | None) -> list[dict[str, Any]]:
    """Read mock entity records from a JSON file or the bundled default."""
    if path is not None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        with resources.files("sampletrace._mock_data").joinpath(
            "benchling_entities.json"
        ).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    entities = data.get("entities", data) if isinstance(data, dict) else data
    if not isinstance(entities, list):
        raise ValueError("mock data must be a list of entities or {'entities': [...]}")
    return entities


class BenchlingClient:
    """Fetches sample registrations from Benchling (or mock data)."""

    def __init__(self, config: BenchlingConfig):
        self.config = config
        self._sdk_client: Any | None = None  # lazily constructed

    @classmethod
    def from_yaml(cls, path: Path | str) -> BenchlingClient:
        return cls(load_config(path))

    @classmethod
    def mock(cls, mock_data_path: Path | str | None = None) -> BenchlingClient:
        """Convenience constructor for mock mode without a YAML file."""
        return cls(
            BenchlingConfig(
                mock=True,
                mock_data_path=Path(mock_data_path) if mock_data_path else None,
            )
        )

    @property
    def is_mock(self) -> bool:
        return self.config.mock

    def fetch_samples(self) -> list[CanonicalSample]:
        """Return all sample registrations matching the configured filters."""
        if self.is_mock:
            return self._fetch_mock()
        return self._fetch_real()

    def _fetch_mock(self) -> list[CanonicalSample]:
        entities = _load_mock_entities(self.config.mock_data_path)
        samples: list[CanonicalSample] = []
        for ent in entities:
            sample = _map_entity(ent, self.config.schema_mapping)
            if sample is not None:
                samples.append(sample)
        logger.info("loaded %d mock samples", len(samples))
        return samples

    def _fetch_real(self) -> list[CanonicalSample]:
        """Fetch from a real Benchling tenant via benchling-sdk.

        Imports the SDK lazily so users in mock mode never pay the import cost
        or need the optional dependency installed.
        """
        if not self.config.tenant_url or not self.config.api_key:
            raise ValueError(
                "real Benchling mode requires tenant_url and api_key "
                "(set BENCHLING_API_KEY env var or api_key in config)"
            )
        try:
            from benchling_sdk.auth.api_key_auth import ApiKeyAuth
            from benchling_sdk.benchling import Benchling
        except ImportError as e:
            raise ImportError(
                "benchling-sdk not installed. Install with: "
                "pip install 'sampletrace[benchling]' "
                "— or run in mock mode with --mock."
            ) from e

        if self._sdk_client is None:
            self._sdk_client = Benchling(
                url=self.config.tenant_url,
                auth_method=ApiKeyAuth(self.config.api_key),
            )

        # Page through custom entities matching the configured schema.
        raw_entities: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {}
        if self.config.schema_id:
            kwargs["schema_id"] = self.config.schema_id
        if self.config.project_id:
            kwargs["project_id"] = self.config.project_id

        pages = self._sdk_client.custom_entities.list(**kwargs)
        for page in pages:
            for entity in page:
                raw_entities.append(_entity_to_dict(entity))

        samples: list[CanonicalSample] = []
        for ent in raw_entities:
            sample = _map_entity(ent, self.config.schema_mapping)
            if sample is not None:
                samples.append(sample)
        logger.info("fetched %d Benchling samples", len(samples))
        return samples


def _entity_to_dict(entity: Any) -> dict[str, Any]:
    """Convert a benchling-sdk CustomEntity model to our internal dict shape."""
    fields: dict[str, Any] = {}
    sdk_fields = getattr(entity, "fields", None)
    if sdk_fields is not None:
        for field_name, field in (sdk_fields.additional_keys or {}).items() if hasattr(sdk_fields, "additional_keys") else []:
            value = getattr(field, "value", None) or getattr(field, "display_value", None)
            fields[field_name] = value
        # Best-effort: some SDK versions expose fields as a dict directly.
        if not fields and isinstance(sdk_fields, dict):
            for k, v in sdk_fields.items():
                fields[k] = getattr(v, "value", v) if not isinstance(v, str | int | float) else v
    return {
        "id": getattr(entity, "id", None),
        "name": getattr(entity, "name", None),
        "schema_id": getattr(getattr(entity, "schema", None), "id", None),
        "fields": fields,
    }
