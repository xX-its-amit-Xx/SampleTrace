"""Streamlit dashboard for interactive triage of a reconciliation report.

Reads ``sample_provenance.json`` (produced by ``sampletrace reconcile``) and
lets the user filter by status, drill into per-sample notes, and export the
flagged subset. Kept deliberately simple — the CLI's HTML report is the
primary deliverable; this is for the human-in-the-loop pass after a run.

Launched via ``sampletrace dashboard --report <path>``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def _load_report(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": row["sample_id"],
        "confidence": row["confidence"],
        "is_flagged": bool(row["mismatches"]) or row["confidence"] in {"low", "none"},
        "sources_present": ", ".join(row.get("sources_present", [])),
        "sources_missing": ", ".join(row.get("sources_missing", [])) or "—",
        "mismatches": ", ".join(row.get("mismatches", [])) or "—",
        "fuzzy_score": row.get("fuzzy_score"),
        "notes": " | ".join(row.get("notes", [])),
    }


def main() -> None:
    args = _parse_args()
    st.set_page_config(page_title="SampleTrace", layout="wide")
    st.title("SampleTrace reconciliation")

    report = _load_report(args.report)
    summary = report["summary"]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", summary["total"])
    c2.metric("Green", summary["green"])
    c3.metric("Yellow", summary["yellow"])
    c4.metric("Red", summary["red"])
    c5.metric("Flagged", summary["flagged"])

    st.caption(
        f"Run: {report.get('run_id') or 'unnamed'} · "
        f"generated {report.get('generated_at')} · "
        f"sources: {', '.join(report.get('sources_used', []))}"
    )

    df = pd.DataFrame([_row_to_record(r) for r in report["rows"]])

    st.sidebar.header("Filters")
    show_flagged_only = st.sidebar.checkbox("Show flagged only", value=True)
    confidences = st.sidebar.multiselect(
        "Confidence",
        ["exact", "high", "medium", "low", "none"],
        default=["medium", "low", "none"] if show_flagged_only else [],
    )

    filtered = df
    if show_flagged_only:
        filtered = filtered[filtered["is_flagged"]]
    if confidences:
        filtered = filtered[filtered["confidence"].isin(confidences)]

    st.subheader(f"Samples ({len(filtered)} of {len(df)})")
    st.dataframe(filtered, width="stretch", hide_index=True)

    if not filtered.empty:
        st.download_button(
            "Download filtered as CSV",
            filtered.to_csv(index=False),
            file_name="filtered_samples.csv",
            mime="text/csv",
        )

    st.subheader("Sample drilldown")
    if not df.empty:
        sample_id = st.selectbox("Pick a sample", options=df["sample_id"].tolist())
        row = next(r for r in report["rows"] if r["sample_id"] == sample_id)
        st.json(row)


if __name__ == "__main__":
    main()
