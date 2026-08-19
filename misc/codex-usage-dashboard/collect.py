from __future__ import annotations
from pathlib import Path
import argparse
from parser import parse_rollout
from db import connect, replace_file

DEFAULT_SESSIONS=Path.home()/".codex"/"sessions"
DEFAULT_DB=Path(__file__).resolve().parent/"data"/"usage_v05.db"

def collect(session_dir=DEFAULT_SESSIONS,db_path=DEFAULT_DB):
    session_dir=Path(session_dir).expanduser()
    paths=sorted(session_dir.rglob("*.jsonl")) if session_dir.exists() else []
    con=connect(db_path)
    totals={"files_found":len(paths),"model_calls":0,"tasks":0,"token_events":0,
            "compactions":0,"tool_calls":0,"rate_limit_snapshots":0}
    for p in paths:
        calls,tasks,diag,rates=parse_rollout(p)
        replace_file(con,str(p.resolve()),calls,tasks,diag,rates)
        totals["model_calls"]+=len(calls)
        totals["tasks"]+=len(tasks)
        totals["token_events"]+=sum(d["classification"] in (
            "counted_model_call","counted_after_reset","duplicate_snapshot",
            "duplicate_last_snapshot","cumulative_baseline","empty_token_event"
        ) for d in diag)
        totals["compactions"]+=sum(t["compactions"] for t in tasks)
        totals["tool_calls"]+=sum(t["tool_calls"] for t in tasks)
        totals["rate_limit_snapshots"]+=len(rates)
    con.close()
    return totals

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--sessions",default=str(DEFAULT_SESSIONS))
    ap.add_argument("--db",default=str(DEFAULT_DB))
    a=ap.parse_args()
    for k,v in collect(a.sessions,a.db).items():print(f"{k}: {v}")
