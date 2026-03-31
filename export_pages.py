#!/usr/bin/env python3
"""Export NARC as a static site for GitHub Pages.

Generates proper HTML files using the same templates/CSS/JS as the Flask server,
but with all data embedded so no backend is needed.

Usage:
    python export_pages.py [--output-dir docs]
"""

import json
import shutil
from pathlib import Path

import click

from db import init_db, get_all_puzzles, puzzle_to_json, get_variants, get_trials
from grids import grid_to_base64_png


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
@click.option("--output-dir", default="docs")
def main(output_dir):
    conn = init_db()
    out = Path(output_dir)
    out.mkdir(exist_ok=True)

    # Copy static assets
    static_out = out / "static"
    if static_out.exists():
        shutil.rmtree(static_out)
    shutil.copytree("static", static_out)

    # Build puzzle data with variants and model results
    all_puzzles = []
    for p in get_all_puzzles(conn):
        pdata = puzzle_to_json(p)
        pid = pdata["puzzle_id"]
        variants = []
        for v in get_variants(conn, pid):
            variants.append({
                "variant": v["variant"],
                "source_domain": v["source_domain"],
                "narrative": v["narrative"],
            })
        model_results = {}
        for m in MODELS:
            status, results = _narc_status(conn, pid, m)
            model_results[m] = {"status": status, "results": results}
        pdata["variants"] = variants
        pdata["model_results"] = model_results
        all_puzzles.append(pdata)

    conn.close()

    # Stats
    total_variants = sum(len(p["variants"]) for p in all_puzzles)
    grid_sizes = set()
    for p in all_puzzles:
        for item in p["sequence"]:
            grid_sizes.add((item["rows"], item["cols"]))

    # Spectrum tag ordering
    spectrum_order = [
        "spectrum:human-forte", "spectrum:human-edge", "spectrum:balanced",
        "spectrum:ai-edge", "spectrum:ai-forte", "spectrum:domain-dependent"
    ]

    # Collect tag counts
    tag_counts = {}
    for p in all_puzzles:
        for t in (p.get("tags") or "").split(","):
            t = t.strip()
            if t:
                tag_counts[t] = tag_counts.get(t, 0) + 1

    def tags_by_prefix(prefix):
        return sorted([t for t in tag_counts if t.startswith(prefix + ":")],
                       key=lambda t: -tag_counts[t])

    puzzles_json = json.dumps(all_puzzles)

    # Write the single-page app
    html = _build_page(
        all_puzzles, puzzles_json, tag_counts, spectrum_order, tags_by_prefix,
        total_variants, len(grid_sizes), MODELS
    )

    (out / "index.html").write_text(html)
    click.echo(f"Static site exported to {out}/ ({len(all_puzzles)} puzzles)")
    click.echo(f"  {out}/index.html")
    click.echo(f"  {out}/static/css/")
    click.echo(f"  {out}/static/js/")


