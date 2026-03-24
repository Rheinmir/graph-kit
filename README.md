# code-graph — Local Code Knowledge Graph for AI Assistants



Phân tích codebase → lưu graph vào SQLite → AI trong IDE query được structure của code.

## Yêu cầu

- Python 3.10+
- Cursor, VS Code + Continue, hoặc Claude Desktop

```bash
pip install -r requirements.txt
```

---

## Cài nhanh (tự động)
Kéo về inject vào model và nhờ agent cài hoặc thủ công:
```bash
python setup.py --repo /path/to/your/project
```

Restart Cursor → xong.

---

## Cài thủ công

### Cursor

Tạo hoặc chỉnh file `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "code-graph": {
      "command": "python",
      "args": ["D:/graph-agent/server.py", "--repo", "D:/your-project", "--watch"]
    }
  }
}
```

Restart Cursor.

### Claude Desktop

Chỉnh file:
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "code-graph": {
      "command": "python",
      "args": ["D:/graph-agent/server.py", "--repo", "D:/your-project", "--watch"]
    }
  }
}
```

Restart Claude Desktop.

---

## Dùng thủ công (không qua IDE)

```bash
# Index repo
python indexer.py /path/to/repo --db ./graph.db

# Chỉ watch (không serve)
python watcher.py /path/to/repo --db ./graph.db

# Server + index + watch
python server.py --repo /path/to/repo --db ./graph.db --watch
```

---

## Các tool MCP

| Tool | Mô tả |
|---|---|
| `search_symbols(query)` | Tìm hàm/class theo tên |
| `get_callers(name)` | Ai gọi hàm này |
| `get_callees(name)` | Hàm này gọi gì |
| `get_file_imports(path)` | File import gì |
| `get_file_symbols(path)` | Tất cả symbols trong file |

---

## Ngôn ngữ hỗ trợ

Python, JavaScript, TypeScript — thêm ngôn ngữ trong `parser.py`.
