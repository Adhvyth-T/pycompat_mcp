# pycompat_mcp

A Python MCP server that resolves, audits, and looks up PyPI package versions — with full compatibility checking against a target Python runtime.

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
6. Suppresses the block with an actionable message otherwise — hints to increase `max_candidates` when the conflict uses an exact-pin (`==`) specifier

**Input:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `packages` | `list[str]` | ✅ | — | PyPI package names (max 30) |
| `python_version` | `str` | ✅ | — | Target Python e.g. `"3.11"` |
| `max_candidates` | `int` | — | `5` | Versions to evaluate per package (1–20) |

**Output:** Markdown table + `requirements.txt` block + EOL/pre-release warnings + conflict details

---

### `pycompat_package_info`
Fetches full metadata for a single PyPI package (optionally at a specific version).

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

Faster than `pycompat_package_info` — one API call per package (all concurrent), returns only version number, publish date, and PyPI link. No full metadata.

**Input:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `packages` | `list[str]` | ✅ | — | PyPI package names (max 50) |
| `include_prerelease` | `bool` | — | `false` | Also return the latest pre-release when newer than stable |

**Output:** Markdown table — Package / Latest Stable / (Pre-release) / Published / PyPI link

---

## Input validation

| Check | Behaviour |
|-------|-----------|
| Invalid Python version (`abc`) | Pydantic validation error |
| Nonsense minor version (`3.999`) | Rejected — shows valid range `3.0–3.13` |
| EOL Python (`2.7`, `3.0`–`3.9`) | Accepted but warns in all three tools |
| Future Python (`3.14`) | Rejected — see maintainer note in `models.py` |
| Duplicate packages (e.g. `scikit-learn` + `scikit_learn`) | Rejected with normalization error |
| Duplicate pinned names (`Numpy` + `numpy`) | Rejected with normalization error |
| Package not on PyPI | Clear error, other packages continue resolving |
| Version not on PyPI | Distinct error from "package not found" |

---

## File structure

```
pycompat_mcp/
├── server.py       # Entry point — FastMCP init + register_tools() + mcp.run()
├── models.py       # Pydantic input models + shared validators (EOL list, version range)
├── pypi.py         # PyPI HTTP client + all metadata helpers
├── resolver.py     # Greedy resolver + constraint propagation loop
├── tools.py        # MCP tool definitions (4 tools registered against FastMCP)
└── .mcp.json       # Claude Code project config
```

---

## Installation

```bash
pip install "mcp[cli]" httpx packaging
```

---

## Running

### stdio — Claude Desktop / Claude Code

```bash
python server.py
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac):
```json
{
  "mcpServers": {
    "pycompat": {
      "command": "python",
      "args": ["/absolute/path/to/pycompat_mcp/server.py"]
    }
  }
}
```

**Claude Code** (one-liner):
```bash
claude mcp add pycompat python "/absolute/path/to/pycompat_mcp/server.py"
```

Or drop a `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "pycompat": {
      "command": "python",
      "args": ["/absolute/path/to/pycompat_mcp/server.py"]
    }
  }
}
```

### Streamable HTTP — remote access

Edit the last line of `server.py`:
```python
mcp.run(transport="streamable_http", port=8000)
```

---

## Example prompts (Claude Code)

```
Resolve compatible versions for fastapi, sqlalchemy, pydantic, httpx on Python 3.12

Get the latest versions of numpy, pandas, torch, transformers, scikit-learn

Check if numpy==1.24.0 and pandas==2.2.1 are compatible on Python 3.11

Get full metadata for the pydantic package

What's the latest version of opentelemetry-instrumentation-fastapi?
```

---

## Known limitations

| Limitation | Detail |
|------------|--------|
| ABI/runtime incompatibilities | The numpy 2.0 C-ABI break with pandas 1.5 is invisible to PyPI metadata — only declared constraint violations are caught |
| `max_candidates` window | Propagation can only pick from already-fetched versions; tight exact-pin conflicts may need `max_candidates` increased to 10–20 |
| Python 3.14 | Rejected at validation until `VALID_PYTHON_MINORS` in `models.py` is bumped — a commented placeholder is already there |
| Python 3.10 EOL | Scheduled Oct 2026 — commented placeholder in `EOL_PYTHON_VERSIONS` in `models.py` |

---

## Maintainer notes

**When a new Python version ships (e.g. 3.14):**
In `models.py`, bump the upper bound in `VALID_PYTHON_MINORS["3"]` from `range(0, 14)` to `range(0, 15)`.
Track releases at: https://www.python.org/downloads/

**When a Python version reaches EOL (next: 3.10, Oct 2026):**
In `models.py`, uncomment the `"3.10"` line in `EOL_PYTHON_VERSIONS`.
Track the full schedule at: https://devguide.python.org/versions/

---

## Dependencies

```
mcp[cli]>=1.0
httpx>=0.27
packaging>=24.0
```