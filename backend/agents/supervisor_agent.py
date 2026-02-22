"""Supervisor Agent — thin wrapper around LangGraph workflow."""

import asyncio
import logging

from core.llm_manager import get_brain_router
from core.routing import get_router
from core.workflow_graph import get_workflow

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """Delegates all work to the LangGraph StateGraph pipeline."""

    def __init__(self, user_id="default"):
        self.user_id = user_id
        self.brain = get_brain_router()
        self._router = get_router()
        self._workflow = get_workflow()
        logger.info(f"SupervisorAgent initialized (LangGraph workflow) for user: {user_id}")

    async def arun(self, query):
        """Async entry point — routes query through LangGraph pipeline."""
        initial_state = {
            "user_id": self.user_id,
            "query": query,
            "agent_results": [],
        }
        try:
            final_state = await self._workflow.ainvoke(initial_state)
            return final_state.get("final_response", {
                "type": "error", "message": "Workflow produced no response", "query": query,
            })
        except Exception as e:
            logger.error(f"Workflow failed for query '{query[:50]}': {e}")
            return {"type": "error", "message": f"Workflow error: {str(e)}", "query": query}

    def run(self, query):
        """Sync wrapper for backward compatibility. Prefer arun() in async code."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.arun(query))
                return future.result()
        else:
            return asyncio.run(self.arun(query))

    def route_query(self, query):
        """Delegates to QueryRouter. Returns (route, routed_by)."""
        return self._router.route_query(query)

    def status(self):
        return {
            "supervisor": "active",
            "workflow": "langgraph",
            "brain": self.brain.status(),
        }
