from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_TS_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) ')
_BEGIN_RE = re.compile(
    r'Begin sending message \(ConversationId:([^,]+), CorrelationId:([^,]+), MessageId:\s*([^\)]+)\)'
)
_REQ_RE = re.compile(r'Request content:\s*"(.*)"')
_USAGE_RE = re.compile(
    r'Model:\s*([^\r\n]+)\s*\r?\nUsage:\s*\r?\n'
    r'- InputTokenCount:\s*(\d+)\s*\r?\n'
    r'- OutputTokenCount:\s*(\d+)\s*\r?\n'
    r'- prompt_tokens_details_cached_tokens:\s*(\d+)\s*\r?\n'
    r'- reasoning_tokens:\s*(\d+)',
    re.M,
)
_DURATION_RE = re.compile(r'\[CopilotClient EventType\(2\)\] Duration:\s*(\d+)')
_TOOL_RE = re.compile(r'^-\s+[^:]+:\s*([A-Za-z0-9_.-]+)\(', re.M)
_ADDITIONAL_RE = re.compile(r'Additional request context:\s*(\{.*\})')
_CURRENT_FILE_RE = re.compile(r"The user's current file:\s*([^\r\n]+)", re.I)
_DOC_FILE_RE = re.compile(r"Successfully created document context from file '([^']+)'", re.I)
_ACTIVE_WORKSPACE_RE = re.compile(r'Active workspace path changed to\s+(.+?)[\\/]?$', re.I)
_ACTIVE_SOLUTION_RE = re.compile(r'Active solution path changed to\s+(.+)$', re.I)
_LEASE_RE = re.compile(r'(?:Successfully obtained model lease token with model|Reusing existing model lease for model)\s+([^\s]+)', re.I)


def _clean(value):
    return ' '.join(str(value or '').split()).strip()


def _to_iso(ts: str | None):
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=timezone.utc)
        return dt.isoformat().replace('+00:00', 'Z')
    except Exception:
        return None


def _timestamp_at(text: str, pos: int):
    line_start = text.rfind('\n', 0, pos) + 1
    m = _TS_RE.match(text[line_start:])
    return _to_iso(m.group(1)) if m else None


def _safe_json(line: str):
    try:
        return json.loads(line)
    except Exception:
        return {}


