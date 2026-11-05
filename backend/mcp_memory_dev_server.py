"""Run with python -m backend.mcp_memory_dev_server (stdio transport)."""
from mcp.server.fastmcp import FastMCP
from backend import memory_dev_tools as tools

mcp = FastMCP("memory-dev", instructions="Persistent local notes and restricted project utilities.")
for tool in (tools.save_note, tools.search_notes, tools.get_all_notes,
             tools.read_file, tools.list_directory, tools.run_safe_command,
             tools.fetch_url_content):
    mcp.tool()(tool)

if __name__ == "__main__":
    mcp.run(transport="stdio")
