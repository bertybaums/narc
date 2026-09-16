/**
 * Narrate: write a narrative, test it against the model, see the result, repeat.
 *
 * Server is the source of truth for everything recorded (attempts, reveals,
 * whether the reference was seen before a submission). The browser only keeps
 * an unsent draft in localStorage as a convenience.
 */

const CELL = 28;
const SMALL_CELL = 12;
const POLL_MS = 2500;

let attempts = NARRATE.attempts.slice();
let draftStartedAt = Date.now();
let pollTimer = null;
let elapsedTimer = null;
let revealConfirmed = false;

const $ = id => document.getElementById(id);
const draftKey = 'narc_narrate_draft_' + NARRATE.puzzle_id;

document.addEventListener('DOMContentLoaded', () => {
    renderSequences();
    setupReveal();
    setupEditor();
    renderHistory();

    const active = attempts.find(a => a.status === 'queued' || a.status === 'running');
    if (active) {
        showPending(active);
        poll(active.attempt_id);
    } else if (attempts.length) {
        showResult(attempts[attempts.length - 1], false);
    }
});

// --- Grids ------------------------------------------------------------------

function isHidden(pos) {
    return NARRATE.masked_positions.includes(pos);
}

function renderSequences() {
    const truth = $('truth-sequence');
    const model = $('model-sequence');
    NARRATE.sequence.forEach((item, i) => {
        if (i > 0) {
            truth.appendChild(arrow());
            model.appendChild(arrow());
        }
        truth.appendChild(slot(item, CELL, false));
        model.appendChild(slot(item, SMALL_CELL, true));
    });
    $('hidden-list').textContent = NARRATE.masked_positions.map(p => 'grid ' + (p + 1)).join(' and ');

    const update = () => {
        const canScroll = truth.scrollWidth > truth.clientWidth + 1;
        $('sequence-wrapper').classList.toggle('has-overflow-left', canScroll && truth.scrollLeft > 1);
        $('sequence-wrapper').classList.toggle('has-overflow-right',
            canScroll && truth.scrollLeft + truth.clientWidth < truth.scrollWidth - 1);
    };
    truth.addEventListener('scroll', update);
    window.addEventListener('resize', update);
    requestAnimationFrame(update);
}

function arrow() {
    const a = document.createElement('div');
    a.className = 'sequence-arrow';
    a.textContent = '→';
    return a;
}

function slot(item, cellSize, asModelSeesIt) {
    const hidden = isHidden(item.position);
    const s = document.createElement('div');
    s.className = 'grid-slot' + (hidden ? (asModelSeesIt ? ' masked' : ' hidden-from-model') : '');

    const label = document.createElement('div');
    label.className = 'slot-label';
    label.textContent = String(item.position + 1);
    s.appendChild(label);

    const wrapper = document.createElement('div');
    wrapper.className = 'grid-wrapper';
    const canvas = document.createElement('div');
    if (hidden && asModelSeesIt) {
        renderMaskedPlaceholder(canvas, item.rows, item.cols, cellSize);
        canvas.style.fontSize = '1rem';
    } else {
        renderGrid(canvas, new Grid(item.rows, item.cols, item.grid), {cellSize});
    }
    wrapper.appendChild(canvas);
    s.appendChild(wrapper);

    if (hidden && !asModelSeesIt) {
        const tag = document.createElement('div');
        tag.className = 'narrate-hidden-tag';
        tag.textContent = 'Hidden from the model';
        s.appendChild(tag);
    }
    return s;
}

// --- Reveal -----------------------------------------------------------------

function setupReveal() {
    if (NARRATE.revealed) {
        showReference(NARRATE.reference);
        return;
    }
    const modalEl = $('reveal-modal');
    const modal = new bootstrap.Modal(modalEl);

    $('btn-reveal').addEventListener('click', () => {
        revealConfirmed = false;
        postReveal('opened');
        modal.show();
    });
    $('btn-reveal-confirm').addEventListener('click', async () => {
        const btn = $('btn-reveal-confirm');
        btn.disabled = true;
        try {
            const data = await postReveal('confirmed');
            if (!data || !data.reference) throw new Error('no reference returned');
            revealConfirmed = true;
            NARRATE.revealed = true;
            modal.hide();
            showReference(data.reference);
        } catch (e) {
            btn.disabled = false;
            modalEl.querySelector('.modal-body').insertAdjacentHTML('beforeend',
                '<p class="text-danger small mt-2 mb-0">Something went wrong. Please try again.</p>');
        }
    });
    modalEl.addEventListener('hidden.bs.modal', () => {
        if (!revealConfirmed) postReveal('cancelled');
    });
}

