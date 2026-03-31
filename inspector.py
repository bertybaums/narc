#!/usr/bin/env python3
"""Interactive HTML inspector for NARC puzzle experiments.

Shows each puzzle with its grid sequence, narrative, and per-model results
across all three conditions (grids_only, narrative_only, both).

Usage:
    python inspector.py [--output inspect.html]
"""

import json
from collections import defaultdict

import click

from db import init_db, get_all_puzzles, puzzle_to_json, get_trials, get_variants
from grids import grid_to_base64_png, COLOR_RGB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODELS = ["gpt-oss-120b", "gpt-oss-20b", "qwen3.5-122b", "nemotron-3-super"]
CONDITIONS = ["grids_only", "narrative_only", "both"]


def _grid_img(grid, border_color="#555", max_cell=24):
    """Render grid as inline base64 PNG image tag."""
    if not grid:
        return '<div class="no-grid">?</div>'
    # Clamp values to 0-9 for rendering (model predictions may exceed range)
    clamped = [[max(0, min(9, c)) for c in row] for row in grid]
    b64 = grid_to_base64_png(clamped)
    return f'<img src="data:image/png;base64,{b64}" class="grid-img" style="border:2px solid {border_color};">'


def _diff_img(predicted, expected):
    """Render a diff grid: green border if match, red if not."""
    if not predicted or not expected:
        return '<div class="no-grid">—</div>'
    if predicted == expected:
        return _grid_img(predicted, border_color="#2ECC40")
    return _grid_img(predicted, border_color="#FF4136")


def _esc(text, max_len=500):
    if not text:
        return ""
    text = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _narc_status(conn, puzzle_id, model_name):
    """Compute NARC status for a puzzle/model."""
    trials = get_trials(conn, puzzle_id, model_name=model_name)
    results = {}
    for t in trials:
        if t["correct"] is not None:
            results[t["condition"]] = {
                "correct": t["correct"],
                "cell_accuracy": t["cell_accuracy"] or 0,
                "predicted_grids": json.loads(t["predicted_grids"]) if t["predicted_grids"] else None,
                "reasoning": t["reasoning"],
            }

    g = results.get("grids_only", {}).get("correct", 0)
    n = results.get("narrative_only", {}).get("correct", 0)
    b = results.get("both", {}).get("correct", 0)

    if g:
        status = "grids_sufficient"
    elif n:
        status = "narrative_sufficient"
    elif b:
        status = "narc"
    else:
        status = "unsolvable"

    return status, results


def _status_dot(status):
    cls = {"narc": "dot-narc", "grids_sufficient": "dot-grids",
           "narrative_sufficient": "dot-narr", "unsolvable": "dot-fail"}.get(status, "dot-fail")
    label = {"narc": "NARC", "grids_sufficient": "grids",
             "narrative_sufficient": "narr", "unsolvable": "fail"}.get(status, "?")
    return f'<span class="dot {cls}" title="{label}">{label}</span>'


def _cond_cell(result, expected_grids, masked_positions):
    """Render a condition result cell with accuracy and predicted grid."""
    if not result:
        return '<td class="result-none">—</td>'

    correct = result.get("correct", 0)
    acc = result.get("cell_accuracy", 0)
    predicted = result.get("predicted_grids")

    cls = "result-pass" if correct else "result-fail"
    icon = "&#10003;" if correct else "&#10007;"
    acc_str = f"{acc:.0%}" if acc is not None else "—"

    # Show predicted grid for first masked position
    grid_html = ""
    if predicted and masked_positions:
        for mp in masked_positions[:1]:
            pg = predicted.get(str(mp))
            eg = expected_grids.get(str(mp))
            if pg:
                grid_html = _diff_img(pg, eg)

    return f'<td class="{cls}">{icon} {acc_str}{grid_html}</td>'


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_inspector(conn):
    puzzles = get_all_puzzles(conn)

    # Discover which models have data
    active_models = []
    for m in MODELS:
        count = conn.execute(
            "SELECT COUNT(*) as c FROM trials WHERE model_name=?", (m,)
        ).fetchone()["c"]
        if count > 0:
            active_models.append(m)

    # Count NARC per model
    narc_counts = {}
    for m in active_models:
        narc_counts[m] = 0

    puzzle_data = []
    for p in puzzles:
        pdata = puzzle_to_json(p)
        pid = pdata["puzzle_id"]

        model_results = {}
        for m in active_models:
            status, results = _narc_status(conn, pid, m)
            model_results[m] = {"status": status, "results": results}
            if status == "narc":
                narc_counts[m] += 1

        puzzle_data.append((pdata, model_results))

    total = len(puzzle_data)

    # --- Build HTML ---
    html = f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
