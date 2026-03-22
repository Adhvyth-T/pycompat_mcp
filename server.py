"""
server.py — Entry point for pycompat_mcp.

Supports two transport modes via the TRANSPORT env var:
  - stdio (default): local subprocess mode for Claude Desktop / Claude Code
  - http:            Streamable HTTP API — connectable by any LLM client
                     that speaks MCP-over-HTTP

Usage:
  stdio:  python server.py
  http:   TRANSPORT=http python server.py
  http:   TRANSPORT=http PORT=9000 python server.py

LLM connector URL (when running in HTTP mode):
  http://<host>:<port>/mcp
"""

import os
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from tools import register_tools

# ---------------------------------------------------------------------------
# Transport config from environment
# ---------------------------------------------------------------------------
TRANSPORT = os.getenv("TRANSPORT", "stdio").lower()
HOST      = os.getenv("HOST", "0.0.0.0")
PORT      = int(os.getenv("PORT", "8000"))

# ---------------------------------------------------------------------------
# Server init — host/port passed here so run() picks them up automatically
# ---------------------------------------------------------------------------
mcp = FastMCP("pycompat_mcp", host=HOST, port=PORT)
register_tools(mcp)

# ---------------------------------------------------------------------------
# Health endpoint — registered on the FastMCP app directly so it shares
# the same lifespan as the MCP session manager (no wrapper Starlette app
# needed, which caused the "Task group not initialized" error)
# ---------------------------------------------------------------------------
@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "pycompat_mcp"})

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if TRANSPORT == "http":
        print(f"Starting pycompat_mcp in HTTP mode")
        print(f"  MCP endpoint : http://{HOST}:{PORT}/mcp")
        print(f"  Health check : http://{HOST}:{PORT}/health")
        mcp.run(transport="streamable-http")
    else:
        print("Starting pycompat_mcp in stdio mode")
        mcp.run()