#!/usr/bin/env python3
"""Sync Copilot resources between VS Code and GitHub Copilot CLI.

Handles three resource types at the user/global scope:

  mcp-servers — VS Code mcp.json  <->  CLI mcp-config.json
  agents      — VS Code prompts/*.agent.md  <->  CLI ~/.copilot/agents/
  skills      — ~/.copilot/skills/  <->  ~/.agents/skills/
"""

import argparse
import filecmp
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# ─── Lock file ──────────────────────────────────────────────────────────────

LOCK_FILE = Path.home() / ".copilot" / ".copilot-sync.lock"


class SyncLock:
    """File-based lock to prevent overlapping scheduled runs."""

    def __init__(self):
        self._fd = None

    def acquire(self) -> bool:
        try:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(
                str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
            os.write(self._fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            return False

    def release(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self):
        if not self.acquire():
            print("Another sync is already running. Skipping.")
            sys.exit(0)
        return self

    def __exit__(self, *exc):
        self.release()


# ─── Platform path helpers ──────────────────────────────────────────────────

def _vscode_user_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Code" / "User"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
        )
    return Path.home() / ".config" / "Code" / "User"


def default_mcp_paths() -> tuple[Path, Path]:
    return _vscode_user_dir() / "mcp.json", Path.home() / ".copilot" / "mcp-config.json"


def default_vscode_prompts_dir() -> Path:
    return _vscode_user_dir() / "prompts"


def default_cli_agents_dir() -> Path:
    return Path.home() / ".copilot" / "agents"


def default_copilot_skills_dir() -> Path:
    return Path.home() / ".copilot" / "skills"


def default_agents_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


# ─── Generic I/O ────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent="\t", ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(f".{ts}.bak")
    if path.is_dir():
        shutil.copytree(path, bak)
    else:
        shutil.copy2(path, bak)
    return bak


def copy_file_safe(src: Path, dst: Path, do_backup: bool):
    """Copy *src* to *dst*, creating parents and optionally backing up."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and do_backup:
        backup_file(dst)
    shutil.copy2(src, dst)


def copy_dir_safe(src: Path, dst: Path, do_backup: bool):
    """Copy a directory tree, optionally backing up the destination."""
    if dst.exists():
        if do_backup:
            backup_file(dst)
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


# ═════════════════════════════════════════════════════════════════════════════
#  MCP SERVER SYNC  (JSON-level bidirectional merge)
# ═════════════════════════════════════════════════════════════════════════════

COMMON_FIELDS = ("type", "command", "args", "url", "env", "headers", "tools")


def _normalize_pkg(arg: str) -> str:
    for sep in ("==", ">=", "<=", "~=", "!="):
        if sep in arg:
            return arg.split(sep)[0]
    if arg.startswith("@") and "/" in arg:
        scope_rest = arg[1:]
        scope, pkg_ver = scope_rest.split("/", 1)
        return f"@{scope}/{pkg_ver.split('@')[0]}"
    if "@" in arg:
        return arg.split("@")[0]
    return arg


def _server_fingerprint(cfg: dict) -> tuple[str, str] | None:
    url = cfg.get("url", "")
    if url:
        return ("url", url.rstrip("/").lower())
    cmd = cfg.get("command", "")
    args = cfg.get("args", [])
    if cmd:
        core = [_normalize_pkg(a).lower() for a in args if not a.startswith("-")]
        return ("cmd", f"{cmd.lower()}|{'|'.join(core)}")
    return None


def _build_index(servers: dict):
    by_name: dict[str, str] = {}
    by_fp: dict[tuple, str | None] = {}
    for name, cfg in servers.items():
        by_name[name.lower().strip()] = name
        fp = _server_fingerprint(cfg)
        if fp:
            by_fp[fp] = None if fp in by_fp else name
    return by_name, by_fp


def _find_match(name, cfg, t_name, t_fp):
    norm = name.lower().strip()
    if norm in t_name:
        return t_name[norm]
    fp = _server_fingerprint(cfg)
    if fp and fp in t_fp:
        m = t_fp[fp]
        if m is not None:
            return m
    return None


def _extract_common(cfg: dict) -> dict:
    d = {k: v for k, v in cfg.items() if k in COMMON_FIELDS}
    d.setdefault("tools", ["*"])
    return d


def _vscode_to_cli(cfg: dict, cli_path: Path) -> dict:
    entry = {k: cfg[k] for k in COMMON_FIELDS if k in cfg}
    entry.setdefault("tools", ["*"])
    entry["source"] = "user"
    entry["sourcePath"] = str(cli_path)
    return entry


def _cli_to_vscode(cfg: dict) -> dict:
    entry = {k: cfg[k] for k in COMMON_FIELDS if k in cfg}
    if entry.get("type") == "local":
        entry["type"] = "stdio"
    entry.pop("source", None)
    entry.pop("sourcePath", None)
    return entry


def sync_mcp(vscode_path: Path, cli_path: Path, direction: str,
             dry_run: bool, do_backup: bool) -> list[str]:
    vsc_data = load_json(vscode_path)
    cli_data = load_json(cli_path)
    vsc_servers = vsc_data.get("servers", {})
    cli_servers = cli_data.get("mcpServers", {})

    cli_bn, cli_bf = _build_index(cli_servers)
    matched, m_vsc, m_cli = [], set(), set()
    for vn, vc in vsc_servers.items():
        cm = _find_match(vn, vc, cli_bn, cli_bf)
        if cm:
            matched.append((vn, cm)); m_vsc.add(vn); m_cli.add(cm)

    vsc_only = {k: v for k, v in vsc_servers.items() if k not in m_vsc}
    cli_only = {k: v for k, v in cli_servers.items() if k not in m_cli}

    new_vsc, new_cli = dict(vsc_servers), dict(cli_servers)
    changes: list[str] = []

    for vn, cn in matched:
        if _extract_common(vsc_servers[vn]) != _extract_common(cli_servers[cn]):
            if direction in ("bidirectional", "vscode-to-cli"):
                new_cli[cn] = _vscode_to_cli(vsc_servers[vn], cli_path)
                changes.append(f"  UPDATE  CLI  '{cn}' \u2190 VSCode '{vn}'")
            elif direction == "cli-to-vscode":
                new_vsc[vn] = _cli_to_vscode(cli_servers[cn])
                changes.append(f"  UPDATE  VSCode '{vn}' \u2190 CLI '{cn}'")

    if direction in ("bidirectional", "vscode-to-cli"):
        for n, c in vsc_only.items():
            new_cli[n] = _vscode_to_cli(c, cli_path)
            changes.append(f"  ADD     CLI  '{n}' \u2190 VSCode")

    if direction in ("bidirectional", "cli-to-vscode"):
        for n, c in cli_only.items():
            new_vsc[n] = _cli_to_vscode(c)
            changes.append(f"  ADD     VSCode '{n}' \u2190 CLI")

    if not changes:
        return []

    if not dry_run:
        if do_backup:
            for p in (vscode_path, cli_path):
                bak = backup_file(p)
                if bak:
                    changes.append(f"  Backup: {bak}")
        vsc_data["servers"] = new_vsc
        cli_data["mcpServers"] = new_cli
        if direction in ("bidirectional", "cli-to-vscode"):
            save_json(vscode_path, vsc_data)
        if direction in ("bidirectional", "vscode-to-cli"):
            save_json(cli_path, cli_data)

    return changes


# ═════════════════════════════════════════════════════════════════════════════
#  AGENT SYNC  (file-level copy of *.agent.md)
# ═════════════════════════════════════════════════════════════════════════════
#
# VS Code keeps agent definitions as individual .agent.md files in the
# user prompts directory.  The CLI reads them from ~/.copilot/agents/.
# Same markdown format, so a straight file copy works.

AGENT_SUFFIX = ".agent.md"


def _list_agents(directory: Path) -> dict[str, Path]:
    """Return {filename: path} for every .agent.md in *directory*."""
    if not directory.exists():
        return {}
    return {
        f.name: f
        for f in directory.iterdir()
        if f.is_file() and f.name.endswith(AGENT_SUFFIX)
    }


def sync_agents(
    vscode_dir: Path,
    cli_dir: Path,
    direction: str,
    dry_run: bool,
    do_backup: bool,
) -> list[str]:
    """Sync .agent.md files between VS Code prompts and CLI agents dir."""
    changes: list[str] = []

    vsc_agents = _list_agents(vscode_dir)
    cli_agents = _list_agents(cli_dir)

    vsc_only = set(vsc_agents) - set(cli_agents)
    cli_only = set(cli_agents) - set(vsc_agents)
    common = set(vsc_agents) & set(cli_agents)

    # VS Code → CLI
    if direction in ("bidirectional", "vscode-to-cli"):
        for name in sorted(vsc_only):
            changes.append(f"  ADD     CLI      '{name}' \u2190 VSCode")
            if not dry_run:
                copy_file_safe(vsc_agents[name], cli_dir / name, do_backup)

    # CLI → VS Code
    if direction in ("bidirectional", "cli-to-vscode"):
        for name in sorted(cli_only):
            changes.append(f"  ADD     VSCode   '{name}' \u2190 CLI")
            if not dry_run:
                copy_file_safe(cli_agents[name], vscode_dir / name, do_backup)

    # Common — check content; VS Code takes precedence for bidirectional
    for name in sorted(common):
        if not filecmp.cmp(str(vsc_agents[name]), str(cli_agents[name]), shallow=False):
            if direction in ("bidirectional", "vscode-to-cli"):
                changes.append(f"  UPDATE  CLI      '{name}' \u2190 VSCode")
                if not dry_run:
                    copy_file_safe(vsc_agents[name], cli_dir / name, do_backup)
            elif direction == "cli-to-vscode":
                changes.append(f"  UPDATE  VSCode   '{name}' \u2190 CLI")
                if not dry_run:
                    copy_file_safe(cli_agents[name], vscode_dir / name, do_backup)

    return changes


# ═════════════════════════════════════════════════════════════════════════════
#  SKILL SYNC  (directory-level copy)
# ═════════════════════════════════════════════════════════════════════════════
#
# Skills live in two user-level directories:
#   ~/.copilot/skills/  — user-created skills (read by CLI)
#   ~/.agents/skills/   — managed/installed skills (read by VS Code)
# Syncing makes skills from each location available in the other.

def _list_skill_dirs(directory: Path) -> dict[str, Path]:
    """Return {name: path} for every skill directory (contains SKILL.md)."""
    if not directory.exists():
        return {}
    return {
        d.name: d
        for d in directory.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    }


def _dirs_equal(a: Path, b: Path) -> bool:
    cmp_result = filecmp.dircmp(str(a), str(b))
    if cmp_result.left_only or cmp_result.right_only or cmp_result.diff_files:
        return False
    return all(_dirs_equal(a / s, b / s) for s in cmp_result.common_dirs)


def sync_skills(
    copilot_dir: Path,
    agents_dir: Path,
    direction: str,
    dry_run: bool,
    do_backup: bool,
) -> list[str]:
    """Sync skill directories between ~/.copilot/skills/ and ~/.agents/skills/."""
    changes: list[str] = []

    copilot_skills = _list_skill_dirs(copilot_dir)
    agents_skills = _list_skill_dirs(agents_dir)

    copilot_only = set(copilot_skills) - set(agents_skills)
    agents_only = set(agents_skills) - set(copilot_skills)
    common = set(copilot_skills) & set(agents_skills)

    # ~/.copilot/skills → ~/.agents/skills
    if direction in ("bidirectional", "vscode-to-cli"):
        for name in sorted(copilot_only):
            changes.append(f"  ADD     .agents  '{name}' \u2190 .copilot")
            if not dry_run:
                copy_dir_safe(copilot_skills[name], agents_dir / name, do_backup)

    # ~/.agents/skills → ~/.copilot/skills
    if direction in ("bidirectional", "cli-to-vscode"):
        for name in sorted(agents_only):
            changes.append(f"  ADD     .copilot '{name}' \u2190 .agents")
            if not dry_run:
                copy_dir_safe(agents_skills[name], copilot_dir / name, do_backup)

    # Common — compare trees; .copilot takes precedence (user-created)
    for name in sorted(common):
        if not _dirs_equal(copilot_skills[name], agents_skills[name]):
            if direction in ("bidirectional", "vscode-to-cli"):
                changes.append(f"  UPDATE  .agents  '{name}' \u2190 .copilot")
                if not dry_run:
                    copy_dir_safe(copilot_skills[name], agents_dir / name, do_backup)
            elif direction == "cli-to-vscode":
                changes.append(f"  UPDATE  .copilot '{name}' \u2190 .agents")
                if not dry_run:
                    copy_dir_safe(agents_skills[name], copilot_dir / name, do_backup)

    return changes


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════

RESOURCE_TYPES = ("all", "mcp-servers", "agents", "skills")
DIRECTIONS = ("bidirectional", "vscode-to-cli", "cli-to-vscode")


def main():
    ap = argparse.ArgumentParser(
        description="Sync Copilot resources between VS Code and CLI"
    )
    ap.add_argument(
        "--resource-type", "-r",
        choices=RESOURCE_TYPES, default="all",
        help="Which resource type(s) to sync (default: all)",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes without writing")
    ap.add_argument(
        "--direction",
        choices=DIRECTIONS, default="bidirectional",
        help="Sync direction (default: bidirectional)",
    )
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip creating backup files before writing")
    ap.add_argument("--vscode-path",
                    help="Override path to VS Code mcp.json")
    ap.add_argument("--cli-path",
                    help="Override path to CLI mcp-config.json")
    ap.add_argument("--vscode-prompts-path",
                    help="Override VS Code prompts directory (for agents)")
    ap.add_argument("--cli-agents-path",
                    help="Override CLI agents directory")
    ap.add_argument("--copilot-skills-path",
                    help="Override ~/.copilot/skills/ directory")
    ap.add_argument("--agents-skills-path",
                    help="Override ~/.agents/skills/ directory")

    args = ap.parse_args()
    rtype = args.resource_type
    do_backup = not args.no_backup
    total_changes = 0

    # ── MCP Servers ──────────────────────────────────────────────────────
    if rtype in ("all", "mcp-servers"):
        vsc_mcp, cli_mcp = default_mcp_paths()
        if args.vscode_path:
            vsc_mcp = Path(args.vscode_path)
        if args.cli_path:
            cli_mcp = Path(args.cli_path)

        print("═══ MCP Servers ═══")
        print(f"  VSCode: {vsc_mcp}")
        print(f"  CLI:    {cli_mcp}")
        print(f"  Direction: {args.direction}")

        if not vsc_mcp.exists() and not cli_mcp.exists():
            print("  Neither config file exists. Skipping.\n")
        else:
            changes = sync_mcp(vsc_mcp, cli_mcp, args.direction,
                               args.dry_run, do_backup)
            if changes:
                print(f"  Changes ({len(changes)}):\n")
                for c in changes:
                    print(c)
                total_changes += len(changes)
            else:
                print("  \u2713 MCP servers are in sync.")
        print()

    # ── Agents ───────────────────────────────────────────────────────────
    if rtype in ("all", "agents"):
        vsc_prompts = (
            Path(args.vscode_prompts_path)
            if args.vscode_prompts_path
            else default_vscode_prompts_dir()
        )
        cli_agents = (
            Path(args.cli_agents_path)
            if args.cli_agents_path
            else default_cli_agents_dir()
        )

        print("═══ Agents ═══")
        print(f"  VSCode prompts: {vsc_prompts}")
        print(f"  CLI agents:     {cli_agents}")
        print(f"  Direction: {args.direction}")

        changes = sync_agents(
            vsc_prompts, cli_agents, args.direction, args.dry_run, do_backup
        )
        if changes:
            print(f"  Changes ({len(changes)}):\n")
            for c in changes:
                print(c)
            total_changes += len(changes)
        else:
            print("  \u2713 Agents are in sync.")
        print()

    # ── Skills ───────────────────────────────────────────────────────────
    if rtype in ("all", "skills"):
        copilot_skills = (
            Path(args.copilot_skills_path)
            if args.copilot_skills_path
            else default_copilot_skills_dir()
        )
        agents_skills = (
            Path(args.agents_skills_path)
            if args.agents_skills_path
            else default_agents_skills_dir()
        )

        print("═══ Skills ═══")
        print(f"  .copilot/skills: {copilot_skills}")
        print(f"  .agents/skills:  {agents_skills}")
        print(f"  Direction: {args.direction}")

        changes = sync_skills(
            copilot_skills, agents_skills, args.direction, args.dry_run, do_backup
        )
        if changes:
            print(f"  Changes ({len(changes)}):\n")
            for c in changes:
                print(c)
            total_changes += len(changes)
        else:
            print("  \u2713 Skills are in sync.")
        print()

    # ── Summary ──────────────────────────────────────────────────────────
    if args.dry_run and total_changes:
        print("(dry run \u2014 no files modified)")
    elif total_changes:
        print(f"\u2713 Sync complete. {total_changes} change(s) applied.")
    else:
        print("\u2713 Everything is already in sync.")


if __name__ == "__main__":
    with SyncLock():
        main()
