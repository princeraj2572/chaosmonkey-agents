"""Terminal, JSON, and self-contained HTML reports."""

from __future__ import annotations

import difflib
import html
import json
from pathlib import Path
from typing import Any, Mapping


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError("report input must be a mapping or expose to_dict()")


def _runs(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(run) for run in data.get("runs", []) if isinstance(run, Mapping)]


def _counts(data: Mapping[str, Any]) -> dict[str, int]:
    counts = data.get("counts")
    if isinstance(counts, Mapping):
        return {str(key): int(value) for key, value in counts.items()}
    found: dict[str, int] = {}
    for run in _runs(data):
        outcome = str(run.get("outcome", "unknown"))
        found[outcome] = found.get(outcome, 0) + 1
    return found


def _severity(outcome: Any) -> int:
    return {"hijacked": 6, "silent_failure": 5, "error": 4, "timeout": 4, "unknown": 3, "flagged": 2, "recovered": 1, "unaffected": 0, "not_injected": 0}.get(str(outcome), 3)


def _silent_rate(data: Mapping[str, Any]) -> float:
    if "silent_failure_rate" in data:
        return float(data["silent_failure_rate"])
    chaos = [run for run in _runs(data) if run.get("mode") != "baseline" and run.get("injected")]
    return sum(run.get("outcome") == "silent_failure" for run in chaos) / len(chaos) if chaos else 0.0


def worst_traces(result: Any, limit: int = 3) -> list[dict[str, Any]]:
    data = _as_dict(result)
    runs = [run for run in _runs(data) if run.get("mode") != "baseline"]
    return sorted(runs, key=lambda run: (-_severity(run.get("outcome")), run.get("seed") if run.get("seed") is not None else -1))[:limit]


def render_json(result: Any, *, indent: int = 2) -> str:
    return json.dumps(_as_dict(result), indent=indent, sort_keys=True, default=str) + "\n"


MOCK_NOTE = "mock-naive and mock-careful are scripted stand-ins, not language models; their numbers do not describe real model behavior."


def _mock_note(data: Mapping[str, Any]) -> str:
    return MOCK_NOTE if str((data.get("metadata") or {}).get("model", "")).startswith("mock-") else ""


def render_terminal(result: Any, *, worst: int = 3) -> str:
    data = _as_dict(result)
    counts = _counts(data)
    lines = [f"ChaosMonkey report: {data.get('example', 'experiment')}"]
    lines.append(f"runs={len(_runs(data))} seeds={data.get('seeds', [])} model={(data.get('metadata') or {}).get('model', 'custom')}")
    lines.append("outcomes: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    lines.append(f"silent failure rate: {_silent_rate(data):.1%}")
    if _mock_note(data):
        lines.append("note: " + MOCK_NOTE)
    baseline = next((run for run in _runs(data) if run.get("mode") == "baseline"), None)
    if baseline:
        lines.append(f"baseline: {baseline.get('outcome', 'unknown')} ({baseline.get('duration_ms', 0)} ms)")
    bad = worst_traces(data, worst)
    if bad:
        lines.append("worst traces:")
        for run in bad:
            judgement = run.get("judgement") or {}
            lines.append(f"  seed {run.get('seed')}: {run.get('outcome', 'unknown')} — {judgement.get('reason', run.get('error', ''))}")
            for event in run.get("trace", []):
                detail = f": {event.get('detail')}" if event.get("detail") else ""
                lines.append(f"    [{event.get('step', '?')}] {event.get('kind', 'event')}.{event.get('name', '')}{detail}")
    return "\n".join(lines) + "\n"


_STYLE = """
:root{color-scheme:light dark;--bg:#f6f8fa;--fg:#17202a;--muted:#5f6b76;--card:#fff;--line:#d0d7de;--danger:#b42318;--warn:#b54708;--good:#067647}
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:var(--fg);background:var(--bg)}
.muted{color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.75rem;margin:1rem 0 2rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:.5rem;padding:1rem;text-align:center}.card .label{color:var(--muted);font-size:.9rem}
.value{font-size:1.7rem;font-weight:700;margin-top:.25rem}.headline{border:2px solid var(--danger)}
.unaffected,.not_injected,.recovered{color:var(--good)}.flagged,.unknown{color:var(--warn)}.silent_failure,.hijacked,.error,.timeout{color:var(--danger)}
table{border-collapse:collapse;width:100%;background:var(--card);margin-bottom:1.5rem}
th,td{border:1px solid var(--line);padding:.55rem;text-align:left;vertical-align:top}th{background:color-mix(in srgb,var(--card) 75%,var(--line))}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#111827;color:#e5e7eb;border-radius:.5rem;padding:1rem}.diff .removed{color:#fecaca}.diff .added{color:#bbf7d0}
@media(max-width:600px){body{margin:1rem auto;padding:0 .6rem}table{font-size:.85rem}th,td{padding:.35rem}}
"""


def _trace_html(run: Mapping[str, Any]) -> str:
    lines = []
    for event in run.get("trace", []):
        lines.append(f"[{event.get('step', '?')}] {event.get('kind', 'event')}.{event.get('name', '')} {event.get('detail', '')}")
    return "\n".join(lines)


def _transcript_html(run: Mapping[str, Any]) -> str:
    output = run.get("output")
    transcript = output.get("transcript") if isinstance(output, Mapping) else None
    if transcript is None:
        transcript = run.get("transcript", [])
    return json.dumps(transcript or [], indent=2, ensure_ascii=False, default=str)


def _records_html(run: Mapping[str, Any]) -> str:
    blocks = []
    for record in run.get("tool_records", []):
        original = str(record.get("original", "")) if isinstance(record, Mapping) else ""
        delivered = str(record.get("delivered", "")) if isinstance(record, Mapping) else ""
        diff = "\n".join(difflib.ndiff(original.splitlines(), delivered.splitlines()))
        blocks.append(
            f"<h4>{html.escape(str(record.get('tool', 'tool')))} — fault: {html.escape(str(record.get('fault') or 'none'))}</h4>"
            f"<pre>{html.escape(diff)}</pre>"
        )
    return "".join(blocks) or "<p class=\"muted\">No wrapped tool calls.</p>"


def render_html(result: Any, *, title: str = "ChaosMonkey report", worst: int = 5) -> str:
    data = _as_dict(result)
    counts = _counts(data)
    cards = (
        f'<div class="card headline"><div class="label">silent failure rate</div><div class="value silent_failure">{_silent_rate(data):.1%}</div></div>'
        + "".join(f'<div class="card"><div class="label">{html.escape(key)}</div><div class="value {html.escape(key)}">{value}</div></div>' for key, value in sorted(counts.items()))
    )
    rows = []
    for run in _runs(data):
        outcome = str(run.get("outcome", "unknown"))
        judgement = run.get("judgement") or {}
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(run.get('mode', '')))}</td><td>{html.escape(str(run.get('seed', '—')))}</td>"
            f"<td class=\"{html.escape(outcome)}\">{html.escape(outcome)}</td>"
            f"<td>{html.escape(str(run.get('fault') or '—'))}</td><td>{html.escape(str(run.get('call_index') if run.get('call_index') is not None else '—'))}</td>"
            f"<td>{html.escape(str(judgement.get('reason', run.get('error', ''))))}</td></tr>"
        )
    details = []
    for run in worst_traces(data, worst):
        label = f"seed {run.get('seed')}: {run.get('outcome', 'unknown')}"
        details.append(
            f"<article><h3>{html.escape(label)}</h3><p>fault: {html.escape(str(run.get('fault') or 'none'))}; call index: {html.escape(str(run.get('call_index') if run.get('call_index') is not None else '—'))}</p>"
            f"<h4>Trace</h4><pre>{html.escape(_trace_html(run))}</pre><h4>Tool output diff</h4>{_records_html(run)}"
            f"<h4>Agent transcript</h4><pre>{html.escape(_transcript_html(run))}</pre></article>"
        )
    body = (
        f"<h1>{html.escape(title)}</h1><p class=\"muted\">Example: {html.escape(str(data.get('example', 'experiment')))} · "
        f"Model: {html.escape(str((data.get('metadata') or {}).get('model', 'custom')))} · seeds: {html.escape(str(data.get('seeds', [])))}</p>"
        f"<p class=\"muted\">{html.escape(_mock_note(data))}</p>"
        f"<div class=\"cards\">{cards}</div><h2>Runs</h2>"
        "<table><thead><tr><th>Mode</th><th>Seed</th><th>Outcome</th><th>Fault</th><th>Call</th><th>Reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table><h2>Worst traces</h2>{''.join(details) or '<p>No chaos traces.</p>'}"
    )
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>\n"


