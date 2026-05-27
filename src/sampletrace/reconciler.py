"""Reconcile Benchling registrations against downstream NGS observations.

The reconciler is intentionally dumb about *where* data comes from — it takes
two lists of ``CanonicalSample`` (one trusted: Benchling; one observed:
parsed from run artifacts) and produces a ``ReconciliationReport``.

Matching strategy (in order):

1. **Exact**: ``sample_id`` byte-equal across both sides.
2. **Normalized**: case-insensitive, whitespace/punctuation collapsed.
3. **Fuzzy**: rapidfuzz token_sort_ratio above a configurable threshold.

We deliberately separate "the IDs look similar" from "this is a confident
match." A fuzzy hit at 87 is reported as ``MEDIUM`` and surfaced as
``AMBIGUOUS_FUZZY_MATCH`` so a human can sign off — silent fuzzy matching is
how sample swaps slip through.

Metadata drift checks (organism, indices, etc.) only run on matched pairs;
they answer "is the *content* of this row consistent?", not "do we even
have this sample?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from sampletrace.schemas import (
    CanonicalSample,
    MatchConfidence,
    MismatchKind,
    ReconciliationReport,
    ReconciliationRow,
    SampleSource,
)

_NORMALIZE_RE = re.compile(r"[\s\-_.]+")


@dataclass(frozen=True)
class ReconcilerConfig:
    """Tuning knobs for matching and drift detection."""

    fuzzy_threshold: float = 80.0  # below this: no match
    high_confidence: float = 95.0  # at or above (and not exact): HIGH
    require_index_match: bool = True
    check_organism: bool = True
    drift_fields: tuple[str, ...] = (
        "organism",
        "tissue",
        "sample_type",
        "project_id",
        "library_id",
        "index_i7",
        "index_i5",
    )

    def __post_init__(self) -> None:
        if not 0 <= self.fuzzy_threshold <= 100:
            raise ValueError("fuzzy_threshold must be in [0, 100]")
        if not 0 <= self.high_confidence <= 100:
            raise ValueError("high_confidence must be in [0, 100]")
        if self.high_confidence < self.fuzzy_threshold:
            raise ValueError("high_confidence must be >= fuzzy_threshold")


@dataclass
class _MatchResult:
    """Internal result of trying to match one Benchling sample to downstream."""

    benchling: CanonicalSample
    matches_by_source: dict[SampleSource, CanonicalSample] = field(default_factory=dict)
    fuzzy_scores: dict[SampleSource, float] = field(default_factory=dict)
    confidence: MatchConfidence = MatchConfidence.NONE
    mismatches: list[MismatchKind] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    matched_ids: dict[str, str] = field(default_factory=dict)


def _normalize_id(s: str) -> str:
    return _NORMALIZE_RE.sub("", s).lower()


def _classify_score(score: float, cfg: ReconcilerConfig, exact: bool) -> MatchConfidence:
    if exact:
        return MatchConfidence.EXACT
    if score >= cfg.high_confidence:
        return MatchConfidence.HIGH
    if score >= cfg.fuzzy_threshold:
        return MatchConfidence.MEDIUM
    if score > 0:
        return MatchConfidence.LOW
    return MatchConfidence.NONE


def _match_one(
    bch: CanonicalSample,
    candidates_by_source: dict[SampleSource, list[CanonicalSample]],
    cfg: ReconcilerConfig,
) -> _MatchResult:
    """Find the best match in each downstream source for one Benchling sample."""
    result = _MatchResult(benchling=bch)
    bch_norm = _normalize_id(bch.sample_id)

    best_overall_score = 0.0
    any_exact = False
    any_fuzzy_only = False

    for source, candidates in candidates_by_source.items():
        if not candidates:
            continue

        # Tier 1: exact.
        for c in candidates:
            if c.sample_id == bch.sample_id:
                result.matches_by_source[source] = c
                result.fuzzy_scores[source] = 100.0
                any_exact = True
                break
        if source in result.matches_by_source:
            continue

        # Tier 2: normalized exact.
        for c in candidates:
            if _normalize_id(c.sample_id) == bch_norm:
                result.matches_by_source[source] = c
                result.fuzzy_scores[source] = 99.0
                result.notes.append(
                    f"{source.value}: matched on normalized id ({c.sample_id!r} ~ {bch.sample_id!r})"
                )
                result.matched_ids[source.value] = c.sample_id
                any_fuzzy_only = True
                break
        if source in result.matches_by_source:
            continue

        # Tier 3: fuzzy.
        choices = {c.sample_id: c for c in candidates}
        best = process.extractOne(bch.sample_id, list(choices.keys()), scorer=fuzz.token_sort_ratio)
        if best is None:
            continue
        match_id, score, _ = best
        if score >= cfg.fuzzy_threshold:
            matched = choices[match_id]
            result.matches_by_source[source] = matched
            result.fuzzy_scores[source] = float(score)
            result.matched_ids[source.value] = match_id
            any_fuzzy_only = True
            if score < cfg.high_confidence:
                result.mismatches.append(MismatchKind.AMBIGUOUS_FUZZY_MATCH)
                result.notes.append(
                    f"{source.value}: fuzzy match {match_id!r} score={score:.1f} — needs review"
                )
        else:
            # Track best-effort score even if below threshold so reports can show it.
            best_overall_score = max(best_overall_score, float(score))

    # Aggregate confidence: the *worst* matched source defines the row,
    # because one weak match means human attention.
    if not result.matches_by_source:
        result.confidence = MatchConfidence.LOW if best_overall_score > 0 else MatchConfidence.NONE
    elif any_exact and not any_fuzzy_only:
        result.confidence = MatchConfidence.EXACT
    else:
        worst = min(result.fuzzy_scores.values())
        result.confidence = _classify_score(worst, cfg, exact=False)

    return result


def _check_drift(
    bch: CanonicalSample, observed: CanonicalSample, cfg: ReconcilerConfig
) -> list[str]:
    """Return human-readable drift notes between matched samples on a single field."""
    notes: list[str] = []
    for field_name in cfg.drift_fields:
        a = getattr(bch, field_name, None)
        b = getattr(observed, field_name, None)
        if a in (None, "") or b in (None, ""):
            continue
        if str(a).lower() != str(b).lower():
            notes.append(f"{field_name}: Benchling={a!r} vs {observed.source.value}={b!r}")
    return notes


def reconcile(
    benchling_samples: list[CanonicalSample],
    downstream_samples: list[CanonicalSample],
    config: ReconcilerConfig | None = None,
    run_id: str | None = None,
) -> ReconciliationReport:
    """Produce a full reconciliation report.

    Args:
        benchling_samples: trusted registrations from Benchling.
        downstream_samples: parsed observations from sample sheets, FASTQs,
            count matrices. Source is read from each sample's ``.source``.
        config: tuning knobs; sensible defaults if omitted.
        run_id: optional run identifier to embed in the report.
    """
    cfg = config or ReconcilerConfig()

    # Bucket downstream by source.
    by_source: dict[SampleSource, list[CanonicalSample]] = {}
    for s in downstream_samples:
        by_source.setdefault(s.source, []).append(s)

    sources_used = [SampleSource.BENCHLING, *sorted(by_source.keys(), key=lambda x: x.value)]

    rows: list[ReconciliationRow] = []
    matched_observed_ids: dict[SampleSource, set[str]] = {src: set() for src in by_source}

    for bch in benchling_samples:
        m = _match_one(bch, by_source, cfg)

        # Drift checks on matched pairs.
        for src, obs in m.matches_by_source.items():
            drift = _check_drift(bch, obs, cfg)
            if drift:
                m.mismatches.append(MismatchKind.METADATA_DRIFT)
                m.notes.extend(f"{src.value} drift — {d}" for d in drift)
            matched_observed_ids[src].add(obs.sample_id)

        sources_present = [
            SampleSource.BENCHLING,
            *sorted(m.matches_by_source.keys(), key=lambda x: x.value),
        ]
        sources_missing = [src for src in by_source if src not in m.matches_by_source]

        # If any expected downstream source is missing the sample, flag it.
        if sources_missing:
            m.mismatches.append(MismatchKind.MISSING_FROM_DOWNSTREAM)
            m.notes.append(f"missing from: {', '.join(s.value for s in sources_missing)}")
            if m.confidence == MatchConfidence.EXACT:
                # Still exact for what matched, but downgrade so it's not green.
                m.confidence = MatchConfidence.MEDIUM

        # If we used fuzzy matching and didn't already flag as ambiguous, mark as id_mismatch.
        if (
            any(score < 100.0 for score in m.fuzzy_scores.values())
            and MismatchKind.AMBIGUOUS_FUZZY_MATCH not in m.mismatches
        ):
            m.mismatches.append(MismatchKind.ID_MISMATCH)

        best_score = max(m.fuzzy_scores.values()) if m.fuzzy_scores else None

        # Dedup mismatches.
        seen: set[MismatchKind] = set()
        deduped: list[MismatchKind] = []
        for kind in m.mismatches:
            if kind not in seen:
                deduped.append(kind)
                seen.add(kind)

        rows.append(
            ReconciliationRow(
                sample_id=bch.sample_id,
                confidence=m.confidence,
                sources_present=sources_present,
                sources_missing=sources_missing
                if m.confidence != MatchConfidence.NONE
                else [
                    *sources_missing,
                    # If we matched nothing at all, all downstream are "missing" for this id.
                ],
                mismatches=deduped,
                notes=m.notes,
                matched_sample_ids=m.matched_ids,
                fuzzy_score=best_score,
            )
        )

    # Extras: downstream samples that didn't match any Benchling registration.
    for src, candidates in by_source.items():
        matched = matched_observed_ids[src]
        for c in candidates:
            if c.sample_id in matched:
                continue
            # Is this sample id also in another row's matched_ids? If yes, skip.
            if any(row.matched_sample_ids.get(src.value) == c.sample_id for row in rows):
                continue
            rows.append(
                ReconciliationRow(
                    sample_id=c.sample_id,
                    confidence=MatchConfidence.NONE,
                    sources_present=[src],
                    sources_missing=[SampleSource.BENCHLING],
                    mismatches=[MismatchKind.EXTRA_IN_DOWNSTREAM],
                    notes=[
                        f"{src.value} has sample {c.sample_id!r} with no Benchling registration"
                    ],
                )
            )

    rows.sort(key=lambda r: (not r.is_flagged, r.sample_id))

    return ReconciliationReport(
        run_id=run_id,
        sources_used=sources_used,
        rows=rows,
        benchling_samples=benchling_samples,
        downstream_samples=downstream_samples,
    )
