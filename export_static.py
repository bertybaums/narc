#!/usr/bin/env python3
"""Export NARC as a single self-contained HTML file.

Browse, solve, and inspect puzzles — no server required.
All puzzle data, CSS, JS, and grid images are embedded inline.

Usage:
    python export_static.py [--output narc.html]
"""

import json
from pathlib import Path

import click

from db import init_db, get_all_puzzles, puzzle_to_json, get_variants, get_trials
from grids import grid_to_base64_png, COLOR_RGB

MODELS = ["gpt-oss-120b", "gpt-oss-20b", "qwen3.5-122b", "nemotron-3-super"]


def _narc_status(conn, puzzle_id, model_name):
    trials = get_trials(conn, puzzle_id, model_name=model_name)
    results = {}
    for t in trials:
        if t["correct"] is not None:
            results[t["condition"]] = {
                "correct": t["correct"],
                "cell_accuracy": t["cell_accuracy"] or 0,
            }
    g = results.get("grids_only", {}).get("correct", 0)
    n = results.get("narrative_only", {}).get("correct", 0)
    b = results.get("both", {}).get("correct", 0)
    if g: return "grids_sufficient", results
    elif n: return "narrative_sufficient", results
    elif b: return "narc", results
    else: return "unsolvable", results


@click.command()
@click.option("--output", default="narc.html")
def main(output):
    conn = init_db()
    puzzles_raw = get_all_puzzles(conn)

    # Build puzzle data with variants and model results
    all_puzzles = []
    for p in puzzles_raw:
        pdata = puzzle_to_json(p)
        pid = pdata["puzzle_id"]

        # Variants
        variants = []
        for v in get_variants(conn, pid):
            variants.append({
                "variant": v["variant"],
                "source_domain": v["source_domain"],
                "narrative": v["narrative"],
            })

        # Model results
        model_results = {}
        for m in MODELS:
            status, results = _narc_status(conn, pid, m)
            model_results[m] = {"status": status, "results": results}

        pdata["variants"] = variants
        pdata["model_results"] = model_results
        all_puzzles.append(pdata)

    conn.close()

    # Count stats
    total_variants = sum(len(p["variants"]) for p in all_puzzles)
    grid_sizes = set()
    for p in all_puzzles:
        for item in p["sequence"]:
            grid_sizes.add((item["rows"], item["cols"]))

    # Read static assets
    css_grids = Path("static/css/grids.css").read_text()
    js_grids = Path("static/js/grids.js").read_text()

    # Embed puzzle data as JSON
    puzzles_json = json.dumps(all_puzzles)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NARC — Narrative Augmented Reasoning Challenges</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YcnS/1WR6zNiV6+RzEiEoKj/Ay/GSTZ2aSA" crossorigin="anonymous">
<style>
{css_grids}

