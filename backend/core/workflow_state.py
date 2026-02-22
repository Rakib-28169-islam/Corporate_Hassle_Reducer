"""WorkflowState — shared state that flows through the LangGraph pipeline."""

import operator
from typing import Annotated
from typing_extensions import TypedDict


class WorkflowState(TypedDict, total=False):
    user_id: str
    query: str

    route: str                    # "GMAIL", "SLACK", "OUTLOOK", "GENERAL"
    routed_by: str                # "keyword" or "llm"

    operations: list[str]         # ["SEARCH", "CALCULATE", ...]
    pipeline: list[dict]          # [{step:1, op:"SEARCH", depends_on:[]}, ...]

    parsed_intent: dict           # ParsedIntent.to_dict() — structured query params

    is_multi_agent: bool
    target_agents: list[str]      # ["GMAIL", "SLACK"] for multi-agent

    # operator.add merges lists from parallel nodes automatically
    agent_results: Annotated[list[dict], operator.add]

    final_response: dict          # WebSocket-ready response dict
    error: str
