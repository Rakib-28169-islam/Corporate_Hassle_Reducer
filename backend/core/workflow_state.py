"""
WorkflowState — The shared state that flows through the LangGraph pipeline.

Every node reads from and writes to this TypedDict.
agent_results uses Annotated[list, operator.add] so parallel nodes
auto-merge their results into a single list.
"""

import operator
from typing import Annotated
from typing_extensions import TypedDict


class WorkflowState(TypedDict, total=False):
    # --- Input (set at START) ---
    user_id: str
    query: str

    # --- Routing (set by route_node) ---
    route: str                    # "GMAIL", "SLACK", "OUTLOOK", "GENERAL"
    routed_by: str                # "keyword" or "llm"

    # --- Classification (set by classify_node) ---
    operations: list[str]         # ["SEARCH", "CALCULATE", ...]
    pipeline: list[dict]          # [{step:1, op:"SEARCH", depends_on:[]}, ...]

    # --- Multi-agent detection (set by classify_node) ---
    is_multi_agent: bool
    target_agents: list[str]      # ["GMAIL", "SLACK"] for multi-agent

    # --- Execution results ---
    # operator.add merges lists from parallel nodes automatically
    agent_results: Annotated[list[dict], operator.add]

    # --- Output (set by answer_node) ---
    final_response: dict          # The WebSocket-ready response dict
    error: str                    # Error message if something failed
