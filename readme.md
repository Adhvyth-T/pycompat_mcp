# pycompat_mcp

A Python MCP server that resolves, audits, and looks up PyPI package versions with full compatibility checking against a target Python runtime. Runs as a local stdio process or a remote HTTP API server.

---

## Tools (4)

### `pycompat_resolve`
Resolves the most recent mutually compatible versions for a list of packages and a target Python version.

**How it works:**
1. Fetches live metadata from PyPI for each package
2. Filters versions by `requires_python` against the **target** Python (not the host interpreter)
3. Picks the newest passing version per package (greedy, newest-first)
4. Runs **constraint propagation** — when a conflict is detected, scans alternate candidates to fix it automatically (up to 5 rounds)
5. Emits a `requirements.txt` block only when the resolved set is fully conflict-free
6. Suppresses the block with an actionable hint otherwise — suggests increasing `max_candidates` when the conflict uses an exact-pin (`==`) specifier

**Input:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `packages` | `list[str]` | ✅ | — | PyPI package names (max 30) |
| `python_version` | `str` | ✅ | — | Target Python e.g. `"3.11"` |
| `max_candidates` | `int` | — | `5` | Versions to evaluate per package (1–20) |

**Output:** Markdown table + `requirements.txt` block + EOL/pre-release warnings + conflict details

---

### `pycompat_package_info`
Fetches full metadata for a single PyPI package, optionally at a specific version.

**Input:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `package` | `str` | ✅ | PyPI package name |
| `version` | `str` | — | Specific version; omit for latest |

**Output (JSON):**
```json
{
  "name": "numpy",
  "version": "2.4.3",
  "summary": "...",
  "requires_python": ">=3.10",
  "license": "BSD-3-Clause",
  "author": "...",
  "home_page": "...",
  "requires_dist": ["..."],
  "latest_versions": ["2.4.3", "2.4.2", "..."],
  "only_prerelease_versions": false
}
```

---

### `pycompat_check_pinned`
Audits an existing set of pinned versions for mutual compatibility and Python runtime compatibility.

**Input:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `pinned` | `dict[str, str]` | ✅ | `{"numpy": "1.26.4", "pandas": "2.2.1"}` |
| `python_version` | `str` | ✅ | Target Python e.g. `"3.11"` |

**Output:** Markdown table — per-package Python OK/fail + any cross-package constraint violations

---

### `pycompat_latest_versions`
Gets the latest stable version for up to 50 packages in a single lightweight call.

Faster than `pycompat_package_info` — one concurrent API call per package, returns only version number, publish date, and PyPI link.

**Input:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `packages` | `list[str]` | ✅ | — | PyPI package names (max 50) |
| `include_prerelease` | `bool` | — | `false` | Also return latest pre-release when newer than stable |

**Output:** Markdown table — Package / Latest Stable / (Pre-release) / Published / PyPI link

---

## Input validation

| Check | Behaviour |
|-------|-----------|
| Invalid Python version (`abc`) | Pydantic validation error |
| Nonsense minor version (`3.999`) | Rejected — shows valid range `3.0–3.13` |
| EOL Python (`2.7`, `3.0`–`3.9`) | Accepted but warns in all tools |
| Future Python (`3.14`) | Rejected — see maintainer note in `models.py` |
| Duplicate packages (`scikit-learn` + `scikit_learn`) | Rejected with normalization error |
| Duplicate pinned names (`Numpy` + `numpy`) | Rejected with normalization error |
| Package not on PyPI | Clear error, other packages continue resolving |
| Version not on PyPI | Distinct error from "package not found" |

---

## Transport modes

| `TRANSPORT` env var | Mode | Use case |
|---|---|---|
| `stdio` (default) | Local subprocess | Claude Desktop, Claude Code local |
| `http` | Streamable HTTP API at `/mcp` | Any LLM client that speaks MCP-over-HTTP |

---

## Installation

```bash
pip install -r requirements.txt
```

**`requirements.txt`:**
```
mcp[cli]>=1.26.0
httpx>=0.28.1
packaging>=26.0
uvicorn>=0.42.0
starlette>=0.52.1
```

---

## Running

### stdio (default)
```bash
python server.py
```

### HTTP mode
```bash
# Windows (PowerShell)
$env:TRANSPORT="http"; python server.py

# Linux / macOS
TRANSPORT=http python server.py

# Custom port
TRANSPORT=http PORT=9000 python server.py
```

Starts at:
```
MCP endpoint : http://0.0.0.0:8000/mcp
Health check : http://0.0.0.0:8000/health
```

### Docker
```bash
docker compose up --build
```

---

## Connecting LLM clients

### Claude Code — stdio (local)
```bash
claude mcp add pycompat python "/absolute/path/to/server.py"
```

Or via `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "pycompat": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

### Claude Code — HTTP (remote)
```bash
claude mcp add --transport http pycompat http://localhost:8000/mcp
```

Or via `.mcp.json`:
```json
{
  "mcpServers": {
    "pycompat": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### Claude Desktop — HTTP (via mcp-remote proxy)
Claude Desktop speaks stdio only — use `mcp-remote` as a bridge:
```json
{
  "mcpServers": {
    "pycompat": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

### LangChain / LangGraph
```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "pycompat": {
        "url": "http://localhost:8000/mcp",
        "transport": "streamable_http",
    }
})
tools = await client.get_tools()
```

### OpenAI / LiteLLM
```python
response = litellm.completion(
    model="gpt-4o",
    mcp_servers=[{"url": "http://localhost:8000/mcp"}],
    messages=[{"role": "user", "content": "Resolve fastapi and pydantic for Python 3.11"}]
)
```

---

## File structure

```
pycompat_mcp/
├── server.py         # Entry point — transport selection, health endpoint, mcp.run()
├── models.py         # Pydantic input models + shared validators (EOL list, version range)
├── pypi.py           # PyPI HTTP client + all metadata helpers
├── resolver.py       # Greedy resolver + constraint propagation loop
├── tools.py          # MCP tool definitions (4 tools)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .mcp.json         # Claude Code project config (HTTP mode)
└── .gitignore
```

---

## Known limitations

| Limitation | Detail |
|------------|--------|
| ABI/runtime incompatibilities | The numpy 2.0 C-ABI break with pandas 1.5 is invisible to PyPI metadata — only declared constraint violations are caught |
| `max_candidates` window | Propagation only picks from already-fetched versions; tight exact-pin conflicts may need `max_candidates` raised to 10–20 |
| Python 3.14 | Rejected at validation until `VALID_PYTHON_MINORS` in `models.py` is bumped — a commented placeholder is already there |
| Python 3.10 EOL | Scheduled Oct 2026 — commented placeholder already in `EOL_PYTHON_VERSIONS` in `models.py` |

---

## Maintainer notes

**When Python 3.14 ships (expected Oct 2025):**
In `models.py`, bump `VALID_PYTHON_MINORS["3"]` from `range(0, 14)` → `range(0, 15)`.
Track: https://www.python.org/downloads/

**When Python 3.10 reaches EOL (Oct 2026):**
In `models.py`, uncomment `"3.10"` in `EOL_PYTHON_VERSIONS`.
Track: https://devguide.python.org/versions/

---

## Dependencies

| Package | Version |
|---------|---------|
| `mcp[cli]` | ≥ 1.26.0 |
| `httpx` | ≥ 0.28.1 |
| `packaging` | ≥ 26.0 |
| `uvicorn` | ≥ 0.42.0 |
| `starlette` | ≥ 0.52.1 |
