#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a Codex proxy session report JSON file to single-page HTML."
    )
    parser.add_argument("report_json", type=Path, help="Path to report.json.")
    parser.add_argument(
        "--out",
        type=Path,
        help="Output HTML path. Defaults to report.html next to the JSON file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = json.loads(args.report_json.read_text(encoding="utf-8"))
    out_path = args.out or args.report_json.with_suffix(".html")
    out_path.write_text(render_html(report), encoding="utf-8")
    print(out_path)
    return 0


def render_html(report: dict[str, Any]) -> str:
    title = "Codex Proxy Session Report"
    data = json.dumps(report, sort_keys=True).replace("</", "<\\/")
    window = report["window"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    @layer reset, tokens, base, layout, components, utilities;
    @layer reset {{
      *, *::before, *::after {{ box-sizing: border-box; }}
      html {{ scroll-behavior: smooth; }}
      body {{ margin: 0; }}
      button, input {{ font: inherit; }}
    }}
    @layer tokens {{
      :root {{
        --ink: #17202a;
        --muted: #5d6978;
        --paper: #f6f8fb;
        --panel: #ffffff;
        --line: #d9e0ea;
        --line-strong: #b8c3d3;
        --teal: #147b75;
        --blue: #315fbd;
        --amber: #a96506;
        --red: #b33d35;
        --green: #317247;
        --shadow: 0 12px 32px rgb(22 33 48 / 10%);
        --radius: 8px;
        --space-1: .5rem;
        --space-2: .85rem;
        --space-3: 1.25rem;
        --space-4: 1.8rem;
        --step--1: .86rem;
        --step-0: 1rem;
        --step-1: 1.2rem;
        --step-2: 1.55rem;
        --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
    }}
    @layer base {{
      body {{
        min-height: 100vh;
        color: var(--ink);
        background: var(--paper);
        font-family: var(--sans);
        font-size: var(--step-0);
        line-height: 1.45;
      }}
      h1, h2, h3, p {{ margin-block-start: 0; }}
      h1 {{ font-size: var(--step-2); line-height: 1.15; margin-bottom: .4rem; }}
      h2 {{ font-size: var(--step-1); line-height: 1.2; margin-bottom: .65rem; }}
      h3 {{ font-size: 1rem; line-height: 1.25; margin-bottom: .4rem; }}
      code, pre {{ font-family: var(--mono); }}
      button {{ border: 1px solid var(--line-strong); background: var(--panel); color: var(--ink); border-radius: 6px; padding: .48rem .7rem; cursor: pointer; }}
      button:hover {{ border-color: var(--blue); color: var(--blue); }}
      button[aria-selected="true"] {{ background: var(--ink); border-color: var(--ink); color: white; }}
      :focus-visible {{ outline: 3px solid #79a7ff; outline-offset: 2px; }}
    }}
    @layer layout {{
      .shell {{ width: min(1360px, calc(100% - 2rem)); margin-inline: auto; padding: var(--space-3) 0 var(--space-4); }}
      .topbar {{ display: flex; justify-content: space-between; gap: var(--space-3); align-items: flex-start; padding-bottom: var(--space-3); border-bottom: 1px solid var(--line); }}
      .grid {{ display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: var(--space-2); }}
      .span-3 {{ grid-column: span 3; }}
      .span-4 {{ grid-column: span 4; }}
      .span-5 {{ grid-column: span 5; }}
      .span-7 {{ grid-column: span 7; }}
      .span-8 {{ grid-column: span 8; }}
      .span-12 {{ grid-column: 1 / -1; }}
      section {{ margin-top: var(--space-3); }}
    }}
    @layer components {{
      .meta {{ display: grid; gap: .25rem; color: var(--muted); font-size: var(--step--1); text-align: right; }}
      .verdict {{ max-width: 76ch; color: var(--muted); margin: 0; }}
      .card {{ min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: var(--space-2); }}
      .metric {{ display: grid; gap: .2rem; min-height: 96px; }}
      .metric span {{ color: var(--muted); font-size: var(--step--1); }}
      .metric strong {{ font-size: 1.45rem; line-height: 1.05; overflow-wrap: anywhere; }}
      .metric small {{ color: var(--muted); font-size: .78rem; }}
      .tone-good strong {{ color: var(--green); }}
      .tone-warn strong {{ color: var(--amber); }}
      .tone-bad strong {{ color: var(--red); }}
      .timeline-wrap {{ display: grid; gap: var(--space-2); }}
      .slider-row {{ display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: .6rem; align-items: center; }}
      .slider-row input[type="range"] {{ width: 100%; accent-color: var(--blue); }}
      .bucket-readout {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .6rem; }}
      .readout-item {{ border: 1px solid var(--line); border-radius: 6px; padding: .65rem; background: #fafdff; }}
      .readout-item span {{ display: block; color: var(--muted); font-size: .78rem; }}
      .readout-item strong {{ display: block; margin-top: .1rem; }}
      .chart {{ width: 100%; min-height: 280px; border: 1px solid var(--line); border-radius: var(--radius); background: linear-gradient(#fff, #f9fbfd); }}
      .chart svg {{ display: block; width: 100%; height: 280px; }}
      .legend {{ display: flex; flex-wrap: wrap; gap: .8rem; color: var(--muted); font-size: var(--step--1); }}
      .swatch {{ display: inline-block; width: .8rem; height: .8rem; border-radius: 3px; margin-right: .3rem; vertical-align: -.1rem; }}
      .swatch-raw {{ background: var(--teal); }}
      .swatch-cache {{ background: var(--blue); }}
      .swatch-billing {{ background: var(--amber); }}
      table {{ width: 100%; border-collapse: collapse; font-size: var(--step--1); }}
      th, td {{ text-align: left; padding: .55rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
      th {{ color: var(--muted); font-weight: 700; background: #f8fafc; position: sticky; top: 0; }}
      td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
      .table-scroll {{ overflow: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); max-height: 460px; }}
      .tabs {{ display: flex; flex-wrap: wrap; gap: .45rem; margin-bottom: .6rem; }}
      pre {{ overflow: auto; max-height: 420px; padding: .85rem; margin: 0; border-radius: var(--radius); color: #eef6ff; background: #111927; font-size: .78rem; line-height: 1.45; }}
      .pill {{ display: inline-flex; align-items: center; border-radius: 999px; padding: .2rem .5rem; background: #eaf1ff; color: #284b89; font-size: .76rem; font-weight: 700; }}
      .note {{ color: var(--muted); font-size: var(--step--1); }}
    }}
    @layer utilities {{
      .compact {{ margin-bottom: 0; }}
      .mono {{ font-family: var(--mono); }}
      .nowrap {{ white-space: nowrap; }}
    }}
    @media (max-width: 860px) {{
      .shell {{ width: min(100% - 1rem, 1360px); }}
      .topbar {{ display: grid; }}
      .meta {{ text-align: left; }}
      .grid {{ grid-template-columns: minmax(0, 1fr); }}
      .span-3, .span-4, .span-5, .span-7, .span-8, .span-12 {{ grid-column: 1; }}
      .bucket-readout {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .slider-row {{ grid-template-columns: auto minmax(0, 1fr) auto; }}
      .slider-row output {{ grid-column: 1 / -1; }}
      th {{ position: static; }}
    }}
    @media (max-width: 480px) {{
      .bucket-readout {{ grid-template-columns: minmax(0, 1fr); }}
      .metric strong {{ font-size: 1.2rem; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ animation-duration: .01ms !important; scroll-behavior: auto !important; transition-duration: .01ms !important; }}
    }}
    @media print {{
      body {{ background: white; color: black; }}
      .card {{ box-shadow: none; }}
      .tabs, .slider-row {{ display: none; }}
      .table-scroll {{ max-height: none; overflow: visible; }}
      pre {{ max-height: none; white-space: pre-wrap; color: black; background: #f2f2f2; }}
    }}
  </style>
</head>
<body>
  <main id="main" class="shell" aria-labelledby="page-title">
    <header class="topbar">
      <div>
        <span class="pill">Session diagnostics</span>
        <h1 id="page-title">Codex proxy session</h1>
        <p id="verdict" class="verdict"></p>
      </div>
      <div class="meta" aria-label="Report metadata">
        <span>Client <strong class="mono">{html.escape(str(report.get("client", "")))}</strong></span>
        <span>Window <strong>{html.escape(str(window["start"]))}</strong></span>
        <span>to <strong>{html.escape(str(window["end"]))}</strong></span>
      </div>
    </header>

    <section aria-labelledby="metrics-title">
      <h2 id="metrics-title">Current Window</h2>
      <div id="metrics" class="grid"></div>
    </section>

    <section class="grid" aria-labelledby="timeline-title">
      <article class="card span-8 timeline-wrap">
        <div>
          <h2 id="timeline-title">Time Travel</h2>
          <p class="note compact">Move through minute buckets to compare local compression delta, billing-equivalent input estimates, and provider cache behavior.</p>
        </div>
        <div class="slider-row">
          <button id="prev" type="button" aria-label="Previous minute">Prev</button>
          <input id="timeline" type="range" min="0" max="0" value="0" aria-label="Timeline minute">
          <button id="next" type="button" aria-label="Next minute">Next</button>
          <output id="bucket-label" for="timeline"></output>
        </div>
        <div id="bucket-readout" class="bucket-readout"></div>
        <div class="chart" id="chart" aria-label="Timeline chart"></div>
        <div class="legend">
          <span><i class="swatch swatch-raw"></i>Local compression delta</span>
          <span><i class="swatch swatch-cache"></i>Provider cache hit</span>
          <span><i class="swatch swatch-billing"></i>Billing input delta</span>
        </div>
      </article>
      <aside class="card span-4">
        <h2>What The Timeline Shows</h2>
        <div id="findings"></div>
      </aside>
    </section>

    <section class="grid" aria-labelledby="breakdowns-title">
      <article class="card span-5">
        <h2 id="breakdowns-title">Compression Status</h2>
        <div id="status-table" class="table-scroll"></div>
      </article>
      <article class="card span-7">
        <h2>Session Affinity</h2>
        <p class="note">Codex cache works when repeated requests carry stable provider session affinity derived from the prompt cache key.</p>
        <div id="affinity-table" class="table-scroll"></div>
      </article>
    </section>

    <section aria-labelledby="requests-title">
      <article class="card">
        <h2 id="requests-title">Recent Safe Request Rows</h2>
        <div id="request-table" class="table-scroll"></div>
      </article>
    </section>

    <section class="grid" aria-labelledby="sql-title">
      <article class="card span-12">
        <h2 id="sql-title">SQL Evidence</h2>
        <div id="sql-tabs" class="tabs" role="tablist" aria-label="SQL queries"></div>
        <pre aria-live="polite"><code id="sql-panel"></code></pre>
      </article>
    </section>
  </main>
  <script type="application/json" id="report-data">{data}</script>
  <script>
    const report = JSON.parse(document.getElementById('report-data').textContent);
    const buckets = report.minute_buckets || [];
    const fmt = new Intl.NumberFormat('en-US');
    const pct = (value) => value === null || value === undefined ? '-' : `${{Number(value).toFixed(2)}}%`;
    const num = (value) => value === null || value === undefined ? '-' : fmt.format(Number(value));
    const shortTime = (value) => value ? String(value).replace('T', ' ').replace('+00:00', 'Z') : '-';

    function setText(id, value) {{
      document.getElementById(id).textContent = value;
    }}

    function metric(label, value, detail, tone = '') {{
      const article = document.createElement('article');
      article.className = `card metric span-3 ${{tone}}`;
      const labelEl = document.createElement('span');
      labelEl.textContent = label;
      const valueEl = document.createElement('strong');
      valueEl.textContent = value;
      const detailEl = document.createElement('small');
      detailEl.textContent = detail;
      article.append(labelEl, valueEl, detailEl);
      return article;
    }}

    function renderMetrics() {{
      const summary = report.summary || {{}};
      const grid = document.getElementById('metrics');
      grid.append(
        metric('Local token delta', num(summary.tokens_saved), `${{pct(summary.raw_savings_percent)}} before-vs-after compression`),
        metric('Provider cache hit', pct(summary.provider_cache_hit_percent), `${{num(summary.provider_cached_input_tokens)}} cached input`, 'tone-good'),
        metric('Billing input estimate', num(summary.billing_equivalent_input_tokens), `${{pct(summary.billing_equivalent_savings_percent)}} one-sided delta`, 'tone-warn'),
        metric('Executions', num(summary.executions), `${{num(summary.executions_succeeded)}} succeeded, ${{num(summary.executions_skipped)}} skipped`)
      );
    }}

    function findingLine(label, marker) {{
      const p = document.createElement('p');
      if (!marker) {{
        p.textContent = `${{label}}: not observed.`;
        return p;
      }}
      p.textContent = `${{label}}: ${{shortTime(marker.bucket)}}; cache ${{pct(marker.provider_cache_hit_percent)}}; local delta ${{pct(marker.raw_savings_percent)}}; billing input delta ${{pct(marker.billing_equivalent_savings_percent)}}.`;
      return p;
    }}

    function renderFindings() {{
      const box = document.getElementById('findings');
      const n = report.narrative || {{}};
      const verdict = document.createElement('p');
      verdict.textContent = n.negative_savings_note || '';
      box.append(
        findingLine('First >=70% provider cache', n.first_provider_cache_ge_70),
        findingLine('First >=95% provider cache', n.first_provider_cache_ge_95),
        findingLine('Worst raw minute', n.worst_raw_savings_bucket),
        findingLine('Worst billing minute', n.worst_billing_equivalent_bucket),
        verdict
      );
    }}

    function readoutItem(label, value) {{
      const div = document.createElement('div');
      div.className = 'readout-item';
      const span = document.createElement('span');
      span.textContent = label;
      const strong = document.createElement('strong');
      strong.textContent = value;
      div.append(span, strong);
      return div;
    }}

    function renderBucket(index) {{
      const bucket = buckets[index];
      const label = document.getElementById('bucket-label');
      const readout = document.getElementById('bucket-readout');
      if (!bucket) {{
        label.textContent = 'No bucket data';
        readout.replaceChildren();
        return;
      }}
      label.textContent = shortTime(bucket.bucket);
      readout.replaceChildren(
        readoutItem('Executions', num(bucket.executions)),
        readoutItem('Local delta', `${{num(bucket.tokens_saved)}} (${{pct(bucket.raw_savings_percent)}})`),
        readoutItem('Provider cache', `${{num(bucket.provider_cached_input_tokens)}} (${{pct(bucket.provider_cache_hit_percent)}})`),
        readoutItem('Billing input delta', pct(bucket.billing_equivalent_savings_percent))
      );
      highlightChart(index);
    }}

    function renderChart() {{
      const chart = document.getElementById('chart');
      chart.replaceChildren();
      const width = 920;
      const height = 280;
      const pad = 32;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-label', 'Minute by minute local token delta, billing input estimate, and provider cache percentages');
      const axis = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      axis.setAttribute('x1', pad);
      axis.setAttribute('x2', width - pad);
      axis.setAttribute('y1', height - pad);
      axis.setAttribute('y2', height - pad);
      axis.setAttribute('stroke', '#b8c3d3');
      svg.append(axis);
      const scaleY = (value) => {{
        const clamped = Math.max(-100, Math.min(100, Number(value || 0)));
        return (height - pad) - ((clamped + 100) / 200) * (height - 2 * pad);
      }};
      const xAt = (i) => buckets.length <= 1 ? width / 2 : pad + (i / (buckets.length - 1)) * (width - 2 * pad);
      const linePath = (field) => buckets.map((b, i) => `${{i === 0 ? 'M' : 'L'}} ${{xAt(i)}} ${{scaleY(b[field])}}`).join(' ');
      const paths = [
        ['raw_savings_percent', '#147b75'],
        ['provider_cache_hit_percent', '#315fbd'],
        ['billing_equivalent_savings_percent', '#a96506'],
      ];
      for (const [field, color] of paths) {{
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', linePath(field));
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', color);
        path.setAttribute('stroke-width', '3');
        path.setAttribute('stroke-linejoin', 'round');
        path.setAttribute('stroke-linecap', 'round');
        svg.append(path);
      }}
      buckets.forEach((bucket, i) => {{
        const marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        marker.setAttribute('class', 'bucket-marker');
        marker.setAttribute('data-index', String(i));
        marker.setAttribute('cx', xAt(i));
        marker.setAttribute('cy', scaleY(bucket.provider_cache_hit_percent));
        marker.setAttribute('r', '4');
        marker.setAttribute('fill', '#315fbd');
        marker.setAttribute('opacity', '.45');
        svg.append(marker);
      }});
      const cursor = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      cursor.setAttribute('id', 'chart-cursor');
      cursor.setAttribute('y1', pad);
      cursor.setAttribute('y2', height - pad);
      cursor.setAttribute('stroke', '#17202a');
      cursor.setAttribute('stroke-width', '2');
      cursor.setAttribute('stroke-dasharray', '4 4');
      svg.append(cursor);
      chart.append(svg);
    }}

    function highlightChart(index) {{
      const cursor = document.getElementById('chart-cursor');
      if (!cursor || buckets.length === 0) return;
      const width = 920;
      const pad = 32;
      const x = buckets.length <= 1 ? width / 2 : pad + (index / (buckets.length - 1)) * (width - 2 * pad);
      cursor.setAttribute('x1', x);
      cursor.setAttribute('x2', x);
      document.querySelectorAll('.bucket-marker').forEach((node) => {{
        const selected = Number(node.dataset.index) === index;
        node.setAttribute('r', selected ? '7' : '4');
        node.setAttribute('opacity', selected ? '1' : '.45');
      }});
    }}

    function table(headers, rows) {{
      const tableEl = document.createElement('table');
      const thead = document.createElement('thead');
      const trHead = document.createElement('tr');
      headers.forEach(([label, cls]) => {{
        const th = document.createElement('th');
        th.textContent = label;
        if (cls) th.className = cls;
        trHead.append(th);
      }});
      thead.append(trHead);
      const tbody = document.createElement('tbody');
      rows.forEach((row) => {{
        const tr = document.createElement('tr');
        row.forEach(([value, cls]) => {{
          const td = document.createElement('td');
          td.textContent = value;
          if (cls) td.className = cls;
          tr.append(td);
        }});
        tbody.append(tr);
      }});
      tableEl.append(thead, tbody);
      return tableEl;
    }}

    function renderTables() {{
      document.getElementById('status-table').append(table(
        [['Status'], ['Reason'], ['Exec', 'num'], ['Delta', 'num'], ['Delta %', 'num']],
        (report.status_breakdown || []).map((row) => [
          [row.status || '-'],
          [row.skip_reason || '-'],
          [num(row.executions), 'num'],
          [num(row.tokens_saved), 'num'],
          [pct(row.raw_savings_percent), 'num'],
        ])
      ));
      document.getElementById('affinity-table').append(table(
        [['Source'], ['Hash'], ['Requests', 'num'], ['First'], ['Last']],
        (report.session_affinity || []).map((row) => [
          [row.source || '-'],
          [(row.hash || '-').slice(0, 24)],
          [num(row.requests), 'num'],
          [shortTime(row.first_seen)],
          [shortTime(row.last_seen)],
        ])
      ));
      document.getElementById('request-table').append(table(
        [['Time'], ['Route'], ['Model'], ['Compression'], ['Delta', 'num'], ['Input', 'num'], ['Cached', 'num'], ['Cache', 'num'], ['Affinity']],
        (report.recent_requests || []).map((row) => [
          [shortTime(row.request_time)],
          [row.incoming_route || '-'],
          [row.provider_model || row.model_hint || '-'],
          [row.compression_status || '-'],
          [num(row.tokens_saved), 'num'],
          [num(row.provider_input_tokens), 'num'],
          [num(row.provider_cached_input_tokens), 'num'],
          [pct(row.provider_cache_hit_percent), 'num'],
          [(row.affinity_hash || '-').slice(0, 12)],
        ])
      ));
    }}

    function renderSqlTabs() {{
      const tabs = document.getElementById('sql-tabs');
      const panel = document.getElementById('sql-panel');
      const entries = Object.entries(report.sql || {{}});
      const select = (name, sql, button) => {{
        tabs.querySelectorAll('button').forEach((item) => item.setAttribute('aria-selected', String(item === button)));
        panel.textContent = sql.trim();
      }};
      entries.forEach(([name, sql], index) => {{
        const button = document.createElement('button');
        button.type = 'button';
        button.role = 'tab';
        button.textContent = name;
        button.setAttribute('aria-selected', String(index === 0));
        button.addEventListener('click', () => select(name, sql, button));
        tabs.append(button);
        if (index === 0) panel.textContent = sql.trim();
      }});
    }}

    function initTimeline() {{
      const slider = document.getElementById('timeline');
      slider.max = String(Math.max(buckets.length - 1, 0));
      slider.value = String(Math.max(buckets.length - 1, 0));
      slider.addEventListener('input', () => renderBucket(Number(slider.value)));
      document.getElementById('prev').addEventListener('click', () => {{
        slider.value = String(Math.max(0, Number(slider.value) - 1));
        renderBucket(Number(slider.value));
      }});
      document.getElementById('next').addEventListener('click', () => {{
        slider.value = String(Math.min(Number(slider.max), Number(slider.value) + 1));
        renderBucket(Number(slider.value));
      }});
      renderChart();
      renderBucket(Number(slider.value));
    }}

    setText('verdict', report.narrative?.verdict || 'Report loaded.');
    renderMetrics();
    renderFindings();
    renderTables();
    renderSqlTabs();
    initTimeline();
    window.__rendered = true;
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
