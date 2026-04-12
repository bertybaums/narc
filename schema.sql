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
    parent_puzzle_id TEXT,                -- grid variant parent (e.g. narc_003)
    stance_group    TEXT,                 -- stance experiment group name (e.g. The Chase)
    stance          TEXT,                 -- intentional, design, or physical
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
    PRIMARY KEY (puzzle_id, variant_id, model_name)
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
    active_variant TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS variant_views (
    view_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    puzzle_id   TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    variant     TEXT NOT NULL,
    viewed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_variant_views_puzzle ON variant_views(puzzle_id);
CREATE INDEX IF NOT EXISTS idx_variant_views_session ON variant_views(session_id);

CREATE TABLE IF NOT EXISTS oddoneout_trials (
    trial_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id      TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    distractor_id  TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    model_name     TEXT NOT NULL,
    condition      TEXT NOT NULL,             -- 'grids_only' or 'grids_and_narrative'
    repeat_num     INTEGER DEFAULT 1,
    prompt_text    TEXT,
    raw_response   TEXT,
    response_text  TEXT,
    response_at    TEXT,
    latency_ms     INTEGER,
    error          TEXT,
    predicted_odd  INTEGER,                   -- 0-3 index of predicted odd-one-out
    correct_odd    INTEGER NOT NULL,          -- 0-3 index of actual distractor
    correct        INTEGER,                   -- 1 if predicted == correct
    reasoning      TEXT,
    UNIQUE(puzzle_id, distractor_id, model_name, condition, repeat_num)
);

-- Voting

CREATE TABLE IF NOT EXISTS votes (
    vote_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id   TEXT NOT NULL REFERENCES puzzles(puzzle_id),
    voter_id    TEXT NOT NULL,
    value       INTEGER NOT NULL,  -- +1 or -1
    ip_address  TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(puzzle_id, voter_id)
);

CREATE INDEX IF NOT EXISTS idx_votes_puzzle ON votes(puzzle_id);
CREATE INDEX IF NOT EXISTS idx_votes_voter ON votes(voter_id);

-- Auth and submission review

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('owner', 'reviewer')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS submissions (
    submission_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_type  TEXT NOT NULL CHECK(submission_type IN ('new_puzzle', 'revision', 'variant')),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK(status IN ('pending', 'approved', 'rejected', 'reversed')),
    payload_json     TEXT NOT NULL,
    target_puzzle_id TEXT,
    submitter_name   TEXT,
    submitter_email  TEXT,
    reviewer_id      INTEGER REFERENCES users(user_id),
    review_note      TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at      TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
    log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER REFERENCES users(user_id),
    action        TEXT NOT NULL,
    target_type   TEXT,          -- 'puzzle', 'variant', 'submission', 'user'
    target_id     TEXT,          -- puzzle_id, submission_id, etc.
    detail        TEXT,          -- human-readable summary
    snapshot_json TEXT,          -- state before change (for reversals)
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
