"""Render ``ReconciliationReport`` into the four output formats.

Outputs (one per call to ``write_all``):

- ``reconciliation_report.html`` — self-contained, no external assets
- ``reconciliation_report.md`` — same content, Markdown for Slack/PR review
- ``mismatches.csv`` — flagged rows only, for spreadsheet triage
- ``sample_provenance.json`` — full audit trail (every row, every source)

The HTML uses inline CSS so it works when emailed or attached to a ticket —
no broken stylesheets, no CDN dependency.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from sampletrace.schemas import MatchConfidence, ReconciliationReport, ReconciliationRow

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SampleTrace reconciliation — {{ report.run_id or "unnamed run" }}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 1100px; margin: 2em auto; padding: 0 1em; color: #1a1a1a; }
  h1 { border-bottom: 2px solid #ddd; padding-bottom: 0.3em; }
  .summary { display: flex; gap: 1em; flex-wrap: wrap; margin: 1em 0 2em 0; }
  .stat { padding: 0.7em 1.2em; border-radius: 6px; background: #f5f5f5; min-width: 70px; }
  .stat .num { font-size: 1.6em; font-weight: 600; display: block; }
  .stat .label { font-size: 0.85em; color: #555; text-transform: uppercase; letter-spacing: 0.05em; }
  .stat.green { background: #e6f4ea; } .stat.green .num { color: #1e8e3e; }
  .stat.yellow { background: #fef7e0; } .stat.yellow .num { color: #b07000; }
  .stat.red { background: #fce8e6; } .stat.red .num { color: #c5221f; }
  table { width: 100%; border-collapse: collapse; margin-top: 1em; font-size: 0.92em; }
  th, td { padding: 0.5em 0.7em; text-align: left; border-bottom: 1px solid #eee; vertical-align: top; }
  th { background: #fafafa; font-weight: 600; font-size: 0.85em; text-transform: uppercase;
       letter-spacing: 0.04em; color: #555; }
  tr.green { background: #fbfff7; }
  tr.yellow { background: #fffcef; }
  tr.red { background: #fff5f4; }
  .badge { display: inline-block; padding: 0.15em 0.55em; border-radius: 10px;
           font-size: 0.78em; font-weight: 600; }
  .badge.green { background: #1e8e3e; color: white; }
  .badge.yellow { background: #b07000; color: white; }
  .badge.red { background: #c5221f; color: white; }
  .mismatch { display: inline-block; background: #f5f5f5; padding: 0.1em 0.45em;
              border-radius: 3px; font-size: 0.82em; margin-right: 0.3em; }
  .notes { font-size: 0.85em; color: #666; margin-top: 0.3em; }
  .meta { color: #666; font-size: 0.9em; }
  footer { margin-top: 3em; padding-top: 1em; border-top: 1px solid #eee;
           color: #888; font-size: 0.85em; }
</style>
</head>
<body>
<h1>Reconciliation report{% if report.run_id %} — {{ report.run_id }}{% endif %}</h1>
<p class="meta">Generated {{ report.generated_at.strftime("%Y-%m-%d %H:%M UTC") }}
   &middot; Sources: {{ report.sources_used | map(attribute="value") | join(", ") }}
   &middot; SampleTrace v{{ report.tool_version }}</p>

<div class="summary">
  <div class="stat"><span class="num">{{ summary.total }}</span><span class="label">Total</span></div>
  <div class="stat green"><span class="num">{{ summary.green }}</span><span class="label">Green</span></div>
  <div class="stat yellow"><span class="num">{{ summary.yellow }}</span><span class="label">Yellow</span></div>
  <div class="stat red"><span class="num">{{ summary.red }}</span><span class="label">Red</span></div>
  <div class="stat"><span class="num">{{ summary.flagged }}</span><span class="label">Flagged</span></div>
</div>

<table>
<thead><tr>
  <th>Sample</th>
  <th>Confidence</th>
  <th>Sources present</th>
  <th>Missing</th>
  <th>Issues</th>
</tr></thead>
<tbody>
{% for row in report.rows %}
  <tr class="{{ traffic_light(row) }}">
    <td><strong>{{ row.sample_id }}</strong>
      {% if row.matched_sample_ids %}
        <div class="notes">matched as: {{ row.matched_sample_ids | tojson }}</div>
      {% endif %}
    </td>
    <td><span class="badge {{ traffic_light(row) }}">{{ row.confidence.value }}</span>
      {% if row.fuzzy_score is not none %}
        <div class="notes">score: {{ "%.1f" | format(row.fuzzy_score) }}</div>
      {% endif %}
    </td>
    <td>{{ row.sources_present | map(attribute="value") | join(", ") }}</td>
    <td>{{ row.sources_missing | map(attribute="value") | join(", ") or "—" }}</td>
    <td>
      {% for m in row.mismatches %}<span class="mismatch">{{ m.value }}</span>{% endfor %}
      {% if not row.mismatches %}—{% endif %}
      {% if row.notes %}
        <div class="notes">{% for n in row.notes %}{{ n }}<br>{% endfor %}</div>
      {% endif %}
    </td>
  </tr>
{% endfor %}
</tbody>
</table>

{% if not report.rows %}
<p><em>No samples to report.</em></p>
{% endif %}

<footer>SampleTrace — Benchling-linked NGS metadata reconciliation. GPL v3.</footer>
</body>
</html>
"""

