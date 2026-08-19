from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_name TEXT,
    session_id TEXT NOT NULL,
    task_id TEXT,
    task_key TEXT,
    task_index INTEGER,
    call_index INTEGER NOT NULL,
    call_index_in_task INTEGER,
    timestamp TEXT,
    day TEXT,
    project TEXT,
    cwd TEXT,
    model TEXT,
    reasoning_effort TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    fresh_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    context_window INTEGER,
    cache_ratio REAL,
    context_utilization REAL,
    source_method TEXT,
    UNIQUE(file_path, call_index)
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_name TEXT,
    session_id TEXT NOT NULL,
    task_id TEXT,
    task_key TEXT,
    task_label TEXT,
    task_title TEXT,
    prompt_preview TEXT,
    task_index INTEGER NOT NULL,
    start_timestamp TEXT,
    end_timestamp TEXT,
    day TEXT,
    project TEXT,
    cwd TEXT,
    model TEXT,
    reasoning_effort TEXT,
    status TEXT,
    model_calls INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    cached_input_tokens INTEGER DEFAULT 0,
    fresh_input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    compactions INTEGER DEFAULT 0,
    duration_seconds REAL,
    UNIQUE(file_path, task_index)
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_name TEXT,
    session_id TEXT,
    timestamp TEXT,
    line_index INTEGER,
    top_type TEXT,
    payload_type TEXT,
    task_index INTEGER,
    classification TEXT,
    cumulative_input INTEGER,
    cumulative_cached INTEGER,
    cumulative_output INTEGER,
    cumulative_reasoning INTEGER,
    cumulative_total INTEGER,
    last_input INTEGER,
    last_cached INTEGER,
    last_output INTEGER,
    last_reasoning INTEGER,
    last_total INTEGER,
    context_window INTEGER,
    note TEXT
);

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    session_id TEXT,
    timestamp TEXT,
    task_index INTEGER,
    bucket TEXT,
    used_percent REAL,
    window_minutes INTEGER,
    resets_in_seconds INTEGER
);

CREATE INDEX IF NOT EXISTS idx_calls_day ON model_calls(day);
CREATE INDEX IF NOT EXISTS idx_calls_task ON model_calls(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_day ON tasks(day);
CREATE INDEX IF NOT EXISTS idx_diag_file ON diagnostics(file_path);
"""

def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con

def replace_file(con, file_path, calls, tasks, diagnostics, rate_limits):
    for table in ("model_calls","tasks","diagnostics","rate_limits"):
        con.execute(f"DELETE FROM {table} WHERE file_path=?", (file_path,))

    def ins(table, rows):
        if not rows:
            return
        cols = list(rows[0].keys())
        q = ",".join("?" for _ in cols)
        con.executemany(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({q})",
            [[r.get(c) for c in cols] for r in rows]
        )

    ins("model_calls", calls)
    ins("tasks", tasks)
    ins("diagnostics", diagnostics)
    ins("rate_limits", rate_limits)
    con.commit()

def load(con, table):
    return pd.read_sql_query(f"SELECT * FROM {table}", con)

COPILOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS copilot_calls (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ide TEXT NOT NULL,
 file_path TEXT NOT NULL,
 session_id TEXT NOT NULL,
 correlation_id TEXT,
 message_id TEXT,
 call_index INTEGER NOT NULL,
 timestamp TEXT,
 project TEXT,
 workspace TEXT,
 solution TEXT,
 active_file TEXT,
 task_title TEXT,
 prompt_preview TEXT,
 model TEXT,
 debug_name TEXT,
 input_tokens INTEGER DEFAULT 0,
 cached_input_tokens INTEGER DEFAULT 0,
 fresh_input_tokens INTEGER DEFAULT 0,
 output_tokens INTEGER DEFAULT 0,
 reasoning_tokens INTEGER DEFAULT 0,
 total_tokens INTEGER DEFAULT 0,
 aiu REAL,
 duration_ms INTEGER DEFAULT 0,
 ttft_ms INTEGER DEFAULT 0,
 status TEXT,
 tool_calls_in_turn INTEGER DEFAULT 0,
 tool_names TEXT,
 repo_url TEXT,
 branch TEXT,
 UNIQUE(ide, file_path, session_id, call_index)
);
CREATE TABLE IF NOT EXISTS copilot_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ide TEXT NOT NULL,
 file_path TEXT NOT NULL,
 session_id TEXT NOT NULL,
 correlation_id TEXT,
 message_id TEXT,
 task_title TEXT,
 prompt_preview TEXT,
 start_timestamp TEXT,
 end_timestamp TEXT,
 day TEXT,
 project TEXT,
 workspace TEXT,
 solution TEXT,
 active_file TEXT,
 model TEXT,
 model_calls INTEGER DEFAULT 0,
 tool_calls INTEGER DEFAULT 0,
 input_tokens INTEGER DEFAULT 0,
 cached_input_tokens INTEGER DEFAULT 0,
 fresh_input_tokens INTEGER DEFAULT 0,
 output_tokens INTEGER DEFAULT 0,
 reasoning_tokens INTEGER DEFAULT 0,
 total_tokens INTEGER DEFAULT 0,
 aiu REAL,
 duration_seconds REAL,
 errors INTEGER DEFAULT 0,
 repo_url TEXT,
 branch TEXT,
 UNIQUE(ide, file_path, session_id)
);
CREATE INDEX IF NOT EXISTS idx_copilot_calls_day ON copilot_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_copilot_calls_ide ON copilot_calls(ide);
CREATE INDEX IF NOT EXISTS idx_copilot_tasks_day ON copilot_tasks(day);
CREATE INDEX IF NOT EXISTS idx_copilot_tasks_ide ON copilot_tasks(ide);
"""

def ensure_copilot_schema(con):
    # v0.6.5 expands Copilot storage from VS Code-only to VS Code + Visual Studio.
    # The collector always rebuilds these import tables, so a one-time schema reset
    # is safe and avoids carrying incompatible UNIQUE constraints from v0.6.4.
    cols = {row[1] for row in con.execute("PRAGMA table_info(copilot_tasks)").fetchall()}
    if cols and ("ide" not in cols or "reasoning_tokens" not in cols or "correlation_id" not in cols):
        con.execute("DROP TABLE IF EXISTS copilot_calls")
        con.execute("DROP TABLE IF EXISTS copilot_tasks")
        con.commit()
    con.executescript(COPILOT_SCHEMA)

def _insert_row(con, table, row):
    if not row:
        return
    cols = list(row)
    q = ','.join('?' for _ in cols)
    con.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({q})", [row[c] for c in cols])

def replace_copilot_file(con, file_path, calls, task):
    """Backward-compatible helper for a single VS Code debug session."""
    ensure_copilot_schema(con)
    con.execute('DELETE FROM copilot_calls WHERE file_path=?', (file_path,))
    con.execute('DELETE FROM copilot_tasks WHERE file_path=?', (file_path,))
    for r in calls:
        _insert_row(con, 'copilot_calls', r)
    _insert_row(con, 'copilot_tasks', task)
    con.commit()

def insert_copilot_rows(con, calls, tasks):
    ensure_copilot_schema(con)
    for r in calls:
        _insert_row(con, 'copilot_calls', r)
    for r in tasks:
        _insert_row(con, 'copilot_tasks', r)
    con.commit()
