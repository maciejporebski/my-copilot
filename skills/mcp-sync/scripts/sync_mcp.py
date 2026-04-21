#!/usr/bin/env python3
"""Sync MCP server configurations between GitHub Copilot in VSCode and Copilot CLI.

VSCode config: %APPDATA%/Code/User/mcp.json        (key: "servers")
CLI config:    ~/.copilot/mcp-config.json            (key: "mcpServers")
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# ─── Lock file for scheduler overlap protection ─────────────────────────────

LOCK_FILE = Path.home() / ".copilot" / ".mcp-sync.lock"


class SyncLock:
    """Simple file-based lock to prevent overlapping scheduled runs."""

    def __init__(self):
        self._fd = None

    def acquire(self) -> bool:
        try:
            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
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


# ─── Config file discovery ───────────────────────────────────────────────────

def get_default_paths():
    """Return default config file paths based on the current platform."""
    home = Path.home()
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        vscode_path = Path(appdata) / "Code" / "User" / "mcp.json"
    elif sys.platform == "darwin":
        vscode_path = home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
    else:
        vscode_path = home / ".config" / "Code" / "User" / "mcp.json"

    cli_path = home / ".copilot" / "mcp-config.json"
    return vscode_path, cli_path


# ─── JSON I/O ────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    """Atomic write: write to a temp file in the same directory, then rename."""
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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".{timestamp}.bak")
    shutil.copy2(path, backup_path)
    return backup_path


# ─── Server identity helpers ─────────────────────────────────────────────────

def normalize_package_name(arg: str) -> str:
    """Strip version specifiers from a package argument.

    @playwright/mcp@latest  → @playwright/mcp
    markitdown-mcp==0.0.1a4 → markitdown-mcp
    @azure/mcp@latest       → @azure/mcp
    """
    # pip-style: pkg==1.0, pkg>=2.0, etc.
    for sep in ("==", ">=", "<=", "~=", "!="):
        if sep in arg:
            return arg.split(sep)[0]

    # npm scoped: @scope/pkg@version
    if arg.startswith("@") and "/" in arg:
        scope_rest = arg[1:]          # "scope/pkg@version"
        scope, pkg_ver = scope_rest.split("/", 1)
        pkg = pkg_ver.split("@")[0]   # strip @version if present
        return f"@{scope}/{pkg}"

    # npm unscoped: pkg@version
    if "@" in arg:
        return arg.split("@")[0]

    return arg


def get_server_fingerprint(config: dict) -> tuple[str, str] | None:
    """Generate a comparable identity for a server based on its connection details."""
    url = config.get("url", "")
    if url:
        return ("url", url.rstrip("/").lower())

    cmd = config.get("command", "")
    args = config.get("args", [])
    if cmd:
        core_args = []
        for a in args:
            if a.startswith("-"):
                continue
            core_args.append(normalize_package_name(a).lower())
        return ("cmd", f"{cmd.lower()}|{'|'.join(core_args)}")

    return None


def build_server_index(servers: dict) -> tuple[dict, dict]:
    """Build name-based and fingerprint-based lookup indices.

    If multiple servers share the same fingerprint, that fingerprint is marked
    ambiguous (mapped to None) so we don't silently pick one at random.
    """
    by_name = {}
    by_fingerprint: dict[tuple, str | None] = {}
    for name, config in servers.items():
        by_name[name.lower().strip()] = name
        fp = get_server_fingerprint(config)
        if fp:
            if fp in by_fingerprint:
                by_fingerprint[fp] = None  # ambiguous — more than one server
            else:
                by_fingerprint[fp] = name
    return by_name, by_fingerprint


def find_match(name: str, config: dict, target_by_name: dict, target_by_fp: dict) -> str | None:
    """Find the matching server in a target config, or None.

    Returns None for ambiguous fingerprint matches (multiple servers share
    the same identity) to avoid silently merging the wrong entries.
    """
    norm = name.lower().strip()
    if norm in target_by_name:
        return target_by_name[norm]

    fp = get_server_fingerprint(config)
    if fp and fp in target_by_fp:
        match = target_by_fp[fp]
        if match is not None:  # skip ambiguous fingerprints
            return match

    return None


# ─── Format conversion ───────────────────────────────────────────────────────

COMMON_FIELDS = ("type", "command", "args", "url", "env", "headers", "tools")
VSCODE_ONLY = ("gallery", "version")
CLI_ONLY = ("source", "sourcePath")


def extract_common(config: dict) -> dict:
    """Extract only the fields shared between both formats."""
    return {k: v for k, v in config.items() if k in COMMON_FIELDS}


def vscode_to_cli(config: dict, cli_config_path: Path) -> dict:
    entry = {}
    for k in COMMON_FIELDS:
        if k in config:
            entry[k] = config[k]
    entry.setdefault("tools", ["*"])
    entry["source"] = "user"
    entry["sourcePath"] = str(cli_config_path)
    return entry


def cli_to_vscode(config: dict) -> dict:
    entry = {}
    for k in COMMON_FIELDS:
        if k in config:
            entry[k] = config[k]
    # CLI uses "local" as a type alias; VSCode expects "stdio"
    if entry.get("type") == "local":
        entry["type"] = "stdio"
    # Drop CLI metadata that leaked into common fields
    entry.pop("source", None)
    entry.pop("sourcePath", None)
    return entry


# ─── Core sync logic ─────────────────────────────────────────────────────────

def classify_servers(vscode_servers: dict, cli_servers: dict):
    """Split servers into three groups: VSCode-only, CLI-only, matched pairs."""
    cli_by_name, cli_by_fp = build_server_index(cli_servers)

    matched_pairs = []        # [(vscode_name, cli_name)]
    matched_vsc = set()
    matched_cli = set()

    for vsc_name, vsc_config in vscode_servers.items():
        cli_match = find_match(vsc_name, vsc_config, cli_by_name, cli_by_fp)
        if cli_match:
            matched_pairs.append((vsc_name, cli_match))
            matched_vsc.add(vsc_name)
            matched_cli.add(cli_match)

    vscode_only = {k: v for k, v in vscode_servers.items() if k not in matched_vsc}
    cli_only = {k: v for k, v in cli_servers.items() if k not in matched_cli}
    return vscode_only, cli_only, matched_pairs


def configs_differ(a: dict, b: dict) -> bool:
    """Check whether two server configs differ on common fields.

    Treats absent ``tools`` the same as ``["*"]`` since both sides default to it.
    """
    ca = extract_common(a)
    cb = extract_common(b)
    ca.setdefault("tools", ["*"])
    cb.setdefault("tools", ["*"])
    return ca != cb


def sync(vscode_path: Path, cli_path: Path, direction: str = "bidirectional",
         dry_run: bool = False, no_backup: bool = False) -> bool:
    vscode_data = load_json(vscode_path)
    cli_data = load_json(cli_path)

    vscode_servers = vscode_data.get("servers", {})
    cli_servers = cli_data.get("mcpServers", {})

    vscode_only, cli_only, matched = classify_servers(vscode_servers, cli_servers)

    new_vscode = dict(vscode_servers)
    new_cli = dict(cli_servers)
    changes: list[str] = []

    # ── Matched pairs: resolve conflicts ──
    for vsc_name, cli_name in matched:
        vsc_cfg = vscode_servers[vsc_name]
        cli_cfg = cli_servers[cli_name]
        if configs_differ(vsc_cfg, cli_cfg):
            # VSCode wins in bidirectional and vscode-to-cli
            if direction in ("bidirectional", "vscode-to-cli"):
                new_cli[cli_name] = vscode_to_cli(vsc_cfg, cli_path)
                changes.append(f"  UPDATE  CLI  '{cli_name}' \u2190 VSCode '{vsc_name}'")
            elif direction == "cli-to-vscode":
                new_vscode[vsc_name] = cli_to_vscode(cli_cfg)
                changes.append(f"  UPDATE  VSCode '{vsc_name}' \u2190 CLI '{cli_name}'")

    # ── VSCode-only servers ──
    if direction in ("bidirectional", "vscode-to-cli"):
        for name, cfg in vscode_only.items():
            new_cli[name] = vscode_to_cli(cfg, cli_path)
            changes.append(f"  ADD     CLI  '{name}' \u2190 VSCode")

    # ── CLI-only servers ──
    if direction in ("bidirectional", "cli-to-vscode"):
        for name, cfg in cli_only.items():
            new_vscode[name] = cli_to_vscode(cfg)
            changes.append(f"  ADD     VSCode '{name}' \u2190 CLI")

    # ── Report ──
    if not changes:
        print("\u2713 Configs are already in sync. No changes needed.")
        return True

    print(f"Changes ({len(changes)}):\n")
    for c in changes:
        print(c)

    if dry_run:
        print("\n(dry run \u2014 no files modified)")
        return True

    # ── Backup & write ──
    if not no_backup:
        for path in (vscode_path, cli_path):
            bak = backup_file(path)
            if bak:
                print(f"\n  Backup: {bak}")

    vscode_data["servers"] = new_vscode
    cli_data["mcpServers"] = new_cli

    if direction in ("bidirectional", "cli-to-vscode"):
        save_json(vscode_path, vscode_data)
    if direction in ("bidirectional", "vscode-to-cli"):
        save_json(cli_path, cli_data)

    print(f"\n\u2713 Sync complete. {len(changes)} change(s) applied.")
    return True


# ─── CLI entry point ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync MCP server configs between VSCode and GitHub Copilot CLI"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without modifying files")
    parser.add_argument("--direction",
                        choices=["bidirectional", "vscode-to-cli", "cli-to-vscode"],
                        default="bidirectional",
                        help="Sync direction (default: bidirectional)")
    parser.add_argument("--no-backup", action="store_true",
                        help="Skip creating backup files before writing")
    parser.add_argument("--vscode-path",
                        help="Override path to VSCode mcp.json")
    parser.add_argument("--cli-path",
                        help="Override path to Copilot CLI mcp-config.json")

    args = parser.parse_args()

    vscode_default, cli_default = get_default_paths()
    vscode_path = Path(args.vscode_path) if args.vscode_path else vscode_default
    cli_path = Path(args.cli_path) if args.cli_path else cli_default

    print(f"VSCode config: {vscode_path}")
    print(f"CLI config:    {cli_path}")
    print(f"Direction:     {args.direction}\n")

    if not vscode_path.exists() and not cli_path.exists():
        print("Error: Neither config file exists. Nothing to sync.")
        sys.exit(1)

    if not vscode_path.exists():
        print(f"Note: VSCode config not found at {vscode_path}")
        if args.direction == "vscode-to-cli":
            print("  Cannot sync VSCode \u2192 CLI without a VSCode config.")
            sys.exit(1)
        print("  Will only sync CLI \u2192 VSCode.\n")

    if not cli_path.exists():
        print(f"Note: CLI config not found at {cli_path}")
        if args.direction == "cli-to-vscode":
            print("  Cannot sync CLI \u2192 VSCode without a CLI config.")
            sys.exit(1)
        print("  Will only sync VSCode \u2192 CLI.\n")

    with SyncLock():
        ok = sync(vscode_path, cli_path, args.direction, args.dry_run, args.no_backup)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
