"""Drive local_notes_search.py over stdio the way an MCP client does.

    uv run python examples/stdio_demo.py

Indexes the fixture notes next to this file (examples/notes/, written for this
repository - not anyone's real notes) into a THROWAWAY index in a temp directory,
then prints what the server really answers: initialize, tools/list, an index call,
two searches, the file list and a few error paths. Nothing here touches
~/.local-notes-search, and the only network access is the one-time model download.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent
NOTES = Path(__file__).resolve().parent / "notes"


def show(title: str, text: str) -> None:
    print(f"\n### {title}\n{text}")


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lns-demo-") as tmp:
        env = {**os.environ, "LOCAL_NOTES_SEARCH_DB": str(Path(tmp) / "index.db")}
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "local_notes_search.py")], env=env)

        def short(text: str) -> str:
            # Display only: absolute paths become examples/notes/... and $TMP/...;
            # no other character of a tool answer is touched.
            text = text.replace(str(NOTES), "examples/notes").replace(tmp, "$TMP")
            return text.replace("examples/notes\\", "examples/notes/").replace("$TMP\\", "$TMP/")

        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            show("initialize", f"server={init.server_info.name} protocol={init.protocol_version}")

            tools = await session.list_tools()
            show("tools/list", "\n".join(f"{t.name}({', '.join(t.input_schema.get('properties', {}))})" for t in tools.tools))

            async def call(name: str, **args) -> str:
                res = await session.call_tool(name, args)
                return short("".join(getattr(c, "text", "") for c in res.content)) + ("   [isError]" if res.is_error else "")

            show("index_directory(examples/notes)", await call("index_directory", path=str(NOTES)))
            for query in ("what did I decide about the auth redesign?", "how do I cook pasta"):
                show(f'search_notes("{query}", top_k=2)', await call("search_notes", query=query, top_k=2))
            show("list_indexed_files()", await call("list_indexed_files"))
            show('search_notes(query="")', await call("search_notes", query=""))
            show("index_directory($TMP/no-such-dir)", await call("index_directory", path=str(Path(tmp) / "no-such-dir")))
            show("remove_directory(examples/notes)", await call("remove_directory", path=str(NOTES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
