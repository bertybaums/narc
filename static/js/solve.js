/**
 * NARC puzzle solving interface — supports single or multi-mask puzzles.
 *
 * Flow:
 * - Solver sees grids + answer editor(s) + "Reveal Narrative" button
 * - They can submit a guess before revealing (recorded as pre-narrative attempt)
 * - They can reveal the narrative at any time (recorded whether they guessed first or not)
 * - After revealing, they can submit again (recorded as post-narrative attempt)
 * - After submit: banner shows right/wrong; user can either try again or click
 *   "Reveal Answer" to see the diff + expected grids.
 * - Per-action events streamed to /api/solve-events for full session replay.
 */

let selectedColor = 0;
let answerGrids = {};      // keyed by position string: {"2": Grid, "3": Grid}
let narrativeRevealed = false;
let preNarrativeSubmitted = false;
let answerRevealed = false;  // sticky once user clicks "Reveal Answer"
let lastFeedback = null;     // {submitted, expected, correct, cellAccuracy}
let activeVariant = (PUZZLE.variants && PUZZLE.variants.length > 0) ? PUZZLE.variants[0].variant : 'original';

// --- Event recording -------------------------------------------------------

const pageLoadMs = Date.now();
let eventQueue = [];
let flushTimer = null;
const FLUSH_INTERVAL_MS = 5000;
const FLUSH_THRESHOLD = 20;

function recordEvent(type, payload = null) {
    eventQueue.push({
        type,
        payload,
        client_ms: Date.now() - pageLoadMs
    });
    if (eventQueue.length >= FLUSH_THRESHOLD) {
        flushEvents();
    } else if (!flushTimer) {
        flushTimer = setTimeout(flushEvents, FLUSH_INTERVAL_MS);
    }
}

function flushEvents(useBeacon = false) {
    if (flushTimer) {
        clearTimeout(flushTimer);
        flushTimer = null;
    }
    if (eventQueue.length === 0) return;
    const events = eventQueue;
    eventQueue = [];
    const body = JSON.stringify({
        session_id: SESSION_ID,
        puzzle_id: PUZZLE.puzzle_id,
        events
    });
    if (useBeacon && navigator.sendBeacon) {
        try {
            navigator.sendBeacon('/api/solve-events', new Blob([body], {type: 'application/json'}));
        } catch (e) {}
    } else {
        fetch('/api/solve-events', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body,
            keepalive: true
        }).catch(() => {});
    }
}

window.addEventListener('beforeunload', () => flushEvents(true));
window.addEventListener('pagehide', () => flushEvents(true));

// --- Init ------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    renderPuzzleSequence();
    setupAnswerEditors();
    recordEvent('page_load', {
        puzzle_id: PUZZLE.puzzle_id,
        n_grids: PUZZLE.sequence.length,
        n_masked: PUZZLE.masked_positions.length,
        n_variants: (PUZZLE.variants || []).length
    });
    updateScrollAffordance();
    const seqEl = document.getElementById('puzzle-sequence');
    if (seqEl) seqEl.addEventListener('scroll', updateScrollAffordance);
    window.addEventListener('resize', updateScrollAffordance);
});

// --- Scroll affordance -----------------------------------------------------

function updateScrollAffordance() {
    const container = document.getElementById('puzzle-sequence');
    const wrapper = document.getElementById('sequence-wrapper');
    if (!container || !wrapper) return;
    const canScroll = container.scrollWidth > container.clientWidth + 1;
    const atStart = container.scrollLeft <= 1;
    const atEnd = container.scrollLeft + container.clientWidth >= container.scrollWidth - 1;
    wrapper.classList.toggle('has-overflow-left', canScroll && !atStart);
    wrapper.classList.toggle('has-overflow-right', canScroll && !atEnd);
}

// --- Rendering -------------------------------------------------------------

