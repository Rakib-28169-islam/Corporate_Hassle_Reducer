"""
=============================================================================
SUPERVISOR AGENT — Thin Wrapper Around LangGraph Workflow
=============================================================================

Phase 4 rewrite: The supervisor no longer handles routing or execution
directly. Instead, it delegates to the LangGraph StateGraph pipeline:

  arun(query) -> graph.ainvoke(state) -> final_response

What was REMOVED:
  - _handle_gmail/slack/outlook/general (replaced by graph nodes)
  - Internal keyword rules (moved to core/routing.py)
  - Lazy-loaded agent properties (graph creates agents per-invocation)

What was KEPT:
  - route_query()  -> delegates to QueryRouter (for external callers)
  - run()          -> sync wrapper for backward compat
  - status()       -> updated to show "langgraph" workflow

What was ADDED:
  - arun()         -> async entry point, calls graph.ainvoke()
=============================================================================
"""

import sys
import os
import asyncio
import logging

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_manager import get_brain_router
from core.routing import get_router
from core.workflow_graph import get_workflow

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """
    The Mother Agent (Supervisor).

    Now a thin wrapper around the LangGraph workflow.
    All routing, classification, and execution happen inside the graph.

    Usage:
        supervisor = SupervisorAgent(user_id="user_1")
        result = await supervisor.arun("check my unread emails")
        # result = {"type": "response", "route": "GMAIL", ...}
    """

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.brain = get_brain_router()
        self._router = get_router()
        self._workflow = get_workflow()
        logger.info(f"SupervisorAgent initialized (LangGraph workflow) "
                    f"for user: {user_id}")

    # ==================== ASYNC ENTRY POINT ====================

    async def arun(self, query):
        """
        Async entry point. Routes query through the LangGraph pipeline.

        This is the PRIMARY method — called by main.py's WebSocket handler.

        Args:
            query (str): The user's raw query

        Returns:
            dict: WebSocket-ready response from answer_node
                  {"type": "response", "route": ..., "agent": ..., "data": ...}
        """
        initial_state = {
            "user_id": self.user_id,
            "query": query,
            "agent_results": [],
        }

        try:
            final_state = await self._workflow.ainvoke(initial_state)
            return final_state.get("final_response", {
                "type": "error",
                "message": "Workflow produced no response",
                "query": query,
            })
        except Exception as e:
            logger.error(f"Workflow failed for query '{query[:50]}': {e}")
            return {
                "type": "error",
                "message": f"Workflow error: {str(e)}",
                "query": query,
            }

    # ==================== SYNC WRAPPER (backward compat) ====================

    def run(self, query):
        """
        Sync wrapper around arun() for backward compatibility.

        WARNING: This blocks the event loop. Use arun() in async contexts.
        Kept only for non-async callers (tests, scripts, etc.).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context — can't use asyncio.run()
            # Create a new thread to avoid blocking the event loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.arun(query))
                return future.result()
        else:
            return asyncio.run(self.arun(query))

    # ==================== ROUTING (delegates to QueryRouter) ====================

    def route_query(self, query):
        """
        Smart 2-layer routing — delegates to QueryRouter.

        Returns:
            tuple: (route, routed_by) e.g. ("GMAIL", "keyword")
        """
        return self._router.route_query(query)

    # ==================== STATUS ====================

    def status(self):
        """Get system status."""
        return {
            "supervisor": "active",
            "workflow": "langgraph",
            "brain": self.brain.status(),
        }


# --- TEST ---
if __name__ == "__main__":
    print("Supervisor Agent Test (LangGraph)")
    print("=" * 50)
    agent = SupervisorAgent(user_id="default")
    print(f"Brain: {agent.brain.status()}")
    print(f"Status: {agent.status()}")

    # Test routing (no graph invocation, just keyword matching)
    test_queries = [
        "Check my unread emails",
        "Send a Slack message to #general",
        "What meetings do I have today?",
        "Hello, how are you?",
        "Draft an email to boss@company.com",
        "Search Slack for project updates",
        "Create a calendar event for tomorrow",
        "Check is there any emails from Alice in gmail?",
    ]

    print("\nRouting Tests:")
    print("-" * 60)
    for q in test_queries:
        route, method = agent.route_query(q)
        print(f"  [{route:8s}] ({method:7s}) {q}")
