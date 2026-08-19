from __future__ import annotations
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st
from collect import collect,DEFAULT_SESSIONS,DEFAULT_DB
from db import connect, load

st.set_page_config(page_title="Codex Usage Dashboard v0.6", layout="wide")
st.title("Codex Usage Dashboard")
st.caption("v0.6 · Local Codex rollout analytics")

with st.sidebar:
    st.header("Data")
    sessions=st.text_input("Codex sessions folder",str(DEFAULT_SESSIONS))
    dbpath=st.text_input("SQLite database",str(DEFAULT_DB))
    if st.button("Refresh from JSONL",type="primary",use_container_width=True):
        with st.spinner("Parsing Codex rollouts..."):
            r=collect(sessions,dbpath)
        st.success(
            f"{r['files_found']} files · {r['tasks']} tasks · "
            f"{r['model_calls']} model calls · {r['compactions']} compactions"
        )


con=connect(dbpath)
calls=load(con,"model_calls")
tasks=load(con,"tasks")
diag=load(con,"diagnostics")
rates=load(con,"rate_limits")
con.close()

if calls.empty and tasks.empty:
    st.info("Click **Refresh from JSONL**.")
    st.stop()

def fmt(n):
    n=float(n or 0)
    if abs(n)>=1e9:return f"{n/1e9:.2f}B"
    if abs(n)>=1e6:return f"{n/1e6:.2f}M"
    if abs(n)>=1e3:return f"{n/1e3:.1f}K"
    return f"{n:.0f}"

def pct(x):
    return "—" if pd.isna(x) else f"{100*x:.1f}%"

with st.sidebar:
    st.header("Display")
    display_tz = st.selectbox(
        "Task label timezone",
        ["Asia/Kolkata","UTC","America/New_York","America/Los_Angeles","Europe/London"],
        index=0
    )

def add_display_labels(frame):
    if frame.empty:
        return frame.copy()
    q = frame.copy()
    stamps = pd.to_datetime(q["start_timestamp"], utc=True, errors="coerce")
    try:
        local = stamps.dt.tz_convert(display_tz)
        time_text = local.dt.strftime("%b %d %H:%M")
    except Exception:
        time_text = stamps.dt.strftime("%b %d %H:%M UTC")
    # task_index is unique only inside a rollout/session, so include a compact
    # session suffix for collision-free visible labels without exposing full UUIDs.
    short_session = q["session_id"].astype(str).str[-6:]
    fallback = (
        time_text.fillna("Unknown time")
        + " · " + q["project"].fillna("(unknown)").astype(str)
        + " · Task " + q["task_index"].fillna(0).astype(int).astype(str)
        + " · " + short_session
    )
    title = q["task_title"].fillna("").astype(str).str.strip()
    q["task"] = title.where(title != "", fallback)
    q["when"] = time_text.fillna("Unknown time")
    return q

# Filters from model calls because they provide broadest date coverage.
if not calls.empty:
    calls["day_dt"]=pd.to_datetime(calls["day"],errors="coerce")
    valid=calls["day_dt"].dropna()
    min_day=valid.min().date() if not valid.empty else pd.Timestamp.today().date()
    max_day=valid.max().date() if not valid.empty else pd.Timestamp.today().date()
    with st.sidebar:
        st.header("Filters")
        dr=st.date_input("Date range",(min_day,max_day),min_value=min_day,max_value=max_day)
        projects_all=sorted(calls["project"].dropna().unique())
        sel_projects=st.multiselect("Projects",projects_all,default=projects_all)
        models_all=sorted(calls["model"].dropna().unique())
        sel_models=st.multiselect("Models",models_all,default=models_all)
    start,end=dr if isinstance(dr,tuple) and len(dr)==2 else (dr,dr)
    mask=(calls["day_dt"]>=pd.Timestamp(start))&(calls["day_dt"]<=pd.Timestamp(end))
    f=calls[mask & calls["project"].isin(sel_projects) & calls["model"].isin(sel_models)].copy()
else:
    f=calls.copy()
    start=end=pd.Timestamp.today().date()
    sel_projects=[];sel_models=[]

# Match tasks approximately by day/project/model filters.
tf=tasks.copy()
if not tf.empty:
    tf["day_dt"]=pd.to_datetime(tf["day"],errors="coerce")
    tf=tf[(tf["day_dt"]>=pd.Timestamp(start))&(tf["day_dt"]<=pd.Timestamp(end))]
    if sel_projects:tf=tf[tf["project"].isin(sel_projects)]
    if sel_models:tf=tf[tf["model"].isin(sel_models)]

