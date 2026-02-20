"""
=============================================================================
WORKFLOW GRAPH — LangGraph StateGraph for query processing
=============================================================================

Replaces the ad-hoc supervisor dispatch with a proper async graph:

Single-agent (90%):
  START -> classify -> route -> execute_agent -> answer -> END

Multi-agent (10%):
  START -> classify -> route -> [execute_gmail + execute_slack + ...] -> merge -> answer -> END

General/Chat:
  START -> classify -> route -> handle_general -> answer -> END

KEY DESIGN DECISIONS:
  - Graph is a singleton; agents are created per-invocation (they hold user state)
  - Each platform = separate node (LangGraph needs named nodes for parallel)
  - Multi-agent requires conjunction ("and", "both") to prevent false positives
  - answer_node produces exact WebSocket format so main.py just sends it
=============================================================================
"""

import re
import logging

from langgraph.graph import StateGraph, END

from core.workflow_state import WorkflowState
from core.operation_classifier import get_classifier
from core.routing import get_router
from core.llm_manager import get_brain_router

logger = logging.getLogger(__name__)

# Platform agents — imported lazily inside nodes to avoid circular imports
# and because agents hold per-user state (created fresh per invocation).

AGENT_PLATFORMS = {"GMAIL", "SLACK", "OUTLOOK"}

# Conjunction patterns that signal multi-agent intent
_MULTI_AGENT_PATTERN = re.compile(
    r"\b(and|both|as well as|also|plus)\b", re.IGNORECASE
)


# =============================================================================
# HELPER: Detect multi-agent queries
# =============================================================================

def _detect_multi_agent(query: str) -> list[str]:
    """
    Detect if a query targets multiple platforms.

    Rules:
      1. Must contain a conjunction word ("and", "both", "also", ...)
      2. Must mention 2+ platform keywords
      3. Returns list of matched platforms, or empty list

    Examples:
      "check gmail and slack" -> ["GMAIL", "SLACK"]
      "check my emails"       -> []  (single platform, no conjunction)
      "gmail slack outlook"   -> []  (no conjunction, ambiguous)
    """
    if not _MULTI_AGENT_PATTERN.search(query):
        return []

    query_lower = query.lower()
    matched = []

    # Check for platform mentions
    platform_keywords = {
        "GMAIL": ["gmail", "g-mail", "google mail", "email", "mail", "inbox"],
        "SLACK": ["slack", "channel", "message"],
        "OUTLOOK": ["outlook", "calendar", "meeting", "schedule", "event"],
    }

    for platform, keywords in platform_keywords.items():
        if any(kw in query_lower for kw in keywords):
            matched.append(platform)

    return matched if len(matched) >= 2 else []


# =============================================================================
# NODE 1: classify_node
# =============================================================================

async def classify_node(state: WorkflowState) -> dict:
    """
    Runs OperationClassifier + multi-agent detection.

    Sets: operations, pipeline, is_multi_agent, target_agents
    """
    query = state["query"]
    classifier = get_classifier()
    classification = classifier.classify(query)

    # Detect multi-agent
    targets = _detect_multi_agent(query)

    return {
        "operations": classification["operations"],
        "pipeline": classification["pipeline"],
        "is_multi_agent": len(targets) >= 2,
        "target_agents": targets,
    }


# =============================================================================
# NODE 2: route_node
# =============================================================================

async def route_node(state: WorkflowState) -> dict:
    """
    Runs QueryRouter (keyword -> LLM fallback).

    For multi-agent queries, route is set to "MULTI" (routing already
    determined by classify_node's target_agents).

    Sets: route, routed_by
    """
    if state.get("is_multi_agent"):
        return {"route": "MULTI", "routed_by": "multi_detect"}

    router = get_router()
    route, routed_by = router.route_query(state["query"])
    return {"route": route, "routed_by": routed_by}


# =============================================================================
# NODE 3: execute_single_agent_node
# =============================================================================

async def execute_single_agent_node(state: WorkflowState) -> dict:
    """
    Calls the appropriate agent's execute() for single-agent queries.

    Creates a fresh agent instance per invocation (agents hold per-user state).
    """
    from agents.gmail_agent import GmailAgent
    from agents.slack_agent import SlackAgent
    from agents.outlook_agent import OutlookAgent

    route = state["route"]
    user_id = state.get("user_id", "default")
    query = state["query"]
    operations = state.get("operations")

    agent_map = {
        "GMAIL": ("GmailAgent", lambda: GmailAgent(user_id=user_id)),
        "SLACK": ("SlackAgent", lambda: SlackAgent(user_id=user_id)),
        "OUTLOOK": ("OutlookAgent", lambda: OutlookAgent(user_id=user_id)),
    }

    entry = agent_map.get(route)
    if not entry:
        return {
            "agent_results": [{
                "agent": route,
                "error": f"Unknown route: {route}",
            }],
        }

    agent_name, factory = entry
    try:
        agent = factory()
        result = await agent.execute(query, operations=operations)
        return {
            "agent_results": [{
                "agent": agent_name,
                "result": result,
            }],
        }
    except Exception as e:
        logger.error(f"[{route}] Agent execution failed: {e}")
        return {
            "agent_results": [{
                "agent": agent_name,
                "error": str(e),
            }],
        }


