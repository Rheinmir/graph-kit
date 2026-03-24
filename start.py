"""
start.py — single entry point, không cần pip install thủ công.

Lần đầu chạy:
    python start.py --repo /path/to/project

Hoặc trên Mac/Linux:
    python3 start.py --repo /path/to/project

Script tự:
  1. Tạo .venv trong thư mục này (nếu chưa có)
  2. Cài requirements vào .venv (nếu chưa cài)
  3. Chạy lại chính nó bên trong .venv
  4. Gọi setup.py để đăng ký MCP vào Cursor/Claude Desktop
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

HERE  = Path(__file__).resolve().parent
VENV  = HERE / ".venv"
REQS  = HERE / "requirements.txt"

# ── Helpers ───────────────────────────────────────────────────────────

def _venv_python() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _venv_pip() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "pip.exe"
    return VENV / "bin" / "pip"


def _in_venv() -> bool:
    return Path(sys.executable).resolve().is_relative_to(VENV.resolve())


def _create_venv():
    print("[start] Creating .venv ...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)


def _install_deps():
    print("[start] Installing dependencies ...")
    subprocess.run([str(_venv_pip()), "install", "-q", "-r", str(REQS)], check=True)
    print("[start] Done.")


def _stamp_path() -> Path:
    return VENV / ".deps_installed"


def _deps_up_to_date() -> bool:
    stamp = _stamp_path()
    if not stamp.exists():
        return False
    return stamp.stat().st_mtime >= REQS.stat().st_mtime


# ── Bootstrap: re-execute inside venv ────────────────────────────────

if not _in_venv():
    if not VENV.exists():
        _create_venv()

    if not _deps_up_to_date():
        _install_deps()
        _stamp_path().touch()

    # re-execute this script inside the venv
    python = _venv_python()
    os.execv(str(python), [str(python)] + sys.argv)
    sys.exit()  # unreachable, but for linters


# ── Now running inside venv ───────────────────────────────────────────

import argparse

ap = argparse.ArgumentParser(
    description="Bootstrap + setup code-graph MCP.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  python start.py --repo /path/to/project     # install
  python start.py --uninstall                 # remove everything
  python start.py --uninstall --keep-dbs      # remove but keep indexed data
""",
)
ap.add_argument("--repo",      default=None, help="Repo to index on server startup")
ap.add_argument("--target",    default="cursor",
                choices=["cursor", "cursor-project", "claude"],
                help="Where to install MCP config (default: cursor)")
ap.add_argument("--dry-run",   action="store_true", help="Preview without writing files")
ap.add_argument("--uninstall", action="store_true", help="Remove graph-kit from this machine")
ap.add_argument("--keep-dbs",  action="store_true", help="Keep ~/.graph-agent/*.db when uninstalling")
args = ap.parse_args()

# ── Uninstall ─────────────────────────────────────────────────────────

def _uninstall(dry_run: bool, keep_dbs: bool):
    import shutil as _shutil

    def _remove(path: Path, label: str):
        if not path.exists():
            print(f"  [skip] {label} not found")
            return
        if dry_run:
            print(f"  would remove {path}")
            return
        if path.is_dir():
            _shutil.rmtree(path)
        else:
            path.unlink()
        print(f"  Removed {label}")

    print("Uninstalling graph-kit...\n")

    # 1. Claude Code CLI
    if _shutil.which("claude"):
        cmd = ["claude", "mcp", "remove", "graph-kit", "-s", "user"]
        if dry_run:
            print(f"  would run: {' '.join(cmd)}")
        else:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                print("  Removed from Claude Code CLI")
            else:
                print(f"  [skip] Claude Code CLI: {r.stderr.strip() or 'not registered'}")

    # 2. Cursor config
    cursor_cfg = Path.home() / ".cursor" / "mcp.json"
    if cursor_cfg.exists() and not dry_run:
        try:
            data = json.loads(cursor_cfg.read_text(encoding="utf-8"))
            data.get("mcpServers", {}).pop("code-graph", None)
            cursor_cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
            print("  Removed from ~/.cursor/mcp.json")
        except Exception as e:
            print(f"  [warn] cursor config: {e}")
    elif cursor_cfg.exists() and dry_run:
        print(f"  would remove 'code-graph' from {cursor_cfg}")

    # 3. graph-index wrapper
    if sys.platform == "win32":
        cmd_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "graph-agent" / "graph-index.bat"
    else:
        cmd_path = Path.home() / ".local" / "bin" / "graph-index"
    _remove(cmd_path, "graph-index command")

    # 4. .venv
    _remove(VENV, ".venv")

    # 5. DBs in ~/.graph-agent/
    from db import _GRAPH_DIR
    if keep_dbs:
        print(f"  [kept] ~/.graph-agent/ (--keep-dbs)")
    else:
        _remove(_GRAPH_DIR, "~/.graph-agent/ (all indexed data)")

    if not dry_run:
        print()
        print("Done. Restart Cursor / Claude Code to apply.")