function renderPuzzleSequence() {
    const container = document.getElementById('puzzle-sequence');
    container.innerHTML = '';

    const seq = PUZZLE.sequence;
    for (let i = 0; i < seq.length; i++) {
        if (i > 0) {
            const arrow = document.createElement('div');
            arrow.className = 'sequence-arrow';
            arrow.textContent = '→';
            container.appendChild(arrow);
        }

        const slot = document.createElement('div');
        slot.className = 'grid-slot' + (seq[i].masked ? ' masked' : '');

        const label = document.createElement('div');
        label.className = 'slot-label';
        // Show position number always; show descriptive label only after narrative reveal
        label.textContent = String(i + 1);
        if (seq[i].label) {
            label.dataset.fullLabel = seq[i].label;
        }
        slot.appendChild(label);

        const wrapper = document.createElement('div');
        wrapper.className = 'grid-wrapper';
        const canvas = document.createElement('div');

        if (seq[i].masked) {
            renderMaskedPlaceholder(canvas, seq[i].rows, seq[i].cols, 28);
        } else {
            const g = new Grid(seq[i].rows, seq[i].cols, seq[i].grid);
            renderGrid(canvas, g, { cellSize: 28 });
        }

        wrapper.appendChild(canvas);
        slot.appendChild(wrapper);
        container.appendChild(slot);
    }

    // Defer affordance update so layout has settled
    requestAnimationFrame(updateScrollAffordance);
}

function setupAnswerEditors() {
    buildColorPicker(document.getElementById('answer-picker'), c => {
        selectedColor = c;
        recordEvent('color_change', {color: c});
    });

    const container = document.getElementById('answer-editors');
    container.innerHTML = '';
    const maskedPositions = PUZZLE.masked_positions;

    for (const pos of maskedPositions) {
        const item = PUZZLE.sequence[pos];
        const rows = item.rows;
        const cols = item.cols;
        const posStr = String(pos);

        answerGrids[posStr] = new Grid(rows, cols);

        const wrapper = document.createElement('div');
        wrapper.className = 'mb-3';
        if (maskedPositions.length > 1) {
            const heading = document.createElement('h6');
            heading.textContent = `Grid ${pos + 1} (${rows}x${cols})`;
            wrapper.appendChild(heading);
        } else {
            const dim = document.createElement('small');
            dim.className = 'text-muted';
            dim.textContent = `${rows}x${cols}`;
            wrapper.appendChild(dim);
        }

        const canvas = document.createElement('div');
        canvas.id = 'answer-grid-' + posStr;
        canvas.style.display = 'inline-block';
        wrapper.appendChild(canvas);
        container.appendChild(wrapper);

        renderAnswerGrid(posStr);
    }
}

function renderAnswerGrid(posStr) {
    const canvas = document.getElementById('answer-grid-' + posStr);
    const gridObj = answerGrids[posStr];
    renderGrid(canvas, gridObj, {
        editable: true,
        cellSize: 28,
        onCellClick: (r, c) => {
            const prev = gridObj.grid[r][c];
            if (prev !== selectedColor) {
                recordEvent('cell_paint', {pos: posStr, r, c, from: prev, to: selectedColor});
            }
            gridObj.grid[r][c] = selectedColor;
            const cell = canvas.querySelectorAll('.grid_row')[r].querySelectorAll('.cell')[c];
            setCellColor(cell, selectedColor);
        }
    });
}

// --- Narrative reveal ------------------------------------------------------

function revealNarrative() {
    if (narrativeRevealed) return;
    narrativeRevealed = true;
    recordEvent('reveal_narrative', {
        skipped_phase1: preNarrativeSubmitted ? 0 : 1
    });

    // Record whether they made an initial guess or not
    if (!preNarrativeSubmitted) {
        fetch('/api/solve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                puzzle_id: PUZZLE.puzzle_id,
                session_id: SESSION_ID,
                phase: 1,
                saw_narrative: 0,
                skipped_phase1: 1,
                submitted_grids: null,
                time_spent_ms: Date.now() - startTime,
                active_variant: null
            })
        });
    }

    document.getElementById('narrative-text').classList.add('visible');
    document.getElementById('btn-reveal').style.display = 'none';

    // Show variant tabs if present
    const vtabs = document.getElementById('variant-tabs');
    if (vtabs) vtabs.classList.add('visible');

    // Reveal descriptive labels
    document.querySelectorAll('.slot-label[data-full-label]').forEach(el => {
        el.textContent = el.dataset.fullLabel;
    });

    const p1continue = document.getElementById('phase1-continue');
    if (p1continue) p1continue.style.display = 'none';

    document.getElementById('feedback').style.display = 'none';
    answerRevealed = false;
    lastFeedback = null;
    startTime = Date.now();
}

