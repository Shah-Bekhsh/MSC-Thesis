"""
just a tool to extract the real, auto-generated JSON input schemas for every MCP tool,
straight from the FastMCP server object, so the appendix reflects ground
truth rather than hand-transcription.

Run from the repo root, in the project environment:

    uv run python dump_tool_schemas.py

It writes two things:
  - tool_schemas.json  : machine-readable, all tools, full input schema + description
  - tool_schemas.tex   : a ready-to-drop-in LaTeX appendix (one subsection per tool)

Output examples are NOT auto-filled (the tools return bare dict/list[dict] with
no formal output schema). Placeholders are emitted for you to paste a real
sample from your query-result files.
"""

import asyncio
import json

# Import the server module. Adjust the import path if your module layout differs.
# server.py does `import src.pg_mcp.database as db`, so run from repo root.
from src.pg_mcp.server import mcp


async def main():
    # FastMCP exposes the registered tools; list_tools() returns the same Tool
    # objects (name, description, inputSchema) that a connecting client/LLM sees.
    tools = await mcp.list_tools()

    records = []
    for t in tools:
        # Does this tool have a FORMAL output schema? Only populated if the
        # return type hint is structured (Pydantic/TypedDict), not bare dict.
        output_schema = getattr(t, "outputSchema", None)
        records.append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": t.inputSchema,   # the real JSON Schema FastMCP built
            "output_schema": output_schema,  # expected None for `-> dict` returns
        })

    # Report definitively whether ANY tool has a formal output schema.
    with_output = [r["name"] for r in records if r["output_schema"]]
    print("Tools WITH a formal output schema:",
          with_output if with_output else "NONE")
    print("(If NONE, your `-> dict` return hints produce no output schema; "
          "use representative output examples in the appendix instead.)\n")

    # Stable ordering by name so the appendix is deterministic.
    records.sort(key=lambda r: r["name"])

    # 1) machine-readable dump
    with open("tool_schemas.json", "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    # 2) LaTeX appendix
    lines = []
    for r in records:
        name_tex = r["name"].replace("_", r"\_")
        schema_str = json.dumps(r["input_schema"], indent=2, ensure_ascii=False)
        desc = r["description"]
        lines.append(f"\\subsection{{\\texttt{{{name_tex}}}}}")
        lines.append("")
        lines.append("\\noindent\\textbf{Description.}")
        lines.append("")
        lines.append(desc if desc else "\\todo{No description found.}")
        lines.append("")
        lines.append("\\noindent\\textbf{Input schema.}")
        lines.append("\\begin{lstlisting}[language=json]")
        lines.append(schema_str)
        lines.append("\\end{lstlisting}")
        lines.append("")
        lines.append("\\noindent\\textbf{Representative output.}")
        lines.append("\\begin{lstlisting}[language=json]")
        lines.append("% TODO: paste a real sample output for "
                     f"{r['name']} from your query-result files.")
        lines.append("\\end{lstlisting}")
        lines.append("")
    with open("tool_schemas.tex", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Wrote {len(records)} tools to tool_schemas.json and tool_schemas.tex")
    for r in records:
        n_props = len(r["input_schema"].get("properties", {}))
        print(f"  {r['name']:28s} {n_props} input field(s)")


if __name__ == "__main__":
    asyncio.run(main())
