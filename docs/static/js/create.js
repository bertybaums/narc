/**
 * NARC puzzle creation interface — supports multi-mask.
 */

let selectedColor = 0;
let slotGrids = [];        // Array of Grid objects
let maskedSlots = new Set(); // Which slots are masked
let numSlots = 4;

document.addEventListener('DOMContentLoaded', () => {
    buildColorPicker(document.getElementById('color-picker'), c => { selectedColor = c; });

    const slider = document.getElementById('seq-length');
    const display = document.getElementById('seq-length-display');
    slider.addEventListener('input', () => {
        display.textContent = slider.value;
        numSlots = parseInt(slider.value);
        buildSlots();
    });

    document.getElementById('narrative').addEventListener('input', (e) => {
        document.getElementById('char-count').textContent = e.target.value.length;
    });

    if (EDIT_DATA) {
        loadFromData(EDIT_DATA);
    } else {
        numSlots = parseInt(slider.value);
        buildSlots();
    }
});

function loadFromData(data) {
    document.getElementById('puzzle-id').value = data.puzzle_id || '';
    document.getElementById('puzzle-title').value = data.title || '';
    document.getElementById('narrative').value = data.narrative || '';
    document.getElementById('char-count').textContent = (data.narrative || '').length;

    const seq = data.sequence;
    numSlots = seq.length;
    document.getElementById('seq-length').value = numSlots;
    document.getElementById('seq-length-display').textContent = numSlots;

    maskedSlots = new Set(data.masked_positions || []);
    slotGrids = [];
    buildSlots();

    const answerGrids = data.answer_grids || {};
    for (let i = 0; i < seq.length; i++) {
        const item = seq[i];
        const dimInput = document.querySelector(`#slot-${i} .dim-input`);
        const labelInput = document.querySelector(`#slot-${i} .label-input`);

        dimInput.value = item.cols + 'x' + item.rows;
        if (item.label) labelInput.value = item.label;

        if (maskedSlots.has(i)) {
            const ag = answerGrids[String(i)];
            if (ag) slotGrids[i] = new Grid(item.rows, item.cols, ag);
        } else if (item.grid) {
            slotGrids[i] = new Grid(item.rows, item.cols, item.grid);
        }
        resizeSlotGrid(i);
    }
}

function buildSlots() {
    const container = document.getElementById('grid-slots');
    container.innerHTML = '';
    slotGrids = [];

    for (let i = 0; i < numSlots; i++) {
        if (i > 0) {
            const arrow = document.createElement('div');
            arrow.className = 'sequence-arrow';
            arrow.textContent = '\u2192';
            container.appendChild(arrow);
        }

        const isMasked = maskedSlots.has(i);
        const slot = document.createElement('div');
        slot.className = 'grid-slot' + (isMasked ? ' masked' : '');
        slot.id = 'slot-' + i;

        slot.innerHTML = `
            <div class="slot-label">Grid ${i + 1}</div>
            <div class="mb-1">
                <input type="text" class="dim-input" value="5x5" placeholder="WxH"
                       onchange="resizeSlotGrid(${i})">
                <input type="text" class="label-input dim-input" value="" placeholder="label"
                       style="width:80px; margin-left:4px;">
            </div>
            <div class="grid-wrapper">
                <div class="grid-canvas" id="canvas-${i}"></div>
            </div>
            <div class="mt-1">
                <button class="btn btn-sm ${isMasked ? 'btn-warning' : 'btn-outline-secondary'}"
                        onclick="toggleMask(${i})" id="mask-btn-${i}">
                    ${isMasked ? 'Masked' : 'Mask'}
                </button>
            </div>
        `;
        container.appendChild(slot);

        slotGrids[i] = new Grid(5, 5);
        renderSlotGrid(i);
    }
}

function renderSlotGrid(idx) {
    const canvas = document.getElementById('canvas-' + idx);
    renderGrid(canvas, slotGrids[idx], {
        editable: true,
        cellSize: 28,
        onCellClick: (r, c) => {
            slotGrids[idx].grid[r][c] = selectedColor;
            const cell = canvas.querySelectorAll('.grid_row')[r].querySelectorAll('.cell')[c];
            setCellColor(cell, selectedColor);
        }
    });
}

function resizeSlotGrid(idx) {
    const dimInput = document.querySelector(`#slot-${idx} .dim-input`);
    const parts = dimInput.value.split('x');
    if (parts.length !== 2) return;
    const w = parseInt(parts[0]);
    const h = parseInt(parts[1]);
    if (isNaN(w) || isNaN(h) || w < 1 || h < 1 || w > 30 || h > 30) return;
    slotGrids[idx].resize(h, w);
    renderSlotGrid(idx);
}

