-- NARC Schema: Narrative Augmented Reasoning Challenges

CREATE TABLE IF NOT EXISTS puzzles (
    puzzle_id       TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    narrative       TEXT NOT NULL,
    sequence_json   TEXT NOT NULL,
    masked_positions TEXT NOT NULL,       -- JSON array of ints, e.g. [2] or [2,3]
    answer_grids    TEXT NOT NULL,        -- JSON dict: {"2": [[...]], "3": [[...]]}
    creator         TEXT DEFAULT 'human',
    difficulty      TEXT,
    human_difficulty INTEGER,             -- 1-5 predicted difficulty for humans
    ai_difficulty    INTEGER,             -- 1-5 predicted difficulty for AI
    tags            TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS narrative_variants (
    variant_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    variant        TEXT NOT NULL DEFAULT 'original',
    source_domain  TEXT,
    narrative      TEXT NOT NULL,
    generator      TEXT NOT NULL DEFAULT 'human',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(puzzle_id, variant)
);

CREATE TABLE IF NOT EXISTS trials (
    trial_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    variant_id     INTEGER REFERENCES narrative_variants(variant_id),
    model_name     TEXT NOT NULL,
    condition      TEXT NOT NULL,
    repeat_num     INTEGER DEFAULT 1,
    prompt_text    TEXT NOT NULL,
    raw_response   TEXT,
    response_text  TEXT,
    response_at    TEXT,
    latency_ms     INTEGER,
    error          TEXT,
    predicted_grids TEXT,                -- JSON dict: {"2": [[...]]} (multi-mask)
    reasoning      TEXT,
    correct        INTEGER,
    cell_accuracy  REAL,
    UNIQUE(puzzle_id, variant_id, model_name, condition, repeat_num)
);

CREATE TABLE IF NOT EXISTS classifications (
    puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    variant_id     INTEGER REFERENCES narrative_variants(variant_id),
    model_name     TEXT NOT NULL,
    grids_only     INTEGER,
    narrative_only INTEGER,
    both           INTEGER,
    has_narc       INTEGER,
    PRIMARY KEY (puzzle_id, model_name)
);

CREATE TABLE IF NOT EXISTS solve_attempts (
    attempt_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    session_id     TEXT NOT NULL,
    solver_name    TEXT,
    phase          INTEGER NOT NULL,
    saw_narrative  INTEGER DEFAULT 0,
    submitted_grids TEXT,                -- JSON dict: {"2": [[...]]}
    correct        INTEGER,
    cell_accuracy  REAL,
    time_spent_ms  INTEGER,
    skipped_phase1 INTEGER DEFAULT 0,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
