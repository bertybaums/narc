/**
 * NARC Grid utilities — rendering and editing.
 * Adapted from LARC collection grids.js (no jQuery dependency).
 */

const COLOR_NAMES = [
    'black', 'blue', 'red', 'green', 'yellow',
    'grey', 'magenta', 'orange', 'azure', 'maroon'
];

class Grid {
    constructor(height, width, values) {
        this.height = height;
        this.width = width;
        this.grid = [];
        for (let i = 0; i < height; i++) {
            this.grid[i] = [];
            for (let j = 0; j < width; j++) {
                if (values && values[i] && values[i][j] !== undefined) {
                    this.grid[i][j] = values[i][j];
                } else {
                    this.grid[i][j] = 0;
                }
            }
        }
    }

    toArray() {
        return this.grid.map(row => [...row]);
    }

    resize(newHeight, newWidth) {
        const old = this.grid;
        this.height = newHeight;
        this.width = newWidth;
        this.grid = [];
        for (let i = 0; i < newHeight; i++) {
            this.grid[i] = [];
            for (let j = 0; j < newWidth; j++) {
                this.grid[i][j] = (old[i] && old[i][j] !== undefined) ? old[i][j] : 0;
            }
        }
    }
}

function setCellColor(cell, symbol) {
    cell.dataset.symbol = symbol;
    for (let i = 0; i < 10; i++) {
        cell.classList.remove('symbol_' + i);
    }
    cell.classList.add('symbol_' + symbol);
}

function renderGrid(container, dataGrid, opts = {}) {
    const { editable = false, cellSize = null, onCellClick = null } = opts;
    container.innerHTML = '';

    if (editable) container.classList.add('grid-editor');

    for (let i = 0; i < dataGrid.height; i++) {
        const row = document.createElement('div');
        row.className = 'grid_row';
        for (let j = 0; j < dataGrid.width; j++) {
            const cell = document.createElement('div');
            cell.className = 'cell';
            cell.dataset.row = i;
            cell.dataset.col = j;
            setCellColor(cell, dataGrid.grid[i][j]);
            if (cellSize) {
                cell.style.width = cellSize + 'px';
                cell.style.height = cellSize + 'px';
            }
            if (editable || onCellClick) {
                cell.addEventListener('mousedown', (e) => {
                    if (onCellClick) onCellClick(i, j, e);
                });
                cell.addEventListener('mouseover', (e) => {
                    if (e.buttons === 1 && onCellClick) onCellClick(i, j, e);
                });
            }
            row.appendChild(cell);
        }
        container.appendChild(row);
    }

    fitCells(container, dataGrid.height, dataGrid.width, cellSize);
}

function fitCells(container, height, width, fixedSize) {
    if (fixedSize) return;
    const maxDim = Math.max(height, width);
    const containerWidth = container.offsetWidth || 300;
    const size = Math.max(12, Math.min(40, Math.floor(containerWidth / maxDim) - 2));
    container.querySelectorAll('.cell').forEach(cell => {
        cell.style.width = size + 'px';
        cell.style.height = size + 'px';
    });
}

function readGridFromContainer(container, height, width) {
    const grid = [];
    const rows = container.querySelectorAll('.grid_row');
    rows.forEach((row, i) => {
        grid[i] = [];
        row.querySelectorAll('.cell').forEach((cell, j) => {
            grid[i][j] = parseInt(cell.dataset.symbol) || 0;
        });
    });
    return grid;
}

function renderMaskedPlaceholder(container, rows, cols, cellSize = null) {
    container.innerHTML = '';
    container.classList.add('masked-placeholder');
    const size = cellSize || 30;
    container.style.width = (cols * size) + 'px';
    container.style.height = (rows * size) + 'px';
    container.textContent = '?';
}

function renderFeedbackGrid(container, submitted, expected, cellSize = null) {
    container.innerHTML = '';
    container.classList.add('feedback-grid');

    const maxRows = Math.max(submitted.length, expected.length);
    for (let i = 0; i < maxRows; i++) {
        const row = document.createElement('div');
        row.className = 'grid_row';
        const maxCols = Math.max(
            (submitted[i] || []).length,
            (expected[i] || []).length
        );
        for (let j = 0; j < maxCols; j++) {
            const cell = document.createElement('div');
            cell.className = 'cell';
            const subVal = (submitted[i] && submitted[i][j] !== undefined) ? submitted[i][j] : -1;
            const expVal = (expected[i] && expected[i][j] !== undefined) ? expected[i][j] : -2;
            setCellColor(cell, subVal >= 0 ? subVal : 0);
            cell.classList.add(subVal === expVal ? 'correct' : 'wrong');
            if (cellSize) {
                cell.style.width = cellSize + 'px';
                cell.style.height = cellSize + 'px';
            }
            row.appendChild(cell);
        }
        container.appendChild(row);
    }
}

function buildColorPicker(container, onSelect) {
    container.innerHTML = '';
    container.classList.add('symbol-picker');
    for (let i = 0; i < 10; i++) {
        const swatch = document.createElement('div');
        swatch.className = 'symbol_preview symbol_' + i;
        swatch.dataset.symbol = i;
        swatch.title = COLOR_NAMES[i] + ' (' + i + ')';
        if (i === 0) swatch.classList.add('selected');
        swatch.addEventListener('click', () => {
            container.querySelectorAll('.symbol_preview').forEach(s => s.classList.remove('selected'));
            swatch.classList.add('selected');
            if (onSelect) onSelect(i);
        });
        container.appendChild(swatch);
    }
}

function getSelectedColor(pickerContainer) {
    const selected = pickerContainer.querySelector('.symbol_preview.selected');
    return selected ? parseInt(selected.dataset.symbol) : 0;
}