def parse_visual_studio_log(log_path):
    """Parse Visual Studio GitHub Copilot chat logs.

    Returns (calls, tasks). The Visual Studio log can contain many conversations,
    unlike a VS Code Agent Debug main.jsonl which represents one session.
    """
    p = Path(log_path)
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return [], []

    # Global workspace/solution hints present near startup. Per-request Additional
    # request context below can override solution/repo details.
    workspace = None
    solution = None
    for line in text.splitlines():
        m = _ACTIVE_WORKSPACE_RE.search(line)
        if m:
            workspace = m.group(1).strip().rstrip('\\/')
        m = _ACTIVE_SOLUTION_RE.search(line)
        if m:
            solution = m.group(1).strip()

    starts = list(_BEGIN_RE.finditer(text))
    calls = []
    tasks = []

    for task_index, sm in enumerate(starts, 1):
        seg_start = sm.start()
        seg_end = starts[task_index].start() if task_index < len(starts) else len(text)
        seg = text[seg_start:seg_end]
        conversation_id, correlation_id, message_id = [x.strip() for x in sm.groups()]
        start_ts = _timestamp_at(text, seg_start)

        req = _REQ_RE.search(seg)
        prompt = _clean(req.group(1)) if req else None
        if not prompt:
            continue

        # Request metadata.
        repo_url = None
        branch = None
        relative_solution = None
        local_workspace = workspace
        add = _ADDITIONAL_RE.search(seg)
        if add:
            obj = _safe_json(add.group(1))
            repo_url = obj.get('repoUrl')
            branch = obj.get('branch')
            relative_solution = obj.get('relativeSolutionPath')

        active_file = None
        fm = _CURRENT_FILE_RE.search(seg) or _DOC_FILE_RE.search(seg)
        if fm:
            active_file = fm.group(1).strip()
            if local_workspace and not re.match(r'^[A-Za-z]:[\\/]', active_file):
                active_file = str(Path(local_workspace) / Path(active_file.replace('\\', '/')))

        project = None
        if relative_solution:
            project = Path(relative_solution).stem
        elif solution:
            project = Path(solution).stem
        elif local_workspace:
            project = Path(local_workspace).name
        else:
            project = '(unknown)'

        # Raw responses are bounded by their own timestamped log entry. Each usage
        # block corresponds to one model turn. Associate the duration event that
        # immediately follows that turn.
        usage_matches = list(_USAGE_RE.finditer(seg))
        task_calls = []
        for call_index, um in enumerate(usage_matches, 1):
            model, inp, out, cached, reasoning = um.groups()
            inp, out, cached, reasoning = map(int, (inp, out, cached, reasoning))
            raw_marker = seg.rfind('Raw response from Copilot chat response request:', 0, um.start())
            call_abs = seg_start + (raw_marker if raw_marker >= 0 else um.start())
            timestamp = _timestamp_at(text, call_abs)

            next_pos = usage_matches[call_index].start() if call_index < len(usage_matches) else len(seg)
            after = seg[um.end():next_pos]
            dm = _DURATION_RE.search(after)
            duration_ms = int(dm.group(1)) if dm else 0

            # Tool calls belong to this response block. Search only from the raw
            # response marker preceding this usage block through the usage block.
            block_start = raw_marker if raw_marker >= 0 else max(0, um.start() - 2500)
            response_block = seg[block_start:um.start()]
            tools = _TOOL_RE.findall(response_block)
            status_match = re.search(r'Status:\s*([^\r\n]+)', response_block)
            status = _clean(status_match.group(1)) if status_match else 'Success'

            row = {
                'ide': 'Visual Studio',
                'file_path': str(p.resolve()),
                'session_id': conversation_id,
                'correlation_id': correlation_id,
                'message_id': message_id,
                'call_index': call_index,
                'timestamp': timestamp,
                'project': project,
                'workspace': local_workspace,
                'solution': solution or relative_solution,
                'active_file': active_file,
                'task_title': prompt[:90] + ('…' if len(prompt) > 90 else ''),
                'prompt_preview': prompt[:500],
                'model': _clean(model),
                'debug_name': None,
                'input_tokens': inp,
                'cached_input_tokens': min(inp, cached),
                'fresh_input_tokens': max(0, inp - min(inp, cached)),
                'output_tokens': out,
                'reasoning_tokens': reasoning,
                'total_tokens': inp + out,
                'aiu': None,  # Not exposed by Visual Studio .chat.log telemetry.
                'duration_ms': duration_ms,
                'ttft_ms': 0,
                'status': status,
                'tool_calls_in_turn': len(tools),
                'tool_names': ', '.join(tools),
                'repo_url': repo_url,
                'branch': branch,
            }
            calls.append(row)
            task_calls.append(row)

        if not task_calls:
            continue

        end_ts = task_calls[-1]['timestamp']
        # Prefer request-to-last-response elapsed time when both timestamps exist.
        duration_seconds = None
        try:
            a = datetime.fromisoformat(start_ts.replace('Z', '+00:00')) if start_ts else None
            b = datetime.fromisoformat(end_ts.replace('Z', '+00:00')) if end_ts else None
            if a and b:
                duration_seconds = max(0.0, (b - a).total_seconds())
        except Exception:
            pass
        if duration_seconds is None:
            duration_seconds = sum(c['duration_ms'] for c in task_calls) / 1000.0

        tool_calls = sum(c['tool_calls_in_turn'] for c in task_calls)
        task = {
            'ide': 'Visual Studio',
            'file_path': str(p.resolve()),
            'session_id': conversation_id,
            'correlation_id': correlation_id,
            'message_id': message_id,
            'task_title': prompt[:90] + ('…' if len(prompt) > 90 else ''),
            'prompt_preview': prompt[:500],
            'start_timestamp': start_ts,
            'end_timestamp': end_ts,
            'day': (start_ts or '')[:10] or None,
            'project': project,
            'workspace': local_workspace,
            'solution': solution or relative_solution,
            'active_file': active_file,
            'model': ', '.join(sorted({c['model'] for c in task_calls})),
            'model_calls': len(task_calls),
            'tool_calls': tool_calls,
            'input_tokens': sum(c['input_tokens'] for c in task_calls),
            'cached_input_tokens': sum(c['cached_input_tokens'] for c in task_calls),
            'fresh_input_tokens': sum(c['fresh_input_tokens'] for c in task_calls),
            'output_tokens': sum(c['output_tokens'] for c in task_calls),
            'reasoning_tokens': sum(c['reasoning_tokens'] for c in task_calls),
            'total_tokens': sum(c['total_tokens'] for c in task_calls),
            'aiu': None,
            'duration_seconds': duration_seconds,
            'errors': sum(1 for c in task_calls if str(c['status']).lower() not in {'success', 'functioncall', 'ok'}),
            'repo_url': repo_url,
            'branch': branch,
        }
        tasks.append(task)

    return calls, tasks