tabs=st.tabs(["Overview","Tasks","Model Calls","Projects","Efficiency","Rate Limits","Diagnostics"])

with tabs[0]:
    ti=f["input_tokens"].sum() if not f.empty else 0
    tc=f["cached_input_tokens"].sum() if not f.empty else 0
    tfresh=f["fresh_input_tokens"].sum() if not f.empty else 0
    tout=f["output_tokens"].sum() if not f.empty else 0
    tr=f["reasoning_tokens"].sum() if not f.empty else 0

    a,b,c,d,e,fm=st.columns(6)
    a.metric("User tasks",len(tf))
    b.metric("Model calls",len(f))
    c.metric("Input",fmt(ti))
    d.metric("Cached input",fmt(tc))
    e.metric("Fresh input",fmt(tfresh))
    fm.metric("Output",fmt(tout))
    x,y,z,w=st.columns(4)
    x.metric("Reasoning output",fmt(tr))
    y.metric("Cache ratio",pct(tc/ti if ti else float("nan")))
    z.metric("Compactions",int(tf["compactions"].sum()) if not tf.empty else 0)
    w.metric("Tool calls",int(tf["tool_calls"].sum()) if not tf.empty else 0)

    st.info(
        "**Task names now come from the actual user instruction when the rollout exposes it.** "
        "Only a short prompt preview is stored; assistant responses, reasoning, tool arguments/output "
        "and source code are not persisted. Model calls are still separate from user prompts."
    )

    if not f.empty:
        daily=f.groupby("day",as_index=False)[["input_tokens","cached_input_tokens","fresh_input_tokens","output_tokens"]].sum()
        dl=daily.melt("day",var_name="metric",value_name="tokens")
        st.plotly_chart(px.line(dl,x="day",y="tokens",color="metric",markers=True,
                               title="Daily model-call token usage"),use_container_width=True)
    if not tf.empty:
        td=tf.groupby("day",as_index=False).agg(tasks=("task_index","count"),model_calls=("model_calls","sum"))
        st.plotly_chart(px.bar(td,x="day",y=["tasks","model_calls"],barmode="group",
                              title="User tasks vs model calls"),use_container_width=True)

with tabs[1]:
    if tf.empty:st.info("No reconstructed tasks in selected range.")
    else:
        q=add_display_labels(tf)
        q["cache_ratio"]=q["cached_input_tokens"]/q["input_tokens"].where(q["input_tokens"]!=0)
        q["calls_per_task"]=q["model_calls"]
        sort=st.selectbox("Rank tasks by",["input_tokens","fresh_input_tokens","model_calls","tool_calls","compactions","duration_seconds"])
        q=q.sort_values(sort,ascending=False)
        cols=["task","when","project","status","model","model_calls","tool_calls","compactions",
              "input_tokens","cached_input_tokens","fresh_input_tokens","output_tokens","reasoning_tokens",
              "duration_seconds","prompt_preview"]
        st.dataframe(q[cols],use_container_width=True,hide_index=True)
        st.subheader("Most expensive tasks")
        st.plotly_chart(px.bar(q.head(30),x="task",y="input_tokens",color="project",
                              hover_data=["model_calls","tool_calls","compactions","fresh_input_tokens"]),
                        use_container_width=True)

with tabs[2]:
    if f.empty:st.info("No model calls.")
    else:
        q=f.sort_values("input_tokens",ascending=False).copy()
        q["task_ref"] = q.apply(
            lambda r: f"Task {int(r['task_index'])} · {str(r['session_id'])[-6:]}"
            if pd.notna(r["task_index"]) else "Unassigned", axis=1
        )
        cols=["timestamp","project","model","task_ref","call_index_in_task","input_tokens",
              "cached_input_tokens","fresh_input_tokens","output_tokens","reasoning_tokens",
              "context_window","source_method","session_id","file_name"]
        st.dataframe(q[cols].head(500),use_container_width=True,hide_index=True)
        st.plotly_chart(px.histogram(f,x="input_tokens",nbins=60,title="Distribution of input tokens per model call"),
                        use_container_width=True)

