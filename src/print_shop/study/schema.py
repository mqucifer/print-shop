"""The tables a settings study keeps in the ledger (mqucifer/print-shop#339, `study.lessons`):
here alone, with nothing imported, so the ledger can create them without importing the study."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings_studies (
    -- A part's settings studied for strength, filament and time (mqucifer/print-shop#339):
    -- nothing printed, so no row of `prints`.
    id TEXT PRIMARY KEY,
    part TEXT NOT NULL,                       -- its name in its assembly file
    stl_name TEXT NOT NULL,
    stl_sha256 TEXT NOT NULL,
    features TEXT NOT NULL,                   -- JSON [Feature], as it prints
    keys TEXT NOT NULL,                       -- JSON [key]: its traits and joints
    filament TEXT NOT NULL,
    cases TEXT NOT NULL,                      -- JSON: each load case
    model TEXT NOT NULL,
    ended TEXT NOT NULL,
    readings TEXT NOT NULL,                   -- JSON: each candidate read, and its figures
    recommended TEXT,                         -- a candidate's name, or NULL for none
    reason TEXT,
    calls TEXT NOT NULL,                      -- JSON: every tool call
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_lessons (
    -- What a study found, for a later part with every one of its keys.
    id INTEGER PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES settings_studies(id),
    drawn_from TEXT NOT NULL,                 -- finding (the model's) or recommendation (code's)
    text TEXT NOT NULL,
    keys TEXT NOT NULL,                       -- JSON [key]
    evidence TEXT NOT NULL,                   -- JSON: the candidates that show it, and figures
    written_by TEXT NOT NULL,
    written_at TEXT NOT NULL,
    status TEXT NOT NULL,                     -- candidate; accepted, edited or rejected
    status_by TEXT,
    status_at TEXT
);
CREATE TABLE IF NOT EXISTS study_lesson_steps (
    id INTEGER PRIMARY KEY,
    lesson INTEGER NOT NULL REFERENCES study_lessons(id),
    status TEXT NOT NULL,
    by TEXT NOT NULL,
    why TEXT,
    was TEXT,
    at TEXT NOT NULL
);
"""
