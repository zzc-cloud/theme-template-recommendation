#!/usr/bin/env python3
"""
Theme Template Recommendation - MySQL MCP 服务器

此服务器当前无工具（所有工具均为冗余）。
如需启用，请添加对应的 MCP 工具。
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("theme-resources")


if __name__ == "__main__":
    mcp.run()