with tabs[3]:
    if f.empty:st.info("No project data.")
    else:
        p=f.groupby("project",as_index=False).agg(
            sessions=("session_id","nunique"),model_calls=("call_index","count"),
            input_tokens=("input_tokens","sum"),cached_input_tokens=("cached_input_tokens","sum"),
            fresh_input_tokens=("fresh_input_tokens","sum"),output_tokens=("output_tokens","sum"),
            reasoning_tokens=("reasoning_tokens","sum")
        )
        p["cache_ratio"]=p["cached_input_tokens"]/p["input_tokens"].where(p["input_tokens"]!=0)
        p=p.sort_values("input_tokens",ascending=False)
        st.dataframe(p,use_container_width=True,hide_index=True)
        st.plotly_chart(px.bar(p,x="project",y=["fresh_input_tokens","cached_input_tokens"],
                              title="Fresh vs cached input by project"),use_container_width=True)

with tabs[4]:
    if tf.empty:st.info("No task data.")
    else:
        q=add_display_labels(tf)
        q["calls_per_task"]=q["model_calls"]
        q["tokens_per_tool_call"]=q["input_tokens"]/q["tool_calls"].where(q["tool_calls"]!=0)
        c1,c2,c3=st.columns(3)
        c1.metric("Avg model calls / task",f"{q['model_calls'].mean():.1f}")
        c2.metric("Median model calls / task",f"{q['model_calls'].median():.0f}")
        c3.metric("Tasks with compaction",int((q["compactions"]>0).sum()))

        st.subheader("Tasks with many model calls")
        cols=["task","when","project","model","model_calls","tool_calls","compactions",
              "input_tokens","fresh_input_tokens","duration_seconds"]
        st.dataframe(q.sort_values("model_calls",ascending=False)[cols].head(100),
                     use_container_width=True,hide_index=True)

        st.subheader("Compaction-heavy tasks")
        st.dataframe(q.sort_values(["compactions","input_tokens"],ascending=False)[cols].head(100),
                     use_container_width=True,hide_index=True)

with tabs[5]:
    if rates.empty:
        st.info("No non-null rate-limit snapshots were found in these rollout files.")
    else:
        rr=rates.copy()
        rr["timestamp_dt"]=pd.to_datetime(rr["timestamp"],errors="coerce")
        rr=rr.dropna(subset=["timestamp_dt"]).sort_values("timestamp_dt")
        latest=rr.groupby("bucket",as_index=False).tail(1)
        st.subheader("Latest locally recorded rate-limit snapshots")
        st.dataframe(latest[["timestamp","bucket","used_percent","window_minutes","resets_in_seconds","session_id"]],
                     use_container_width=True,hide_index=True)
        st.plotly_chart(px.line(rr,x="timestamp_dt",y="used_percent",color="bucket",
                               title="Recorded rate-limit used % over time"),use_container_width=True)
        st.caption("Availability varies by Codex client/version; null rate_limits are common.")

with tabs[6]:
    d1,d2,d3,d4=st.columns(4)
    d1.metric("Token events",int((diag["payload_type"]=="token_count").sum()) if not diag.empty else 0)
    d2.metric("Duplicate token snapshots",int((diag["classification"].isin(["duplicate_snapshot","duplicate_last_snapshot"])).sum()) if not diag.empty else 0)
    d3.metric("Task starts",int((diag["classification"]=="task_started").sum()) if not diag.empty else 0)
    d4.metric("Task completes",int((diag["classification"]=="task_complete").sum()) if not diag.empty else 0)

    if not diag.empty:
        st.subheader("Event classification")
        ec=diag.groupby("classification",as_index=False).size().sort_values("size",ascending=False)
        st.dataframe(ec,use_container_width=True,hide_index=True)

    if not f.empty:
        st.subheader("Largest model calls")
        cols=["timestamp","input_tokens","cached_input_tokens","fresh_input_tokens","output_tokens",
              "reasoning_tokens","project","model","task_index","call_index_in_task","source_method","file_path"]
        st.dataframe(f.sort_values("input_tokens",ascending=False)[cols].head(100),
                     use_container_width=True,hide_index=True)

    if not tasks.empty:
        st.subheader("Incomplete / unusual tasks")
        bad=add_display_labels(tasks[tasks["status"]!="completed"]).sort_values("start_timestamp",ascending=False)
        visible=["task","status","model","model_calls","tool_calls","compactions","input_tokens","fresh_input_tokens","duration_seconds"]
        st.dataframe(bad[visible],use_container_width=True,hide_index=True)

