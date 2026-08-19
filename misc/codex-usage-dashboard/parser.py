from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

USAGE_FIELDS = ("input_tokens","cached_input_tokens","output_tokens","reasoning_output_tokens","total_tokens")

def as_int(v, default=0):
    try: return int(v or 0)
    except (TypeError, ValueError): return default

def usage(d):
    d = d if isinstance(d, dict) else {}
    return {k:max(0,as_int(d.get(k))) for k in USAGE_FIELDS}

def tup(u):
    return tuple(u.get(k,0) for k in USAGE_FIELDS)

def monotonic(cur, prev):
    keys=("input_tokens","cached_input_tokens","output_tokens","total_tokens")
    return all(cur.get(k,0)>=prev.get(k,0) for k in keys)

def delta(cur, prev):
    return {k:max(0,cur.get(k,0)-prev.get(k,0)) for k in USAGE_FIELDS}

def find_first(obj: Any, keys:set[str]):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in keys and v not in (None,""):
                return v
        for v in obj.values():
            x=find_first(v,keys)
            if x not in (None,""): return x
    elif isinstance(obj,list):
        for v in obj:
            x=find_first(v,keys)
            if x not in (None,""): return x
    return None

def project_of(cwd):
    if not cwd:return "(unknown)"
    try:return Path(cwd).name or str(cwd)
    except:return str(cwd)

def seconds_between(a,b):
    if not a or not b:return None
    try:
        aa=datetime.fromisoformat(a.replace("Z","+00:00"))
        bb=datetime.fromisoformat(b.replace("Z","+00:00"))
        return max(0.0,(bb-aa).total_seconds())
    except:return None

def task_key_of(session_id, task_index):
    return f"{session_id}:{task_index}"

def task_label_of(ts, project, task_index):
    # Keep the stored label timezone-neutral; dashboard renders local time.
    stamp = ts or "unknown-time"
    return f"{stamp} · {project or '(unknown)'} · Task {task_index}"


def _extract_text_fragments(obj):
    """Extract plain textual fragments from a user-message payload only."""
    out=[]
    if isinstance(obj,str):
        if obj.strip(): out.append(obj.strip())
    elif isinstance(obj,list):
        for x in obj:
            out.extend(_extract_text_fragments(x))
    elif isinstance(obj,dict):
        # Common rollout content shapes: {"type":"input_text","text":"..."}
        # or message content arrays. Avoid arbitrary recursive values such as metadata.
        typ=str(obj.get("type",""))
        if typ in ("input_text","text") and isinstance(obj.get("text"),str):
            if obj["text"].strip(): out.append(obj["text"].strip())
        elif "content" in obj:
            out.extend(_extract_text_fragments(obj.get("content")))
        elif isinstance(obj.get("text"),str) and typ not in ("output_text","reasoning"):
            if obj["text"].strip(): out.append(obj["text"].strip())
    return out

def _user_message_text(event):
    """Return text only when the event is clearly a user message."""
    if not isinstance(event,dict): return None
    top=event.get("type")
    payload=event.get("payload",{})
    if not isinstance(payload,dict): return None

    role=payload.get("role")
    ptype=payload.get("type")

    # Typical response_item message representation.
    if top=="response_item" and ptype=="message" and role=="user":
        parts=_extract_text_fragments(payload.get("content"))
        return " ".join(parts).strip() or None

    # Some rollout versions use event_msg/user_message.
    if top=="event_msg" and ptype in ("user_message","user_input"):
        parts=_extract_text_fragments(payload.get("content") or payload.get("message") or payload.get("text"))
        return " ".join(parts).strip() or None

    return None

def _clean_prompt(text):
    if not text:return None
    # Collapse whitespace; keep enough context for useful dashboard previews.
    return " ".join(str(text).split()).strip()