_MD_TEMPLATE = """# Reconciliation report{% if report.run_id %} — {{ report.run_id }}{% endif %}

Generated: {{ report.generated_at.strftime("%Y-%m-%d %H:%M UTC") }}
Sources: {{ report.sources_used | map(attribute="value") | join(", ") }}
Total samples: **{{ summary.total }}** — {{ summary.green }} green, {{ summary.yellow }} yellow, {{ summary.red }} red, **{{ summary.flagged }} flagged**

| Sample | Confidence | Sources present | Missing | Issues |
|--------|------------|-----------------|---------|--------|
{% for row in report.rows -%}
| `{{ row.sample_id }}` | {{ traffic_light(row) }} `{{ row.confidence.value }}` | {{ row.sources_present | map(attribute="value") | join(", ") }} | {{ row.sources_missing | map(attribute="value") | join(", ") or "—" }} | {{ (row.mismatches | map(attribute="value") | join(", ")) or "—" }} |
{% endfor %}

{% if report.flagged -%}
## Flagged samples — details

{% for row in report.flagged %}
### `{{ row.sample_id }}` — {{ row.confidence.value }}

{% if row.matched_sample_ids %}- Matched as: `{{ row.matched_sample_ids | tojson }}`
{% endif -%}
{% if row.fuzzy_score is not none %}- Fuzzy score: {{ "%.1f" | format(row.fuzzy_score) }}
{% endif -%}
{% if row.mismatches %}- Issues: {{ row.mismatches | map(attribute="value") | join(", ") }}
{% endif -%}
{% if row.notes %}- Notes:
{% for n in row.notes %}  - {{ n }}
{% endfor %}
{% endif %}
{% endfor %}
{% endif %}

---
Generated by SampleTrace v{{ report.tool_version }}.
"""


def _traffic_light(row: ReconciliationRow) -> str:
    if row.is_flagged or row.confidence.is_red:
        return "red"
    if row.confidence == MatchConfidence.MEDIUM:
        return "yellow"
    return "green"


def render_html(report: ReconciliationReport) -> str:
    """Render the report as a single self-contained HTML document."""
    env = Environment(autoescape=select_autoescape(["html"]))
    env.globals["traffic_light"] = _traffic_light
    template = env.from_string(_HTML_TEMPLATE)
    return template.render(report=report, summary=report.summary())


def render_markdown(report: ReconciliationReport) -> str:
    """Render the report as Markdown suitable for PR / Slack."""
    env = Environment(autoescape=False)
    env.globals["traffic_light"] = lambda row: {"red": "🔴", "yellow": "🟡", "green": "🟢"}[
        _traffic_light(row)
    ]
    template = env.from_string(_MD_TEMPLATE)
    return template.render(report=report, summary=report.summary())


def write_mismatches_csv(report: ReconciliationReport, path: Path) -> None:
    """Write flagged rows only — for spreadsheet triage."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "sample_id",
                "confidence",
                "mismatches",
                "sources_present",
                "sources_missing",
                "fuzzy_score",
                "matched_sample_ids",
                "notes",
            ]
        )
        for row in report.flagged:
            writer.writerow(
                [
                    row.sample_id,
                    row.confidence.value,
                    ";".join(m.value for m in row.mismatches),
                    ";".join(s.value for s in row.sources_present),
                    ";".join(s.value for s in row.sources_missing),
                    "" if row.fuzzy_score is None else f"{row.fuzzy_score:.1f}",
                    json.dumps(row.matched_sample_ids, sort_keys=True),
                    " | ".join(row.notes),
                ]
            )


def write_provenance_json(report: ReconciliationReport, path: Path) -> None:
    """Write the full audit trail as JSON — every row, every source observation."""
    data: dict[str, Any] = {
        "run_id": report.run_id,
        "generated_at": report.generated_at.isoformat(),
        "tool_version": report.tool_version,
        "sources_used": [s.value for s in report.sources_used],
        "summary": report.summary(),
        "rows": [row.model_dump(mode="json") for row in report.rows],
        "benchling_samples": [s.model_dump(mode="json") for s in report.benchling_samples],
        "downstream_samples": [s.model_dump(mode="json") for s in report.downstream_samples],
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def write_all(report: ReconciliationReport, output_dir: Path) -> dict[str, Path]:
    """Write all four output files into ``output_dir``. Returns paths written."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / "reconciliation_report.html"
    md_path = output_dir / "reconciliation_report.md"
    csv_path = output_dir / "mismatches.csv"
    json_path = output_dir / "sample_provenance.json"

    html_path.write_text(render_html(report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    write_mismatches_csv(report, csv_path)
    write_provenance_json(report, json_path)
    return {
        "html": html_path,
        "markdown": md_path,
        "csv": csv_path,
        "json": json_path,
    }
