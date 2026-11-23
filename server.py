"""兼容入口：`python server.py`。

等价于 `python -m comsol_mcp.server`，方便 MCP 客户端直接指向本文件。
"""

from comsol_mcp.server import main

if __name__ == "__main__":
    main()