def _build_page(puzzles, puzzles_json, tag_counts, spectrum_order, tags_by_prefix,
                total_variants, num_grid_sizes, models):
    """Build the full single-page app HTML."""

    # Read JS for embedding
    js_grids = Path("static/js/grids.js").read_text()

    models_json = json.dumps(models)

    return f"""<!DOCTYPE html>
<html lang="en" data-bs-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NARC — Narrative Augmented Reasoning Challenges</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link rel="stylesheet" href="static/css/grids.css">
<link rel="stylesheet" href="static/css/narc.css">
<style>
.view {{ display: none; }}
.view.active {{ display: block; }}
.nav-link {{ cursor: pointer; }}
.nav-link.active-view {{ color: #fff !important; }}

/* Inspect details */
details {{ border: 1px solid #2a2a3e; border-radius: 6px; margin-bottom: 6px; overflow: hidden; }}
details summary {{ padding: 8px 14px; background: #1a1a2e; cursor: pointer; display: flex; align-items: center; gap: 10px; }}
details summary:hover {{ background: #1f2040; }}
details > div {{ padding: 14px; background: #111122; }}

/* Status dots */
.dot {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 0.7em; font-weight: 600; margin: 0 1px; }}
.dot-narc {{ background: #134e1f; color: #2ECC40; }}
.dot-grids {{ background: #0a3050; color: #0074D9; }}
.dot-narr {{ background: #4a3000; color: #FF851B; }}
.dot-fail {{ background: #2a1515; color: #555; }}

/* Variant tabs */
.variant-tabs {{ display: flex; gap: 4px; flex-wrap: wrap; margin-top: 8px; }}
.vtab {{ background: #1f2937; border: 1px solid #374151; color: #888; padding: 4px 10px;
         border-radius: 4px; cursor: pointer; font-size: 0.8em; }}
.vtab:hover {{ background: #2a2a3e; color: #ddd; }}
.vtab.active {{ background: #2d1f00; color: #daa520; border-color: #f59e0b; }}

/* Tag chips on cards */
.tag-chip {{ background: #1f2937; color: #888; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; }}
</style>
</head>
<body>

<nav class="navbar navbar-expand-lg navbar-dark mb-4">
    <div class="container">
        <a class="navbar-brand fw-bold" href="#" onclick="showView('about')">NARC</a>
        <div class="navbar-nav">
            <a class="nav-link active-view" id="nav-about" href="#" onclick="showView('about')">About</a>
            <a class="nav-link" id="nav-browse" href="#" onclick="showView('browse')">Browse</a>
            <a class="nav-link" id="nav-create" href="#" onclick="showView('create')">Create</a>
            <a class="nav-link" id="nav-inspect" href="#" onclick="showView('inspect')">Inspect</a>
        </div>
    </div>
</nav>

<div class="container">

<!-- ==================== ABOUT ==================== -->
<div id="view-about" class="view active">
<div class="row justify-content-center"><div class="col-lg-8">
<h1 class="mb-3">NARC</h1>
<p class="lead" style="color:#f59e0b;">Narrative Augmented Reasoning Challenges</p>

<div class="card mb-4"><div class="card-body">
<h4>What is NARC?</h4>
<p>NARC is a new kind of abstract reasoning puzzle. Each puzzle presents a sequence of colored grids
that tell a visual "story." One or more grids in the sequence are hidden, and your goal is to
reconstruct them pixel-perfectly.</p>
<p>The catch: <strong>the grids alone aren't enough.</strong> Each puzzle comes with a short
narrative clue. Without it, the missing grid is ambiguous &mdash; multiple answers seem plausible.
With the clue, exactly one answer is correct.</p>
<p>This is the <strong>NARC property</strong>: neither the grids nor the narrative suffice alone,
but together they uniquely determine the answer.</p>
</div></div>

<div class="card mb-4"><div class="card-body">
<h4>How to play</h4>
<ol>
<li><strong>Look at the grid sequence.</strong> One or more grids are hidden
    (shown as <span style="color:#f59e0b;">?</span>).</li>
<li><strong>Try to guess</strong> what the missing grid looks like from the visual pattern alone.
    You can submit a guess before seeing the clue.</li>
<li><strong>Reveal the clue.</strong> A short narrative provides the key insight.</li>
<li><strong>Draw your answer</strong> using the color picker and submit.</li>
</ol>
</div></div>

<div class="card mb-4"><div class="card-body">
<h4>Why does this matter?</h4>
<p>NARC investigates a fundamental question: <strong>how does narrative transform visual reasoning?</strong></p>
<p>Abstract grid patterns that look meaningless can become instantly comprehensible when accompanied
by the right story. This "narrative augmentation" works differently for humans and AI systems &mdash;
and studying where they diverge reveals something deep about how each processes language and vision together.</p>
<p>NARC is a sibling project to
<a href="https://bertybaums.github.io/marc2/" style="color:#f59e0b;">MARC2</a>
(Metaphor Abstraction and Reasoning Corpus), which explored how figurative language helps AI solve
abstract reasoning tasks from the <a href="https://arcprize.org/" style="color:#f59e0b;">ARC-AGI</a> benchmark.</p>
</div></div>

<div class="card mb-4"><div class="card-body">
<h4>The puzzle corpus</h4>
<div class="row text-center my-3">
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">{len(puzzles)}</div>
<div class="text-muted">Puzzles</div></div>
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">{total_variants}</div>
<div class="text-muted">Narrative variants</div></div>
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">{num_grid_sizes}</div>
<div class="text-muted">Unique grid sizes</div></div>
<div class="col"><div style="font-size:2em;font-weight:bold;color:#f59e0b;">3&ndash;8</div>
<div class="text-muted">Grids per puzzle</div></div>
</div>
<p>Puzzles span literary classics (Hemingway, Kafka, Shelley), scientific concepts (natural selection,
entropy, plate tectonics), philosophical thought experiments (the trolley problem, Plato's cave),
and more. Each is rated on both <strong>human difficulty</strong> and <strong>AI difficulty</strong>
(1&ndash;5).</p>
</div></div>

<div class="card mb-4"><div class="card-body">
<h4>Research context</h4>
<ul>
<li><strong>ARC-AGI</strong> &mdash; abstract visual reasoning as an intelligence benchmark</li>
<li><strong>MARC</strong> &mdash; figurative language as a bridge between human and machine reasoning</li>
<li><strong>Econarratology</strong> &mdash; Erin James's framework for how narratives construct
    "storyworlds" that organize spatial and temporal understanding</li>
<li><strong>Story Prism</strong> &mdash; Erin James's narrative decomposition framework, breaking stories into five facets:
    <em>Teller &amp; Told</em> (voice, perspective, audience),
    <em>World</em> (setting in space and time),
    <em>Events</em> (temporal ordering and pacing),
    <em>Actors</em> (characters and their salience),
    and <em>How It Feels</em> (emotion, sensation, tone).
    NARC puzzles are tagged by which facets their narratives foreground, and variants systematically
    vary one facet at a time to measure its effect on solvability.</li>
<li><strong>Focalization</strong> &mdash; how the same story told from different characters'
    perspectives changes what information is foregrounded</li>
</ul>
</div></div>

<div class="text-center text-muted mb-4"><small>University of Idaho &middot; 2026</small></div>
</div></div>
</div>

<!-- ==================== BROWSE ==================== -->
<div id="view-browse" class="view">
<div class="d-flex justify-content-between align-items-center mb-3">
    <h2 class="mb-0">Puzzles <small class="text-muted" id="browse-count"></small></h2>
    <input type="text" id="browse-search" class="form-control form-control-sm"
           placeholder="Search..." style="width:200px;" oninput="filterBrowse()">
</div>
<div id="browse-filters" class="mb-3"></div>
<div id="browse-grid" class="row g-3"></div>
</div>

<!-- ==================== SOLVE ==================== -->
<div id="view-solve" class="view">
<div class="mb-2">
    <a href="#" onclick="showView('browse')" class="text-muted">&larr; Back to puzzles</a>
</div>
<div id="solve-container"></div>
</div>

<!-- ==================== INSPECT ==================== -->
<div id="view-inspect" class="view">
<h2 class="mb-3">Inspect <small class="text-muted">AI model results</small></h2>
<div class="mb-3">
    <input type="text" id="inspect-search" class="form-control form-control-sm d-inline-block"
           placeholder="Search..." style="width:200px;" oninput="filterInspect()">
    <select id="inspect-status" class="form-select form-select-sm d-inline-block"
            style="width:160px;" onchange="filterInspect()">
        <option value="">All statuses</option>
        <option value="narc">NARC only</option>
        <option value="grids_sufficient">Grids sufficient</option>
        <option value="narrative_sufficient">Narrative sufficient</option>
        <option value="unsolvable">Unsolvable</option>
    </select>
    <span class="text-muted ms-2" id="inspect-count"></span>
</div>
<div id="inspect-list"></div>
</div>

<!-- ==================== CREATE ==================== -->
<div id="view-create" class="view">
<div class="alert alert-warning d-flex align-items-center mb-3">
    <strong class="me-2">Preview only.</strong> This page lets you design puzzles locally, but submissions are not yet being accepted. To share a puzzle you create here, download the JSON and email it to Bert.
</div>
<h2 class="mb-3">Create Puzzle</h2>
<div class="d-flex gap-3 mb-3 align-items-end flex-wrap">
    <div><label class="form-label">Puzzle ID</label>
    <input type="text" id="create-pid" class="form-control form-control-sm" placeholder="narc_new_001" style="width:140px;"></div>
    <div><label class="form-label">Title</label>
    <input type="text" id="create-title" class="form-control form-control-sm" placeholder="My Puzzle" style="width:250px;"></div>
    <div><label class="form-label">Sequence Length</label>
    <input type="range" id="create-seq-len" class="form-range" min="3" max="8" value="4" style="width:120px;" oninput="document.getElementById('create-seq-display').textContent=this.value;buildCreateSlots();">
    <span id="create-seq-display">4</span></div>
</div>
<div class="mb-3"><label class="form-label">Color</label><div id="create-picker"></div></div>
<div class="card mb-3"><div class="card-header">Grid Sequence</div>
<div class="card-body"><div id="create-slots" class="sequence-container"></div></div></div>
<div class="card mb-3"><div class="card-header">Narrative</div>
<div class="card-body"><textarea id="create-narrative" class="form-control" rows="3" placeholder="Write the clue..."></textarea></div></div>
<div class="d-flex gap-2 mb-3">
    <button class="btn btn-narc" onclick="downloadCreateJSON()">Download JSON</button>
    <button class="btn btn-outline-secondary" onclick="previewCreate()">Preview</button>
</div>
<div id="create-preview" class="card mb-4" style="display:none;">
    <div class="card-header">Preview</div>
    <div class="card-body"><div id="create-preview-seq" class="sequence-container mb-3"></div>
    <button class="narrative-reveal-btn" onclick="document.getElementById('create-preview-narr').classList.toggle('visible')">Reveal Clue</button>
    <div class="narrative-text" id="create-preview-narr"></div></div>
</div>
</div>

</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>{js_grids}</script>
<script>
const PUZZLES = {puzzles_json};
const MODELS = {models_json};
const SPECTRUM_ORDER = {json.dumps(spectrum_order)};

// ==================== VIEW SWITCHING ====================
function showView(name) {{
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-' + name).classList.add('active');
    document.querySelectorAll('.nav-link').forEach(n => n.classList.remove('active-view'));
    const navEl = document.getElementById('nav-' + name);
    if (navEl) navEl.classList.add('active-view');
    if (name === 'browse') renderBrowse();
    if (name === 'inspect') renderInspect();
}}

// ==================== STATUS DOT ====================
function statusDot(status, label) {{
    const cls = status === 'narc' ? 'dot-narc' : status === 'grids_sufficient' ? 'dot-grids' :
                status === 'narrative_sufficient' ? 'dot-narr' : 'dot-fail';
    return '<span class="dot ' + cls + '" title="' + label + ': ' + status + '">' +
           label.slice(0,5) + '</span>';
}}

function modelDots(p) {{
    let html = '';
    MODELS.forEach(m => {{
        const mr = (p.model_results || {{}})[m];
        if (!mr) return;
        const short = m.replace('gpt-oss-','').replace('qwen3.5-','q').replace('nemotron-3-','nem-');
        html += statusDot(mr.status, short) + ' ';
    }});
    return html;
}}

// ==================== BROWSE ====================
let browseRendered = false;
const activeTags = new Set();

function renderBrowse() {{
    if (browseRendered) return;
    browseRendered = true;

    const tagCounts = {{}};
    PUZZLES.forEach(p => {{
        (p.tags || '').split(',').forEach(t => {{
            t = t.trim();
            if (t) tagCounts[t] = (tagCounts[t] || 0) + 1;
        }});
    }});

    const dims = [
        ['Spectrum', 'spectrum', 'btn-outline-danger', SPECTRUM_ORDER.filter(t => tagCounts[t])],
        ['Audience', 'audience', 'btn-outline-info', null],
        ['Arc', 'arc', 'btn-outline-warning', null],
        ['Clue', 'clue', 'btn-outline-success', null],
        ['Domain', 'domain', 'btn-outline-secondary', null],
        ['Grids', 'grids', 'btn-outline-light', null],
    ];

    const filtersEl = document.getElementById('browse-filters');
    dims.forEach(([label, prefix, cls, order]) => {{
        let tags = order || Object.keys(tagCounts).filter(t => t.startsWith(prefix + ':'))
            .sort((a,b) => (tagCounts[b]||0) - (tagCounts[a]||0));
        if (!tags.length) return;
        let row = document.createElement('div');
        row.className = 'mb-1';
        row.innerHTML = '<strong class="text-muted me-2">' + label + ':</strong>' +
            tags.map(t => '<button class="btn btn-sm ' + cls + ' tag-btn mb-1 me-1" data-tag="' + t +
                '" onclick="toggleTag(this)">' + t.split(':')[1] +
                ' <span class="badge bg-secondary">' + (tagCounts[t]||0) + '</span></button>').join('');
        filtersEl.appendChild(row);
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
        for (const t of activeTags) if (!tags.includes(t)) return;

        const nMasked = p.masked_positions.length;
        const maskedStr = nMasked === 1 ? 'masked #' + (p.masked_positions[0]+1) : nMasked + ' masked';
        const diffStr = p.human_difficulty && p.ai_difficulty ?
            'H' + p.human_difficulty + '/A' + p.ai_difficulty : '';

        const col = document.createElement('div');
        col.className = 'col-md-4';
        col.innerHTML = '<div class="card puzzle-card" onclick="solvePuzzle(\\'' + p.puzzle_id + '\\')">' +
            '<div class="card-body">' +
            '<h5 class="card-title">' + p.title + '</h5>' +
            '<p class="text-muted mb-1">' + p.sequence.length + ' grids &middot; ' + maskedStr +
            (diffStr ? ' &middot; ' + diffStr : '') + '</p>' +
            '<div class="mb-1">' + modelDots(p) + '</div>' +
            '<p class="card-text small text-truncate">' + (p.narrative||'').slice(0,80) + '...</p>' +
            '<button class="btn btn-sm btn-narc" onclick="event.stopPropagation();solvePuzzle(\\'' +
            p.puzzle_id + '\\')">Solve</button></div></div>';
        grid.appendChild(col);
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
        (p.masked_positions.length === 1 ? 'grid #' + (p.masked_positions[0]+1) :
         p.masked_positions.length + ' grids') + '</p>';

    // Sequence
    const seqDiv = document.createElement('div');
    seqDiv.className = 'card mb-3';
    seqDiv.innerHTML = '<div class="card-header">Grid Sequence</div><div class="card-body"><div class="sequence-container" id="solve-seq"></div></div>';
    c.appendChild(seqDiv);

    const seqContainer = document.getElementById('solve-seq');
    p.sequence.forEach((item, i) => {{
        if (i > 0) {{
            const arrow = document.createElement('div');
            arrow.className = 'sequence-arrow';
            arrow.innerHTML = '&rarr;';
            seqContainer.appendChild(arrow);
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
        if (item.masked) renderMaskedPlaceholder(canvas, item.rows, item.cols, 28);
        else renderGrid(canvas, new Grid(item.rows, item.cols, item.grid), {{ cellSize: 28 }});
        wrapper.appendChild(canvas);
        slot.appendChild(wrapper);
        seqContainer.appendChild(slot);
    }});

    // Narrative + variants
    let narrHTML = '<div class="narrative-container">' +
        '<button class="narrative-reveal-btn" id="reveal-btn" onclick="revealNarr()">Reveal Clue</button>' +
        '<div class="narrative-text" id="narr-text">' + escHTML(p.narrative) + '</div>';
    if (p.variants && p.variants.length > 1) {{
        narrHTML += '<div class="variant-tabs" id="solve-vtabs" style="display:none;">';
        p.variants.forEach((v, vi) => {{
            const label = v.variant === 'original' ? 'Original' : (v.source_domain || v.variant);
            narrHTML += '<button class="vtab' + (vi===0?' active':'') + '" onclick="switchVar(' + vi + ')">' + escHTML(label) + '</button>';
        }});
        narrHTML += '</div>';
    }}
    narrHTML += '</div>';
    c.innerHTML += narrHTML;

    // Answer editor
    c.innerHTML += '<div class="card mb-3"><div class="card-header">Your Answer</div><div class="card-body">' +
        '<div id="solve-picker" class="mb-2"></div><div id="solve-editors"></div>' +
        '<div class="mt-3"><button class="btn btn-narc" onclick="submitSolve()">Submit Answer</button> ' +
        '<button class="btn btn-outline-secondary ms-2" onclick="clearSolve()">Clear</button></div></div></div>';

    // Feedback
    c.innerHTML += '<div id="solve-feedback" class="card mb-4" style="display:none;">' +
        '<div class="card-header">Result</div><div class="card-body">' +
        '<div id="solve-banner" class="alert mb-3"></div><div id="solve-diff" class="row"></div></div></div>';

    buildColorPicker(document.getElementById('solve-picker'), col => {{ solveState.selectedColor = col; }});
    const editors = document.getElementById('solve-editors');
    p.masked_positions.forEach(pos => {{
        const item = p.sequence[pos];
        const ps = String(pos);
        solveState.answerGrids[ps] = new Grid(item.rows, item.cols);
        if (p.masked_positions.length > 1) {{
            const h = document.createElement('h6');
            h.textContent = 'Grid ' + (pos+1) + ' (' + item.rows + 'x' + item.cols + ')';
            editors.appendChild(h);
        }}
        const canvas = document.createElement('div');
        canvas.id = 'solve-grid-' + ps;
        canvas.style.display = 'inline-block';
        editors.appendChild(canvas);
        renderSolveGrid(ps);
    }});
}}

function renderSolveGrid(ps) {{
    const canvas = document.getElementById('solve-grid-' + ps);
    const g = solveState.answerGrids[ps];
    renderGrid(canvas, g, {{ editable: true, cellSize: 28, onCellClick: (r, c) => {{
        g.grid[r][c] = solveState.selectedColor;
        setCellColor(canvas.querySelectorAll('.grid_row')[r].querySelectorAll('.cell')[c], solveState.selectedColor);
    }}}});
}}

function revealNarr() {{
    solveState.narrativeRevealed = true;
    document.getElementById('narr-text').classList.add('visible');
    document.getElementById('reveal-btn').style.display = 'none';
    const vtabs = document.getElementById('solve-vtabs');
    if (vtabs) vtabs.style.display = 'flex';
    document.querySelectorAll('.slot-label[data-full-label]').forEach(el => {{
        el.textContent = el.dataset.fullLabel;
    }});
}}

function switchVar(idx) {{
    const v = solveState.puzzle.variants[idx];
    if (v) document.getElementById('narr-text').textContent = v.narrative;
    document.querySelectorAll('#solve-vtabs .vtab').forEach((b, i) => b.classList.toggle('active', i === idx));
}}

function submitSolve() {{
    const p = solveState.puzzle;
    const expected = p.answer_grids;
    let total = 0, matching = 0;
    const submitted = {{}};
    for (const ps of Object.keys(solveState.answerGrids)) {{
        submitted[ps] = solveState.answerGrids[ps].toArray();
        const sub = submitted[ps], exp = expected[ps];
        if (!exp) continue;
        for (let r = 0; r < Math.max(sub.length, exp.length); r++) {{
            const sr = sub[r]||[], er = exp[r]||[];
            for (let c = 0; c < Math.max(sr.length, er.length); c++) {{
                total++;
                if ((sr[c]!==undefined?sr[c]:-1) === (er[c]!==undefined?er[c]:-2)) matching++;
            }}
        }}
    }}
    const correct = JSON.stringify(submitted) === JSON.stringify(expected);
    const acc = total > 0 ? matching / total : 0;

    const fb = document.getElementById('solve-feedback');
    fb.style.display = 'block';
    const banner = document.getElementById('solve-banner');
    banner.className = correct ? 'alert alert-success' : 'alert alert-warning';
    banner.textContent = correct ? 'Correct! Perfect match.' : 'Incorrect. Cell accuracy: ' + (acc*100).toFixed(1) + '%';

    const diffRow = document.getElementById('solve-diff');
    diffRow.innerHTML = '';
    for (const ps of Object.keys(expected)) {{
        const sub = submitted[ps]||[], exp = expected[ps];
        ['Your Answer','Diff','Expected'].forEach(label => {{
            const col = document.createElement('div');
            col.className = 'col-md-4 text-center';
            col.innerHTML = '<small class="text-muted">' +
                (p.masked_positions.length > 1 ? 'Grid '+(parseInt(ps)+1)+' — ' : '') + label + '</small>';
            const gDiv = document.createElement('div');
            if (label === 'Your Answer' && sub.length)
                renderGrid(gDiv, new Grid(sub.length, sub[0].length, sub), {{cellSize:24}});
            else if (label === 'Expected')
                renderGrid(gDiv, new Grid(exp.length, exp[0].length, exp), {{cellSize:24}});
            else if (label === 'Diff' && sub.length)
                renderFeedbackGrid(gDiv, sub, exp, 24);
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

function escHTML(s) {{ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}

// ==================== INSPECT ====================
let inspectRendered = false;

function renderInspect() {{
    if (inspectRendered) return;
    inspectRendered = true;
    filterInspect();
}}

function filterInspect() {{
    const search = (document.getElementById('inspect-search').value || '').toLowerCase();
    const sf = document.getElementById('inspect-status').value;
    const list = document.getElementById('inspect-list');
    list.innerHTML = '';
    let shown = 0;

    PUZZLES.forEach(p => {{
        const pid = p.puzzle_id.toLowerCase();
        const title = (p.title||'').toLowerCase();
        if (search && !pid.includes(search) && !title.includes(search)) return;
        const statuses = MODELS.map(m => ((p.model_results||{{}})[m]||{{}}).status||'').join(' ');
        if (sf && !statuses.includes(sf)) return;

        let rows = '';
        MODELS.forEach(m => {{
            const mr = (p.model_results||{{}})[m];
            if (!mr) return;
            const short = m.replace('gpt-oss-','').replace('qwen3.5-','q').replace('nemotron-3-','nem-');
            let cells = '';
            ['grids_only','narrative_only','both'].forEach(cond => {{
                const r = (mr.results||{{}})[cond];
                if (!r) {{ cells += '<td style="color:#444;">—</td>'; return; }}
                const icon = r.correct ? '&#10003;' : '&#10007;';
                const color = r.correct ? '#2ECC40' : '#666';
                cells += '<td style="color:'+color+';">'+icon+' '+Math.round((r.cell_accuracy||0)*100)+'%</td>';
            }});
            rows += '<tr><td style="text-align:left;font-weight:600;">'+short+'</td><td>'+
                statusDot(mr.status, short)+'</td>'+cells+'</tr>';
        }});

        list.innerHTML += '<details><summary>' +
            '<span style="color:#666;font-family:monospace;font-size:0.85em;width:130px;flex-shrink:0;">'+p.puzzle_id+'</span>' +
            '<span style="flex:1;font-weight:600;">'+p.title+'</span>' +
            '<span style="color:#888;font-size:0.85em;margin-right:8px;">'+p.sequence.length+'g</span>' +
            '<span>'+modelDots(p)+'</span></summary>' +
            '<div><div class="narrative-text visible" style="display:block;margin-bottom:10px;">'+
            escHTML((p.narrative||'').slice(0,300))+'</div>' +
            '<table class="table table-sm mb-0" style="font-size:0.9em;"><thead>' +
            '<tr><th style="text-align:left;">Model</th><th>Status</th><th>Grids</th><th>Narr</th><th>Both</th></tr></thead>' +
            '<tbody>'+rows+'</tbody></table></div></details>';
        shown++;
    }});
    document.getElementById('inspect-count').textContent = shown + ' / ' + PUZZLES.length;
}}

// ==================== CREATE ====================
let createColor = 0;
let createGrids = [];
let createMasked = new Set();

function initCreate() {{
    buildColorPicker(document.getElementById('create-picker'), c => {{ createColor = c; }});
    buildCreateSlots();
}}

function buildCreateSlots() {{
    const n = parseInt(document.getElementById('create-seq-len').value);
    const container = document.getElementById('create-slots');
    container.innerHTML = '';
    createGrids = [];
    for (let i = 0; i < n; i++) {{
        if (i > 0) {{
            const arrow = document.createElement('div');
            arrow.className = 'sequence-arrow';
            arrow.innerHTML = '&rarr;';
            container.appendChild(arrow);
        }}
        const isMasked = createMasked.has(i);
        const slot = document.createElement('div');
        slot.className = 'grid-slot' + (isMasked ? ' masked' : '');
        slot.id = 'create-slot-' + i;
        slot.innerHTML = '<div class="slot-label">Grid ' + (i+1) + '</div>' +
            '<div class="mb-1"><input type="text" class="form-control form-control-sm d-inline-block" ' +
            'value="5x5" style="width:60px;" onchange="resizeCreateGrid('+i+',this.value)" id="create-dim-'+i+'">' +
            ' <input type="text" class="form-control form-control-sm d-inline-block" placeholder="label" ' +
            'style="width:70px;" id="create-label-'+i+'"></div>' +
            '<div class="grid-wrapper"><div id="create-canvas-'+i+'"></div></div>' +
            '<button class="btn btn-sm mt-1 '+(isMasked?'btn-warning':'btn-outline-secondary')+'" ' +
            'onclick="toggleCreateMask('+i+')">'+(isMasked?'Masked':'Mask')+'</button>';
        container.appendChild(slot);
        createGrids[i] = new Grid(5, 5);
        renderCreateGrid(i);
    }}
}}

function renderCreateGrid(idx) {{
    const canvas = document.getElementById('create-canvas-' + idx);
    renderGrid(canvas, createGrids[idx], {{
        editable: true, cellSize: 28,
        onCellClick: (r, c) => {{
            createGrids[idx].grid[r][c] = createColor;
            setCellColor(canvas.querySelectorAll('.grid_row')[r].querySelectorAll('.cell')[c], createColor);
        }}
    }});
}}

function resizeCreateGrid(idx, val) {{
    const parts = val.split('x');
    if (parts.length !== 2) return;
    const w = parseInt(parts[0]), h = parseInt(parts[1]);
    if (isNaN(w)||isNaN(h)||w<1||h<1||w>30||h>30) return;
    createGrids[idx].resize(h, w);
    renderCreateGrid(idx);
}}

function toggleCreateMask(idx) {{
    if (createMasked.has(idx)) createMasked.delete(idx);
    else createMasked.add(idx);
    const slot = document.getElementById('create-slot-' + idx);
    const btn = slot.querySelector('button:last-child');
    if (createMasked.has(idx)) {{
        slot.classList.add('masked'); btn.className = 'btn btn-sm mt-1 btn-warning'; btn.textContent = 'Masked';
    }} else {{
        slot.classList.remove('masked'); btn.className = 'btn btn-sm mt-1 btn-outline-secondary'; btn.textContent = 'Mask';
    }}
}}

function collectCreateData() {{
    const pid = document.getElementById('create-pid').value.trim();
    const title = document.getElementById('create-title').value.trim();
    const narrative = document.getElementById('create-narrative').value.trim();
    if (!pid || !title || !narrative || createMasked.size === 0) return null;

    const n = parseInt(document.getElementById('create-seq-len').value);
    const sequence = [];
    const maskedPositions = [...createMasked].sort((a,b) => a-b);
    const answerGrids = {{}};

    for (let i = 0; i < n; i++) {{
        const dim = document.getElementById('create-dim-'+i).value.split('x');
        const cols = parseInt(dim[0])||5, rows = parseInt(dim[1])||5;
        const label = document.getElementById('create-label-'+i).value.trim();
        const gridData = createGrids[i].toArray();
        if (createMasked.has(i)) {{
            answerGrids[String(i)] = gridData;
            sequence.push({{position:i, grid:null, rows:rows, cols:cols, label:label, masked:true}});
        }} else {{
            sequence.push({{position:i, grid:gridData, rows:rows, cols:cols, label:label}});
        }}
    }}
    return {{ puzzle_id:pid, title:title, narrative:narrative, sequence:sequence,
              masked_positions:maskedPositions, answer_grids:answerGrids,
              metadata:{{creator:'human',created_at:new Date().toISOString().slice(0,10)}} }};
}}

function downloadCreateJSON() {{
    const data = collectCreateData();
    if (!data) {{ alert('Fill in ID, title, narrative, and mask at least one grid.'); return; }}
    const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = data.puzzle_id + '.json';
    a.click();
}}

function previewCreate() {{
    const data = collectCreateData();
    if (!data) {{ alert('Fill in all fields first.'); return; }}
    const preview = document.getElementById('create-preview');
    preview.style.display = 'block';
    const seqC = document.getElementById('create-preview-seq');
    seqC.innerHTML = '';
    data.sequence.forEach((item, i) => {{
        if (i > 0) {{ const a = document.createElement('div'); a.className='sequence-arrow'; a.innerHTML='&rarr;'; seqC.appendChild(a); }}
        const slot = document.createElement('div');
        slot.className = 'grid-slot' + (item.masked ? ' masked' : '');
        const label = document.createElement('div'); label.className='slot-label'; label.textContent='Grid '+(i+1);
        slot.appendChild(label);
        const wrapper = document.createElement('div'); wrapper.className='grid-wrapper';
        const canvas = document.createElement('div');
        if (item.masked) renderMaskedPlaceholder(canvas, item.rows, item.cols, 28);
        else renderGrid(canvas, new Grid(item.rows, item.cols, item.grid), {{cellSize:28}});
        wrapper.appendChild(canvas); slot.appendChild(wrapper); seqC.appendChild(slot);
    }});
    document.getElementById('create-preview-narr').textContent = data.narrative;
}}

// Init create when first shown
let createInited = false;
const origShowView = showView;
showView = function(name) {{
    origShowView(name);
    if (name === 'create' && !createInited) {{ createInited = true; initCreate(); }}
}};
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
