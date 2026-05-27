"""Canonical pydantic v2 schemas for SampleTrace.

The CanonicalSample model is the single source of truth that every other
module converts to or from. Parsers produce these; the reconciler consumes
them; reports render them. If a field belongs here, it must be observable
in at least one real input source (Benchling registration, sample sheet,
FASTQ header, or count matrix).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SampleSource(StrEnum):
    """Where a sample observation came from."""

    BENCHLING = "benchling"
    SAMPLE_SHEET = "sample_sheet"
    FASTQ_HEADER = "fastq_header"
    COUNT_MATRIX = "count_matrix"


class MismatchKind(StrEnum):
    """Categorical reason a sample is flagged in the reconciliation report.

    Each kind has a stable string value because they appear in machine-readable
    outputs (mismatches.csv, sample_provenance.json) and downstream pipelines
    may switch on them.
    """

    MISSING_FROM_DOWNSTREAM = "missing_from_downstream"
    EXTRA_IN_DOWNSTREAM = "extra_in_downstream"
    ID_MISMATCH = "id_mismatch"
    AMBIGUOUS_FUZZY_MATCH = "ambiguous_fuzzy_match"
    SCHEMA_VIOLATION = "schema_violation"
    METADATA_DRIFT = "metadata_drift"


class MatchConfidence(StrEnum):
    """Traffic-light status for per-sample reconciliation."""

    EXACT = "exact"          # green: byte-identical match across sources
    HIGH = "high"            # green: fuzzy match >= 95
    MEDIUM = "medium"        # yellow: fuzzy match 80-95
    LOW = "low"              # red: fuzzy match < 80
    NONE = "none"            # red: no match at all

    @property
    def is_green(self) -> bool:
        return self in (MatchConfidence.EXACT, MatchConfidence.HIGH)

    @property
    def is_red(self) -> bool:
        return self in (MatchConfidence.LOW, MatchConfidence.NONE)


class CanonicalSample(BaseModel):
    """A single sample observation from one source.

    Field philosophy: ``sample_id`` is the only required field, because that
    is the minimum a sample sheet or FASTQ header can give us. Everything
    else is optional and only populated when the source provides it.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )

    sample_id: str = Field(
        ...,
        min_length=1,
        description="Primary identifier used to match across sources.",
    )
    source: SampleSource = Field(..., description="Origin of this observation.")

    # Optional fields populated when the source carries them.
    library_id: str | None = None
    project_id: str | None = None
    sample_name: str | None = None
    index_i7: str | None = None
    index_i5: str | None = None
    organism: str | None = None
    tissue: str | None = None
    sample_type: str | None = None
    lane: int | None = None
    flowcell_id: str | None = None
    run_id: str | None = None
    benchling_entity_id: str | None = None
    benchling_schema_id: str | None = None

    # Free-form bag for source-specific fields the canonical model doesn't
    # promote to first-class. Reconciler ignores this for matching but
    # reports surface it for audit.
    extra: dict[str, Any] = Field(default_factory=dict)

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When SampleTrace observed this record (not when it was created upstream).",
    )

    @field_validator("sample_id", "library_id", "project_id", "sample_name")
    @classmethod
    def _no_whitespace_only(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("must not be whitespace-only")
        return v

    @field_validator("index_i7", "index_i5")
    @classmethod
    def _validate_index(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.upper()
        # Illumina indices are ACGT, occasionally with N. Reject anything else
        # at schema time so the reconciler doesn't have to guess.
        if not all(c in "ACGTN" for c in v):
            raise ValueError(f"index must be ACGTN only, got {v!r}")
        return v

    @field_validator("lane")
    @classmethod
    def _validate_lane(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("lane must be >= 1")
        return v

    def merge_evidence(self, other: CanonicalSample) -> CanonicalSample:
        """Combine two observations of the same sample from different sources.

        Used by the reconciler when it has matched a sample sheet row to a
        Benchling registration: the returned sample carries fields from both.
        Caller-provided ``self`` wins on conflicts so the source-of-truth
        (Benchling) is preserved when ``self`` is the Benchling record.
        """
        merged_data = self.model_dump()
        for field_name, other_value in other.model_dump().items():
            if field_name in ("source", "observed_at", "extra"):
                continue
            if merged_data.get(field_name) in (None, "") and other_value not in (None, ""):
                merged_data[field_name] = other_value
        # Extras union; conflicts go to self.
        merged_extra = dict(other.extra)
        merged_extra.update(self.extra)
        merged_data["extra"] = merged_extra
        return CanonicalSample(**merged_data)


class ReconciliationRow(BaseModel):
    """One row of the reconciliation report — one sample's worth of status."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str
    confidence: MatchConfidence
    sources_present: list[SampleSource] = Field(default_factory=list)
    sources_missing: list[SampleSource] = Field(default_factory=list)
    mismatches: list[MismatchKind] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    matched_sample_ids: dict[str, str] = Field(
        default_factory=dict,
        description="When fuzzy-matched: source name -> the id we matched against.",
    )
    fuzzy_score: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Best fuzzy-match score for this row, 0-100 (None if not fuzzy-matched).",
    )

    @property
    def is_flagged(self) -> bool:
        """True when this row needs human attention."""
        return bool(self.mismatches) or self.confidence.is_red

    @model_validator(mode="after")
    def _check_confidence_consistency(self) -> ReconciliationRow:
        if self.confidence == MatchConfidence.NONE and not self.sources_missing:
            raise ValueError("confidence=NONE requires at least one missing source")
        return self


class ReconciliationReport(BaseModel):
    """Aggregate report across all samples in a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sources_used: list[SampleSource] = Field(default_factory=list)
    rows: list[ReconciliationRow] = Field(default_factory=list)
    benchling_samples: list[CanonicalSample] = Field(default_factory=list)
    downstream_samples: list[CanonicalSample] = Field(default_factory=list)
    tool_version: str = "0.1.0"

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def flagged(self) -> list[ReconciliationRow]:
        return [r for r in self.rows if r.is_flagged]

    @property
    def green(self) -> list[ReconciliationRow]:
        return [r for r in self.rows if r.confidence.is_green and not r.mismatches]

    @property
    def yellow(self) -> list[ReconciliationRow]:
        return [
            r for r in self.rows
            if r.confidence == MatchConfidence.MEDIUM and not r.confidence.is_red
        ]

    @property
    def red(self) -> list[ReconciliationRow]:
        return [r for r in self.rows if r.confidence.is_red]

    def summary(self) -> dict[str, int]:
        return {
            "total": self.total,
            "green": len(self.green),
            "yellow": len(self.yellow),
            "red": len(self.red),
            "flagged": len(self.flagged),
        }
