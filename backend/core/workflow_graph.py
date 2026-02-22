"""LangGraph StateGraph for query processing — routes queries to agents.

Phase 8: Single understand_node replaces classify + parse_intent + route.
Flow: understand → [execute_*] → answer
"""

import re
import logging
from datetime import datetime, timezone

from langgraph.graph import StateGraph, END

from core.workflow_state import WorkflowState
from core.operation_classifier import get_classifier
from core.llm_manager import get_brain_router
from core.query_parser import (
    ParsedIntent, UnifiedUnderstanding, Platform, OperationType,
    UNDERSTAND_PROMPT, post_validate,
)

logger = logging.getLogger(__name__)

AGENT_PLATFORMS = {"GMAIL", "SLACK", "OUTLOOK"}

_MULTI_AGENT_PATTERN = re.compile(
    r"\b(and|both|as well as|also|plus)\b", re.IGNORECASE
)

PLATFORM_KEYWORDS = {
    "GMAIL": ["gmail", "g-mail", "google mail", "email", "mail", "inbox"],
    "SLACK": ["slack", "channel", "message"],
    "OUTLOOK": ["outlook", "calendar", "meeting", "schedule", "event"],
}

# General/greeting keywords — queries matching ONLY these go to GENERAL
_GENERAL_KEYWORDS = [
    "hello", "hi", "hey", "howdy", "sup", "yo",
    "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "bye", "goodbye",
    "who are you", "what are you", "what can you do",
    "whats your agenda", "your agenda",
    "how does this work", "help me", "help",
]


def _detect_multi_agent(query: str) -> list[str]:
    """Return list of matched platforms if query targets 2+, else empty list."""
    if not _MULTI_AGENT_PATTERN.search(query):
        return []

    query_lower = query.lower()
    matched = [
        platform for platform, keywords in PLATFORM_KEYWORDS.items()
        if any(kw in query_lower for kw in keywords)
    ]
    return matched if len(matched) >= 2 else []


# --- Keyword fast-path helpers ---

def _keyword_detect_platform(query_lower: str):
    """Detect platform from keywords. Returns platform string or None."""
    matched = []
    for platform, keywords in PLATFORM_KEYWORDS.items():
        if any(kw in query_lower for kw in keywords):
            matched.append(platform)

    if len(matched) == 1:
        return matched[0]

    # Check for pure general/greeting
    if len(matched) == 0:
        for kw in _GENERAL_KEYWORDS:
            if kw in query_lower:
                return "GENERAL"

    return None  # Ambiguous or no match


def _is_simple_query(query_lower: str) -> bool:
    """Check if query is simple enough for keyword-only understanding.

    Returns False (needs LLM) when the query contains:
    - Topic/concept markers that need semantic understanding
    - Person/sender references that need filter extraction
    - Field specifications that need field extraction
    - Date references that need resolution
    """
    llm_markers = [
        # Topic/concept
        "about", "related to", "regarding", "concerning",
        "describe", "explain", "summarize",
        # Person/sender (space-padded to reduce false matches)
        " from ", " of ", " by ", " sent by ", " to ",
        # Field specifications
        " with ",
        # Date references needing resolution
        "yesterday", "last week", "this week", "last month",
        "today", "tomorrow",
    ]
    return not any(marker in f" {query_lower} " for marker in llm_markers)


def _extract_limit_from_query(query_lower: str):
    """Extract numeric limit from simple patterns like 'last 10', 'top 5'."""
    m = re.search(r'\b(?:last|top|recent|first|latest)\s+(\d+)\b', query_lower)
    if m:
        return int(m.group(1))
    return None


