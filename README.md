# My Copilot

A collection of custom GitHub Copilot agents, skills, and extensions.

## Agents

| Name | Description |
|------|-------------|
| [troubleshoot-functions-startup](.github/agents/troubleshoot-functions-startup.agent.md) | Diagnose and resolve Azure Functions host startup failures — the 'Function host is not running' errors, 503s, missing functions, and restart loops. Walks through a systematic checklist covering app settings, storage connectivity, host.json, extension bundles, deployment packages, startup code, worker runtime, networking, and platform issues |

## Skills

| Name | Description |
|------|-------------|
| [mcp-sync](.github/skills/mcp-sync) | Sync Copilot configurations between VS Code and CLI — MCP servers, agent definitions, and skills. Keeps resources aligned across tools so adding something in one place makes it available everywhere |

## Extensions

| Name | Commands | Description |
|------|----------|-------------|
| [fast-command](.github/extensions/fast-command) | /fast | Run one easy request on a lightweight model, then restore the foreground model. |