<meta charset="UTF-8">
<title>NARC Inspector</title>
<style>
* {{ box-sizing: border-box; }}
body {{ background: #0f0f1a; color: #d0d0d0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 16px; font-size: 14px; }}
h1 {{ color: #f59e0b; margin-bottom: 4px; }}
.subtitle {{ color: #888; margin-bottom: 16px; }}

/* Summary bar */
.summary {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; padding: 12px; background: #1a1a2e; border-radius: 8px; }}
.summary-card {{ background: #1f2937; padding: 12px 20px; border-radius: 6px; text-align: center; min-width: 120px; }}
.summary-card .value {{ font-size: 1.8em; font-weight: bold; }}
.summary-card .label {{ color: #888; font-size: 0.85em; }}

/* Search/filter */
.controls {{ margin-bottom: 16px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.controls input {{ background: #1f2937; border: 1px solid #374151; color: #e0e0e0; padding: 6px 12px; border-radius: 4px; width: 250px; }}
.controls select {{ background: #1f2937; border: 1px solid #374151; color: #e0e0e0; padding: 6px 8px; border-radius: 4px; }}
.count {{ color: #888; margin-left: 8px; }}

/* Puzzle list */
.puzzle {{ border: 1px solid #2a2a3e; border-radius: 8px; margin-bottom: 8px; overflow: hidden; }}
.puzzle-header {{ padding: 10px 16px; cursor: pointer; display: flex; align-items: center; gap: 12px; background: #1a1a2e; }}
.puzzle-header:hover {{ background: #1f2040; }}
.puzzle-header .title {{ font-weight: 600; color: #e0e0e0; flex: 1; }}
.puzzle-header .pid {{ color: #666; font-family: monospace; font-size: 0.85em; width: 130px; }}
.puzzle-header .meta {{ color: #888; font-size: 0.85em; }}
.toggle {{ color: #666; transition: transform 0.2s; }}
.toggle.open {{ transform: rotate(90deg); }}

/* Status dots */
.dots {{ display: flex; gap: 4px; }}
.dot {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.75em; font-weight: 600; }}
.dot-narc {{ background: #134e1f; color: #2ECC40; }}
.dot-grids {{ background: #0a3050; color: #0074D9; }}
.dot-narr {{ background: #4a3000; color: #FF851B; }}
.dot-fail {{ background: #2a1515; color: #666; }}

/* Puzzle body */
.puzzle-body {{ display: none; padding: 16px; background: #111122; }}

/* Grid sequence */
.sequence {{ display: flex; gap: 8px; align-items: flex-start; overflow-x: auto; padding: 8px 0; flex-wrap: nowrap; }}
.seq-item {{ text-align: center; flex-shrink: 0; }}
.seq-label {{ font-size: 0.8em; color: #888; margin-bottom: 4px; }}
.seq-arrow {{ color: #444; font-size: 1.2em; padding-top: 20px; }}
.masked-box {{ display: flex; align-items: center; justify-content: center; background: #1f2937; border: 2px dashed #f59e0b; border-radius: 4px; color: #f59e0b; font-size: 1.5em; font-weight: bold; min-width: 60px; min-height: 60px; }}
.grid-img {{ display: block; border-radius: 2px; }}

/* Narrative */
.narrative {{ padding: 10px 14px; background: #1a1500; border-left: 3px solid #f59e0b; color: #daa520; margin: 12px 0; font-style: italic; line-height: 1.5; border-radius: 0 4px 4px 0; }}

/* Results table */
.results-table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.9em; }}
.results-table th {{ background: #1a1a2e; padding: 6px 10px; text-align: center; border-bottom: 2px solid #333; }}
.results-table td {{ padding: 6px 10px; text-align: center; border-bottom: 1px solid #222; vertical-align: top; }}
.results-table .model-name {{ text-align: left; font-weight: 600; white-space: nowrap; }}
.result-pass {{ color: #2ECC40; }}
.result-fail {{ color: #888; }}
.result-none {{ color: #444; }}
.results-table .grid-img {{ max-height: 60px; margin: 4px auto 0; }}
.no-grid {{ color: #444; font-size: 0.9em; }}

/* Answer row */
.answer-section {{ margin-top: 8px; }}
.answer-section h4 {{ color: #888; font-size: 0.9em; margin-bottom: 6px; }}

/* Reasoning toggle */
.reasoning-toggle {{ color: #666; cursor: pointer; font-size: 0.8em; text-decoration: underline; }}
.reasoning-text {{ display: none; white-space: pre-wrap; font-family: monospace; font-size: 0.8em; color: #888; background: #0a0a15; padding: 8px; border-radius: 4px; margin-top: 4px; max-height: 200px; overflow-y: auto; }}

/* Variant tabs */
.variant-tabs {{ display: flex; gap: 4px; flex-wrap: wrap; margin: 12px 0 0; }}
.vtab {{ background: #1f2937; border: 1px solid #374151; color: #888; padding: 4px 10px; border-radius: 4px 4px 0 0; cursor: pointer; font-size: 0.8em; border-bottom: none; }}
.vtab:hover {{ background: #2a2a3e; color: #ddd; }}
.vtab.active {{ background: #1a1500; color: #daa520; border-color: #f59e0b; }}
.variant-narr {{ margin-top: 0; border-radius: 0 4px 4px 4px; }}

/* Tags */
.tags {{ display: flex; gap: 4px; flex-wrap: wrap; margin-top: 8px; }}
.tag {{ background: #1f2937; color: #888; padding: 2px 8px; border-radius: 3px; font-size: 0.75em; }}
</style>
</head>
<body>

<h1>NARC Inspector</h1>
<p class="subtitle">{total} puzzles &middot; {len(active_models)} models tested</p>

<div class="summary">
"""

    # Summary cards per model
    for m in active_models:
        short = m.replace("gpt-oss-", "").replace("qwen3.5-", "q")
        nc = narc_counts[m]
        html += f"""<div class="summary-card">
            <div class="value" style="color:#2ECC40;">{nc}</div>
            <div class="label">{short} NARC</div>
        </div>\n"""

    html += f"""<div class="summary-card">
        <div class="value">{total}</div>
        <div class="label">Total puzzles</div>
    </div>
</div>

<div class="controls">
    <input type="text" id="search" placeholder="Search title or ID..." oninput="filterPuzzles()">
    <select id="status-filter" onchange="filterPuzzles()">
        <option value="">All statuses</option>
        <option value="narc">NARC only</option>
        <option value="grids_sufficient">Grids sufficient</option>
        <option value="narrative_sufficient">Narrative sufficient</option>
        <option value="unsolvable">Unsolvable</option>
    </select>
    <select id="model-filter" onchange="filterPuzzles()">
        <option value="">Any model</option>
"""
    for m in active_models:
        html += f'        <option value="{m}">{m}</option>\n'

    html += """    </select>
    <span class="count" id="count-display"></span>
</div>

<div id="puzzle-list">
"""

    # --- Each puzzle ---
    for pdata, model_results in puzzle_data:
        pid = pdata["puzzle_id"]
        title = pdata["title"]
        seq = pdata["sequence"]
        masked_pos = pdata["masked_positions"]
        answer_grids = pdata["answer_grids"]
        narrative = pdata["narrative"]
        tags = pdata.get("tags") or ""
        hdiff = pdata.get("human_difficulty") or ""
        adiff = pdata.get("ai_difficulty") or ""

        # Statuses for filtering
        statuses = " ".join(mr["status"] for mr in model_results.values())

        # Model dots
        dots_html = ""
        for m in active_models:
            mr = model_results.get(m, {})
            dots_html += _status_dot(mr.get("status", "unsolvable"))

        diff_html = f"H{hdiff}/A{adiff}" if hdiff and adiff else ""

        html += f"""
<div class="puzzle" data-statuses="{statuses}" data-pid="{pid}" data-title="{_esc(title, 200)}">
  <div class="puzzle-header" onclick="toggle('{pid}')">
    <span class="toggle" id="tog-{pid}">&#9654;</span>
    <span class="pid">{pid}</span>
    <span class="title">{_esc(title, 80)}</span>
    <span class="meta">{len(seq)}g {diff_html}</span>
    <span class="dots">{dots_html}</span>
  </div>
  <div class="puzzle-body" id="body-{pid}">
"""

        # Grid sequence
        html += '<div class="sequence">\n'
        for item in seq:
            pos = item["position"]
            label = item.get("label", f"Grid {pos+1}")
            if item.get("masked"):
                r, c = item["rows"], item["cols"]
                html += f"""<div class="seq-item">
                    <div class="seq-label">{_esc(label)}</div>
                    <div class="masked-box" style="width:{c*20+10}px;height:{r*20+10}px;">?</div>
                </div>\n"""
            else:
                html += f"""<div class="seq-item">
                    <div class="seq-label">{_esc(label)}</div>
                    {_grid_img(item["grid"])}
                </div>\n"""
            if pos < len(seq) - 1:
                html += '<div class="seq-arrow">&rarr;</div>\n'
        html += '</div>\n'

        # Narrative + variants (tabbed)
        variants = get_variants(conn, pid)
        if len(variants) > 1:
            html += f'<div class="variant-tabs" id="vtabs-{pid}">\n'
            html += f'<button class="vtab active" onclick="switchVariant(\'{pid}\', 0)">Original</button>\n'
            for vi, v in enumerate(variants):
                if v["variant"] == "original":
                    continue
                domain = v["source_domain"] or v["variant"]
                html += f'<button class="vtab" onclick="switchVariant(\'{pid}\', {vi})">{_esc(domain, 20)}</button>\n'
            html += '</div>\n'

            # Variant narratives (hidden by default except original)
            for vi, v in enumerate(variants):
                display = "block" if v["variant"] == "original" else "none"
                html += f'<div class="narrative variant-narr" id="vnarr-{pid}-{vi}" style="display:{display};">{_esc(v["narrative"], 600)}</div>\n'
        else:
            html += f'<div class="narrative">{_esc(narrative, 600)}</div>\n'

        # Answer grids
        html += '<div class="answer-section"><h4>Answer:</h4><div class="sequence">\n'
        for mp in masked_pos:
            ag = answer_grids.get(str(mp))
            if ag:
                html += f'<div class="seq-item"><div class="seq-label">Grid {mp+1}</div>{_grid_img(ag, border_color="#2ECC40")}</div>\n'
        html += '</div></div>\n'

        # Results table
        html += """<table class="results-table">
<tr><th>Model</th><th>Status</th><th>Grids Only</th><th>Narrative Only</th><th>Both</th></tr>\n"""

        for m in active_models:
            mr = model_results.get(m, {})
            status = mr.get("status", "—")
            results = mr.get("results", {})
            short = m.replace("gpt-oss-", "").replace("qwen3.5-", "q")

            html += f'<tr><td class="model-name">{short}</td>'
            html += f'<td>{_status_dot(status)}</td>'
            for cond in CONDITIONS:
                html += _cond_cell(results.get(cond), answer_grids, masked_pos)
            html += '</tr>\n'

        html += '</table>\n'

        # Tags
        if tags:
            tag_list = tags.split(",") if isinstance(tags, str) else tags
            html += '<div class="tags">'
            for t in tag_list[:15]:
                t = t.strip()
                if t:
                    html += f'<span class="tag">{_esc(t)}</span>'
            html += '</div>\n'

        html += '  </div>\n</div>\n'

    # --- Footer + JS ---
    html += """
</div>

<script>
function toggle(pid) {
    var body = document.getElementById('body-' + pid);
    var icon = document.getElementById('tog-' + pid);
    if (body.style.display === 'none') {
        body.style.display = 'block';
        icon.classList.add('open');
    } else {
        body.style.display = 'none';
        icon.classList.remove('open');
    }
}

function switchVariant(pid, idx) {
    // Hide all variant narratives for this puzzle
    var narrs = document.querySelectorAll('[id^="vnarr-' + pid + '-"]');
    narrs.forEach(function(n) { n.style.display = 'none'; });
    // Show selected
    var target = document.getElementById('vnarr-' + pid + '-' + idx);
    if (target) target.style.display = 'block';
    // Update tab active state
    var tabs = document.querySelectorAll('#vtabs-' + pid + ' .vtab');
    tabs.forEach(function(t) { t.classList.remove('active'); });
    // Find the clicked tab (the idx-th tab, accounting for original at 0)
    if (tabs[idx]) tabs[idx].classList.add('active');
    // If idx > 0, we need to map: original is at button 0, then non-original variants follow
    // Actually the button index matches the variant index in the list
}

function filterPuzzles() {
    var search = document.getElementById('search').value.toLowerCase();
    var statusFilter = document.getElementById('status-filter').value;
    var modelFilter = document.getElementById('model-filter').value;
    var puzzles = document.querySelectorAll('.puzzle');
    var shown = 0;

    puzzles.forEach(function(p) {
        var pid = (p.dataset.pid || '').toLowerCase();
        var title = (p.dataset.title || '').toLowerCase();
        var statuses = p.dataset.statuses || '';

        var matchSearch = !search || pid.includes(search) || title.includes(search);
        var matchStatus = !statusFilter || statuses.includes(statusFilter);
        // Model filter: check if that model has the selected status
        var matchModel = true; // TODO: could be more specific

        if (matchSearch && matchStatus) {
            p.style.display = '';
            shown++;
        } else {
            p.style.display = 'none';
        }
    });

    document.getElementById('count-display').textContent = shown + ' / ' + puzzles.length;
}

filterPuzzles();
</script>
</body>
</html>"""

    return html


@click.command()
@click.option("--output", default="inspect.html")
def main(output):
    conn = init_db()
    html = build_inspector(conn)
    conn.close()
    with open(output, "w") as f:
        f.write(html)
    click.echo(f"Inspector written to {output} ({len(html)//1024}KB)")


if __name__ == "__main__":
    main()
