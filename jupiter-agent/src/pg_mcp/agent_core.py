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
    """Translate the MCP tool list into the Anthropic tool schema.

    The final tool carries a cache_control marker so the entire tool block
    (which is identical on every call) is cached. Caching covers the prefix in
    tools -> system -> messages order, so marking the last tool caches all tools.
    """
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema,
        }
        for tool in tools_response.tools
    ]
    if tools:
        tools[-1]["cache_control"] = {"type": "ephemeral"}
    return tools


async def run_claude_turn(
    anthropic_client,
    model: str,
    session: ClientSession,
    tools: list[dict],
    system_prompt: str,
    messages: list[dict],
    return_usage: bool = False,
):
    """
    Process ONE user turn with the Claude backend.

    `messages` must already include the new user message as its last element.
    The list is mutated in place to append the assistant/tool exchange, so the
    caller can persist conversation state across turns.

    Returns:
        (answer_text, trace)                       if return_usage is False
        (answer_text, trace, usage)                if return_usage is True

    where trace is a list of {"tool", "input", "result"} dicts, and usage is a
    dict accumulating token counts across every API call made during this turn:
        {
          "api_calls": int,          # number of model invocations this turn
          "input_tokens": int,       # sum of response.usage.input_tokens
          "output_tokens": int,      # sum of response.usage.output_tokens
          "cache_read_input_tokens": int,
          "cache_creation_input_tokens": int,
        }
    """
    trace: list[dict] = []
    answer_parts: list[str] = []
    usage = {
        "api_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }

    while True:
        response = await anthropic_client.messages.create(
            model=model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools,
            messages=messages,
        )

        # --- accumulate usage (safe if fields are absent) ---
        u = getattr(response, "usage", None)
        if u is not None:
            usage["api_calls"] += 1
            usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
            usage["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_creation_input_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0

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
                    answer_parts.append(block.text)
            messages.append({"role": "user", "content": tool_results})
        else:
            for block in assistant_content:
                if block.type == "text":
                    answer_parts.append(block.text)
            break

    answer = "\n\n".join(p for p in answer_parts if p.strip())
    if return_usage:
        return answer, trace, usage
    return answer, trace

async def run_ollama_turn(
    ollama_client,
    model: str,
    session: ClientSession,
    tools: list[dict],
    messages: list[dict],
    return_usage: bool = False,
):
    """
    Process ONE user turn with the Ollama (local) backend.

    Mirrors run_claude_turn's contract but for Ollama's chat API and message
    format. `tools` must be in Ollama's function schema (see build_ollama_tools
    below). `messages` must already include the system message as messages[0]
    and end with the new user message; it is mutated in place.

    Returns (answer, trace) or (answer, trace, usage) like run_claude_turn.
    Ollama usage fields: prompt_eval_count (input), eval_count (output); no cache.
    """
    trace: list[dict] = []
    usage = {
        "api_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,       # always 0 for Ollama (no caching)
        "cache_creation_input_tokens": 0,   # always 0 for Ollama
    }

    while True:
        response = await ollama_client.chat(
            model=model,
            messages=messages,
            tools=tools,
        )

        usage["api_calls"] += 1
        usage["input_tokens"] += getattr(response, "prompt_eval_count", 0) or 0
        usage["output_tokens"] += getattr(response, "eval_count", 0) or 0

        message = response.message
        assistant_msg = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            assistant_msg["tool_calls"] = message.tool_calls
        messages.append(assistant_msg)

        if not message.tool_calls:
            answer = message.content or ""
            if return_usage:
                return answer, trace, usage
            return answer, trace

        for tool_call in message.tool_calls:
            tool_name = tool_call.function.name
            tool_args = tool_call.function.arguments
            result_text = await call_mcp_tool(session, tool_name, tool_args)
            trace.append({
                "tool": tool_name,
                "input": dict(tool_args) if tool_args else {},
                "result": result_text,
            })
            messages.append({"role": "tool", "content": result_text})

def build_ollama_tools(tools_response) -> list[dict]:
    """Translate the MCP tool list into the Ollama function-tool schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            },
        }
        for tool in tools_response.tools
    ]