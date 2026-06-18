import asyncio
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent.parent / "docs" / "system_prompt.md"


def _load_system_prompt() -> str:
    """Extract the system prompt text from docs/system_prompt.md (between the code fences)."""
    text = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    start = text.find("```\n") + 4
    end = text.find("\n```", start)
    return text[start:end].strip()


async def _call_mcp_tool(session: ClientSession, name: str, args: dict) -> str:
    result = await session.call_tool(name, args)
    content = result.content
    if isinstance(content, list):
        return "\n".join(c.text if hasattr(c, "text") else str(c) for c in content)
    return str(content)


async def _run_claude(session: ClientSession, tools_response, system_prompt: str):
    import anthropic

    anthropic_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema,
        }
        for tool in tools_response.tools
    ]

    messages: list[dict] = []

    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit"):
            break
        if not question:
            continue

        messages.append({"role": "user", "content": question})

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
                        print(f"  [tool: {block.name}]")
                        result_text = await _call_mcp_tool(session, block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                for block in assistant_content:
                    if block.type == "text":
                        print(f"\nAssistant: {block.text}\n")
                break


async def _run_ollama(session: ClientSession, tools_response, system_prompt: str):
    import ollama

    model = os.getenv("OLLAMA_MODEL", "deepseek-r1")
    ollama_client = ollama.AsyncClient()

    tools = [
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

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit"):
            break
        if not question:
            continue

        messages.append({"role": "user", "content": question})

        while True:
            response = await ollama_client.chat(
                model=model,
                messages=messages,
                tools=tools,
            )

            message = response.message
            assistant_msg = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                assistant_msg["tool_calls"] = message.tool_calls
            messages.append(assistant_msg)

            if not message.tool_calls:
                print(f"\nAssistant: {message.content}\n")
                break

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = tool_call.function.arguments
                print(f"  [tool: {tool_name}]")
                result_text = await _call_mcp_tool(session, tool_name, tool_args)
                messages.append({"role": "tool", "content": result_text})


async def main():
    system_prompt = _load_system_prompt()
    backend = os.getenv("LLM_BACKEND", "claude").strip().lower()

    server_params = StdioServerParameters(
        command="python",
        args=["src/pg_mcp/server.py"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_response = await session.list_tools()

            print(f"[backend: {backend}]")

            if backend == "ollama":
                await _run_ollama(session, tools_response, system_prompt)
            elif backend == "claude":
                await _run_claude(session, tools_response, system_prompt)
            else:
                raise ValueError(f"Unknown LLM_BACKEND '{backend}' — expected 'claude' or 'ollama'")


if __name__ == "__main__":
    asyncio.run(main())
