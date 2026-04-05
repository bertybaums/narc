/**
 * NARC puzzle solving interface — supports single or multi-mask puzzles.
 *
 * Flow:
 * - Solver sees grids + answer editor(s) + "Reveal Narrative" button
 * - They can submit a guess before revealing (recorded as pre-narrative attempt)
 * - They can reveal the narrative at any time (recorded whether they guessed first or not)
 * - After revealing, they can submit again (recorded as post-narrative attempt)
 */

let selectedColor = 0;
let answerGrids = {};      // keyed by position string: {"2": Grid, "3": Grid}
let narrativeRevealed = false;
let preNarrativeSubmitted = false;

document.addEventListener('DOMContentLoaded', () => {
    renderPuzzleSequence();
    setupAnswerEditors();
});

function renderPuzzleSequence() {
    const container = document.getElementById('puzzle-sequence');
    container.innerHTML = '';

    const seq = PUZZLE.sequence;
    for (let i = 0; i < seq.length; i++) {
        if (i > 0) {
            const arrow = document.createElement('div');
            arrow.className = 'sequence-arrow';
            arrow.textContent = '\u2192';
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
}

function setupAnswerEditors() {
    buildColorPicker(document.getElementById('answer-picker'), c => { selectedColor = c; });

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
            gridObj.grid[r][c] = selectedColor;
            const cell = canvas.querySelectorAll('.grid_row')[r].querySelectorAll('.cell')[c];
            setCellColor(cell, selectedColor);
        }
    });
}

function revealNarrative() {
    if (narrativeRevealed) return;
    narrativeRevealed = true;

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
                time_spent_ms: Date.now() - startTime
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
    startTime = Date.now();
}

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
            time_spent_ms: timeSpent
        })
    });

    showFeedback(submitted, expected, allCorrect, cellAccuracy);
}

function showFeedback(submitted, expected, correct, cellAccuracy) {
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

    // Build feedback grids for each masked position
    const positions = Object.keys(expected);
    const rowEl = feedback.querySelector('.row');
    rowEl.innerHTML = '';

    for (const posStr of positions) {
        const sub = submitted[posStr] || [];
        const exp = expected[posStr];

        const colWidth = positions.length === 1 ? 'col-md-4' : 'col-md-6 col-lg-4';

        // Header for multi-mask
        if (positions.length > 1) {
            const header = document.createElement('div');
            header.className = 'col-12 mt-2';
            header.innerHTML = `<h6>Grid ${parseInt(posStr) + 1}</h6>`;
            rowEl.appendChild(header);
        }

        // Submitted
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

        // Diff
        const diffCol = document.createElement('div');
        diffCol.className = colWidth + ' text-center';
        diffCol.innerHTML = '<small class="text-muted">Diff</small>';
        const diffDiv = document.createElement('div');
        if (sub.length > 0) {
            renderFeedbackGrid(diffDiv, sub, exp, 24);
        }
        diffCol.appendChild(diffDiv);
        rowEl.appendChild(diffCol);

        // Expected
        const expCol = document.createElement('div');
        expCol.className = colWidth + ' text-center';
        expCol.innerHTML = '<small class="text-muted">Expected</small>';
        const expDiv = document.createElement('div');
        const expGrid = new Grid(exp.length, exp[0].length, exp);
        renderGrid(expDiv, expGrid, { cellSize: 24 });
        expCol.appendChild(expDiv);
        rowEl.appendChild(expCol);
    }

    if (!narrativeRevealed && !correct) {
        document.getElementById('phase1-continue').style.display = 'block';
    }
}

function clearAnswer() {
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
}
