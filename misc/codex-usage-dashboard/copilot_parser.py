from __future__ import annotations
import json, re
from pathlib import Path
from datetime import datetime, timezone


def _iso(ms):
    try: return datetime.fromtimestamp(int(ms)/1000, tz=timezone.utc).isoformat().replace('+00:00','Z')
    except Exception: return None

def _clean(s): return ' '.join(str(s or '').split()).strip()

def _title_from_child(folder: Path):
    for p in folder.glob('title-*.jsonl'):
        try:
            for line in p.open(encoding='utf-8', errors='replace'):
                d=json.loads(line)
                if d.get('type')=='agent_response':
                    raw=d.get('attrs',{}).get('response','')
                    try:
                        obj=json.loads(raw)
                        for msg in obj if isinstance(obj,list) else []:
                            for part in msg.get('parts',[]):
                                if part.get('type')=='text' and part.get('content'): return _clean(part['content'])
                    except Exception: pass
        except OSError: pass
    return None

def _active_file_from_request(raw):
    text=raw or ''
    try:
        obj=json.loads(text)
        if isinstance(obj,list):
            text='\n'.join(str(x.get('text','')) for x in obj if isinstance(x,dict))
    except Exception: pass
    m=re.search(r"The user's current file is (.*?)(?:\.\s*\n|\n)", text)
    return m.group(1).strip() if m else None

def _workspace_from_discovery(events):
    # Discovery logs expose workspace custom-agent folders. Strip known suffixes.
    for d in events:
        if d.get('type')!='discovery': continue
        details=str(d.get('attrs',{}).get('details',''))
        for m in re.finditer(r'[/\\]([^,\]]+?)[/\\]\.github[/\\](?:agents|instructions|hooks)', details, re.I):
            # Regex above may lose drive prefix; use a broader extraction around the suffix.
            pass
        m=re.search(r'folders:\s*\[(.*?)\]', details)
        if m:
            for item in m.group(1).split(','):
                s=item.strip()
                mm=re.match(r'(.+?)[/\\]\.github[/\\](?:agents|instructions|hooks)$', s, re.I)
                if mm: return mm.group(1).replace('/c:/','C:/').replace('/C:/','C:/')
    return None

def parse_copilot_session(main_path):
    p=Path(main_path); events=[]
    try:
        for line in p.open(encoding='utf-8',errors='replace'):
            try: events.append(json.loads(line))
            except Exception: pass
    except OSError: return [], None
    if not events: return [], None
    sid=next((str(d.get('sid')) for d in events if d.get('sid')), p.parent.name)
    prompt=next((_clean(d.get('attrs',{}).get('content')) for d in events if d.get('type')=='user_message' and d.get('attrs',{}).get('content')), None)
    title=_title_from_child(p.parent) or (prompt[:90]+'…' if prompt and len(prompt)>90 else prompt) or sid[-8:]
    workspace=_workspace_from_discovery(events)
    llms=[d for d in events if d.get('type')=='llm_request' and d.get('attrs',{}).get('debugName')!='title']

    # A debug-log directory is not necessarily a real Copilot task. VS Code creates
    # lightweight session folders during discovery/startup too. Only import sessions
    # that contain both a real user message and at least one non-title model request.
    # This keeps empty debug sessions out of task counts, filters and charts.
    if not prompt or not llms:
        return [], None

    active_file=None
    if llms: active_file=_active_file_from_request(llms[0].get('attrs',{}).get('userRequest',''))
    if not workspace and active_file:
        # Conservative fallback: use parent folder, rather than inventing repo root.
        workspace=str(Path(active_file).parent)
    project=Path(workspace).name if workspace else '(unknown)'
    calls=[]
    for i,d in enumerate(llms,1):
        a=d.get('attrs',{}); inp=int(a.get('inputTokens') or 0); cached=min(inp,int(a.get('cachedTokens') or 0)); out=int(a.get('outputTokens') or 0)
        nano=int(a.get('copilotUsageNanoAiu') or 0)
        calls.append({
            'ide':'VS Code','file_path':str(p.resolve()),'session_id':sid,'correlation_id':None,'message_id':None,'call_index':i,'timestamp':_iso(d.get('ts')),
            'project':project,'workspace':workspace,'active_file':active_file,'task_title':title,'prompt_preview':prompt[:500] if prompt else None,
            'model':a.get('model') or '(unknown)','debug_name':a.get('debugName'),'input_tokens':inp,'cached_input_tokens':cached,
            'fresh_input_tokens':max(0,inp-cached),'output_tokens':out,'reasoning_tokens':int(a.get('reasoningTokens') or 0),'total_tokens':inp+out,'aiu':nano/1_000_000_000,
            'duration_ms':int(d.get('dur') or 0),'ttft_ms':int(a.get('ttft') or 0),'status':d.get('status') or 'unknown','tool_calls_in_turn':0,'tool_names':None,'solution':None,'repo_url':None,'branch':None
        })
    tools=[d for d in events if d.get('type')=='tool_call']
    start=min((d.get('ts') for d in events if d.get('ts')),default=None); end=max((d.get('ts') for d in events if d.get('ts')),default=None)
    task={
        'ide':'VS Code','file_path':str(p.resolve()),'session_id':sid,'correlation_id':None,'message_id':None,'task_title':title,'prompt_preview':prompt[:500] if prompt else None,
        'start_timestamp':_iso(start),'end_timestamp':_iso(end),'day':(_iso(start) or '')[:10] or None,'project':project,'workspace':workspace,
        'active_file':active_file,'model':', '.join(sorted({c['model'] for c in calls})) if calls else '(none)','model_calls':len(calls),
        'tool_calls':len(tools),'input_tokens':sum(c['input_tokens'] for c in calls),'cached_input_tokens':sum(c['cached_input_tokens'] for c in calls),
        'fresh_input_tokens':sum(c['fresh_input_tokens'] for c in calls),'output_tokens':sum(c['output_tokens'] for c in calls),'reasoning_tokens':sum(c['reasoning_tokens'] for c in calls),
        'total_tokens':sum(c['total_tokens'] for c in calls),'aiu':sum(c['aiu'] for c in calls),'duration_seconds':((end-start)/1000 if start and end else None),
        'errors':sum(1 for d in events if d.get('status') not in (None,'ok')),'solution':None,'repo_url':None,'branch':None
    }
    return calls, task