def _comparison_rows(left: Mapping[str, Any], right: Mapping[str, Any]) -> str:
    left_counts, right_counts = _counts(left), _counts(right)
    outcomes = sorted(set(left_counts) | set(right_counts))
    rows = [
        f"<tr><td>{html.escape(outcome)}</td><td>{left_counts.get(outcome, 0)}</td><td>{right_counts.get(outcome, 0)}</td><td>{right_counts.get(outcome, 0) - left_counts.get(outcome, 0):+d}</td></tr>"
        for outcome in outcomes
    ]
    rows.append(f"<tr><th>silent failure rate</th><th>{_silent_rate(left):.1%}</th><th>{_silent_rate(right):.1%}</th><th>{_silent_rate(right) - _silent_rate(left):+.1%}</th></tr>")
    return "".join(rows)


def render_compare_html(left: Any, right: Any, *, left_name: str = "A", right_name: str = "B", title: str = "ChaosMonkey comparison") -> str:
    left_data, right_data = _as_dict(left), _as_dict(right)
    body = (
        f"<h1>{html.escape(title)}</h1><p class=\"muted\">{html.escape(left_name)}: {html.escape(str(left_data.get('example', 'experiment')))} · {html.escape(right_name)}: {html.escape(str(right_data.get('example', 'experiment')))}</p>"
        f"<table><thead><tr><th>Outcome</th><th>{html.escape(left_name)}</th><th>{html.escape(right_name)}</th><th>Delta</th></tr></thead><tbody>{_comparison_rows(left_data, right_data)}</tbody></table>"
        f"<h2>Worst traces — {html.escape(left_name)}</h2>" + "".join(f"<h3>seed {run.get('seed')}</h3><pre>{html.escape(_trace_html(run))}</pre>" for run in worst_traces(left_data))
        + f"<h2>Worst traces — {html.escape(right_name)}</h2>" + "".join(f"<h3>seed {run.get('seed')}</h3><pre>{html.escape(_trace_html(run))}</pre>" for run in worst_traces(right_data))
    )
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title><style>{_STYLE}</style></head><body>{body}</body></html>\n"


def write_report(result: Any, destination: str | Path, *, format: str = "html") -> Path:
    path = Path(destination)
    renderers = {"terminal": render_terminal, "text": render_terminal, "json": render_json, "html": render_html}
    try:
        content = renderers[format.lower()](result)
    except KeyError as exc:
        raise ValueError(f"unsupported report format: {format}") from exc
    path.write_text(content, encoding="utf-8")
    return path


__all__ = ["render_terminal", "render_json", "render_html", "render_compare_html", "worst_traces", "write_report"]