# ── Install graph-index global command ───────────────────────────────

def _install_global_cmd(dry_run: bool):
    """Install a `graph-index` command so users can index any project globally."""
    python = str(_venv_python())
    indexer = str(HERE / "indexer.py")

    if sys.platform == "win32":
        # Create a .bat in %LOCALAPPDATA%\graph-agent\
        cmd_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "graph-agent"
        cmd_path = cmd_dir / "graph-index.bat"
        content = f'@echo off\n"{python}" "{indexer}" %*\n'
        hint = (f"  Add to PATH: {cmd_dir}\n"
                f"  Or run directly: {cmd_path}")
    else:
        # ~/.local/bin/graph-index (usually already in PATH on Mac/Linux)
        cmd_dir = Path.home() / ".local" / "bin"
        cmd_path = cmd_dir / "graph-index"
        content = f'#!/bin/sh\nexec "{python}" "{indexer}" "$@"\n'
        hint = f"  Make sure ~/.local/bin is in your PATH."

    if dry_run:
        print(f"\n--- would create {cmd_path} ---")
        print(content)
        return

    cmd_dir.mkdir(parents=True, exist_ok=True)
    cmd_path.write_text(content)
    if sys.platform != "win32":
        cmd_path.chmod(0o755)
    print(f"  Installed → {cmd_path}")
    print(hint)


# ── Register with Claude Code CLI (claude mcp add) ────────────────────

def _register_claude_code(dry_run: bool):
    """Register graph-kit into Claude Code via ~/.claude.json (user scope)."""
    python = str(_venv_python())
    cmd = [python, str(HERE / "setup.py"), "--target", "claude-code"]
    if dry_run:
        cmd.append("--dry-run")
        print(f"\n--- would run ---")
        print("  " + " ".join(cmd))
        return

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  Registered with Claude Code (~/.claude.json) ✓")
    else:
        print(f"  [warn] claude-code registration failed: {result.stderr.strip()}")


# ── Dispatch ──────────────────────────────────────────────────────────

if args.uninstall:
    _uninstall(dry_run=args.dry_run, keep_dbs=args.keep_dbs)
    sys.exit(0)

# ── Run setup + install global command ───────────────────────────────

setup_args = [sys.executable, str(HERE / "setup.py")]
setup_args += ["--target", args.target]
if args.dry_run:
    setup_args.append("--dry-run")

subprocess.run(setup_args, check=True)

print()
_install_global_cmd(dry_run=args.dry_run)

print()
_register_claude_code(dry_run=args.dry_run)

# index the first repo if provided
if args.repo and not args.dry_run:
    print()
    print(f"[start] Indexing {args.repo} ...")
    subprocess.run([sys.executable, str(HERE / "indexer.py"), args.repo], check=True)

if not args.dry_run:
    print()
    print("─" * 50)
    print("Next: restart Cursor (or Claude Desktop).")
    print()
    print("To index a project:  graph-index /path/to/project")
    print("                 or: python3 indexer.py /path/to/project")
