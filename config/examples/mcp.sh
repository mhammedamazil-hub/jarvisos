# MCP server examples. Tools are auto-classified READ/WRITE/DELETE/SEND/EXECUTE/SYSTEM_CHANGE
# and routed through the same approval + audit pipeline as every other action.
jarvis mcp add jarvis-system --command "python3 -m jarvis.mcp.servers.system_info"   # bundled
jarvis mcp add github   --command "npx -y @modelcontextprotocol/server-github"       # needs GITHUB_PERSONAL_ACCESS_TOKEN env
jarvis mcp add files    --command "npx -y @modelcontextprotocol/server-filesystem $HOME/Documents"
jarvis mcp add remote   --url https://mcp.example.com/mcp                             # streamable HTTP
jarvis mcp test github
jarvis mcp disable-tool github.delete_repository
