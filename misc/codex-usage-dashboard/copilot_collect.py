from __future__ import annotations

from pathlib import Path
import os

from copilot_parser import parse_copilot_session
from visual_studio_copilot_parser import parse_visual_studio_log
from db import connect, ensure_copilot_schema, insert_copilot_rows

DEFAULT_COPILOT_ROOT = Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'Code' / 'User' / 'workspaceStorage'
DEFAULT_VS_COPILOT_ROOT = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'Temp' / 'VSGitHubCopilotLogs'
DEFAULT_COPILOT_DB = Path(__file__).resolve().parent / 'data' / 'copilot_usage_v06.db'


def collect_copilot(
    root=DEFAULT_COPILOT_ROOT,
    vs_root=DEFAULT_VS_COPILOT_ROOT,
    db_path=DEFAULT_COPILOT_DB,
):
    """Collect VS Code + Visual Studio GitHub Copilot telemetry into one DB."""
    root = Path(root).expanduser()
    vs_root = Path(vs_root).expanduser()
    db_path = Path(db_path).expanduser()

    vscode_paths = sorted(root.glob('*/GitHub.copilot-chat/debug-logs/*/main.jsonl')) if root.exists() else []
    vs_paths = sorted(vs_root.glob('*.chat.log')) if vs_root.exists() else []

    con = connect(db_path)
    ensure_copilot_schema(con)
    con.execute('DELETE FROM copilot_calls')
    con.execute('DELETE FROM copilot_tasks')
    con.commit()

    totals = {
        'files_found': len(vscode_paths) + len(vs_paths),
        'vscode_files': len(vscode_paths),
        'visual_studio_files': len(vs_paths),
        'sessions': 0,
        'vscode_sessions': 0,
        'visual_studio_sessions': 0,
        'model_calls': 0,
        'tool_calls': 0,
        'aiu': 0.0,
    }

    for p in vscode_paths:
        calls, task = parse_copilot_session(p)
        tasks = [task] if task else []
        insert_copilot_rows(con, calls, tasks)
        if task:
            totals['sessions'] += 1
            totals['vscode_sessions'] += 1
            totals['model_calls'] += len(calls)
            totals['tool_calls'] += int(task.get('tool_calls') or 0)
            totals['aiu'] += float(task.get('aiu') or 0)

    for p in vs_paths:
        calls, tasks = parse_visual_studio_log(p)
        insert_copilot_rows(con, calls, tasks)
        totals['sessions'] += len(tasks)
        totals['visual_studio_sessions'] += len(tasks)
        totals['model_calls'] += len(calls)
        totals['tool_calls'] += sum(int(t.get('tool_calls') or 0) for t in tasks)

    con.close()
    return totals
