"""
app.py — Streamlit GUI for the Jupiter water-quality agent.

Run with:
    uv run streamlit run src/pg_mcp/app.py

This is a thin presentation layer over the existing MCP architecture. It does
NOT bypass the MCP server, it launches the same server.py over stdio, lists the
same tools, and drives the same agent loop (via agent_core.run_claude_turn).
The only thing new is the front-end — a chat interface in which each answer
carries a collapsible "reasoning" panel showing the tool calls the agent made,
plus a standing origin note.
"""

import os
import asyncio
import threading
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import anthropic

import agent_core

load_dotenv()

_ASSETS = Path(__file__).parent / "assets"

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent.parent / "docs" / "system_prompt.md"

# System prompt loading (same extraction logic as the CLI client)

def load_system_prompt() -> str:
    text = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    start = text.find("```\n") + 4
    end = text.find("\n```", start)
    return text[start:end].strip()

# Persistent MCP session across Streamlit reruns.
#
# We run a dedicated asyncio event loop in a background thread and keep the MCP
# session open on it for the lifetime of the app. st.cache_resource ensures this
# is created once and reused on every rerun.
class MCPRuntime:
    """Owns a background event loop and a live MCP session."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.session = None
        self.tools = None
        self._stack = None
        # Bootstrap the session on the background loop and wait for it.
        fut = asyncio.run_coroutine_threadsafe(self._start(), self.loop)
        fut.result()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def _start(self):
        from contextlib import AsyncExitStack
        self._stack = AsyncExitStack()
        server_params = StdioServerParameters(
            command="python",
            args=["src/pg_mcp/server.py"],
        )
        read, write = await self._stack.enter_async_context(stdio_client(server_params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.tools_response = await self.session.list_tools()
        self.tools = agent_core.build_claude_tools(self.tools_response)

    def run_turn(self, anthropic_client, model, system_prompt, messages):
        """Run one agent turn on the background loop, blocking until complete."""
        coro = agent_core.run_claude_turn(
            anthropic_client, model, self.session, self.tools, system_prompt, messages
        )
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result()


@st.cache_resource(show_spinner="Connecting to the Jupiter database…")
def get_runtime() -> MCPRuntime:
    return MCPRuntime()


@st.cache_resource
def get_anthropic_client():
    return anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Page configuration and styling

st.set_page_config(
    page_title="Jupiter MCP Agent",
    page_icon= _ASSETS / "MCP.ico",
    layout="centered",
)

# Light custom styling for a cleaner, less "default Streamlit" look.
st.markdown(
    """
    <style>
      .block-container { padding-top: 2.5rem; max-width: 820px; }
      .jupiter-header { display:flex; align-items:center; gap:0.6rem; }
      .jupiter-header h1 { font-size:1.6rem; margin:0; }
      .jupiter-sub { color:#5b6b7a; font-size:0.92rem; margin-top:0.25rem; }
      .provenance { color:#7a8a99; font-size:0.8rem; border-top:1px solid #e6ebf0;
                    padding-top:0.6rem; margin-top:0.4rem; }
      .tool-name { font-family:ui-monospace,Menlo,monospace; color:#1f497d;
                   font-weight:600; }
      div[data-testid="stExpander"] details summary p { font-size:0.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="jupiter-header">
      <span style="font-size:1.8rem;">💧</span>
      <h1>Jupiter Water-Quality Assistant</h1>
    </div>
    <div class="jupiter-sub">
      Ask about Danish drinking water and groundwater. Answers are grounded in the
      Jupiter database (GEUS) via live database queries — not guessed.
    </div>
    """,
    unsafe_allow_html=True,
)
st.write("")

# Session state
if "history" not in st.session_state:
    # Rendered conversation: list of {"role", "content", "trace"}
    st.session_state.history = []
if "messages" not in st.session_state:
    # Raw Anthropic message list (persisted across turns)
    st.session_state.messages = []

system_prompt = load_system_prompt()
model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Render the reasoning trace inside a collapsible expander

def render_trace(trace: list[dict]):
    if not trace:
        return
    with st.expander(f"🔍 Show reasoning · {len(trace)} database "
                     f"{'query' if len(trace) == 1 else 'queries'}"):
        for i, step in enumerate(trace, 1):
            st.markdown(
                f"**Step {i} — <span class='tool-name'>{step['tool']}</span>**",
                unsafe_allow_html=True,
            )
            if step.get("input"):
                st.caption("Input")
                st.json(step["input"], expanded=False)
            st.caption("Result")
            # Results can be long JSON; show compactly.
            result = step["result"]
            if len(result) > 4000:
                result = result[:4000] + "\n… (truncated)"
            st.code(result, language="json")


PROVENANCE = (
    "Data source: Jupiter database (GEUS). Supply-zone data: Schullehner (2022), "
    "coverage 1978–2019. Explore the data at data.geus.dk. This assistant reports "
    "measurements and legal limits; it does not provide medical or legal advice."
)

# Replay existing conversation

for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        if turn["role"] == "assistant":
            render_trace(turn.get("trace", []))

# Chat input + turn handling

placeholder = "e.g. What are the nitrate levels in my water at Vesterbrogade 1, Copenhagen?"
if prompt := st.chat_input(placeholder):
    # Show the user's message immediately
    st.session_state.history.append({"role": "user", "content": prompt, "trace": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Append to the raw message list and run a turn
    st.session_state.messages.append({"role": "user", "content": prompt})

    runtime = get_runtime()
    anthropic_client = get_anthropic_client()

    with st.chat_message("assistant"):
        with st.spinner("Querying the Jupiter database…"):
            try:
                answer, trace = runtime.run_turn(
                    anthropic_client, model, system_prompt, st.session_state.messages
                )
            except Exception as e:  # noqa: BLE001 — surface any failure to the user
                answer = (f"Sorry — something went wrong while answering: `{e}`. "
                          f"Please try rephrasing, or check that the database is running.")
                trace = []

        st.markdown(answer)
        render_trace(trace)
        st.markdown(f"<div class='provenance'>{PROVENANCE}</div>",
                    unsafe_allow_html=True)

    st.session_state.history.append(
        {"role": "assistant", "content": answer, "trace": trace}
    )

# Sidebar: about + examples + reset

with st.sidebar:
    # Institutional logos (DTU + GEUS)
    dtu = _ASSETS / "DTU_logo.png"
    geus = _ASSETS / "GEUS_logo.svg"
    if dtu.exists() or geus.exists():
        c1, c2 = st.columns(2, vertical_alignment="center")
        if dtu.exists():
            c1.image(str(dtu), width=90)
        if geus.exists():
            c2.image(str(geus), width=90)
        st.caption("A DTU MSc project by Shah Bekhsh · Powered by data from GEUS")
        st.divider()

    st.subheader("About")
    st.markdown(
        "This assistant answers questions about Danish drinking water and "
        "groundwater using the **Jupiter** database maintained by **GEUS**. "
        "It locates the waterworks supplying an address, retrieves quality-"
        "filtered chemistry, and compares it against legal limits."
    )
    st.subheader("Try asking")
    st.markdown(
        "- Nitrate at a specific Danish address\n"
        "- Where your drinking water comes from\n"
        "- How treatment changes the raw groundwater\n"
        "- Whether PFAS has been detected in a city's supply"
    )
    st.divider()
    if st.button("🗑️ Clear conversation", use_container_width=True):
        st.session_state.history = []
        st.session_state.messages = []
        st.rerun()