def _try_keyword_fast_path(query: str):
    """Attempt full understanding from keywords alone.
    Returns (UnifiedUnderstanding, ParsedIntent) tuple if confident, else (None, None).
    """
    query_lower = query.lower().strip()
    for ch in "?!.,;:'\"()[]{}":
        query_lower = query_lower.replace(ch, "")

    # Detect platform
    platform = _keyword_detect_platform(query_lower)
    if platform is None:
        return None, None

    # Detect operations via existing classifier
    classification = get_classifier().classify(query)
    ops = classification["operations"]

    # If classifier fell back to default and it's not obviously chat, need LLM
    if ops == ["CHAT"] and classification["classified_by"] == "default":
        if platform != "GENERAL":
            return None, None  # Ambiguous ops for a platform query

    # Complex queries need LLM for keyword/topic extraction
    if not _is_simple_query(query_lower):
        return None, None

    # Extract simple params
    limit = _extract_limit_from_query(query_lower)

    # Determine intent
    intent_type = "search"
    if "CALCULATE" in ops and any(w in query_lower
                                   for w in ["how many", "count", "number of"]):
        intent_type = "count"

    # Build filters from obvious keywords
    filters = {}
    if "unread" in query_lower:
        filters["unread"] = True

    understanding = UnifiedUnderstanding(
        platform=Platform(platform),
        operations=[OperationType(op) for op in ops],
        intent=intent_type,
        limit=limit,
        fields=[],
        sort="date_desc",
        filters=filters,
    )

    intent = post_validate(understanding, query)
    return understanding, intent


# --- LLM structured output ---

