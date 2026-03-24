"""
setup.py — tự động cài code-graph MCP vào Cursor (hoặc Claude Desktop)

Chạy:
    python setup.py                        # cài vào Cursor (global)
    python setup.py --repo D:/my-project   # kèm repo mặc định
    python setup.py --target claude        # cài vào Claude Desktop
    python setup.py --target cursor-project  # cài vào .cursor/mcp.json của project hiện tại
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable


def cursor_global_config() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Cursor" / "User" / "globalStorage" / "cursor-mcp" / "mcp.json"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "cursor-mcp" / "mcp.json"
    else:
        return Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "cursor-mcp" / "mcp.json"


def cursor_dotfile_config() -> Path:
    """~/.cursor/mcp.json — works in most Cursor versions"""
    return Path.home() / ".cursor" / "mcp.json"


def cursor_project_config() -> Path:
    return Path.cwd() / ".cursor" / "mcp.json"


def claude_desktop_config() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def build_server_entry(repo: str | None) -> dict:
    args = [str(HERE / "server.py"), "--watch"]
    if repo:
        args += ["--repo", str(Path(repo).resolve())]
    return {"command": PYTHON, "args": args}


def patch_config(config_path: Path, entry: dict, dry_run: bool):
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  [!] Could not parse existing {config_path}, will overwrite.")

    # Claude Desktop wraps under "mcpServers"; Cursor uses top-level "mcpServers"
    existing.setdefault("mcpServers", {})
    existing["mcpServers"]["code-graph"] = entry

    output = json.dumps(existing, indent=2, ensure_ascii=False)

    if dry_run:
        print(f"\n--- would write to {config_path} ---")
        print(output)
        return

    config_path.write_text(output, encoding="utf-8")
    print(f"  Written → {config_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None,
                    help="Default repo path to index on startup (optional)")
    ap.add_argument("--target", default="cursor",
                    choices=["cursor", "cursor-project", "claude"],
                    help="Where to install: cursor (global), cursor-project, claude (Claude Desktop)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would be written without touching files")
    args = ap.parse_args()

    entry = build_server_entry(args.repo)

    if args.target == "cursor":
        # Try ~/.cursor/mcp.json first (most universal)
        config_path = cursor_dotfile_config()
    elif args.target == "cursor-project":
        config_path = cursor_project_config()
    else:
        config_path = claude_desktop_config()

    print(f"\nInstalling code-graph MCP server")
    print(f"  Target  : {args.target}")
    print(f"  Config  : {config_path}")
    print(f"  Python  : {PYTHON}")
    print(f"  Server  : {HERE / 'server.py'}")
    if args.repo:
        print(f"  Repo    : {args.repo}")
    else:
        print(f"  Repo    : (not set — you can pass --repo or set later in config)")

    patch_config(config_path, entry, dry_run=args.dry_run)

    if not args.dry_run:
        print()
        print("Done! Next steps:")
        if args.target in ("cursor", "cursor-project"):
            print("  1. Restart Cursor")
            print("  2. Open a chat → Claude should now have code-graph tools")
            if not args.repo:
                print("  3. Set --repo in the config or run:")
                print(f"       python {HERE / 'indexer.py'} /path/to/your/repo")
        else:
            print("  1. Restart Claude Desktop")
            print("  2. Start a new conversation — tools will appear automatically")


if __name__ == "__main__":
    main()
