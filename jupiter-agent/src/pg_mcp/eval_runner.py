"""
eval_runner.py — batch evaluation runner for the Jupiter agent.

Reads test cases from a JSON file, runs each case N times through the SAME
agent path the GUI uses (agent_core.run_claude_turn), and records the answer,
the tool-call trace, and token usage for every run. Writes results incrementally
(after each case) so a crash mid-run does not lose completed work.

Run from the repo root (jupiter-agent/):

    uv run python src/pg_mcp/eval_runner.py --cases docs/eval_cases_guardrail.json

Or drop the JSON anywhere and point --cases at it. Backend is read from
LLM_BACKEND in the environment (claude or ollama), same as client.py.

Outputs (written next to the cases file, or to --outdir):
    <cases>_results_<timestamp>.json   — full structured results (for analysis)
    <cases>_results_<timestamp>.md     — human-readable summary (for marking pass/fail)
"""

import os
import sys
import json
import time
import asyncio
import argparse
import datetime
from pathlib import Path
from contextlib import AsyncExitStack

from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import agent_core

load_dotenv()

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent.parent / "docs" / "system_prompt.md"


def load_system_prompt() -> str:
    text = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    start = text.find("```\n") + 4
    end = text.find("\n```", start)
    return text[start:end].strip()


async def run_suite(cases_path: Path, outdir: Path, throttle: float):
    backend = os.getenv("LLM_BACKEND", "claude").strip().lower()
    system_prompt = load_system_prompt()

    with cases_path.open(encoding="utf-8") as f:
        suite = json.load(f)

    cases = suite["cases"]
    runs_per_case = suite.get("runs_per_case", 3)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = cases_path.stem
    json_out = outdir / f"{stem}_results_{timestamp}.json"
    md_out = outdir / f"{stem}_results_{timestamp}.md"

    results = {
        "suite": suite.get("suite", stem),
        "backend": backend,
        "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6") if backend == "claude"
                 else os.getenv("OLLAMA_MODEL", "deepseek-r1:8b"),
        "timestamp": timestamp,
        "runs_per_case": runs_per_case,
        "cases": [],
    }

    # --- launch the MCP server once and reuse the session across all cases ---
    server_params = StdioServerParameters(
        command="python",
        args=["src/pg_mcp/server.py"],
    )

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        tools_response = await session.list_tools()

        if backend == "claude":
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
            tools = agent_core.build_claude_tools(tools_response)
        elif backend == "ollama":
            import ollama
            client = ollama.AsyncClient()
            model = os.getenv("OLLAMA_MODEL", "deepseek-r1:8b")
            tools = agent_core.build_ollama_tools(tools_response)
        else:
            raise ValueError(f"Unknown LLM_BACKEND '{backend}'")

        print(f"[backend: {backend} · model: {model}]")
        print(f"[{len(cases)} cases × {runs_per_case} runs = "
              f"{len(cases) * runs_per_case} agent runs]\n")

        for ci, case in enumerate(cases, 1):
            case_result = {
                "id": case["id"],
                "guardrail": case.get("guardrail", ""),
                "prompt": case["prompt"],
                "expected": case.get("expected", ""),
                "runs": [],
            }
            print(f"[{ci}/{len(cases)}] {case['id']}  ({case.get('guardrail','')})")

            for r in range(1, runs_per_case + 1):
                # FRESH conversation per run so cases never contaminate each other.
                if backend == "claude":
                    messages = [{"role": "user", "content": case["prompt"]}]
                    try:
                        answer, trace, usage = await agent_core.run_claude_turn(
                            client, model, session, tools, system_prompt, messages,
                            return_usage=True,
                        )
                        err = None
                    except Exception as e:  # noqa: BLE001
                        answer, trace, usage, err = "", [], {}, repr(e)
                else:  # ollama
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": case["prompt"]},
                    ]
                    try:
                        answer, trace, usage = await agent_core.run_ollama_turn(
                            client, model, session, tools, messages,
                            return_usage=True,
                        )
                        err = None
                    except Exception as e:  # noqa: BLE001
                        answer, trace, usage, err = "", [], {}, repr(e)

                tools_called = [t["tool"] for t in trace]
                case_result["runs"].append({
                    "run": r,
                    "answer": answer,
                    "tools_called": tools_called,
                    "trace": trace,
                    "usage": usage,
                    "error": err,
                })
                status = "ERR" if err else f"{len(tools_called)} tool call(s)"
                print(f"    run {r}: {status}  tools={tools_called}")

                if throttle:
                    time.sleep(throttle)

            results["cases"].append(case_result)

            # --- write incrementally after each case (crash-safe) ---
            json_out.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            _write_markdown(md_out, results)

        print(f"\nDone. Results:\n  {json_out}\n  {md_out}")


def _write_markdown(path: Path, results: dict):
    lines = [
        f"# Eval results — {results['suite']}",
        "",
        f"- Backend: **{results['backend']}** · model: `{results['model']}`",
        f"- Timestamp: {results['timestamp']}",
        f"- Runs per case: {results['runs_per_case']}",
        "",
        "Mark each case PASS / PARTIAL / FAIL by hand after reading the runs.",
        "",
    ]
    for case in results["cases"]:
        lines.append(f"## {case['id']} — {case['guardrail']}")
        lines.append(f"**Prompt:** {case['prompt']}")
        lines.append("")
        lines.append(f"**Expected:** {case['expected']}")
        lines.append("")
        for run in case["runs"]:
            lines.append(f"### Run {run['run']}  ·  tools: {run['tools_called']}")
            if run["error"]:
                lines.append(f"> ERROR: `{run['error']}`")
            u = run.get("usage") or {}
            if u:
                lines.append(
                    f"> usage: {u.get('api_calls',0)} API calls · "
                    f"in {u.get('input_tokens',0)} · out {u.get('output_tokens',0)} · "
                    f"cache_read {u.get('cache_read_input_tokens',0)} · "
                    f"cache_create {u.get('cache_creation_input_tokens',0)}"
                )
            lines.append("")
            lines.append(run["answer"] or "*(no answer)*")
            lines.append("")
        lines.append("**Verdict:** _____ (PASS / PARTIAL / FAIL)")
        lines.append("")
        lines.append("---")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True, help="Path to the cases JSON file.")
    ap.add_argument("--outdir", default=None,
                    help="Output directory (default: same dir as the cases file).")
    ap.add_argument("--throttle", type=float, default=2.0,
                    help="Seconds to sleep between runs (default 2.0). "
                         "On Scale tier this can be small.")
    args = ap.parse_args()

    cases_path = Path(args.cases).resolve()
    if not cases_path.exists():
        sys.exit(f"Cases file not found: {cases_path}")
    outdir = Path(args.outdir).resolve() if args.outdir else cases_path.parent

    asyncio.run(run_suite(cases_path, outdir, args.throttle))


if __name__ == "__main__":
    main()
