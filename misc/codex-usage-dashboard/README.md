# Codex + Copilot Usage Dashboards v0.6.6

This package contains **two separate dashboards**. Codex and GitHub Copilot data are not mixed.

## Codex dashboard
Run `run_codex_dashboard.bat`.

- Reads Codex rollout JSONL files from `~/.codex/sessions` by default.
- Uses `data/usage_v05.db` by default.
- Displays only Codex tasks, model calls, projects, efficiency, rate limits, and diagnostics.
- The Codex dashboard is unchanged in v0.6.6.

## GitHub Copilot dashboard
Run `run_copilot_dashboard.bat`.

The Copilot dashboard now collects **both VS Code and Visual Studio** Copilot activity into its own database (`data/copilot_usage_v06.db`). Use the IDE filter to show All, VS Code, or Visual Studio.

### VS Code source
`%APPDATA%\Code\User\workspaceStorage\*\GitHub.copilot-chat\debug-logs\*\main.jsonl`

### Visual Studio source
`%LOCALAPPDATA%\Temp\VSGitHubCopilotLogs\*.chat.log`

### Common metrics
For both IDEs the dashboard tracks task/prompt, project/workspace, active file, model turns, tool calls, input tokens, cached input, fresh input, output tokens, reasoning tokens when exposed, total tokens, duration, model, and errors/status.

**AIU/AIC is the only dashboard usage value intentionally unavailable for Visual Studio.** VS Code AIU is read directly from `copilotUsageNanoAiu`; Visual Studio's local `.chat.log` exposes token usage but not the request AIU charge, so Visual Studio rows display `N/A` rather than an estimate.

### Auto refresh
Enable **Auto refresh** in the Copilot sidebar and choose 15 seconds, 30 seconds, 1 minute, 2 minutes, or 5 minutes. Each refresh rescans both IDE log locations. Manual refresh remains available.

Both launchers share the same Python environment only to avoid installing dependencies twice. Their analytics databases and UI views remain separate.


### Interactive Agent Flow (v0.6.6)
Open **Tasks**, select an individual task, then choose the **Agent Flow** tab.

The graph is generated on demand from the task's local raw telemetry and is not stored in the analytics database. It supports:
- self-folding discovery/bootstrap groups;
- click-to-expand/collapse nodes;
- pan and mouse-wheel zoom;
- **Expand all**, **Collapse all**, **Focus model flow**, **Show all**, and **Fit** controls;
- click any node to inspect its details;
- per-model node usage details (input, cached, fresh, output, duration, and VS Code AIU);
- tool-call and agent-response nodes when the source telemetry exposes them.

VS Code can show the richest flow because Agent Debug Logs contain discovery, tool, model, and response events. Visual Studio flow is reconstructed from its `.chat.log`; its per-turn token data is shown, with AIU remaining the only usage metric unavailable for Visual Studio.
