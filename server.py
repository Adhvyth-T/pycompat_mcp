"""
server.py — Entry point for pycompat_mcp.

Initialises FastMCP, registers all tools, and starts the server.
Nothing else lives here — keep it thin.
"""

from mcp.server.fastmcp import FastMCP
from tools import register_tools

mcp = FastMCP("pycompat_mcp")
register_tools(mcp)

if __name__ == "__main__":
    mcp.run()