from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import time

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components

from copilot_collect import (
    collect_copilot,
    DEFAULT_COPILOT_ROOT,
    DEFAULT_VS_COPILOT_ROOT,
    DEFAULT_COPILOT_DB,
)
from db import connect, load, ensure_copilot_schema

VERSION = "0.6.6"

st.set_page_config(page_title=f"Copilot Usage Dashboard v{VERSION}", layout="wide")
st.title("GitHub Copilot Usage Dashboard")
st.caption(f"v{VERSION} · VS Code + Visual Studio Copilot analytics")


def fmt(n):
    n = float(n or 0)
    if abs(n) >= 1e9: return f"{n/1e9:.2f}B"
    if abs(n) >= 1e6: return f"{n/1e6:.2f}M"
    if abs(n) >= 1e3: return f"{n/1e3:.1f}K"
    return f"{n:.0f}"


def pct(x):
    return "—" if pd.isna(x) else f"{100*x:.1f}%"


def aiu_fmt(value):
    return "N/A" if value is None or pd.isna(value) else f"{float(value):.3f}"


def _event_time(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return "—"


def _extract_agent_text(raw):
    try:
        obj = json.loads(raw or "")
        messages = obj if isinstance(obj, list) else [obj]
        texts = []
        for msg in messages:
            if not isinstance(msg, dict): continue
            for part in msg.get("parts", []):
                if isinstance(part, dict) and part.get("type") == "text" and part.get("content"):
                    texts.append(" ".join(str(part["content"]).split()))
        return " ".join(texts)
    except Exception:
        return ""


def load_vscode_task_trace(main_path):
    path = Path(str(main_path))
    if not path.exists(): return None, pd.DataFrame()
    raw_events = []
    try:
        for line in path.open(encoding="utf-8", errors="replace"):
            try: raw_events.append(json.loads(line))
            except Exception: continue
    except OSError:
        return None, pd.DataFrame()

    prompt = next((str(d.get("attrs", {}).get("content")) for d in raw_events
                   if d.get("type") == "user_message" and d.get("attrs", {}).get("content")), None)
    timeline, model_index = [], 0
    for d in sorted(raw_events, key=lambda x: int(x.get("ts") or 0)):
        typ, attrs = d.get("type"), d.get("attrs", {})
        if typ == "user_message":
            timeline.append({"Time (UTC)": _event_time(d.get("ts")), "Step": "User prompt",
                             "Details": " ".join(str(attrs.get("content") or "").split())[:350],
                             "Status": "—", "Duration": "—"})
        elif typ == "llm_request" and attrs.get("debugName") != "title":
            model_index += 1
            timeline.append({"Time (UTC)": _event_time(d.get("ts")), "Step": f"Model turn {model_index}",
                             "Details": str(attrs.get("model") or "(unknown)"),
                             "Status": str(d.get("status") or "unknown"),
                             "Duration": f"{int(d.get('dur') or 0):,} ms"})
        elif typ == "tool_call":
            timeline.append({"Time (UTC)": _event_time(d.get("ts")), "Step": "Tool",
                             "Details": str(d.get("name") or "(unknown tool)"),
                             "Status": str(d.get("status") or "unknown"),
                             "Duration": f"{int(d.get('dur') or 0):,} ms"})
        elif typ == "agent_response":
            text = _extract_agent_text(attrs.get("response"))
            if text:
                timeline.append({"Time (UTC)": _event_time(d.get("ts")), "Step": "Agent response",
                                 "Details": text[:350] + ("…" if len(text) > 350 else ""),
                                 "Status": str(d.get("status") or "—"), "Duration": "—"})
    return prompt, pd.DataFrame(timeline)



def _short(value, limit=160):
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[:limit-1] + "…"


def load_vscode_agent_flow(main_path):
    """Return a compact, interactive-flow-friendly representation of a VS Code agent task."""
    path = Path(str(main_path))
    if not path.exists():
        return []
    events = []
    try:
        for line in path.open(encoding="utf-8", errors="replace"):
            try:
                events.append(json.loads(line))
            except Exception:
                continue
    except OSError:
        return []

    events = sorted(events, key=lambda x: int(x.get("ts") or 0))
    flow = []
    discoveries = [e for e in events if e.get("type") == "discovery"]
    if discoveries:
        flow.append({
            "id": "discovery-group", "parent": None, "kind": "group",
            "label": f"Agent Discovery +{max(0, len(discoveries)-1)} more",
            "subtitle": f"{len(discoveries)} discovery steps", "details": "Environment/bootstrap discovery",
            "collapsed": True, "branch": "discovery"
        })
        prev = "discovery-group"
        for i, e in enumerate(discoveries):
            attrs = e.get("attrs", {})
            node_id = f"discovery-{i}"
            flow.append({
                "id": node_id, "parent": prev, "kind": "discovery",
                "label": str(e.get("name") or attrs.get("category") or "Discovery"),
                "subtitle": _short(attrs.get("details") or attrs.get("source") or "", 80),
                "details": _short(json.dumps(attrs, ensure_ascii=False), 600),
                "collapsed": False, "branch": "discovery-child"
            })
            prev = node_id

    main_parent = "discovery-group" if discoveries else None
    model_index = 0
    response_index = 0
    for idx, e in enumerate(events):
        typ = e.get("type")
        attrs = e.get("attrs", {}) or {}
        if typ in {"session_start", "discovery", "turn_start", "turn_end", "child_session_ref"}:
            continue
        if typ == "user_message":
            node_id = f"user-{idx}"
            flow.append({
                "id": node_id, "parent": main_parent, "kind": "user",
                "label": "User Message",
                "subtitle": _short(attrs.get("content"), 180),
                "details": str(attrs.get("content") or ""),
                "collapsed": False, "branch": "main"
            })
            main_parent = node_id
        elif typ == "tool_call":
            node_id = f"tool-{idx}"
            dur = int(e.get("dur") or 0)
            flow.append({
                "id": node_id, "parent": main_parent, "kind": "tool",
                "label": str(e.get("name") or "Tool"),
                "subtitle": f"{e.get('status') or 'unknown'} · {dur:,} ms",
                "details": _short(json.dumps({"args": attrs.get("args"), "result": attrs.get("result")}, ensure_ascii=False), 1200),
                "collapsed": False, "branch": "main"
            })
            main_parent = node_id
        elif typ == "llm_request" and attrs.get("debugName") != "title":
            model_index += 1
            inp = int(attrs.get("inputTokens") or 0)
            cached = int(attrs.get("cachedTokens") or 0)
            out = int(attrs.get("outputTokens") or 0)
            aiu_raw = attrs.get("copilotUsageNanoAiu")
            try:
                aiu = float(aiu_raw) / 1_000_000_000 if aiu_raw is not None else None
            except Exception:
                aiu = None
            dur = int(e.get("dur") or 0)
            bits = [f"{inp:,} input", f"{cached:,} cached", f"{out:,} output", f"{dur/1000:.1f}s"]
            if aiu is not None:
                bits.append(f"{aiu:.3f} AIU")
            node_id = f"model-{idx}"
            flow.append({
                "id": node_id, "parent": main_parent, "kind": "model",
                "label": str(attrs.get("model") or "Model"),
                "subtitle": " · ".join(bits),
                "details": "\n".join([
                    f"Turn: {model_index}", f"Model: {attrs.get('model') or '—'}",
                    f"Input: {inp:,}", f"Cached: {cached:,}", f"Fresh: {max(0, inp-cached):,}",
                    f"Output: {out:,}", f"TTFT: {int(attrs.get('ttft') or 0):,} ms",
                    f"Duration: {dur:,} ms", f"AIU: {aiu_fmt(aiu)}",
                    f"Status: {e.get('status') or 'unknown'}"
                ]),
                "collapsed": False, "branch": "main"
            })
            main_parent = node_id
        elif typ == "agent_response":
            response_index += 1
            body = _extract_agent_text(attrs.get("response"))
            if not body:
                body = _short(attrs.get("response"), 500)
            node_id = f"response-{idx}"
            flow.append({
                "id": node_id, "parent": main_parent, "kind": "response",
                "label": "Agent Response",
                "subtitle": _short(body, 150),
                "details": body or "Response payload unavailable",
                "collapsed": True, "branch": "main"
            })
            main_parent = node_id
    return flow


def build_vs_agent_flow(task, task_calls):
    """Build a compact flow from Visual Studio task/model-call telemetry."""
    prompt = str(task.get("prompt_preview") or "")
    flow = [{
        "id": "vs-user", "parent": None, "kind": "user", "label": "User Message",
        "subtitle": _short(prompt, 180), "details": prompt, "collapsed": False, "branch": "main"
    }]
    parent = "vs-user"
    for _, c in task_calls.sort_values("call_index").iterrows():
        turn = int(c.get("call_index") or 0)
        inp = int(c.get("input_tokens") or 0)
        cached = int(c.get("cached_input_tokens") or 0)
        out = int(c.get("output_tokens") or 0)
        reasoning = int(c.get("reasoning_tokens") or 0)
        dur = int(c.get("duration_ms") or 0)
        model_id = f"vs-model-{turn}"
        flow.append({
            "id": model_id, "parent": parent, "kind": "model",
            "label": str(c.get("model") or "Model"),
            "subtitle": f"{inp:,} input · {cached:,} cached · {out:,} output · {dur/1000:.1f}s · AIU N/A",
            "details": "\n".join([
                f"Turn: {turn}", f"Model: {c.get('model') or '—'}", f"Input: {inp:,}",
                f"Cached: {cached:,}", f"Fresh: {max(0, inp-cached):,}", f"Output: {out:,}",
                f"Reasoning: {reasoning:,}", f"Duration: {dur:,} ms", "AIU: N/A (not exposed by Visual Studio)",
                f"Status: {c.get('status') or 'unknown'}"
            ]),
            "collapsed": False, "branch": "main"
        })
        parent = model_id
        tools = [x.strip() for x in str(c.get("tool_names") or "").split(",") if x.strip()]
        for ti, tool in enumerate(tools):
            tool_id = f"vs-tool-{turn}-{ti}"
            flow.append({
                "id": tool_id, "parent": parent, "kind": "tool", "label": tool,
                "subtitle": "tool call", "details": f"Tool: {tool}", "collapsed": False, "branch": "main"
            })
            parent = tool_id
    flow.append({
        "id": "vs-response", "parent": parent, "kind": "response", "label": "Agent Response",
        "subtitle": "Final response persisted in Visual Studio session data", 
        "details": "Visual Studio .chat.log exposes the response and model telemetry. The dashboard keeps the response node folded by default.",
        "collapsed": True, "branch": "main"
    })
    return flow


def render_agent_flow(flow, key="agent-flow", height=720):
    """Render a self-contained collapsible/pannable/zoomable SVG agent flow."""
    if not flow:
        st.info("Agent flow data is not available for this task.")
        return

    payload = json.dumps(flow, ensure_ascii=False).replace("</", "<\\/")
    html = r"""
<div id="app" style="height:__HEIGHT__px;background:#111315;border:1px solid #30343a;border-radius:12px;overflow:hidden;position:relative;font-family:Inter,Segoe UI,Arial,sans-serif;color:#e7e9ec">
  <div style="position:absolute;z-index:4;top:10px;left:10px;display:flex;gap:6px;flex-wrap:wrap">
    <button onclick="expandAll()">Expand all</button><button onclick="collapseAll()">Collapse all</button>
    <button onclick="focusCore()">Focus model flow</button><button onclick="showAll()">Show all</button><button onclick="fitGraph()">Fit</button>
  </div>
  <div id="hint" style="position:absolute;z-index:4;top:12px;right:14px;color:#9aa0a6;font-size:12px">Wheel: zoom · drag: pan · click node: details · ▸/▾: fold</div>
  <svg id="svg" width="100%" height="100%" style="cursor:grab;user-select:none">
    <g id="viewport"></g>
  </svg>
  <div id="detail" style="display:none;position:absolute;z-index:5;right:12px;bottom:12px;width:min(420px,42%);max-height:45%;overflow:auto;background:#191c20;border:1px solid #49515a;border-radius:10px;padding:12px;box-shadow:0 8px 32px #0008">
    <div style="display:flex;justify-content:space-between;gap:12px"><strong id="detailTitle"></strong><button onclick="closeDetail()">×</button></div>
    <pre id="detailBody" style="white-space:pre-wrap;word-break:break-word;color:#cdd3da;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace"></pre>
  </div>
</div>
<style>
#app button{background:#22262b;color:#e7e9ec;border:1px solid #555c65;border-radius:7px;padding:6px 9px;cursor:pointer}
#app button:hover{background:#2d3339}
.node-title{font-size:15px;fill:#e8eaed}.node-sub{font-size:11px;fill:#a9afb7}.fold{font-size:16px;fill:#c9d1d9;cursor:pointer}
.edge{stroke:#8a9098;stroke-width:2;fill:none}.node{cursor:pointer}.node:hover rect{filter:brightness(1.18)}
</style>
<script>
const original = __PAYLOAD__;
let hiddenKinds = new Set(), collapsed = new Set(original.filter(n=>n.collapsed).map(n=>n.id));
let scale=1, tx=35, ty=55, dragging=false, sx=0, sy=0;
const svg=document.getElementById('svg'), viewport=document.getElementById('viewport');
const colors={user:'#31a8d5',model:'#2f8cff',tool:'#55c98b',response:'#a8a8a8',group:'#999',discovery:'#888'};
function childrenOf(id){return original.filter(n=>n.parent===id)}
function descendants(id,out=new Set()){for(const c of childrenOf(id)){out.add(c.id);descendants(c.id,out)}return out}
function visibleNodes(){
  const hidden=new Set();
  for(const id of collapsed) for(const d of descendants(id)) hidden.add(d);
  return original.filter(n=>!hidden.has(n.id) && !hiddenKinds.has(n.kind) && !(hiddenKinds.has('discovery') && (n.branch||'').startsWith('discovery')));
}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function layout(nodes){
  const map=new Map(nodes.map(n=>[n.id,n])), pos=new Map();
  let yMain=70, yDisc=70;
  for(const n of nodes){
    if((n.branch||'').startsWith('discovery-child')){pos.set(n.id,{x:610,y:yDisc});yDisc+=92}
    else if(n.kind==='group'){pos.set(n.id,{x:40,y:70})}
    else {pos.set(n.id,{x:40,y:yMain});yMain+=108}
  }
  // Avoid overlap between group and first main node.
  if(nodes.some(n=>n.kind==='group')) {
    let firstMain=nodes.find(n=>n.branch==='main');
    if(firstMain){ let p=pos.get(firstMain.id); if(p.y<165) {let delta=165-p.y; for(const n of nodes){if(n.branch==='main'){let q=pos.get(n.id);q.y+=delta}}}}
  }
  return pos;
}
function render(){
  const nodes=visibleNodes(), ids=new Set(nodes.map(n=>n.id)), pos=layout(nodes); let s='';
  for(const n of nodes){
    if(!n.parent || !ids.has(n.parent)) continue;
    const a=pos.get(n.parent), b=pos.get(n.id); if(!a||!b) continue;
    const x1=a.x+360,y1=a.y+31,x2=b.x,y2=b.y+31, mid=(y1+y2)/2;
    s+=`<path class="edge" d="M${x1} ${y1} C${x1} ${mid},${x2} ${mid},${x2} ${y2}"/>`;
  }
  for(const n of nodes){
    const p=pos.get(n.id), col=colors[n.kind]||'#888', kids=childrenOf(n.id), fold=kids.length?(collapsed.has(n.id)?'▸':'▾'):'';
    const w=360,h=62;
    s+=`<g class="node" transform="translate(${p.x},${p.y})" onclick="showDetail('${n.id}')">
      <rect width="${w}" height="${h}" rx="9" fill="#151719" stroke="${col}" stroke-width="2"/>
      <rect width="6" height="${h}" rx="6" fill="${col}"/>
      <text class="node-title" x="20" y="25">${esc(n.label)}</text>
      <text class="node-sub" x="20" y="47">${esc(n.subtitle)}</text>
      ${fold?`<text class="fold" x="${w-26}" y="26" onclick="toggleFold(event,'${n.id}')">${fold}</text>`:''}
    </g>`;
  }
  viewport.innerHTML=s; applyTransform();
}
function toggleFold(e,id){e.stopPropagation(); collapsed.has(id)?collapsed.delete(id):collapsed.add(id);render()}
function expandAll(){collapsed.clear();render()}
function collapseAll(){for(const n of original)if(childrenOf(n.id).length)collapsed.add(n.id);render()}
function focusCore(){hiddenKinds=new Set(['group','discovery']);render();fitGraph()}
function showAll(){hiddenKinds.clear();render();fitGraph()}
function showDetail(id){const n=original.find(x=>x.id===id);if(!n)return;document.getElementById('detailTitle').textContent=n.label;document.getElementById('detailBody').textContent=n.details||n.subtitle||'';document.getElementById('detail').style.display='block'}
function closeDetail(){document.getElementById('detail').style.display='none'}
function applyTransform(){viewport.setAttribute('transform',`translate(${tx} ${ty}) scale(${scale})`)}
function fitGraph(){scale=.88;tx=25;ty=35;applyTransform()}
svg.addEventListener('wheel',e=>{e.preventDefault();const k=e.deltaY<0?1.1:.9;scale=Math.max(.35,Math.min(2.5,scale*k));applyTransform()},{passive:false});
svg.addEventListener('mousedown',e=>{dragging=true;sx=e.clientX-tx;sy=e.clientY-ty;svg.style.cursor='grabbing'});
window.addEventListener('mousemove',e=>{if(!dragging)return;tx=e.clientX-sx;ty=e.clientY-sy;applyTransform()});
window.addEventListener('mouseup',()=>{dragging=false;svg.style.cursor='grab'});
render();
</script>
""".replace("__HEIGHT__", str(int(height))).replace("__PAYLOAD__", payload)
    components.html(html, height=height, scrolling=False)



def _store_refresh_result(result, automatic=False):
    st.session_state["copilot_refresh_result"] = result
    st.session_state["copilot_refresh_epoch"] = time.time()
    if automatic: st.session_state["copilot_last_auto_refresh"] = time.time()


def _refresh_now(vscode_root, vs_root, db_path, automatic=False):
    result = collect_copilot(vscode_root, vs_root, db_path)
    _store_refresh_result(result, automatic=automatic)
    return result


with st.sidebar:
    st.header("Data")
    vscode_root = st.text_input("VS Code workspaceStorage", str(DEFAULT_COPILOT_ROOT))
    vs_root = st.text_input("Visual Studio Copilot logs", str(DEFAULT_VS_COPILOT_ROOT))
    dbpath = st.text_input("Copilot SQLite database", str(DEFAULT_COPILOT_DB))
    if st.button("Refresh Copilot Logs", type="primary", use_container_width=True):
        with st.spinner("Parsing VS Code + Visual Studio Copilot logs..."):
            r = _refresh_now(vscode_root, vs_root, dbpath)
        st.success(
            f"{r['sessions']} tasks · {r['model_calls']} turns · {r['tool_calls']} tools · "
            f"VS Code AIU {r['aiu']:.2f}"
        )

    st.markdown("#### Auto refresh")
    auto_refresh = st.checkbox("Enable auto refresh", value=False,
                               help="Periodically rescan both VS Code and Visual Studio Copilot logs.")
    auto_interval = st.selectbox("Refresh interval", [15, 30, 60, 120, 300], index=2,
                                 format_func=lambda s: f"{s} seconds" if s < 60 else f"{s//60} minute" + ("s" if s > 60 else ""),
                                 disabled=not auto_refresh)
    if auto_refresh:
        @st.fragment(run_every=auto_interval)
        def _auto_refresh_fragment():
            now = time.time()
            last = float(st.session_state.get("copilot_last_auto_refresh", 0.0))
            if now - last >= max(1, auto_interval - 1):
                _refresh_now(vscode_root, vs_root, dbpath, automatic=True)
                st.rerun()
            done = st.session_state.get("copilot_refresh_epoch")
            if done: st.caption(f"Auto refresh on · last scan {max(0, int(time.time()-float(done)))}s ago")
        _auto_refresh_fragment()

con = connect(dbpath)
ensure_copilot_schema(con)
calls = load(con, "copilot_calls")
tasks = load(con, "copilot_tasks")
con.close()

if tasks.empty and calls.empty:
    st.info("Click **Refresh Copilot Logs** to import VS Code and Visual Studio Copilot telemetry.")
    st.stop()

if not tasks.empty:
    tasks = tasks[(tasks["model_calls"].fillna(0) > 0) & tasks["prompt_preview"].notna()].copy()
if not calls.empty:
    valid_keys = set(zip(tasks["ide"].astype(str), tasks["session_id"].astype(str))) if not tasks.empty else set()
    calls = calls[[ (str(i), str(s)) in valid_keys for i, s in zip(calls["ide"], calls["session_id"]) ]].copy()

# Filters
if not tasks.empty:
    tasks["day_dt"] = pd.to_datetime(tasks["day"], errors="coerce")
    valid = tasks["day_dt"].dropna()
    min_day = valid.min().date() if not valid.empty else pd.Timestamp.today().date()
    max_day = valid.max().date() if not valid.empty else pd.Timestamp.today().date()
    with st.sidebar:
        st.header("Filters")
        ides_all = [x for x in ["VS Code", "Visual Studio"] if x in set(tasks["ide"].astype(str))]
        sel_ides = st.multiselect("IDE", ides_all, default=ides_all)
        dr = st.date_input("Date range", (min_day, max_day), min_value=min_day, max_value=max_day)
        projects_all = sorted(x for x in tasks["project"].dropna().astype(str).unique() if x not in {"(unknown)", "(none)", ""})
        sel_projects = st.multiselect("Projects", projects_all, default=projects_all)
        models_all = sorted(x for x in tasks["model"].dropna().astype(str).unique() if x not in {"(unknown)", "(none)", ""})
        sel_models = st.multiselect("Models", models_all, default=models_all)
    start, end = dr if isinstance(dr, tuple) and len(dr) == 2 else (dr, dr)
    mask = (tasks["day_dt"] >= pd.Timestamp(start)) & (tasks["day_dt"] <= pd.Timestamp(end))
    tf = tasks[mask].copy()
    if sel_ides: tf = tf[tf["ide"].isin(sel_ides)]
    if sel_projects: tf = tf[tf["project"].isin(sel_projects)]
    if sel_models: tf = tf[tf["model"].isin(sel_models)]
else:
    tf = tasks.copy(); start = end = pd.Timestamp.today().date(); sel_ides = sel_projects = sel_models = []

cf = calls.copy()
if not cf.empty:
    cf["timestamp_dt"] = pd.to_datetime(cf["timestamp"], utc=True, errors="coerce")
    cf["day_dt"] = cf["timestamp_dt"].dt.tz_localize(None).dt.normalize()
    cf = cf[(cf["day_dt"] >= pd.Timestamp(start)) & (cf["day_dt"] <= pd.Timestamp(end))]
    if sel_ides: cf = cf[cf["ide"].isin(sel_ides)]
    if sel_projects: cf = cf[cf["project"].isin(sel_projects)]
    if sel_models: cf = cf[cf["model"].isin(sel_models)]

st.info("Visual Studio exposes the same core task/model telemetry as VS Code here; **AIU/AIC is the only usage field shown as N/A for Visual Studio** because its local `.chat.log` does not expose that value.")

tabs = st.tabs(["Overview", "Tasks", "Model Calls", "Projects", "Models", "Efficiency"])

with tabs[0]:
    ci = tf["input_tokens"].sum() if not tf.empty else 0
    cc = tf["cached_input_tokens"].sum() if not tf.empty else 0
    fresh = tf["fresh_input_tokens"].sum() if not tf.empty else 0
    co = tf["output_tokens"].sum() if not tf.empty else 0
    cr = tf["reasoning_tokens"].sum() if not tf.empty else 0
    known_aiu = tf["aiu"].dropna().sum() if not tf.empty else 0
    has_vs = not tf.empty and (tf["ide"] == "Visual Studio").any()

    a,b,c,d,e,f = st.columns(6)
    a.metric("Tasks", len(tf)); b.metric("Model turns", int(tf["model_calls"].sum()) if not tf.empty else 0)
    c.metric("Input", fmt(ci)); d.metric("Cached input", fmt(cc)); e.metric("Fresh input", fmt(fresh)); f.metric("Output", fmt(co))
    x,y,z,w = st.columns(4)
    x.metric("AIU / AIC", f"{known_aiu:.2f}" + (" + VS N/A" if has_vs else ""))
    y.metric("Reasoning", fmt(cr)); z.metric("Cache ratio", pct(cc/ci if ci else float('nan')))
    w.metric("Tool calls", int(tf["tool_calls"].sum()) if not tf.empty else 0)

    if not tf.empty:
        daily = tf.groupby(["day","ide"], as_index=False).agg(tasks=("session_id","count"), model_turns=("model_calls","sum"),
            tool_calls=("tool_calls","sum"), input_tokens=("input_tokens","sum"), cached_input_tokens=("cached_input_tokens","sum"),
            fresh_input_tokens=("fresh_input_tokens","sum"), output_tokens=("output_tokens","sum"), reasoning_tokens=("reasoning_tokens","sum"),
            aiu=("aiu","sum"), errors=("errors","sum"))
        daily["day_label"] = pd.to_datetime(daily["day"], errors="coerce").dt.strftime("%b %d, %Y")
        token_daily = daily.melt(id_vars=["day_label","ide"], value_vars=["input_tokens","cached_input_tokens","fresh_input_tokens"],
                                 var_name="metric", value_name="tokens")
        st.plotly_chart(px.line(token_daily, x="day_label", y="tokens", color="ide", line_dash="metric", markers=True,
                                title="Daily Copilot input usage by IDE"), use_container_width=True)

        aiu_daily = daily[daily["ide"] == "VS Code"].copy()
        if not aiu_daily.empty:
            st.plotly_chart(px.bar(aiu_daily, x="day_label", y="aiu", title="Daily VS Code AIU / AIC usage"), use_container_width=True)
            st.caption("Visual Studio is excluded from AIU charts only; all token, task, model, tool, duration and project analytics include it.")

        summary = daily.rename(columns={"day_label":"Day","ide":"IDE","tasks":"Tasks","model_turns":"Turns","tool_calls":"Tools",
            "input_tokens":"Input","cached_input_tokens":"Cached","fresh_input_tokens":"Fresh","output_tokens":"Output",
            "reasoning_tokens":"Reasoning","aiu":"AIU","errors":"Errors"})
        summary["AIU"] = summary.apply(lambda r: None if r["IDE"] == "Visual Studio" else r["AIU"], axis=1)
        st.subheader("Daily usage summary")
        st.dataframe(summary[["Day","IDE","Tasks","Turns","Tools","Input","Cached","Fresh","Output","Reasoning","AIU","Errors"]],
                     use_container_width=True, hide_index=True, column_config={"AIU": st.column_config.NumberColumn(format="%.3f")})

with tabs[1]:
    if tf.empty:
        st.info("No Copilot tasks in the selected range.")
    else:
        st.subheader("Task analysis")
        q = tf.sort_values("start_timestamp", ascending=False).copy().reset_index(drop=True)
        q["Time"] = pd.to_datetime(q["start_timestamp"], utc=True, errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        q["Cache %"] = (100*q["cached_input_tokens"]/q["input_tokens"].where(q["input_tokens"]!=0)).round(1)
        q["AIU display"] = q.apply(lambda r: "N/A" if r["ide"] == "Visual Studio" else f"{float(r['aiu'] or 0):.3f}", axis=1)
        display = q.rename(columns={"ide":"IDE","task_title":"Task","project":"Project","model":"Model","model_calls":"Turns","tool_calls":"Tools",
            "input_tokens":"Input","cached_input_tokens":"Cached","fresh_input_tokens":"Fresh","output_tokens":"Output","reasoning_tokens":"Reasoning",
            "duration_seconds":"Duration (s)","errors":"Errors"})
        cols=["Time","IDE","Task","Project","Model","Turns","Tools","Input","Cached","Fresh","Output","Reasoning","Cache %","AIU display","Duration (s)","Errors"]
        selected=st.dataframe(display[cols], use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row", key="copilot_task_table")
        st.caption("Select a task row for prompt, project context, tool flow and individual model turns.")
        rows=list(selected.selection.rows) if selected and hasattr(selected,"selection") else []
        if rows:
            task=q.iloc[rows[0]]
            st.divider(); st.subheader(str(task["task_title"]))
            a,b,c,d,e,f=st.columns(6)
            a.metric("IDE", task["ide"]); b.metric("AIU", aiu_fmt(task["aiu"])); c.metric("Turns", int(task["model_calls"] or 0))
            d.metric("Tools", int(task["tool_calls"] or 0)); e.metric("Input", fmt(task["input_tokens"])); f.metric("Output", fmt(task["output_tokens"]))
            with st.expander("User prompt", expanded=True): st.text(task.get("prompt_preview") or "Prompt unavailable")
            m1,m2=st.columns(2)
            with m1:
                st.markdown(f"**Project:** `{task['project']}`"); st.markdown(f"**Workspace:** `{task['workspace'] or '—'}`")
                st.markdown(f"**Solution:** `{task['solution'] or '—'}`")
            with m2:
                st.markdown(f"**Active file:** `{task['active_file'] or '—'}`"); st.markdown(f"**Model:** `{task['model']}`")
                st.markdown(f"**Duration:** `{float(task['duration_seconds'] or 0):.2f} s`")

            task_calls=cf[(cf["ide"].astype(str)==str(task["ide"])) & (cf["session_id"].astype(str)==str(task["session_id"]))].copy().sort_values("call_index")
            flow_tab, timeline_tab, turns_tab = st.tabs(["Agent Flow", "Timeline", "Model turns"])

            with flow_tab:
                st.caption("Interactive flow · discovery/response groups start folded · use the graph controls to expand, collapse, pan, zoom, or focus on model/tool activity.")
                if task["ide"] == "VS Code":
                    agent_flow = load_vscode_agent_flow(task["file_path"])
                else:
                    agent_flow = build_vs_agent_flow(task, task_calls)
                render_agent_flow(agent_flow, key=f"flow-{task['ide']}-{task['session_id']}", height=760)

            with timeline_tab:
                if task["ide"] == "VS Code":
                    _, timeline = load_vscode_task_trace(task["file_path"])
                    if not timeline.empty:
                        st.dataframe(timeline, use_container_width=True, hide_index=True)
                    else:
                        st.info("Raw VS Code debug file is no longer available.")
                else:
                    flow=[]
                    for _, c in task_calls.iterrows():
                        flow.append({"Step":f"Model turn {int(c['call_index'])}","Details":c["model"],"Status":c["status"],"Duration":f"{int(c['duration_ms'] or 0):,} ms"})
                        if c.get("tool_names"):
                            for tool in str(c["tool_names"]).split(', '):
                                flow.append({"Step":"Tool","Details":tool,"Status":"—","Duration":"—"})
                    st.dataframe(pd.DataFrame(flow), use_container_width=True, hide_index=True)

            with turns_tab:
                if not task_calls.empty:
                    td=task_calls.copy(); td["Cache %"]=(100*td["cached_input_tokens"]/td["input_tokens"].where(td["input_tokens"]!=0)).round(1)
                    td["AIU"] = td.apply(lambda r: None if r["ide"]=="Visual Studio" else r["aiu"], axis=1)
                    td=td.rename(columns={"call_index":"Turn","model":"Model","input_tokens":"Input","cached_input_tokens":"Cached","fresh_input_tokens":"Fresh",
                        "output_tokens":"Output","reasoning_tokens":"Reasoning","total_tokens":"Total","duration_ms":"Duration ms","ttft_ms":"TTFT ms","status":"Status","tool_names":"Tools"})
                    st.dataframe(td[["Turn","Model","Input","Cached","Fresh","Output","Reasoning","Total","Cache %","AIU","Duration ms","TTFT ms","Tools","Status"]],
                                 use_container_width=True, hide_index=True, column_config={"AIU":st.column_config.NumberColumn(format="%.6f")})
                else:
                    st.info("No model-turn telemetry is available for this task.")

with tabs[2]:
    if cf.empty: st.info("No Copilot model calls in the selected range.")
    else:
        q=cf.sort_values("timestamp", ascending=False).copy(); q["AIU"]=q.apply(lambda r: None if r["ide"]=="Visual Studio" else r["aiu"], axis=1)
        cols=["timestamp","ide","project","task_title","model","call_index","input_tokens","cached_input_tokens","fresh_input_tokens","output_tokens",
              "reasoning_tokens","total_tokens","aiu","duration_ms","ttft_ms","tool_names","status"]
        st.dataframe(q[cols].head(2000), use_container_width=True, hide_index=True)
        st.plotly_chart(px.scatter(q, x="input_tokens", y="output_tokens", color="ide", symbol="model", size="reasoning_tokens",
                                   hover_data=["task_title","cached_input_tokens","duration_ms"], title="Model-call input vs output"), use_container_width=True)

with tabs[3]:
    if tf.empty: st.info("No Copilot project data in the selected range.")
    else:
        p=tf.groupby(["project","ide"],as_index=False).agg(tasks=("session_id","count"),model_turns=("model_calls","sum"),tool_calls=("tool_calls","sum"),
            input_tokens=("input_tokens","sum"),cached_input_tokens=("cached_input_tokens","sum"),fresh_input_tokens=("fresh_input_tokens","sum"),
            output_tokens=("output_tokens","sum"),reasoning_tokens=("reasoning_tokens","sum"),aiu=("aiu","sum"))
        p["aiu"]=p.apply(lambda r: None if r["ide"]=="Visual Studio" else r["aiu"],axis=1)
        st.dataframe(p,use_container_width=True,hide_index=True)
        st.plotly_chart(px.bar(p,x="project",y="input_tokens",color="ide",title="Copilot input tokens by project and IDE"),use_container_width=True)

with tabs[4]:
    if cf.empty: st.info("No Copilot model data in the selected range.")
    else:
        m=cf.groupby(["model","ide"],as_index=False).agg(model_calls=("call_index","count"),tasks=("session_id","nunique"),input_tokens=("input_tokens","sum"),
            cached_input_tokens=("cached_input_tokens","sum"),fresh_input_tokens=("fresh_input_tokens","sum"),output_tokens=("output_tokens","sum"),
            reasoning_tokens=("reasoning_tokens","sum"),aiu=("aiu","sum"),avg_duration_ms=("duration_ms","mean"),avg_ttft_ms=("ttft_ms","mean"))
        m["aiu"]=m.apply(lambda r: None if r["ide"]=="Visual Studio" else r["aiu"],axis=1)
        st.plotly_chart(px.bar(m,x="model",y="input_tokens",color="ide",title="Input tokens by model and IDE"),use_container_width=True)
        st.dataframe(m,use_container_width=True,hide_index=True,column_config={"aiu":st.column_config.NumberColumn(format="%.3f")})

with tabs[5]:
    if tf.empty: st.info("No Copilot task data in the selected range.")
    else:
        q=tf.copy(); q["cache_ratio"]=q["cached_input_tokens"]/q["input_tokens"].where(q["input_tokens"]!=0)
        a,b,c,d=st.columns(4)
        a.metric("Avg turns / task",f"{q['model_calls'].mean():.1f}"); b.metric("Avg cache ratio",pct(q["cached_input_tokens"].sum()/q["input_tokens"].sum() if q["input_tokens"].sum() else float('nan')))
        c.metric("Avg duration / task",f"{q['duration_seconds'].mean():.1f}s"); d.metric("Reasoning tokens",fmt(q["reasoning_tokens"].sum()))
        scatter=q.copy(); scatter["cache_pct"]=100*scatter["cache_ratio"]
        st.plotly_chart(px.scatter(scatter,x="cache_pct",y="fresh_input_tokens",size="input_tokens",color="ide",symbol="project",
                                   hover_data=["task_title","model_calls"],title="Cache efficiency vs fresh input",
                                   labels={"cache_pct":"Cached input (%)","fresh_input_tokens":"Fresh input tokens"}),use_container_width=True)
        st.subheader("Largest tasks by total input")
        st.dataframe(q.sort_values("input_tokens",ascending=False)[["ide","task_title","project","model","model_calls","tool_calls","input_tokens","cached_input_tokens","fresh_input_tokens","output_tokens","reasoning_tokens","aiu","duration_seconds"]].head(100),
                     use_container_width=True,hide_index=True,column_config={"aiu":st.column_config.NumberColumn(format="%.3f")})