body {{ background-color: #1a1a2e; color: #e0e0e0; }}
.navbar {{ background-color: #16213e !important; }}

/* Cards */
.card {{ background-color: #1f2937; border-color: #374151; }}
.card-header {{ background-color: #111827; border-color: #374151; }}

/* Puzzle cards */
.puzzle-card {{ cursor: pointer; transition: transform 0.1s; }}
.puzzle-card:hover {{ transform: translateY(-2px); }}

/* Sequence */
.sequence-container {{ display: flex; gap: 12px; align-items: flex-start; overflow-x: auto; padding: 12px 0; flex-wrap: nowrap; }}
.grid-slot {{ flex-shrink: 0; text-align: center; }}
.grid-slot .slot-label {{ font-size: 0.8em; color: #9ca3af; margin-bottom: 4px; }}
.grid-slot.masked .grid-wrapper {{ border: 2px dashed #f59e0b; border-radius: 4px; }}
.masked-placeholder {{ display: flex; align-items: center; justify-content: center; background: #374151; color: #f59e0b; font-size: 2rem; font-weight: bold; }}
.sequence-arrow {{ display: flex; align-items: center; color: #6b7280; font-size: 1.2rem; flex-shrink: 0; padding-top: 16px; }}

/* Narrative */
.narrative-container {{ margin: 16px 0; }}
.narrative-reveal-btn {{ cursor: pointer; padding: 10px 18px; background: #1f2937; border: 2px solid #f59e0b; border-radius: 8px; color: #f59e0b; font-size: 1rem; }}
.narrative-reveal-btn:hover {{ background: #374151; }}
.narrative-text {{ display: none; padding: 14px 18px; background: #2d1f00; border: 1px solid #f59e0b; border-radius: 8px; color: #fbbf24; font-style: italic; font-size: 1.05em; line-height: 1.6; margin-top: 8px; }}
.narrative-text.visible {{ display: block; }}

/* Grid editor */
.grid-editor .cell {{ cursor: pointer; }}
.grid-editor .cell:hover {{ opacity: 0.8; }}
.symbol-picker {{ display: flex; gap: 4px; padding: 8px 0; flex-wrap: wrap; }}
.symbol-picker .symbol_preview {{ width: 32px; height: 32px; padding-bottom: 0; float: none; cursor: pointer; border: 2px solid transparent; border-radius: 4px; }}
.symbol-picker .symbol_preview.selected {{ border-color: #fff; box-shadow: 0 0 4px rgba(255,255,255,0.8); }}

/* Feedback */
.feedback-grid .cell.correct {{ box-shadow: inset 0 0 0 2px #2ECC40; }}
.feedback-grid .cell.wrong {{ box-shadow: inset 0 0 0 2px #FF4136; }}

/* Buttons */
.btn-narc {{ background-color: #f59e0b; border-color: #f59e0b; color: #000; }}
.btn-narc:hover {{ background-color: #d97706; border-color: #d97706; color: #000; }}

/* Tag buttons */
.tag-btn {{ font-size: 0.8em; }}
.tag-btn.active {{ color: #fff !important; font-weight: 600; }}
.tag-btn.active.btn-outline-info {{ background-color: #0dcaf0; border-color: #0dcaf0; }}
.tag-btn.active.btn-outline-warning {{ background-color: #ffc107; border-color: #ffc107; color: #000 !important; }}
.tag-btn.active.btn-outline-success {{ background-color: #198754; border-color: #198754; }}
.tag-btn.active.btn-outline-secondary {{ background-color: #6c757d; border-color: #6c757d; }}
.tag-btn.active.btn-outline-light {{ background-color: #f8f9fa; border-color: #f8f9fa; color: #000 !important; }}
.tag-btn.active.btn-outline-danger {{ background-color: #dc3545; border-color: #dc3545; }}

/* Status dots */
.dot {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; font-weight: 600; margin: 0 1px; }}
.dot-narc {{ background: #134e1f; color: #2ECC40; }}
.dot-grids {{ background: #0a3050; color: #0074D9; }}
.dot-narr {{ background: #4a3000; color: #FF851B; }}
.dot-fail {{ background: #2a1515; color: #555; }}

/* Variant tabs */
.variant-tabs {{ display: flex; gap: 4px; flex-wrap: wrap; margin-top: 8px; }}
.vtab {{ background: #1f2937; border: 1px solid #374151; color: #888; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 0.8em; }}
.vtab:hover {{ background: #2a2a3e; color: #ddd; }}
.vtab.active {{ background: #2d1f00; color: #daa520; border-color: #f59e0b; }}

/* Views */
.view {{ display: none; }}
.view.active {{ display: block; }}

/* Tag chips */
.tag-chip {{ background: #1f2937; color: #888; padding: 2px 8px; border-radius: 3px; font-size: 0.75em; display: inline-block; margin: 1px; }}
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg navbar-dark mb-3">
    <div class="container">
        <a class="navbar-brand fw-bold" href="#" onclick="showView('about')">NARC</a>
        <div class="navbar-nav">
            <a class="nav-link" href="#" onclick="showView('about')">About</a>
            <a class="nav-link" href="#" onclick="showView('browse')">Browse</a>
            <a class="nav-link disabled text-muted" title="Requires hosted server">Create</a>
            <a class="nav-link" href="#" onclick="showView('inspect')">Inspect</a>
        </div>
    </div>
</nav>

<div class="container">

<!-- ==================== ABOUT VIEW ==================== -->
<div id="view-about" class="view active">
<div class="row justify-content-center"><div class="col-lg-8">
<h1 class="mb-3">NARC</h1>
<p class="lead" style="color:#f59e0b;">Narrative Augmented Reasoning Challenges</p>

<div class="card mb-3"><div class="card-body">
<h4>What is NARC?</h4>
<p>NARC is a new kind of abstract reasoning puzzle. Each puzzle presents a sequence of colored grids that tell a visual "story." One or more grids are hidden, and your goal is to reconstruct them pixel-perfectly.</p>
<p>The catch: <strong>the grids alone aren't enough.</strong> Each puzzle comes with a short narrative clue. Without it, the missing grid is ambiguous. With the clue, exactly one answer is correct.</p>
<p>This is the <strong>NARC property</strong>: neither the grids nor the narrative suffice alone, but together they uniquely determine the answer.</p>
</div></div>

<div class="card mb-3"><div class="card-body">
<h4>How to play</h4>
<ol>
<li><strong>Look at the grid sequence.</strong> One or more grids are hidden (shown as <span style="color:#f59e0b;">?</span>).</li>
<li><strong>Try to guess</strong> before revealing the clue.</li>
<li><strong>Reveal the clue</strong> and see how it changes your understanding.</li>
<li><strong>Draw your answer</strong> and submit.</li>
</ol>
</div></div>

<div class="card mb-3"><div class="card-body">
<h4>The corpus</h4>
<div class="row text-center my-3">
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">{len(all_puzzles)}</div><div class="text-muted">Puzzles</div></div>
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">{total_variants}</div><div class="text-muted">Narrative variants</div></div>
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">{len(grid_sizes)}</div><div class="text-muted">Unique grid sizes</div></div>
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">3&ndash;8</div><div class="text-muted">Grids per puzzle</div></div>
</div>
<p>Puzzles span literary classics, scientific concepts, philosophical thought experiments, AI/ML concepts, and more. Each is rated on both <strong>human difficulty</strong> and <strong>AI difficulty</strong>.</p>
</div></div>

<div class="card mb-3"><div class="card-body">
<h4>Research context</h4>
<p>NARC draws on ARC-AGI (abstract visual reasoning), MARC (figurative language + reasoning), econarratology (Erin James's storyworld framework), and focalization theory (how narrative perspective shapes understanding).</p>
<p>Sibling project to <a href="https://bertybaums.github.io/marc2/" style="color:#f59e0b;">MARC2</a>.</p>
</div></div>

<div class="text-center text-muted mb-4"><small>University of Idaho &middot; 2026</small></div>
</div></div>
</div>

<!-- ==================== BROWSE VIEW ==================== -->
<div id="view-browse" class="view">
<div class="d-flex justify-content-between align-items-center mb-3">
    <h2 class="mb-0">Puzzles <small class="text-muted" id="browse-count"></small></h2>
    <input type="text" id="browse-search" class="form-control form-control-sm" placeholder="Search..." style="width:200px;" oninput="filterBrowse()">
</div>
<div id="browse-filters" class="mb-3"></div>
<div id="browse-grid" class="row g-3"></div>
</div>

<!-- ==================== SOLVE VIEW ==================== -->
<div id="view-solve" class="view">
<div id="solve-container"></div>
</div>

<!-- ==================== INSPECT VIEW ==================== -->
<div id="view-inspect" class="view">
<h2 class="mb-3">Inspect <small class="text-muted">AI model results</small></h2>
<div class="mb-3">
    <input type="text" id="inspect-search" class="form-control form-control-sm d-inline-block" placeholder="Search..." style="width:200px;" oninput="filterInspect()">
    <select id="inspect-status" class="form-select form-select-sm d-inline-block" style="width:160px;" onchange="filterInspect()">
        <option value="">All statuses</option>
        <option value="narc">NARC only</option>
        <option value="grids_sufficient">Grids sufficient</option>
        <option value="unsolvable">Unsolvable</option>
    </select>
    <span class="text-muted ms-2" id="inspect-count"></span>
</div>
<div id="inspect-list"></div>
</div>

</div><!-- container -->

<script>
{js_grids}
</script>
<script>
// ==================== DATA ====================
const PUZZLES = {puzzles_json};
const MODELS = {json.dumps(MODELS)};

// ==================== VIEW SWITCHING ====================
function showView(name) {{
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-' + name).classList.add('active');
    if (name === 'browse') renderBrowse();
    if (name === 'inspect') renderInspect();
}}

// ==================== BROWSE ====================
let browseRendered = false;
const activeTags = new Set();

function renderBrowse() {{
    if (browseRendered) return;
    browseRendered = true;

    // Build tag counts
    const tagCounts = {{}};
    PUZZLES.forEach(p => {{
        (p.tags || '').split(',').forEach(t => {{
            t = t.trim();
            if (t) tagCounts[t] = (tagCounts[t] || 0) + 1;
        }});
    }});

    // Build filter buttons
    const dims = [
        ['Spectrum', 'spectrum', 'btn-outline-danger',
         ['spectrum:human-forte','spectrum:human-edge','spectrum:balanced','spectrum:ai-edge','spectrum:ai-forte','spectrum:domain-dependent']],
        ['Audience', 'audience', 'btn-outline-info', null],
        ['Arc', 'arc', 'btn-outline-warning', null],
        ['Clue', 'clue', 'btn-outline-success', null],
        ['Domain', 'domain', 'btn-outline-secondary', null],
    ];
    const filtersEl = document.getElementById('browse-filters');
    filtersEl.innerHTML = '';
    dims.forEach(([label, prefix, cls, order]) => {{
        let tags = order ? order.filter(t => tagCounts[t]) :
            Object.keys(tagCounts).filter(t => t.startsWith(prefix + ':')).sort((a,b) => (tagCounts[b]||0) - (tagCounts[a]||0));
        if (!tags.length) return;
        let row = '<div class="mb-1"><strong class="text-muted me-2">' + label + ':</strong>';
        tags.forEach(t => {{
            const short = t.split(':')[1];
            row += '<button class="btn btn-sm ' + cls + ' tag-btn mb-1 me-1" data-tag="' + t + '" onclick="toggleTag(this)">' + short + ' <span class="badge bg-secondary">' + (tagCounts[t]||0) + '</span></button>';
        }});
        row += '</div>';
        filtersEl.innerHTML += row;
    }});
    filtersEl.innerHTML += '<button class="btn btn-sm btn-outline-danger mt-1" onclick="clearTags()">Clear filters</button>';

    filterBrowse();
}}

function toggleTag(btn) {{
    const tag = btn.dataset.tag;
    if (activeTags.has(tag)) {{ activeTags.delete(tag); btn.classList.remove('active'); }}
    else {{ activeTags.add(tag); btn.classList.add('active'); }}
    filterBrowse();
}}

function clearTags() {{
    activeTags.clear();
    document.querySelectorAll('.tag-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('browse-search').value = '';
    filterBrowse();
}}

function filterBrowse() {{
    const search = (document.getElementById('browse-search').value || '').toLowerCase();
    const grid = document.getElementById('browse-grid');
    grid.innerHTML = '';
    let shown = 0;

    PUZZLES.forEach(p => {{
        const tags = p.tags || '';
        const title = (p.title || '').toLowerCase();
        const pid = (p.puzzle_id || '').toLowerCase();

        if (search && !title.includes(search) && !pid.includes(search)) return;
        for (const t of activeTags) {{ if (!tags.includes(t)) return; }}

        const nMasked = p.masked_positions.length;
        const maskedStr = nMasked === 1 ? 'masked #' + (p.masked_positions[0]+1) : nMasked + ' masked';
        const hdiff = p.human_difficulty;
        const adiff = p.ai_difficulty;
        const diffStr = hdiff && adiff ? 'H' + hdiff + '/A' + adiff : '';

        // Model dots
        let dots = '';
        MODELS.forEach(m => {{
            const mr = (p.model_results || {{}})[m];
            if (!mr) return;
            const s = mr.status;
            const cls = s === 'narc' ? 'dot-narc' : s === 'grids_sufficient' ? 'dot-grids' : s === 'narrative_sufficient' ? 'dot-narr' : 'dot-fail';
            dots += '<span class="dot ' + cls + '">' + m.replace('gpt-oss-','').replace('qwen3.5-','q').replace('nemotron-3-','nem-').slice(0,5) + '</span> ';
        }});

        grid.innerHTML += '<div class="col-md-4"><div class="card puzzle-card" onclick="solvePuzzle(\\'' + p.puzzle_id + '\\')">' +
            '<div class="card-body"><h5 class="card-title">' + p.title + '</h5>' +
            '<p class="text-muted mb-1">' + p.sequence.length + ' grids &middot; ' + maskedStr + (diffStr ? ' &middot; ' + diffStr : '') + '</p>' +
            '<div class="mb-1">' + dots + '</div>' +
            '<p class="card-text small text-truncate">' + (p.narrative||'').slice(0,80) + '...</p>' +
            '<button class="btn btn-sm btn-narc" onclick="event.stopPropagation();solvePuzzle(\\'' + p.puzzle_id + '\\')">Solve</button>' +
            '</div></div></div>';
        shown++;
    }});

    document.getElementById('browse-count').textContent = '(' + shown + ')';
}}

// ==================== SOLVE ====================
let solveState = {{}};

function solvePuzzle(pid) {{
    const p = PUZZLES.find(x => x.puzzle_id === pid);
    if (!p) return;
    showView('solve');

    solveState = {{ puzzle: p, selectedColor: 0, answerGrids: {{}}, narrativeRevealed: false }};

    const c = document.getElementById('solve-container');
    c.innerHTML = '<h2>' + p.title + '</h2>' +
        '<p class="text-muted">' + p.sequence.length + ' grids &middot; reconstruct ' +
        (p.masked_positions.length === 1 ? 'grid #' + (p.masked_positions[0]+1) : p.masked_positions.length + ' grids') + '</p>';

    // Sequence
    const seqDiv = document.createElement('div');
    seqDiv.className = 'sequence-container';
    p.sequence.forEach((item, i) => {{
        if (i > 0) {{
            const arrow = document.createElement('span');
            arrow.className = 'sequence-arrow';
            arrow.innerHTML = '&rarr;';
            seqDiv.appendChild(arrow);
        }}
        const slot = document.createElement('div');
        slot.className = 'grid-slot' + (item.masked ? ' masked' : '');
        const label = document.createElement('div');
        label.className = 'slot-label';
        label.textContent = String(i + 1);
        if (item.label) label.dataset.fullLabel = item.label;
        slot.appendChild(label);
        const wrapper = document.createElement('div');
        wrapper.className = 'grid-wrapper';
        const canvas = document.createElement('div');
        if (item.masked) {{
            renderMaskedPlaceholder(canvas, item.rows, item.cols, 28);
        }} else {{
            const g = new Grid(item.rows, item.cols, item.grid);
            renderGrid(canvas, g, {{ cellSize: 28 }});
        }}
        wrapper.appendChild(canvas);
        slot.appendChild(wrapper);
        seqDiv.appendChild(slot);
    }});
    c.appendChild(seqDiv);

    // Narrative reveal
    const narrDiv = document.createElement('div');
    narrDiv.className = 'narrative-container';
    narrDiv.innerHTML = '<button class="narrative-reveal-btn" id="reveal-btn" onclick="revealNarrative()">Reveal Clue</button>' +
        '<div class="narrative-text" id="narr-text">' + p.narrative + '</div>';

    // Variant tabs (if any)
    if (p.variants && p.variants.length > 1) {{
        let tabs = '<div class="variant-tabs" id="solve-vtabs" style="display:none;">';
        p.variants.forEach((v, vi) => {{
            const label = v.variant === 'original' ? 'Original' : (v.source_domain || v.variant);
            tabs += '<button class="vtab' + (vi === 0 ? ' active' : '') + '" onclick="switchSolveVariant(' + vi + ')">' + label + '</button>';
        }});
        tabs += '</div>';
        narrDiv.innerHTML += tabs;
    }}
    c.appendChild(narrDiv);

    // Answer editors
    const ansCard = document.createElement('div');
    ansCard.className = 'card mb-3';
    ansCard.innerHTML = '<div class="card-header">Your Answer</div><div class="card-body">' +
        '<div id="solve-picker" class="mb-2"></div><div id="solve-editors"></div>' +
        '<div class="mt-3"><button class="btn btn-narc" onclick="submitSolve()">Submit Answer</button> ' +
        '<button class="btn btn-outline-secondary" onclick="clearSolve()">Clear</button></div></div>';
    c.appendChild(ansCard);

    // Feedback area
    c.innerHTML += '<div id="solve-feedback" class="card mb-4" style="display:none;">' +
        '<div class="card-header">Result</div><div class="card-body">' +
        '<div id="solve-banner" class="alert mb-3"></div><div id="solve-diff" class="row"></div></div></div>';

    // Build picker and editors
    buildColorPicker(document.getElementById('solve-picker'), col => {{ solveState.selectedColor = col; }});
    const editorsDiv = document.getElementById('solve-editors');
    p.masked_positions.forEach(pos => {{
        const item = p.sequence[pos];
        const posStr = String(pos);
        solveState.answerGrids[posStr] = new Grid(item.rows, item.cols);
        if (p.masked_positions.length > 1) {{
            const h = document.createElement('h6');
            h.textContent = 'Grid ' + (pos+1) + ' (' + item.rows + 'x' + item.cols + ')';
            editorsDiv.appendChild(h);
        }}
        const canvas = document.createElement('div');
        canvas.id = 'solve-grid-' + posStr;
        canvas.style.display = 'inline-block';
        editorsDiv.appendChild(canvas);
        renderSolveGrid(posStr);
    }});
}}

function renderSolveGrid(posStr) {{
    const canvas = document.getElementById('solve-grid-' + posStr);
    const gridObj = solveState.answerGrids[posStr];
    renderGrid(canvas, gridObj, {{
        editable: true, cellSize: 28,
        onCellClick: (r, c) => {{
            gridObj.grid[r][c] = solveState.selectedColor;
            const cell = canvas.querySelectorAll('.grid_row')[r].querySelectorAll('.cell')[c];
            setCellColor(cell, solveState.selectedColor);
        }}
    }});
}}

function revealNarrative() {{
    solveState.narrativeRevealed = true;
    document.getElementById('narr-text').classList.add('visible');
    document.getElementById('reveal-btn').style.display = 'none';
    const vtabs = document.getElementById('solve-vtabs');
    if (vtabs) vtabs.style.display = 'flex';
    // Reveal labels
    document.querySelectorAll('.slot-label[data-full-label]').forEach(el => {{
        el.textContent = el.dataset.fullLabel;
    }});
}}

function switchSolveVariant(idx) {{
    const p = solveState.puzzle;
    if (!p.variants || !p.variants[idx]) return;
    document.getElementById('narr-text').textContent = p.variants[idx].narrative;
    document.querySelectorAll('#solve-vtabs .vtab').forEach((b, i) => {{
        b.classList.toggle('active', i === idx);
    }});
}}

function submitSolve() {{
    const p = solveState.puzzle;
    const expected = p.answer_grids;
    let totalCells = 0, matchingCells = 0;
    const submitted = {{}};

    for (const posStr of Object.keys(solveState.answerGrids)) {{
        submitted[posStr] = solveState.answerGrids[posStr].toArray();
        const sub = submitted[posStr];
        const exp = expected[posStr];
        if (!exp) continue;
        for (let r = 0; r < Math.max(sub.length, exp.length); r++) {{
            const sr = sub[r] || []; const er = exp[r] || [];
            for (let c = 0; c < Math.max(sr.length, er.length); c++) {{
                totalCells++;
                if ((sr[c] !== undefined ? sr[c] : -1) === (er[c] !== undefined ? er[c] : -2)) matchingCells++;
            }}
        }}
    }}
    const correct = JSON.stringify(submitted) === JSON.stringify(expected);
    const acc = totalCells > 0 ? matchingCells / totalCells : 0;

    const fb = document.getElementById('solve-feedback');
    fb.style.display = 'block';
    const banner = document.getElementById('solve-banner');
    banner.className = correct ? 'alert alert-success' : 'alert alert-warning';
    banner.textContent = correct ? 'Correct! Perfect match.' : 'Incorrect. Cell accuracy: ' + (acc * 100).toFixed(1) + '%';

    const diffRow = document.getElementById('solve-diff');
    diffRow.innerHTML = '';
    for (const posStr of Object.keys(expected)) {{
        const sub = submitted[posStr] || [];
        const exp = expected[posStr];
        ['Your Answer', 'Diff', 'Expected'].forEach(label => {{
            const col = document.createElement('div');
            col.className = 'col-md-4 text-center';
            col.innerHTML = '<small class="text-muted">' + (p.masked_positions.length > 1 ? 'Grid ' + (parseInt(posStr)+1) + ' — ' : '') + label + '</small>';
            const gDiv = document.createElement('div');
            if (label === 'Your Answer' && sub.length) {{
                renderGrid(gDiv, new Grid(sub.length, sub[0].length, sub), {{ cellSize: 24 }});
            }} else if (label === 'Expected') {{
                renderGrid(gDiv, new Grid(exp.length, exp[0].length, exp), {{ cellSize: 24 }});
            }} else if (label === 'Diff' && sub.length) {{
                renderFeedbackGrid(gDiv, sub, exp, 24);
            }}
            col.appendChild(gDiv);
            diffRow.appendChild(col);
        }});
    }}
}}

function clearSolve() {{
    const p = solveState.puzzle;
    p.masked_positions.forEach(pos => {{
        const item = p.sequence[pos];
        solveState.answerGrids[String(pos)] = new Grid(item.rows, item.cols);
        renderSolveGrid(String(pos));
    }});
}}

// ==================== INSPECT ====================
let inspectRendered = false;

function renderInspect() {{
    if (inspectRendered) return;
    inspectRendered = true;
    filterInspect();
}}

function filterInspect() {{
    const search = (document.getElementById('inspect-search').value || '').toLowerCase();
    const statusFilter = document.getElementById('inspect-status').value;
    const list = document.getElementById('inspect-list');
    list.innerHTML = '';
    let shown = 0;

    PUZZLES.forEach(p => {{
        const pid = p.puzzle_id.toLowerCase();
        const title = (p.title || '').toLowerCase();
        if (search && !pid.includes(search) && !title.includes(search)) return;

        const statuses = MODELS.map(m => ((p.model_results||{{}})[m]||{{}}).status || '').join(' ');
        if (statusFilter && !statuses.includes(statusFilter)) return;

        // Model dots
        let dots = '';
        MODELS.forEach(m => {{
            const mr = (p.model_results || {{}})[m];
            if (!mr) return;
            const s = mr.status;
            const cls = s === 'narc' ? 'dot-narc' : s === 'grids_sufficient' ? 'dot-grids' : s === 'narrative_sufficient' ? 'dot-narr' : 'dot-fail';
            const short = m.replace('gpt-oss-','').replace('qwen3.5-','q').replace('nemotron-3-','nem-');
            dots += '<span class="dot ' + cls + '" title="' + m + ': ' + s + '">' + short.slice(0,5) + '</span> ';
        }});

        // Condition results per model
        let rows = '';
        MODELS.forEach(m => {{
            const mr = (p.model_results || {{}})[m];
            if (!mr) return;
            const short = m.replace('gpt-oss-','').replace('qwen3.5-','q').replace('nemotron-3-','nem-');
            const s = mr.status;
            const cls = s === 'narc' ? 'dot-narc' : s === 'grids_sufficient' ? 'dot-grids' : s === 'narrative_sufficient' ? 'dot-narr' : 'dot-fail';
            let cells = '';
            ['grids_only','narrative_only','both'].forEach(cond => {{
                const r = (mr.results || {{}})[cond];
                if (!r) {{ cells += '<td style="color:#444;">—</td>'; return; }}
                const icon = r.correct ? '&#10003;' : '&#10007;';
                const color = r.correct ? '#2ECC40' : '#666';
                cells += '<td style="color:' + color + ';">' + icon + ' ' + Math.round((r.cell_accuracy||0)*100) + '%</td>';
            }});
            rows += '<tr><td style="text-align:left;font-weight:600;">' + short + '</td><td><span class="dot ' + cls + '">' + s.slice(0,4) + '</span></td>' + cells + '</tr>';
        }});

        list.innerHTML += '<details class="mb-2" style="border:1px solid #2a2a3e;border-radius:6px;overflow:hidden;">' +
            '<summary style="padding:8px 14px;background:#1a1a2e;cursor:pointer;display:flex;align-items:center;gap:10px;">' +
            '<span style="color:#666;font-family:monospace;font-size:0.85em;width:130px;">' + p.puzzle_id + '</span>' +
            '<span style="flex:1;font-weight:600;">' + p.title + '</span>' +
            '<span style="color:#888;font-size:0.85em;">' + p.sequence.length + 'g</span>' +
            '<span>' + dots + '</span></summary>' +
            '<div style="padding:14px;background:#111122;">' +
            '<div class="narrative-text visible" style="display:block;margin-bottom:10px;">' + (p.narrative||'').slice(0,300) + '</div>' +
            '<table style="width:100%;font-size:0.9em;border-collapse:collapse;">' +
            '<tr><th style="text-align:left;padding:4px 8px;">Model</th><th style="padding:4px 8px;">Status</th><th style="padding:4px 8px;">Grids</th><th style="padding:4px 8px;">Narr</th><th style="padding:4px 8px;">Both</th></tr>' +
            rows + '</table></div></details>';
        shown++;
    }});

    document.getElementById('inspect-count').textContent = shown + ' / ' + PUZZLES.length;
}}
</script>
</body>
</html>"""

    with open(output, "w") as f:
        f.write(html)
    click.echo(f"Exported to {output} ({len(html)//1024}KB)")


if __name__ == "__main__":
    main()