async function postReveal(action) {
    const r = await fetch(`/narrate/api/${encodeURIComponent(NARRATE.puzzle_id)}/reveal`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action})
    });
    if (!r.ok) {
        if (r.status === 401) location.reload();
        throw new Error('HTTP ' + r.status);
    }
    return r.json();
}

function showReference(text) {
    $('reveal-controls').style.display = 'none';
    $('reference-text').textContent = text;
    $('reveal-shown').style.display = '';
}

// --- Writing and submitting --------------------------------------------------

function setupEditor() {
    const ta = $('narrative');
    try {
        const saved = localStorage.getItem(draftKey);
        if (saved) ta.value = saved;
    } catch (e) {}
    if (!ta.value && attempts.length) ta.value = attempts[attempts.length - 1].narrative;

    const count = () => {
        $('char-count').textContent = `${ta.value.length} / ${NARRATE.max_chars}`;
        try { localStorage.setItem(draftKey, ta.value); } catch (e) {}
    };
    ta.addEventListener('input', count);
    count();

    $('btn-submit').addEventListener('click', submit);
    $('btn-again').addEventListener('click', () => {
        $('write-card').scrollIntoView({behavior: 'smooth', block: 'start'});
        ta.focus();
    });
}

async function submit() {
    const ta = $('narrative');
    const narrative = ta.value.trim();
    hideError();
    if (!narrative) {
        showError('Write a narrative first.');
        return;
    }
    setBusy(true);
    let r, data;
    try {
        r = await fetch(`/narrate/api/${encodeURIComponent(NARRATE.puzzle_id)}/attempts`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({narrative, client_elapsed_ms: Date.now() - draftStartedAt})
        });
        data = await r.json().catch(() => ({}));
    } catch (e) {
        setBusy(false);
        showError("Couldn't reach the server. Check your connection and try again.");
        return;
    }
    if (r.status === 401) { location.reload(); return; }
    if (!r.ok) {
        setBusy(false);
        showError(data.error || `Something went wrong (HTTP ${r.status}).`);
        return;
    }
    attempts.push(data);
    renderHistory();
    showPending(data);
    poll(data.attempt_id);
}

function setBusy(busy) {
    $('btn-submit').disabled = busy;
    $('btn-submit').textContent = busy ? 'Testing…' : 'Test with the model';
}

function showError(msg) {
    $('submit-error').textContent = msg;
    $('submit-error').style.display = '';
}

function hideError() {
    $('submit-error').style.display = 'none';
}

// --- Waiting for the model ---------------------------------------------------

function showPending(attempt) {
    setBusy(true);
    $('result-card').style.display = '';
    $('result-done').style.display = 'none';
    $('result-pending').style.display = '';
    updatePendingHeadline(attempt);
    $('result-card').scrollIntoView({behavior: 'smooth', block: 'nearest'});

    const started = attempt.created_at ? Date.parse(attempt.created_at) : Date.now();
    clearInterval(elapsedTimer);
    const tick = () => {
        const s = Math.max(0, Math.floor((Date.now() - started) / 1000));
        $('pending-elapsed').textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    };
    tick();
    elapsedTimer = setInterval(tick, 1000);
}

function updatePendingHeadline(attempt) {
    $('pending-headline').textContent = attempt.status === 'running'
        ? 'The model is working on the puzzle…'
        : 'Waiting for the model to be free…';
}

function poll(attemptId, delay = POLL_MS) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
        let a;
        try {
            const r = await fetch(`/narrate/api/attempts/${attemptId}`);
            if (r.status === 401) { location.reload(); return; }
            if (!r.ok) throw new Error('HTTP ' + r.status);
            a = await r.json();
        } catch (e) {
            poll(attemptId, Math.min(delay * 2, 15000));
            return;
        }
        replaceAttempt(a);
        if (a.status === 'queued' || a.status === 'running') {
            updatePendingHeadline(a);
            poll(attemptId);
        } else {
            clearInterval(elapsedTimer);
            renderHistory();
            showResult(a, true);
        }
    }, delay);
}

function replaceAttempt(a) {
    const i = attempts.findIndex(x => x.attempt_id === a.attempt_id);
    if (i >= 0) attempts[i] = a;
}

// --- Results -----------------------------------------------------------------