# =============================================================================
# NODES 4-6: Platform-specific nodes for multi-agent parallel execution
# =============================================================================

async def execute_gmail_node(state: WorkflowState) -> dict:
    """Execute GmailAgent for multi-agent queries."""
    from agents.gmail_agent import GmailAgent

    user_id = state.get("user_id", "default")
    query = state["query"]
    operations = state.get("operations")

    try:
        agent = GmailAgent(user_id=user_id)
        result = await agent.execute(query, operations=operations)
        return {
            "agent_results": [{
                "agent": "GmailAgent",
                "result": result,
            }],
        }
    except Exception as e:
        logger.error(f"[GMAIL] Multi-agent execution failed: {e}")
        return {
            "agent_results": [{
                "agent": "GmailAgent",
                "error": str(e),
            }],
        }


async def execute_slack_node(state: WorkflowState) -> dict:
    """Execute SlackAgent for multi-agent queries."""
    from agents.slack_agent import SlackAgent

    user_id = state.get("user_id", "default")
    query = state["query"]
    operations = state.get("operations")

    try:
        agent = SlackAgent(user_id=user_id)
        result = await agent.execute(query, operations=operations)
        return {
            "agent_results": [{
                "agent": "SlackAgent",
                "result": result,
            }],
        }
    except Exception as e:
        logger.error(f"[SLACK] Multi-agent execution failed: {e}")
        return {
            "agent_results": [{
                "agent": "SlackAgent",
                "error": str(e),
            }],
        }


async def execute_outlook_node(state: WorkflowState) -> dict:
    """Execute OutlookAgent for multi-agent queries."""
    from agents.outlook_agent import OutlookAgent

    user_id = state.get("user_id", "default")
    query = state["query"]
    operations = state.get("operations")

    try:
        agent = OutlookAgent(user_id=user_id)
        result = await agent.execute(query, operations=operations)
        return {
            "agent_results": [{
                "agent": "OutlookAgent",
                "result": result,
            }],
        }
    except Exception as e:
        logger.error(f"[OUTLOOK] Multi-agent execution failed: {e}")
        return {
            "agent_results": [{
                "agent": "OutlookAgent",
                "error": str(e),
            }],
        }


# =============================================================================
# NODE 7: merge_node
# =============================================================================

async def merge_node(state: WorkflowState) -> dict:
    """
    Pass-through node for multi-agent results.

    operator.add on agent_results already merged the lists from parallel nodes.
    This node exists as the convergence point after parallel execution.
    """
    return {}


# =============================================================================
# NODE 8: handle_general_node
# =============================================================================