// --- Submit + feedback -----------------------------------------------------

async function submitAnswer() {
    const submitted = {};
    const expected = PUZZLE.answer_grids;
    const timeSpent = Date.now() - startTime;

    for (const posStr of Object.keys(answerGrids)) {
        submitted[posStr] = answerGrids[posStr].toArray();
    }

    // Calculate overall accuracy across all masked grids
    let totalCells = 0, matchingCells = 0;
    let allCorrect = true;

    for (const posStr of Object.keys(expected)) {
        const sub = submitted[posStr] || [];
        const exp = expected[posStr];
        if (!exp) continue;

        const maxRows = Math.max(sub.length, exp.length);
        for (let r = 0; r < maxRows; r++) {
            const sr = sub[r] || [];
            const er = exp[r] || [];
            const maxCols = Math.max(sr.length, er.length);
            for (let c = 0; c < maxCols; c++) {
                totalCells++;
                const sv = sr[c] !== undefined ? sr[c] : -1;
                const ev = er[c] !== undefined ? er[c] : -2;
                if (sv === ev) matchingCells++;
                else allCorrect = false;
            }
        }
    }
    const cellAccuracy = totalCells > 0 ? matchingCells / totalCells : 0;

    const phase = narrativeRevealed ? 2 : 1;
    if (phase === 1) preNarrativeSubmitted = true;

    recordEvent('submit', {
        phase,
        correct: allCorrect,
        cell_accuracy: cellAccuracy,
        time_spent_ms: timeSpent,
        saw_narrative: narrativeRevealed,
        active_variant: narrativeRevealed ? activeVariant : null
    });
    flushEvents();  // ensure the submit is durable before navigating away

    await fetch('/api/solve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            puzzle_id: PUZZLE.puzzle_id,
            session_id: SESSION_ID,
            phase: phase,
            saw_narrative: narrativeRevealed ? 1 : 0,
            skipped_phase1: 0,
            submitted_grids: submitted,
            correct: allCorrect ? 1 : 0,
            cell_accuracy: cellAccuracy,
            time_spent_ms: timeSpent,
            active_variant: narrativeRevealed ? activeVariant : null
        })
    });

    showFeedback(submitted, expected, allCorrect, cellAccuracy);
}

function showFeedback(submitted, expected, correct, cellAccuracy) {
    lastFeedback = { submitted, expected, correct, cellAccuracy };

    const feedback = document.getElementById('feedback');
    feedback.style.display = 'block';

    const banner = document.getElementById('feedback-banner');
    if (correct) {
        banner.className = 'alert alert-success';
        banner.textContent = 'Correct! Perfect match.';
    } else {
        banner.className = 'alert alert-warning';
        banner.textContent = `Incorrect. Cell accuracy: ${(cellAccuracy * 100).toFixed(1)}%`;
    }

    const actions = document.getElementById('feedback-actions');
    const details = document.getElementById('feedback-details');
    const tryAgainHint = document.getElementById('try-again-hint');
    const phase1cont = document.getElementById('phase1-continue');

    if (correct) {
        // Auto-show answer when correct (nothing to hide)
        actions.style.display = 'none';
        renderFeedbackDetails(submitted, expected);
        details.style.display = '';
        answerRevealed = true;
        phase1cont.style.display = 'none';
    } else if (answerRevealed) {
        // User already revealed earlier; keep showing the diff
        actions.style.display = 'none';
        renderFeedbackDetails(submitted, expected);
        details.style.display = '';
        phase1cont.style.display = (!narrativeRevealed) ? 'block' : 'none';
    } else {
        // Two-step: offer to reveal answer
        actions.style.display = '';
        tryAgainHint.style.display = '';
        details.style.display = 'none';
        phase1cont.style.display = (!narrativeRevealed) ? 'block' : 'none';
    }

    const votePrompt = document.getElementById('vote-prompt');
    if (votePrompt) votePrompt.style.display = '';

    // Auto-scroll to feedback
    requestAnimationFrame(() => {
        feedback.scrollIntoView({behavior: 'smooth', block: 'start'});
    });
}