function showResult(a, fresh) {
    setBusy(false);
    $('result-card').style.display = '';
    $('result-pending').style.display = 'none';
    $('result-done').style.display = '';

    const banner = $('result-banner');
    const gridsEl = $('result-grids');
    gridsEl.innerHTML = '';
    const which = NARRATE.masked_positions.map(p => 'grid ' + (p + 1)).join(' and ');
    const label = `Narrative #${a.seq_num}: `;

    if (a.failed) {
        banner.className = 'alert alert-warning mb-3';
        banner.textContent = label + "the test didn't finish (the model was unavailable or the server restarted). " +
            "This one doesn't count against your limit. Please submit again.";
    } else if (a.correct) {
        banner.className = 'alert alert-success mb-3';
        banner.textContent = label + `solved. The model rebuilt ${which} exactly.`;
    } else if (a.unreadable) {
        banner.className = 'alert alert-secondary mb-3';
        banner.textContent = label + "not solved. The model answered, but no grid could be read from its answer.";
    } else {
        const pct = Math.round((a.cell_accuracy || 0) * 100);
        banner.className = 'alert alert-secondary mb-3';
        banner.textContent = label + `not solved. The model's grid matched ${pct}% of the cells.`;
    }

    if (a.predicted_grids && !a.failed) {
        for (const pos of NARRATE.masked_positions) {
            const key = String(pos);
            const expected = NARRATE.answer_grids[key];
            const predicted = a.predicted_grids[key];
            if (predicted) {
                gridsEl.appendChild(resultGrid("Model's answer", c => renderFeedbackGrid(c, predicted, expected, CELL),
                    a.correct ? '' : 'Red outlines mark cells that differ from the hidden grid.'));
            }
            gridsEl.appendChild(resultGrid(`Hidden grid ${pos + 1}`,
                c => renderGrid(c, new Grid(expected.length, expected[0].length, expected), {cellSize: CELL})));
        }
    }
    if (fresh) {
        draftStartedAt = Date.now();
        $('result-card').scrollIntoView({behavior: 'smooth', block: 'nearest'});
    }
}

function resultGrid(title, render, note = '') {
    const box = document.createElement('div');
    const h = document.createElement('div');
    h.className = 'small text-muted mb-1';
    h.textContent = title;
    box.appendChild(h);
    const canvas = document.createElement('div');
    canvas.style.display = 'inline-block';
    render(canvas);
    box.appendChild(canvas);
    if (note) {
        const n = document.createElement('div');
        n.className = 'small text-muted mt-1';
        n.style.maxWidth = '220px';
        n.textContent = note;
        box.appendChild(n);
    }
    return box;
}

// --- History -----------------------------------------------------------------

function renderHistory() {
    const el = $('history');
    $('history-card').style.display = attempts.length ? '' : 'none';
    el.innerHTML = '';
    for (const a of attempts.slice().reverse()) {
        const row = document.createElement('div');
        row.className = 'narrate-attempt';

        const meta = document.createElement('div');
        meta.className = 'd-flex flex-wrap align-items-center gap-2 mb-1';
        const num = document.createElement('strong');
        num.textContent = '#' + a.seq_num;
        meta.appendChild(num);
        meta.appendChild(badge(...statusBadge(a)));
        if (a.saw_reference) meta.appendChild(badge('after reading existing narrative', 'bg-warning text-dark'));
        const when = document.createElement('span');
        when.className = 'small text-muted';
        when.textContent = a.created_at ? new Date(a.created_at).toLocaleString([], {
            month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}) : '';
        meta.appendChild(when);

        const reuse = document.createElement('button');
        reuse.type = 'button';
        reuse.className = 'btn btn-link btn-sm p-0 ms-auto';
        reuse.textContent = 'Edit a copy';
        reuse.addEventListener('click', () => {
            const ta = $('narrative');
            ta.value = a.narrative;
            ta.dispatchEvent(new Event('input'));
            $('write-card').scrollIntoView({behavior: 'smooth', block: 'start'});
            ta.focus();
        });
        meta.appendChild(reuse);

        const text = document.createElement('div');
        text.className = 'narr';
        text.textContent = a.narrative;

        row.appendChild(meta);
        row.appendChild(text);
        el.appendChild(row);
    }
}

function statusBadge(a) {
    if (a.status === 'queued' || a.status === 'running') return ['testing…', 'bg-info text-dark'];
    if (a.failed) return ["didn't run", 'bg-dark border border-warning text-warning'];
    if (a.correct) return ['solved', 'bg-success'];
    if (a.unreadable) return ['not solved · no readable grid', 'bg-secondary'];
    return [`not solved · ${Math.round((a.cell_accuracy || 0) * 100)}% of cells`, 'bg-secondary'];
}

function badge(text, cls) {
    const b = document.createElement('span');
    b.className = 'badge ' + cls;
    b.textContent = text;
    return b;
}