async def handle_general_node(state: WorkflowState) -> dict:
    """Handle GENERAL/CHAT queries using LLM with automatic fallback."""
    query = state["query"]
    brain_router = get_brain_router()

    try:
        content = brain_router.invoke_with_fallback(
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
        return {
            "agent_results": [{
                "agent": "Supervisor (direct)",
                "error": str(e),
            }],
        }


# =============================================================================
# NODE 9: answer_node
# =============================================================================

async def answer_node(state: WorkflowState) -> dict:
    """
    Builds the final WebSocket response dict from agent_results.

    For single-agent: extracts the one result.
    For multi-agent: merges all results into a combined response.

    Output format matches what main.py expects for WebSocket send:
    {
        "type": "response",
        "route": "GMAIL",
        "agent": "GmailAgent",
        "routed_by": "keyword",
        "data": { ... agent result ... },
        "query": "..."
    }
    """
    agent_results = state.get("agent_results", [])
    route = state.get("route", "GENERAL")
    query = state.get("query", "")
    routed_by = state.get("routed_by", "unknown")

    if not agent_results:
        return {
            "final_response": {
                "type": "response",
                "route": route,
                "agent": "Supervisor",
                "routed_by": routed_by,
                "data": {"error": "No agent produced results"},
                "query": query,
            }
        }

    # Single-agent or general: use the first (only) result
    if len(agent_results) == 1:
        ar = agent_results[0]
        agent_name = ar.get("agent", "Unknown")
        result_data = ar.get("result", ar.get("error", "No result"))

        return {
            "final_response": {
                "type": "response",
                "route": route,
                "agent": agent_name,
                "routed_by": routed_by,
                "data": result_data,
                "query": query,
            }
        }

    # Multi-agent: merge results from all agents
    merged_data = {}
    agents_used = []
    for ar in agent_results:
        agent_name = ar.get("agent", "Unknown")
        agents_used.append(agent_name)
        result = ar.get("result")
        error = ar.get("error")
        if result:
            merged_data[agent_name] = result
        elif error:
            merged_data[agent_name] = {"error": error}

    # Build combined final answer
    combined_answers = []
    for agent_name, result in merged_data.items():
        if isinstance(result, dict) and "final_answer" in result:
            combined_answers.append(
                f"[{agent_name}] {result['final_answer']}"
            )
        elif isinstance(result, dict) and "error" in result:
            combined_answers.append(
                f"[{agent_name}] Error: {result['error']}"
            )

    return {
        "final_response": {
            "type": "response",
            "route": "MULTI",
            "agent": ", ".join(agents_used),
            "routed_by": routed_by,
            "data": merged_data,
            "combined_answer": "\n\n".join(combined_answers),
            "query": query,
        }
    }


# =============================================================================
# ROUTE DECISION — conditional edge function
# =============================================================================

def route_decision(state: WorkflowState):
    """
    Conditional edge after route_node.

    Returns:
      - "single"  for single-agent queries (GMAIL, SLACK, OUTLOOK)
      - "general" for CHAT/GENERAL queries
      - list of node names for parallel multi-agent execution
    """
    route = state.get("route", "GENERAL")

    # Multi-agent: fan out to all target agents in parallel
    if route == "MULTI":
        targets = state.get("target_agents", [])
        node_map = {
            "GMAIL": "execute_gmail",
            "SLACK": "execute_slack",
            "OUTLOOK": "execute_outlook",
        }
        nodes = [node_map[t] for t in targets if t in node_map]
        return nodes if nodes else ["handle_general"]

    # Single agent
    if route in AGENT_PLATFORMS:
        return ["execute_single_agent"]

    # General / Chat / Unknown
    return ["handle_general"]


# =============================================================================
# BUILD WORKFLOW — Constructs the compiled StateGraph
# =============================================================================

def build_workflow():
    """
    Build and compile the LangGraph StateGraph.

    Graph structure:
      START -> classify -> route -> (conditional) -> answer -> END

    The conditional edge after route fans out to:
      - execute_single_agent (for single-platform queries)
      - execute_gmail + execute_slack + execute_outlook (parallel multi-agent)
      - handle_general (for chat/general)
    """
    graph = StateGraph(WorkflowState)

    # --- Add all nodes ---
    graph.add_node("classify", classify_node)
    graph.add_node("route", route_node)
    graph.add_node("execute_single_agent", execute_single_agent_node)
    graph.add_node("execute_gmail", execute_gmail_node)
    graph.add_node("execute_slack", execute_slack_node)
    graph.add_node("execute_outlook", execute_outlook_node)
    graph.add_node("merge", merge_node)
    graph.add_node("handle_general", handle_general_node)
    graph.add_node("answer", answer_node)

    # --- Entry point ---
    graph.set_entry_point("classify")

    # --- Sequential edges ---
    graph.add_edge("classify", "route")

    # --- Conditional edge: route -> (decision) ---
    graph.add_conditional_edges(
        "route",
        route_decision,
        {
            "execute_single_agent": "execute_single_agent",
            "execute_gmail": "execute_gmail",
            "execute_slack": "execute_slack",
            "execute_outlook": "execute_outlook",
            "handle_general": "handle_general",
        },
    )

    # --- After execution, go to answer (or merge first for multi) ---
    graph.add_edge("execute_single_agent", "answer")
    graph.add_edge("handle_general", "answer")

    # Multi-agent nodes converge at merge, then answer
    graph.add_edge("execute_gmail", "merge")
    graph.add_edge("execute_slack", "merge")
    graph.add_edge("execute_outlook", "merge")
    graph.add_edge("merge", "answer")

    # --- Answer -> END ---
    graph.add_edge("answer", END)

    return graph.compile()


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_workflow_instance = None


def get_workflow():
    """Get the singleton compiled workflow graph."""
    global _workflow_instance
    if _workflow_instance is None:
        _workflow_instance = build_workflow()
    return _workflow_instance