function toggleMask(idx) {
    if (maskedSlots.has(idx)) {
        maskedSlots.delete(idx);
    } else {
        maskedSlots.add(idx);
    }
    // Update just the button and slot class without rebuilding everything
    const slot = document.getElementById('slot-' + idx);
    const btn = document.getElementById('mask-btn-' + idx);
    if (maskedSlots.has(idx)) {
        slot.classList.add('masked');
        btn.className = 'btn btn-sm btn-warning';
        btn.textContent = 'Masked';
    } else {
        slot.classList.remove('masked');
        btn.className = 'btn btn-sm btn-outline-secondary';
        btn.textContent = 'Mask';
    }
}

function collectPuzzleData() {
    const puzzleId = document.getElementById('puzzle-id').value.trim();
    const title = document.getElementById('puzzle-title').value.trim();
    const narrative = document.getElementById('narrative').value.trim();
    const difficulty = document.getElementById('puzzle-difficulty').value;

    if (!puzzleId || !title || !narrative || maskedSlots.size === 0) {
        return null;
    }

    const sequence = [];
    const maskedPositions = [...maskedSlots].sort((a, b) => a - b);
    const answerGrids = {};

    for (let i = 0; i < numSlots; i++) {
        const dimInput = document.querySelector(`#slot-${i} .dim-input`);
        const labelInput = document.querySelector(`#slot-${i} .label-input`);
        const parts = dimInput.value.split('x');
        const cols = parseInt(parts[0]) || 5;
        const rows = parseInt(parts[1]) || 5;
        const label = labelInput ? labelInput.value.trim() : '';

        const gridData = slotGrids[i].toArray();

        if (maskedSlots.has(i)) {
            answerGrids[String(i)] = gridData;
            sequence.push({
                position: i, grid: null, rows: rows, cols: cols,
                label: label, masked: true
            });
        } else {
            sequence.push({
                position: i, grid: gridData, rows: rows, cols: cols,
                label: label
            });
        }
    }

    return {
        puzzle_id: puzzleId,
        title: title,
        narrative: narrative,
        sequence: sequence,
        masked_positions: maskedPositions,
        answer_grids: answerGrids,
        metadata: { creator: 'human', difficulty: difficulty }
    };
}

async function savePuzzle() {
    const data = collectPuzzleData();
    if (!data) {
        showStatus('Please fill in puzzle ID, title, narrative, and mask at least one grid.', 'danger');
        return;
    }

    try {
        const resp = await fetch('/api/puzzles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await resp.json();
        if (resp.ok) {
            showStatus('Puzzle saved: ' + data.puzzle_id, 'success');
        } else {
            showStatus('Error: ' + (result.error || 'Unknown error'), 'danger');
        }
    } catch (e) {
        showStatus('Error: ' + e.message, 'danger');
    }
}

function showStatus(msg, type) {
    const el = document.getElementById('save-status');
    el.className = 'alert alert-' + type;
    el.textContent = msg;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
}

function togglePreview() {
    const area = document.getElementById('preview-area');
    if (area.style.display === 'none') {
        renderPreview();
        area.style.display = 'block';
    } else {
        area.style.display = 'none';
    }
}

function renderPreview() {
    const container = document.getElementById('preview-sequence');
    container.innerHTML = '';

    for (let i = 0; i < numSlots; i++) {
        if (i > 0) {
            const arrow = document.createElement('div');
            arrow.className = 'sequence-arrow';
            arrow.textContent = '\u2192';
            container.appendChild(arrow);
        }

        const slot = document.createElement('div');
        slot.className = 'grid-slot' + (maskedSlots.has(i) ? ' masked' : '');

        const dimInput = document.querySelector(`#slot-${i} .dim-input`);
        const parts = dimInput.value.split('x');
        const cols = parseInt(parts[0]) || 5;
        const rows = parseInt(parts[1]) || 5;

        const label = document.createElement('div');
        label.className = 'slot-label';
        label.textContent = 'Grid ' + (i + 1);
        slot.appendChild(label);

        const wrapper = document.createElement('div');
        wrapper.className = 'grid-wrapper';
        const canvas = document.createElement('div');

        if (maskedSlots.has(i)) {
            renderMaskedPlaceholder(canvas, rows, cols, 28);
        } else {
            renderGrid(canvas, slotGrids[i], { cellSize: 28 });
        }
        wrapper.appendChild(canvas);
        slot.appendChild(wrapper);
        container.appendChild(slot);
    }

    document.getElementById('preview-narrative').textContent =
        document.getElementById('narrative').value;
}

function togglePreviewNarrative() {
    document.getElementById('preview-narrative').classList.toggle('visible');
}

function clearAll() {
    if (!confirm('Clear all grids and narrative?')) return;
    maskedSlots.clear();
    document.getElementById('narrative').value = '';
    document.getElementById('char-count').textContent = '0';
    buildSlots();
}
