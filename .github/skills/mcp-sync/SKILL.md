---
name: mcp-sync
description: "Sync Copilot configurations between VS Code and CLI — MCP servers, agent definitions, and skills. Keeps resources aligned across tools so adding something in one place makes it available everywhere. WHEN: sync MCP servers, sync MCP config, sync agents, sync skills, sync copilot config, copilot sync, keep copilot in sync, synchronize copilot resources, mcp-config.json, mcp.json sync, sync agent files, sync agent definitions, sync copilot skills, scheduled sync, reconcile configs, VS Code to CLI sync, CLI to VS Code sync, copilot resource management, .agent.md sync, share copilot setup across tools, sync .copilot and .agents directories."
---

# Copilot Config Sync

Synchronizes Copilot resources between VS Code and GitHub Copilot CLI. Each tool stores its configuration in different locations — this skill bridges them so resources added in one tool are available in the other.

## Resource Types

| Resource | Location A | Location B | Conflict winner |
|----------|-----------|-----------|-----------------|
| MCP Servers | VS Code `%APPDATA%/Code/User/mcp.json` | CLI `~/.copilot/mcp-config.json` | VS Code |
| Agents | VS Code `%APPDATA%/Code/User/prompts/*.agent.md` | CLI `~/.copilot/agents/*.agent.md` | VS Code |
| Skills | `~/.copilot/skills/` (user-created) | `~/.agents/skills/` (managed/installed) | `.copilot` |

**Why these three?**

- **MCP Servers** — Each tool keeps a separate config file with different JSON formats. The sync merges them intelligently, matching servers by name, URL, or command fingerprint.
- **Agents** — VS Code stores `.agent.md` files in its prompts directory; the CLI reads them from `~/.copilot/agents/`. Same markdown format, so file-level copy works directly.
- **Skills** — User-created skills live in `~/.copilot/skills/`, while managed/installed skills live in `~/.agents/skills/`. The sync copies skills that only exist in one location to the other so both tools see all skills.

## How to Sync

Always preview first with `--dry-run`:

```bash
python <this-skill-path>/scripts/sync.py --dry-run
```

If the output looks right, run without `--dry-run`:

```bash
python <this-skill-path>/scripts/sync.py
```

The script creates timestamped backups before modifying anything. Pass `--no-backup` to skip backups.

### Syncing a specific resource type

```bash
python <this-skill-path>/scripts/sync.py --resource-type mcp-servers --dry-run
python <this-skill-path>/scripts/sync.py --resource-type agents --dry-run
python <this-skill-path>/scripts/sync.py --resource-type skills --dry-run
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `--resource-type`, `-r` | `all`, `mcp-servers`, `agents`, or `skills` | `all` |
| `--dry-run` | Preview changes without writing | off |
| `--direction` | `bidirectional`, `vscode-to-cli`, or `cli-to-vscode` | `bidirectional` |
| `--no-backup` | Don't create backup files | off |
| `--vscode-path` | Override VS Code `mcp.json` path | auto-detected |
| `--cli-path` | Override CLI `mcp-config.json` path | auto-detected |
| `--vscode-prompts-path` | Override VS Code prompts directory | auto-detected |
| `--cli-agents-path` | Override CLI agents directory | auto-detected |
| `--copilot-skills-path` | Override `~/.copilot/skills/` | auto-detected |
| `--agents-skills-path` | Override `~/.agents/skills/` | auto-detected |

### Direction semantics per resource type

| Direction | MCP Servers | Agents | Skills |
|-----------|------------|--------|--------|
| `bidirectional` | VS Code ↔ CLI | VS Code prompts ↔ CLI agents | `.copilot` ↔ `.agents` |
| `vscode-to-cli` | VS Code → CLI | VS Code prompts → CLI agents | `.copilot` → `.agents` |
| `cli-to-vscode` | CLI → VS Code | CLI agents → VS Code prompts | `.agents` → `.copilot` |

## Sync Behavior

### MCP Servers (JSON-level merge)

Servers in the two configs often have different names. The script matches them using:

1. **Name match** — case-insensitive exact match
2. **URL match** — for HTTP-type servers, compare the endpoint URL
3. **Command match** — for stdio-type servers, compare command and package name (ignoring version suffixes)

When the same server exists in both configs with different settings, **VS Code takes precedence**. Each server retains its existing name in each config.

Format conversion details:
- **VS Code → CLI**: Adds `tools: ["*"]` and `source`/`sourcePath` metadata. Drops `gallery` and `version`.
- **CLI → VS Code**: Strips `source`, `sourcePath`. Maps `"local"` type to `"stdio"`.
- Sensitive fields (`headers`, `env`) are synced as-is.
- VS Code `inputs` (secret prompts) are preserved untouched.

### Agents (file-level copy)

Copies `.agent.md` files between VS Code's user prompts directory and the CLI's agents directory. The file format is identical in both locations (markdown with optional YAML frontmatter), so no conversion is needed.

- Files are matched by filename
- When the same file exists in both locations with different content, **VS Code takes precedence** (in bidirectional mode)

### Skills (directory-level copy)

Copies skill directories between `~/.copilot/skills/` and `~/.agents/skills/`. A valid skill directory must contain a `SKILL.md` file.

- Skills are matched by directory name
- When the same skill exists in both locations with different content, **`.copilot` takes precedence** (user-created wins over managed)
- Entire skill directories are copied (preserving internal structure)

## MCP-Only Sync (backward compatible)

The original MCP-only sync script is still available:

```bash
python <this-skill-path>/scripts/sync_mcp.py --dry-run
```

This is equivalent to `sync.py --resource-type mcp-servers` and works exactly as before.

## Setting Up Scheduled Sync

To run the sync automatically on a schedule, use the setup script:

```powershell
powershell -ExecutionPolicy Bypass -File "<this-skill-path>/scripts/setup_scheduler.ps1"
```

This creates a Windows Task Scheduler task that runs the full sync (all resource types) every 60 minutes.

| Parameter | Description | Default |
|-----------|-------------|---------|
| `-IntervalMinutes` | How often to sync | 60 |
| `-TaskName` | Task Scheduler task name | `CopilotSync` |
| `-Remove` | Remove the scheduled task | — |
| `-PythonPath` | Path to Python executable | auto-detected |
| `-ResourceType` | Which resource types to sync | `all` |

To remove the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File "<this-skill-path>/scripts/setup_scheduler.ps1" -Remove
```

## Workflow

When the user asks to sync their Copilot configuration:

1. Run the sync script with `--dry-run` first and show the user the planned changes
2. If they approve, run without `--dry-run` to apply
3. If they only want to sync a specific resource type, use `--resource-type`
4. If they ask about scheduling, run the scheduler setup script with their preferred interval
5. If they want to remove the schedule, run the scheduler script with `-Remove`

Replace `<this-skill-path>` with the actual absolute path to this skill's directory.