def _make_title(text, max_chars=90):
    text=_clean_prompt(text)
    if not text:return None
    # Remove common conversational lead-ins without attempting semantic AI summarization.
    lowered=text.lower()
    prefixes=("please ","can you ","could you ","would you ","i want you to ","i need you to ")
    for pref in prefixes:
        if lowered.startswith(pref):
            text=text[len(pref):].strip()
            break
    # Prefer first sentence/line-like clause, then cap.
    stops=[x for x in (text.find(". "),text.find("? "),text.find("! ")) if x>15]
    if stops:
        text=text[:min(stops)+1]
    if len(text)>max_chars:
        text=text[:max_chars-1].rstrip()+"…"
    return text

def parse_rollout(path):
    path=Path(path)
    resolved=str(path.resolve())
    file_name=path.name
    session_id=path.stem
    cwd=None
    model=None
    effort=None

    calls=[]
    tasks=[]
    diagnostics=[]
    rate_rows=[]

    prev_total=None
    prev_last=None
    line_index=0
    call_index=0
    task_index=0
    current_task=None
    pending_user_text=None
    calls_in_task=0

    # metadata counters for current task
    tool_calls=0
    compactions=0

    def start_task(ts, payload):
        nonlocal task_index,current_task,calls_in_task,tool_calls,compactions,pending_user_text
        # Close an orphan previous task if a new task starts.
        if current_task is not None:
            finish_task(ts, "superseded_by_next_task")
        task_index += 1
        calls_in_task=0; tool_calls=0; compactions=0
        tid = payload.get("task_id") or payload.get("turn_id") or f"{session_id}:task:{task_index}"
        seed_prompt=_clean_prompt(pending_user_text)
        current_task={
            "file_path":resolved,"file_name":file_name,"session_id":session_id,
            "task_id":str(tid),"task_key":task_key_of(session_id,task_index),
            "task_label":task_label_of(ts,project_of(cwd),task_index),
            "task_title":_make_title(seed_prompt),
            "prompt_preview":seed_prompt[:500] if seed_prompt else None,
            "task_index":task_index,"start_timestamp":ts,
            "end_timestamp":None,"day":ts[:10] if isinstance(ts,str) else None,
            "project":project_of(cwd),"cwd":cwd,"model":model or "(unknown)",
            "reasoning_effort":effort or "(unknown)","status":"running",
            "model_calls":0,"input_tokens":0,"cached_input_tokens":0,
            "fresh_input_tokens":0,"output_tokens":0,"reasoning_tokens":0,
            "total_tokens":0,"tool_calls":0,"compactions":0,"duration_seconds":None
        }
        pending_user_text=None

    def finish_task(ts, status):
        nonlocal current_task
        if current_task is None:
            return
        current_task["end_timestamp"]=ts
        current_task["status"]=status
        current_task["tool_calls"]=tool_calls
        current_task["compactions"]=compactions
        current_task["duration_seconds"]=seconds_between(current_task["start_timestamp"],ts)
        tasks.append(current_task)
        current_task=None

    def add_call(ts,u,method,context_window):
        nonlocal call_index,calls_in_task,current_task
        call_index+=1; calls_in_task+=1
        inp=u["input_tokens"]; cached=min(inp,u["cached_input_tokens"])
        out=u["output_tokens"]; reason=u["reasoning_output_tokens"]
        total=u["total_tokens"] or inp+out
        tid=current_task["task_id"] if current_task else None
        ti=current_task["task_index"] if current_task else None
        row={
            "file_path":resolved,"file_name":file_name,"session_id":session_id,
            "task_id":tid,
            "task_key":task_key_of(session_id,ti) if ti is not None else None,
            "task_index":ti,"call_index":call_index,
            "call_index_in_task":calls_in_task if current_task else None,
            "timestamp":ts,"day":ts[:10] if isinstance(ts,str) else None,
            "project":project_of(cwd),"cwd":cwd,"model":model or "(unknown)",
            "reasoning_effort":effort or "(unknown)",
            "input_tokens":inp,"cached_input_tokens":cached,
            "fresh_input_tokens":max(0,inp-cached),"output_tokens":out,
            "reasoning_tokens":reason,"total_tokens":total,
            "context_window":context_window,
            "cache_ratio":cached/inp if inp else None,
            "context_utilization":inp/context_window if inp and context_window else None,
            "source_method":method
        }
        calls.append(row)
        if current_task is not None:
            current_task["model_calls"]+=1
            for k in ("input_tokens","cached_input_tokens","fresh_input_tokens",
                      "output_tokens","reasoning_tokens","total_tokens"):
                current_task[k]+=row[k]
            # Refresh metadata if discovered later in task.
            current_task["project"]=project_of(cwd)
            current_task["cwd"]=cwd
            current_task["task_label"]=task_label_of(
                current_task["start_timestamp"], current_task["project"], current_task["task_index"]
            )
            current_task["model"]=model or current_task["model"]
            current_task["reasoning_effort"]=effort or current_task["reasoning_effort"]

    try:
        fh=path.open("r",encoding="utf-8",errors="replace")
    except OSError:
        return [],[],[],[]

    with fh:
        for line in fh:
            line_index+=1
            try:event=json.loads(line)
            except:continue

            ts=event.get("timestamp") if isinstance(event,dict) else None
            top_type=event.get("type") if isinstance(event,dict) else None
            payload=event.get("payload",{}) if isinstance(event,dict) else {}
            payload_type=payload.get("type") if isinstance(payload,dict) else None

            # Prefer known metadata containers but fall back recursively.
            sid=find_first(event,{"session_id","thread_id","conversation_id"})
            if isinstance(sid,str) and sid:session_id=sid
            mcwd=find_first(event,{"cwd","working_directory","workdir"})
            if isinstance(mcwd,str) and mcwd:cwd=mcwd
            mm=find_first(event,{"model"})
            if isinstance(mm,str) and mm:model=mm
            me=find_first(event,{"reasoning_effort","reasoning_level"})
            if isinstance(me,str) and me:effort=me

            # Capture only clearly identified USER message text. We do not persist
            # assistant text, tool arguments/output, reasoning, or file contents.
            user_text=_user_message_text(event)
            if user_text:
                cleaned=_clean_prompt(user_text)
                if current_task is not None:
                    # First substantial user message inside task is the task prompt.
                    if cleaned and not current_task.get("prompt_preview"):
                        current_task["prompt_preview"]=cleaned[:500]
                        current_task["task_title"]=_make_title(cleaned)
                else:
                    # Many rollout schemas record the user message just before task_started.
                    pending_user_text=cleaned

            # Task boundaries.
            if top_type=="event_msg" and payload_type=="task_started":
                start_task(ts,payload)
                diagnostics.append({
                    "file_path":resolved,"file_name":file_name,"session_id":session_id,
                    "timestamp":ts,"line_index":line_index,"top_type":top_type,
                    "payload_type":payload_type,"task_index":task_index,
                    "classification":"task_started","cumulative_input":0,
                    "cumulative_cached":0,"cumulative_output":0,"cumulative_reasoning":0,
                    "cumulative_total":0,"last_input":0,"last_cached":0,"last_output":0,
                    "last_reasoning":0,"last_total":0,
                    "context_window":as_int(payload.get("model_context_window")) or None,
                    "note":""
                })
                continue

            if top_type=="event_msg" and payload_type in ("task_complete","turn_complete","turn_completed"):
                diagnostics.append({
                    "file_path":resolved,"file_name":file_name,"session_id":session_id,
                    "timestamp":ts,"line_index":line_index,"top_type":top_type,
                    "payload_type":payload_type,"task_index":task_index if current_task else None,
                    "classification":"task_complete","cumulative_input":0,
                    "cumulative_cached":0,"cumulative_output":0,"cumulative_reasoning":0,
                    "cumulative_total":0,"last_input":0,"last_cached":0,"last_output":0,
                    "last_reasoning":0,"last_total":0,"context_window":None,"note":""
                })
                finish_task(ts,"completed")
                continue

            # Compactions.
            is_compaction = (top_type=="compacted") or (top_type=="event_msg" and payload_type=="context_compacted")
            if is_compaction:
                compactions+=1
                diagnostics.append({
                    "file_path":resolved,"file_name":file_name,"session_id":session_id,
                    "timestamp":ts,"line_index":line_index,"top_type":top_type,
                    "payload_type":payload_type,"task_index":task_index if current_task else None,
                    "classification":"compaction","cumulative_input":0,
                    "cumulative_cached":0,"cumulative_output":0,"cumulative_reasoning":0,
                    "cumulative_total":0,"last_input":0,"last_cached":0,"last_output":0,
                    "last_reasoning":0,"last_total":0,"context_window":None,"note":""
                })
                continue

            # Function/tool call count. Do not store name, args, or output.
            if top_type=="response_item" and payload_type in ("function_call","custom_tool_call","local_shell_call"):
                tool_calls+=1

            # Token events.
            if not (top_type=="event_msg" and payload_type=="token_count"):
                continue

            info=payload.get("info") or {}
            if not isinstance(info,dict):info={}
            cur=usage(info.get("total_token_usage"))
            last=usage(info.get("last_token_usage"))
            has_cur=any(tup(cur)); has_last=any(tup(last))
            cw=as_int(info.get("model_context_window")) or None

            classification="empty_token_event"
            note=""
            counted=None
            method=None

            if has_cur:
                if prev_total is None:
                    # For a first snapshot, last_token_usage is the best available
                    # representation of the latest model call.
                    if has_last:
                        counted=last.copy(); method="first_last_usage"
                        classification="counted_model_call"
                    else:
                        classification="cumulative_baseline"
                    prev_total=cur.copy()
                elif tup(cur)==tup(prev_total):
                    classification="duplicate_snapshot"
                elif monotonic(cur,prev_total):
                    counted=delta(cur,prev_total); method="cumulative_delta"
                    classification="counted_model_call"
                    prev_total=cur.copy()
                else:
                    classification="counter_reset"
                    note="Cumulative token counters regressed; new baseline started."
                    prev_total=cur.copy()
                    if has_last and (prev_last is None or tup(last)!=tup(prev_last)):
                        counted=last.copy(); method="reset_last_usage"
                        classification="counted_after_reset"
            elif has_last:
                if prev_last is None or tup(last)!=tup(prev_last):
                    counted=last.copy(); method="unique_last_usage"
                    classification="counted_model_call"
                else:
                    classification="duplicate_last_snapshot"

            if has_last:prev_last=last.copy()

            diagnostics.append({
                "file_path":resolved,"file_name":file_name,"session_id":session_id,
                "timestamp":ts,"line_index":line_index,"top_type":top_type,
                "payload_type":payload_type,"task_index":task_index if current_task else None,
                "classification":classification,
                "cumulative_input":cur["input_tokens"],"cumulative_cached":cur["cached_input_tokens"],
                "cumulative_output":cur["output_tokens"],"cumulative_reasoning":cur["reasoning_output_tokens"],
                "cumulative_total":cur["total_tokens"],"last_input":last["input_tokens"],
                "last_cached":last["cached_input_tokens"],"last_output":last["output_tokens"],
                "last_reasoning":last["reasoning_output_tokens"],"last_total":last["total_tokens"],
                "context_window":cw,"note":note
            })

            # Rate-limit metadata can be useful even when token snapshot is duplicate.
            rl=payload.get("rate_limits")
            if isinstance(rl,dict):
                for bucket,val in rl.items():
                    if isinstance(val,dict):
                        up=val.get("used_percent")
                        if up is None:continue
                        try:up=float(up)
                        except:continue
                        rate_rows.append({
                            "file_path":resolved,"session_id":session_id,"timestamp":ts,
                            "task_index":task_index if current_task else None,
                            "bucket":str(bucket),"used_percent":up,
                            "window_minutes":as_int(val.get("window_minutes")) or None,
                            "resets_in_seconds":as_int(val.get("resets_in_seconds")) or None
                        })

            if counted and any(tup(counted)):
                add_call(ts,counted,method,cw)

    if current_task is not None:
        # Preserve incomplete/hanging task rather than dropping it.
        finish_task(None,"incomplete")

    return calls,tasks,diagnostics,rate_rows
