"""
agent_core.py — backend-agnostic agent turn logic for the Jupiter agent.

This module factors the agent loop out of the terminal client so it can be
driven by either the CLI (client.py) or the Streamlit GUI (app.py). A single
"turn" takes the running MCP session, the tool list, the system prompt, and the
conversation history, processes one user message (including any tool-calling
sub-loop), and RETURNS the final answer together with a structured trace of the
tool calls made. It does no printing and no input() — I/O belongs to the caller.
"""

import os
from mcp import ClientSession


async def call_mcp_tool(session: ClientSession, name: str, args: dict) -> str:
    """Invoke a single MCP tool and return its textual result."""
    result = await session.call_tool(name, args)
    content = result.content
    if isinstance(content, list):
        return "\n".join(c.text if hasattr(c, "text") else str(c) for c in content)
    return str(content)


def build_claude_tools(tools_response) -> list[dict]:
    """Translate the MCP tool list into the Anthropic tool schema."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema,
        }
        for tool in tools_response.tools
    ]


async def run_claude_turn(
    anthropic_client,
    model: str,
    session: ClientSession,
    tools: list[dict],
    system_prompt: str,
    messages: list[dict],
) -> tuple[str, list[dict]]:
    """
    Process ONE user turn with the Claude backend.

    `messages` must already include the new user message as its last element.
    The list is mutated in place to append the assistant/tool exchange, so the
    caller can persist conversation state across turns.

    Returns:
        (answer_text, trace) where trace is a list of dicts:
            {"tool": str, "input": dict, "result": str}
    """
    trace: list[dict] = []
    answer_parts: list[str] = []

    while True:
        response = await anthropic_client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    result_text = await call_mcp_tool(session, block.name, block.input)
                    trace.append({
                        "tool": block.name,
                        "input": block.input,
                        "result": result_text,
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })
                elif block.type == "text" and block.text.strip():
                    # Claude sometimes narrates between tool calls; keep it.
                    answer_parts.append(block.text)
            messages.append({"role": "user", "content": tool_results})
        else:
            for block in assistant_content:
                if block.type == "text":
                    answer_parts.append(block.text)
            break

    return "\n\n".join(p for p in answer_parts if p.strip()), trace