async def _llm_understand(query: str):
    """Call LLM with structured output for unified query understanding.
    Returns (UnifiedUnderstanding, ParsedIntent) tuple.
    On failure returns safe defaults.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = UNDERSTAND_PROMPT.format(query=query, today=today)

    try:
        import asyncio
        understanding = await asyncio.to_thread(
            get_brain_router().invoke_structured,
            prompt, UnifiedUnderstanding, "fast", 15,
        )
        intent = post_validate(understanding, query)
        return understanding, intent
    except Exception as e:
        logger.error(f"[understand] LLM structured output failed: {e}")
        # Safe fallback
        understanding = UnifiedUnderstanding(
            platform=Platform.GENERAL,
            operations=[OperationType.CHAT],
        )
        return understanding, ParsedIntent.default()


# --- Graph nodes ---

async def understand_node(state: WorkflowState) -> dict:
    """Unified understanding: keyword fast-path OR LLM structured output.

    Replaces: classify_node + parse_intent_node + route_node.
    Returns: route, operations, pipeline, parsed_intent, is_multi_agent, target_agents.
    """
    query = state["query"]

    # Step 1: Multi-agent detection (cheap regex)
    targets = _detect_multi_agent(query)
    if len(targets) >= 2:
        understanding, intent = _try_keyword_fast_path(query)
        if understanding is None:
            understanding, intent = await _llm_understand(query)

        ops_str = [op.value for op in understanding.operations]
        logger.info(f"[understand] MULTI | ops={ops_str} | "
                    f"targets={targets}")
        return {
            "route": "MULTI",
            "routed_by": "multi_detect",
            "operations": ops_str,
            "pipeline": get_classifier()._build_pipeline(ops_str),
            "is_multi_agent": True,
            "target_agents": targets,
            "parsed_intent": intent.to_dict(),
        }

    # Step 2: Keyword fast-path (for obvious queries)
    understanding, intent = _try_keyword_fast_path(query)
    if understanding is not None:
        ops_str = [op.value for op in understanding.operations]
        logger.info(
            f"[understand] FAST-PATH | route={understanding.platform.value} | "
            f"ops={ops_str} | intent={intent.intent}/{intent.query_type} | "
            f"limit={intent.limit}")
        return {
            "route": understanding.platform.value,
            "routed_by": "keyword_fast_path",
            "operations": ops_str,
            "pipeline": get_classifier()._build_pipeline(ops_str),
            "is_multi_agent": False,
            "target_agents": [],
            "parsed_intent": intent.to_dict(),
        }

    # Step 3: LLM structured output (one call, schema-validated)
    understanding, intent = await _llm_understand(query)
    ops_str = [op.value for op in understanding.operations]
    logger.info(
        f"[understand] LLM | route={understanding.platform.value} | "
        f"ops={ops_str} | intent={intent.intent}/{intent.query_type} | "
        f"limit={intent.limit} | filters={intent.filters}")
    return {
        "route": understanding.platform.value,
        "routed_by": "llm_structured",
        "operations": ops_str,
        "pipeline": get_classifier()._build_pipeline(ops_str),
        "is_multi_agent": False,
        "target_agents": [],
        "parsed_intent": intent.to_dict(),
    }


async def execute_single_agent_node(state: WorkflowState) -> dict:
    """Execute the appropriate agent for single-platform queries."""
    from agents.gmail_agent import GmailAgent
    from agents.slack_agent import SlackAgent
    from agents.outlook_agent import OutlookAgent

    route = state["route"]
    user_id = state.get("user_id", "default")
    query = state["query"]
    operations = state.get("operations")
    parsed_intent = state.get("parsed_intent")

    agent_map = {
        "GMAIL": ("GmailAgent", lambda: GmailAgent(user_id=user_id)),
        "SLACK": ("SlackAgent", lambda: SlackAgent(user_id=user_id)),
        "OUTLOOK": ("OutlookAgent", lambda: OutlookAgent(user_id=user_id)),
    }

    entry = agent_map.get(route)
    if not entry:
        return {"agent_results": [{"agent": route, "error": f"Unknown route: {route}"}]}

    agent_name, factory = entry
    try:
        result = await factory().execute(
            query, operations=operations, parsed_intent=parsed_intent,
        )
        return {"agent_results": [{"agent": agent_name, "result": result}]}
    except Exception as e:
        logger.error(f"[{route}] Agent execution failed: {e}")
        return {"agent_results": [{"agent": agent_name, "error": str(e)}]}


# --- Multi-agent platform nodes (generated via factory) ---

def _make_platform_node(platform, module_path, class_name):
    """Create a multi-agent execution node for a platform."""
    async def node(state: WorkflowState) -> dict:
        from importlib import import_module
        cls = getattr(import_module(module_path), class_name)
        user_id = state.get("user_id", "default")
        parsed_intent = state.get("parsed_intent")
        try:
            result = await cls(user_id=user_id).execute(
                state["query"], operations=state.get("operations"),
                parsed_intent=parsed_intent,
            )
            return {"agent_results": [{"agent": class_name, "result": result}]}
        except Exception as e:
            logger.error(f"[{platform}] Multi-agent failed: {e}")
            return {"agent_results": [{"agent": class_name, "error": str(e)}]}
    node.__name__ = f"execute_{platform.lower()}_node"
    return node


execute_gmail_node = _make_platform_node("GMAIL", "agents.gmail_agent", "GmailAgent")
execute_slack_node = _make_platform_node("SLACK", "agents.slack_agent", "SlackAgent")
execute_outlook_node = _make_platform_node("OUTLOOK", "agents.outlook_agent", "OutlookAgent")


async def merge_node(state: WorkflowState) -> dict:
    """Convergence point after parallel multi-agent execution."""
    return {}


async def handle_general_node(state: WorkflowState) -> dict:
    """Handle GENERAL/CHAT queries using LLM with automatic fallback."""
    query = state["query"]
    try:
        content = get_brain_router().invoke_with_fallback(
            f"You are a corporate assistant. Answer this briefly:\n{query}",
            task_type="general",
        )
        return {
            "agent_results": [{
                "agent": "Supervisor (direct)",
                "result": {
                    "query": query,
                    "operations": ["CHAT"],
                    "results": {"CHAT": {"response": content}},
                    "final_answer": content,
                },
            }],
        }
    except Exception as e:
        logger.error(f"General handler failed (all LLMs): {e}")
        return {"agent_results": [{"agent": "Supervisor (direct)", "error": str(e)}]}


async def answer_node(state: WorkflowState) -> dict:
    """Build final WebSocket response from agent_results."""
    agent_results = state.get("agent_results", [])
    route = state.get("route", "GENERAL")
    query = state.get("query", "")
    routed_by = state.get("routed_by", "unknown")

    if not agent_results:
        return {"final_response": _build_response(
            route, "Supervisor", routed_by, {"error": "No agent produced results"}, query
        )}

    if len(agent_results) == 1:
        return {"final_response": _build_single_response(agent_results[0], route, routed_by, query)}

    return {"final_response": _build_multi_response(agent_results, routed_by, query)}


def _build_response(route, agent, routed_by, data, query, **extra):
    resp = {"type": "response", "route": route, "agent": agent,
            "routed_by": routed_by, "data": data, "query": query}
    resp.update(extra)
    return resp


def _build_single_response(entry, route, routed_by, query):
    return _build_response(
        route, entry.get("agent", "Unknown"), routed_by,
        entry.get("result", entry.get("error", "No result")), query,
    )


def _build_multi_response(agent_results, routed_by, query):
    merged_data = {}
    agents_used = []
    for entry in agent_results:
        name = entry.get("agent", "Unknown")
        agents_used.append(name)
        merged_data[name] = entry.get("result") or {"error": entry.get("error")}

    combined = []
    for name, result in merged_data.items():
        if isinstance(result, dict) and "final_answer" in result:
            combined.append(f"[{name}] {result['final_answer']}")
        elif isinstance(result, dict) and "error" in result:
            combined.append(f"[{name}] Error: {result['error']}")

    return _build_response(
        "MULTI", ", ".join(agents_used), routed_by, merged_data, query,
        combined_answer="\n\n".join(combined),
    )


# --- Routing decision ---

def route_decision(state: WorkflowState):
    """Conditional edge: returns target node name(s) after routing."""
    route = state.get("route", "GENERAL")

    if route == "MULTI":
        targets = state.get("target_agents", [])
        node_map = {"GMAIL": "execute_gmail", "SLACK": "execute_slack", "OUTLOOK": "execute_outlook"}
        nodes = [node_map[t] for t in targets if t in node_map]
        return nodes if nodes else ["handle_general"]

    if route in AGENT_PLATFORMS:
        return ["execute_single_agent"]

    return ["handle_general"]


# --- Build and compile the graph ---

def build_workflow():
    """Build and compile the LangGraph StateGraph.

    Phase 8 flow: understand → [execute_*] → answer
    """
    graph = StateGraph(WorkflowState)

    graph.add_node("understand", understand_node)
    graph.add_node("execute_single_agent", execute_single_agent_node)
    graph.add_node("execute_gmail", execute_gmail_node)
    graph.add_node("execute_slack", execute_slack_node)
    graph.add_node("execute_outlook", execute_outlook_node)
    graph.add_node("merge", merge_node)
    graph.add_node("handle_general", handle_general_node)
    graph.add_node("answer", answer_node)

    graph.set_entry_point("understand")

    graph.add_conditional_edges("understand", route_decision, {
        "execute_single_agent": "execute_single_agent",
        "execute_gmail": "execute_gmail",
        "execute_slack": "execute_slack",
        "execute_outlook": "execute_outlook",
        "handle_general": "handle_general",
    })

    graph.add_edge("execute_single_agent", "answer")
    graph.add_edge("handle_general", "answer")
    graph.add_edge("execute_gmail", "merge")
    graph.add_edge("execute_slack", "merge")
    graph.add_edge("execute_outlook", "merge")
    graph.add_edge("merge", "answer")
    graph.add_edge("answer", END)

    return graph.compile()


_workflow_instance = None

def get_workflow():
    """Get the singleton compiled workflow graph."""
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = build_workflow()
    return _workflow_instance