function revealAnswer() {
    if (!lastFeedback || answerRevealed) return;
    answerRevealed = true;
    recordEvent('reveal_answer', {
        correct: lastFeedback.correct,
        cell_accuracy: lastFeedback.cellAccuracy
    });

    renderFeedbackDetails(lastFeedback.submitted, lastFeedback.expected);
    document.getElementById('feedback-details').style.display = '';
    document.getElementById('feedback-actions').style.display = 'none';
}

function renderFeedbackDetails(submitted, expected) {
    const rowEl = document.getElementById('feedback-details');
    rowEl.innerHTML = '';

    const positions = Object.keys(expected);
    for (const posStr of positions) {
        const sub = submitted[posStr] || [];
        const exp = expected[posStr];

        const colWidth = positions.length === 1 ? 'col-md-4' : 'col-md-6 col-lg-4';

        if (positions.length > 1) {
            const header = document.createElement('div');
            header.className = 'col-12 mt-2';
            header.innerHTML = `<h6>Grid ${parseInt(posStr) + 1}</h6>`;
            rowEl.appendChild(header);
        }

        const subCol = document.createElement('div');
        subCol.className = colWidth + ' text-center';
        subCol.innerHTML = '<small class="text-muted">Your Answer</small>';
        const subDiv = document.createElement('div');
        if (sub.length > 0) {
            const subGrid = new Grid(sub.length, sub[0].length, sub);
            renderGrid(subDiv, subGrid, { cellSize: 24 });
        }
        subCol.appendChild(subDiv);
        rowEl.appendChild(subCol);

        const diffCol = document.createElement('div');
        diffCol.className = colWidth + ' text-center';
        diffCol.innerHTML = '<small class="text-muted">Diff</small>';
        const diffDiv = document.createElement('div');
        if (sub.length > 0) {
            renderFeedbackGrid(diffDiv, sub, exp, 24);
        }
        diffCol.appendChild(diffDiv);
        rowEl.appendChild(diffCol);

        const expCol = document.createElement('div');
        expCol.className = colWidth + ' text-center';
        expCol.innerHTML = '<small class="text-muted">Expected</small>';
        const expDiv = document.createElement('div');
        const expGrid = new Grid(exp.length, exp[0].length, exp);
        renderGrid(expDiv, expGrid, { cellSize: 24 });
        expCol.appendChild(expDiv);
        rowEl.appendChild(expCol);
    }
}

function clearAnswer() {
    recordEvent('clear_all');
    for (const posStr of Object.keys(answerGrids)) {
        const item = PUZZLE.sequence[parseInt(posStr)];
        answerGrids[posStr] = new Grid(item.rows, item.cols);
        renderAnswerGrid(posStr);
    }
}

function switchVariant(idx) {
    const variants = PUZZLE.variants;
    if (!variants || !variants[idx]) return;
    document.getElementById('narrative-text').textContent = variants[idx].narrative;
    document.querySelectorAll('#variant-tabs .vtab').forEach((btn, i) => {
        btn.classList.toggle('active', i === idx);
    });
    activeVariant = variants[idx].variant;
    recordEvent('variant_switch', {variant: activeVariant, idx});
    fetch('/api/variant-view', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session_id: SESSION_ID,
            puzzle_id: PUZZLE.puzzle_id,
            variant: activeVariant
        })
    });
}
