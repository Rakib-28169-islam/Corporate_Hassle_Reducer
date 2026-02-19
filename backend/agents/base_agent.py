"""
=============================================================================
BASE AGENT — Shared Foundation for All Specialist Agents
=============================================================================

WHY THIS EXISTS:
    Before this, every agent (Gmail, Slack, Outlook) had its own code for
    searching, summarizing, composing — lots of duplicate logic.

    BaseAgent provides the SHARED operations that all agents need:
        _do_search()    -> SQLite -> ChromaDB -> Composio (smart threshold)
        _do_calculate() -> Python math first, LLM last resort
        _do_compose()   -> LLM generates text
        _do_summarize() -> LLM summarizes data
        _do_action()    -> OVERRIDE in each agent (gmail sends, slack posts)

    Each specialist agent INHERITS from BaseAgent and only overrides
    what's unique to its tool (Composio API calls, action methods).

HOW IT WORKS:
    ┌────────────────────────────────────────────────────┐
    │  BaseAgent.execute(query, operations)              │
    │      |                                              │
    │  For each operation in pipeline order:              │
    │      |                                              │
    │  SEARCH    -> _do_search()    -> local first       │
    │  CALCULATE -> _do_calculate() -> Python math       │
    │  ACTION    -> _do_action()    -> Composio API      │
    │  COMPOSE   -> _do_compose()   -> LLM generates     │
    │  SUMMARIZE -> _do_summarize() -> LLM summarizes    │
    │  CHAT      -> _do_chat()      -> LLM responds      │
    │      |                                              │
    │  Merge all results -> Return                       │
    └────────────────────────────────────────────────────┘

SEARCH FALLBACK CHAIN (the core of _do_search):
    1. Check staleness (is cached data too old?)
    2. SQLite keyword search (3ms, free)
    3. If results >= 3 (threshold) -> return immediately
    4. If results < 3 -> ChromaDB semantic search (40ms, free)
    5. If still < 3 -> Composio API call (1500ms, costly)
    6. Cache API results in both SQLite + ChromaDB for next time

INHERITANCE:
    BaseAgent (this file)
        |
        |-- GmailAgent   -> overrides _do_action(), _composio_search()
        |-- SlackAgent   -> overrides _do_action(), _composio_search()
        |-- OutlookAgent -> overrides _do_action(), _composio_search()

USAGE:
    # Agents inherit and override:
    class GmailAgent(BaseAgent):
        def __init__(self, user_id):
            super().__init__(user_id, tool_name="gmail")

        def _composio_search(self, query):
            return self.tools.execute("GMAIL_FETCH_EMAILS", {"query": query})

        def _do_action(self, query, context):
            # Gmail-specific send/delete/label logic
            ...

=============================================================================
"""

import os
import sys
import re
import json
import logging
import asyncio

# Ensure imports work from any location
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from core.llm_manager import get_brain_router
from core.operation_classifier import get_classifier
from tools.local_tools import get_local_tools
from services.sync_service import get_sync_service

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Base class for all specialist agents (Gmail, Slack, Outlook).

    Provides shared operation handlers that use the cost-first fallback chain.
    Specialist agents inherit this and override tool-specific methods.

    Key concepts:
        - execute() runs the full pipeline for a classified query
        - Each _do_* method handles one operation type
        - _composio_search() and _do_action() MUST be overridden by child agents
        - All search/cache logic goes through LocalToolManager (smart threshold)
    """

    def __init__(self, user_id="default", tool_name="general"):
        """
        Initialize the base agent.

        Args:
            user_id   (str): The user this agent works for
            tool_name (str): The tool this agent manages — "gmail", "slack", "outlook"
                             Used for cache keys, logging, and filtering
        """
        self.user_id = user_id
        self.tool_name = tool_name
        self.brain = get_brain_router()
        self.classifier = get_classifier()
        self.local_tools = get_local_tools()
        self.sync = get_sync_service()

    # =========================================================================
    # EXECUTE — Main entry point (runs the full pipeline)
    # =========================================================================

    async def execute(self, query, operations=None):
        """
        Execute a query through the operation pipeline.

        This is the MAIN method that SupervisorAgent calls after routing.
        It takes the classified operations and runs them in pipeline order.

        How it works:
            1. If operations not provided, classify the query
            2. Build the execution pipeline (respecting dependencies)
            3. Run each operation in step order
            4. Pass results from earlier steps to later steps (context chaining)
            5. Return all results merged

        Context chaining:
            Step 1 (SEARCH) returns data -> passed as context to Step 2 (CALCULATE)
            This is how "how many emails from John?" works:
                SEARCH finds 5 emails -> CALCULATE counts them -> "5 emails"

        Args:
            query      (str):           The user's raw query
            operations (list, optional): Pre-classified operations.
                                         If None, classifier runs automatically.

        Returns:
            dict with:
                query       (str):  The original query
                operations  (list): Operations that were executed
                results     (dict): Per-operation results
                final_answer (str): The user-facing answer text

        Example:
            result = await agent.execute("how many unread emails?")
            # result = {
            #     "query": "how many unread emails?",
            #     "operations": ["SEARCH", "CALCULATE"],
            #     "results": {
            #         "SEARCH": {"data": [...], "source": "sqlite"},
            #         "CALCULATE": {"answer": "5 unread emails"},
            #     },
            #     "final_answer": "You have 5 unread emails."
            # }
        """
        # Step 1: Classify if not pre-classified
        if operations is None:
            classification = self.classifier.classify(query)
            operations = classification["operations"]
            pipeline = classification["pipeline"]
        else:
            # Build pipeline from provided operations
            pipeline = self.classifier._build_pipeline(operations)

        logger.info(f"[{self.tool_name}] Executing: {operations} for query: {query[:50]}")

        # Step 2: Execute each operation in pipeline order
        results = {}
        context = {}  # Shared context passed between steps

        for step_info in pipeline:
            op = step_info["op"]
            step_num = step_info["step"]

            try:
                if op == "SEARCH":
                    results["SEARCH"] = await self._do_search(query, context)
                    # Pass search results as context for downstream ops
                    context["search_results"] = results["SEARCH"].get("data", [])

                elif op == "CALCULATE":
                    results["CALCULATE"] = await self._do_calculate(
                        query, context
                    )

                elif op == "ACTION":
                    results["ACTION"] = await self._do_action(query, context)

                elif op == "COMPOSE":
                    results["COMPOSE"] = await self._do_compose(query, context)

                elif op == "SUMMARIZE":
                    results["SUMMARIZE"] = await self._do_summarize(
                        query, context
                    )

                elif op == "CHAT":
                    results["CHAT"] = await self._do_chat(query, context)

            except Exception as e:
                logger.error(f"[{self.tool_name}] {op} failed: {e}")
                results[op] = {"error": str(e)}

        # Step 3: Build final answer
        final_answer = self._build_final_answer(query, operations, results)

        return {
            "query": query,
            "operations": operations,
            "results": results,
            "final_answer": final_answer,
        }

    # =========================================================================
    # SEARCH — Local-first with smart threshold
    # =========================================================================

    async def _do_search(self, query, context):
        """
        Search for data using the cost-first fallback chain.

        Fallback chain:
            1. Check if cached data is stale
            2. SQLite keyword search (3ms)
            3. If threshold met (>= 3) -> return
            4. ChromaDB semantic search (40ms)
            5. If still not enough -> Composio API (1500ms)
            6. Cache API results for next time

        Args:
            query   (str):  The search query
            context (dict): Shared context from previous steps (unused for SEARCH)

        Returns:
            dict with:
                data    (list): The search results (list of content dicts)
                source  (str):  Where results came from ("sqlite", "chromadb",
                                "hybrid", "composio", "empty")
                count   (int):  Number of results
        """
        # Step 1: Check staleness
        stale = await self.local_tools.is_stale(
            self.user_id, self.tool_name, max_age_minutes=30
        )

        # Step 2: Search locally (smart threshold)
        local_result = await self.local_tools.search_local(
            user_id=self.user_id,
            tool=self.tool_name,
            query=query,
        )

        local_data = local_result["results"]
        source = local_result["source"]

        # Step 3: If local has data AND not stale -> return
        if local_data and not stale:
            logger.info(f"[{self.tool_name}] SEARCH: {len(local_data)} results "
                        f"from {source} (fresh cache)")
            return {
                "data": local_data,
                "source": source,
                "count": len(local_data),
            }

        # Step 4: Local empty or stale -> call Composio API
        logger.info(f"[{self.tool_name}] SEARCH: local {'stale' if stale else 'empty'}, "
                     f"calling Composio API...")

        try:
            api_results = self._composio_search(query)

            if api_results:
                # Normalize API results to list of dicts
                items = self._normalize_api_results(api_results)

                if items:
                    # Cache for next time (both SQLite + ChromaDB)
                    await self.local_tools.cache_results(
                        user_id=self.user_id,
                        tool=self.tool_name,
                        data_type=self._get_data_type(),
                        items=items,
                    )

                    return {
                        "data": items,
                        "source": "composio",
                        "count": len(items),
                    }
        except Exception as e:
            logger.error(f"[{self.tool_name}] Composio API failed: {e}")

        # Step 5: If we had stale local data, return it as fallback
        if local_data:
            logger.info(f"[{self.tool_name}] API failed, returning stale local data")
            return {
                "data": local_data,
                "source": f"{source}_stale",
                "count": len(local_data),
            }

        # Step 6: Nothing anywhere
        return {"data": [], "source": "empty", "count": 0}

    # =========================================================================
    # CALCULATE — Python math first, LLM last resort
    # =========================================================================

    async def _do_calculate(self, query, context):
        """
        Perform calculations on search results.

        Fallback chain:
            1. Python len() for "how many" / "count" queries
            2. Python sum() for "total" / "sum" queries
            3. LLM for complex calculations (last resort)

        Args:
            query   (str):  The original query (to understand what to calculate)
            context (dict): Must contain "search_results" from SEARCH step

        Returns:
            dict with:
                answer  (str): The calculation result as text
                value   (any): The raw numeric value
                method  (str): "python" or "llm"
        """
        search_data = context.get("search_results", [])
        query_lower = query.lower()

        # --- Method 1: Python count (how many, count, number of) ---
        if any(w in query_lower for w in ["how many", "count", "number of"]):
            count = len(search_data)
            return {
                "answer": f"{count}",
                "value": count,
                "method": "python",
            }

        # --- Method 2: Python sum (total, sum, add up) ---
        if any(w in query_lower for w in ["total", "sum", "add up"]):
            # Try to extract numbers from search results
            numbers = self._extract_numbers(search_data)
            if numbers:
                total = sum(numbers)
                return {
                    "answer": f"{total}",
                    "value": total,
                    "method": "python",
                }

        # --- Method 3: Python comparison (most, least, top, highest) ---
        if any(w in query_lower for w in ["most", "least", "top",
                                           "highest", "lowest"]):
            count = len(search_data)
            return {
                "answer": f"Found {count} items to compare",
                "value": count,
                "method": "python",
            }

        # --- Method 4: LLM for anything else ---
        brain = self.brain.get_brain(task_type="fast")
        if brain and search_data:
            try:
                data_summary = json.dumps(search_data[:10], default=str)
                response = brain.invoke(
                    f"Based on this data, answer the calculation question.\n"
                    f"Question: {query}\n"
                    f"Data: {data_summary}\n"
                    f"Give a short, direct numerical answer."
                )
                return {
                    "answer": response.content,
                    "value": response.content,
                    "method": "llm",
                }
            except Exception as e:
                logger.error(f"[{self.tool_name}] LLM calculate failed: {e}")

        return {
            "answer": f"Found {len(search_data)} items",
            "value": len(search_data),
            "method": "fallback",
        }

    # =========================================================================
    # ACTION — Must be overridden by child agents
    # =========================================================================

    async def _do_action(self, query, context):
        """
        Perform a mutation (send, delete, move, create, etc.).

        MUST BE OVERRIDDEN by each specialist agent because every tool
        has different APIs:
            GmailAgent._do_action() -> uses GMAIL_SEND_EMAIL, etc.
            SlackAgent._do_action() -> uses SLACK_CHAT_POST_MESSAGE, etc.

        After any action, SyncService is notified to invalidate cache.

        Args:
            query   (str):  The original query
            context (dict): Context from previous steps

        Returns:
            dict with: action (str), status (str), message (str)
        """
        return {
            "action": "unknown",
            "status": "not_implemented",
            "message": f"Action not implemented for {self.tool_name}. "
                       f"Override _do_action() in the specialist agent.",
        }

    # =========================================================================
    # COMPOSE — LLM generates text
    # =========================================================================

    async def _do_compose(self, query, context):
        """
        Generate/draft text content using LLM.

        Used for: "draft a reply", "write an email", "compose a message"

        How it works:
            1. Check if search results exist in context (for reply context)
            2. Send prompt to LLM with context
            3. Return generated text

        Args:
            query   (str):  The original query with compose instructions
            context (dict): May contain "search_results" for context

        Returns:
            dict with:
                content  (str): The generated text
                model    (str): Which LLM was used
        """
        brain = self.brain.get_brain(task_type="general")
        if not brain:
            return {"content": None, "model": "none",
                    "error": "No LLM available"}

        # Build prompt with context if available
        search_data = context.get("search_results", [])
        context_text = ""
        if search_data:
            # Include first few search results as context
            context_text = (
                f"\n\nContext (original content to reference):\n"
                f"{json.dumps(search_data[:3], default=str)}"
            )

        prompt = (
            f"You are a professional assistant. {query}"
            f"{context_text}\n\n"
            f"Write the requested content. Be professional and concise."
        )

        # Try primary brain, fall back to secondary if it fails
        for task_type in ["general", "fast"]:
            brain = self.brain.get_brain(task_type=task_type)
            if not brain:
                continue
            try:
                response = brain.invoke(prompt)
                return {
                    "content": response.content,
                    "model": type(brain).__name__,
                }
            except Exception as e:
                logger.warning(f"[{self.tool_name}] Compose ({task_type}) failed: {e}")
                continue

        return {"content": None, "model": "none", "error": "All LLMs failed"}

    # =========================================================================
    # SUMMARIZE — LLM summarizes data
    # =========================================================================

    async def _do_summarize(self, query, context):
        """
        Summarize search results using LLM.

        Used for: "summarize my emails", "recap slack", "overview of messages"

        How it works:
            1. Get search results from context (SEARCH must run first)
            2. Send data to LLM with summarize prompt
            3. Return summary text

        RAG-aware: The LLM only summarizes data we provide — no hallucination.

        Args:
            query   (str):  The original query
            context (dict): Must contain "search_results" from SEARCH step

        Returns:
            dict with:
                summary (str): The summary text
                model   (str): Which LLM was used
                items_summarized (int): How many items were in the input
        """
        search_data = context.get("search_results", [])

        if not search_data:
            return {
                "summary": "No data found to summarize.",
                "model": "none",
                "items_summarized": 0,
            }

        # Try reading brain (Gemini), fall back to fast brain (Groq)
        data_text = json.dumps(search_data[:20], default=str)
        prompt = (
            f"Summarize the following data based on the user's request.\n"
            f"User asked: {query}\n\n"
            f"Data ({len(search_data)} items):\n{data_text}\n\n"
            f"Rules:\n"
            f"- ONLY use information from the provided data\n"
            f"- Be concise (bullet points preferred)\n"
            f"- Highlight key items, dates, and action items\n"
            f"- If data is insufficient, say so"
        )

        for task_type in ["reading", "fast"]:
            brain = self.brain.get_brain(task_type=task_type)
            if not brain:
                continue
            try:
                response = brain.invoke(prompt)
                return {
                    "summary": response.content,
                    "model": type(brain).__name__,
                    "items_summarized": len(search_data),
                }
            except Exception as e:
                logger.warning(f"[{self.tool_name}] Summarize ({task_type}) failed: {e}")
                continue

        return {"summary": "All LLMs failed for summarization.",
                "model": "none", "items_summarized": 0}

    # =========================================================================
    # CHAT — General conversation
    # =========================================================================

    async def _do_chat(self, query, context):
        """
        Handle general conversation (greetings, help, general questions).

        This doesn't need search results or tool access.
        Just uses the LLM directly.

        Args:
            query   (str):  The user's message
            context (dict): Unused for chat

        Returns:
            dict with:
                response (str): The chat response
                model    (str): Which LLM was used
        """
        brain = self.brain.get_brain(task_type="fast")
        if not brain:
            return {"response": "Hello! How can I help you?",
                    "model": "none"}

        try:
            response = brain.invoke(
                f"You are a helpful corporate assistant. "
                f"Respond briefly and helpfully.\n\n"
                f"User: {query}"
            )
            return {
                "response": response.content,
                "model": type(brain).__name__,
            }
        except Exception as e:
            logger.error(f"[{self.tool_name}] Chat failed: {e}")
            return {"response": f"Sorry, I encountered an error: {e}",
                    "model": "none"}

    # =========================================================================
    # METHODS TO OVERRIDE IN CHILD AGENTS
    # =========================================================================

    def _composio_search(self, query):
        """
        Fetch data from Composio API (tool-specific).

        OVERRIDE THIS in each specialist agent.
        This is called when local cache is empty or stale.

        Args:
            query (str): The search query

        Returns:
            Raw Composio API response (will be normalized by _normalize_api_results)

        Example overrides:
            GmailAgent:  self.tools.execute("GMAIL_FETCH_EMAILS", {"query": query})
            SlackAgent:  self.tools.execute("SLACK_SEARCH_MESSAGES", {"query": query})
            OutlookAgent: self.tools.execute("OUTLOOK_OUTLOOK_LIST_MESSAGES", {...})
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must override _composio_search()"
        )

    def _get_data_type(self):
        """
        Return the data type for this agent's cached items.

        OVERRIDE THIS in each specialist agent.

        Returns:
            str: "email" for Gmail/Outlook, "message" for Slack, etc.
        """
        return "data"

    def _normalize_api_results(self, api_response):
        """
        Normalize raw Composio API response into a list of dicts.

        Composio returns different formats per tool. This method
        extracts the useful data into a consistent list of dicts.

        Override in child agents if the API response format is unusual.

        Args:
            api_response: Raw response from Composio API

        Returns:
            list[dict]: Normalized items, each with at least an "id" field
        """
        if isinstance(api_response, list):
            return api_response

        if isinstance(api_response, dict):
            # Common Composio patterns
            if "data" in api_response:
                data = api_response["data"]
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return [data]

            if "results" in api_response:
                return api_response["results"]

            if "messages" in api_response:
                return api_response["messages"]

            # Return as single-item list
            return [api_response]

        # String or other type — wrap it
        return [{"content": str(api_response)}]

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _extract_numbers(self, data):
        """
        Extract numeric values from search result data.

        Scans all string values in the data for numbers.
        Used by _do_calculate() for sum/total operations.

        Args:
            data (list): List of dicts (search results)

        Returns:
            list[float]: All numbers found in the data
        """
        numbers = []
        for item in data:
            if isinstance(item, dict):
                for value in item.values():
                    if isinstance(value, (int, float)):
                        numbers.append(float(value))
                    elif isinstance(value, str):
                        # Extract numbers from strings like "$500" or "100 items"
                        found = re.findall(r'[\d,]+\.?\d*', value)
                        for n in found:
                            try:
                                numbers.append(float(n.replace(',', '')))
                            except ValueError:
                                pass
        return numbers

    def _build_final_answer(self, query, operations, results):
        """
        Build a user-facing answer from all operation results.

        Combines results from multiple operations into one coherent response.

        Args:
            query      (str):  Original query
            operations (list): Operations that were run
            results    (dict): Per-operation results

        Returns:
            str: The final answer text for the user
        """
        parts = []

        if "SEARCH" in results:
            search = results["SEARCH"]
            count = search.get("count", 0)
            source = search.get("source", "unknown")
            if count > 0:
                parts.append(f"Found {count} results (from {source}).")
            else:
                parts.append("No results found.")

        if "CALCULATE" in results:
            calc = results["CALCULATE"]
            answer = calc.get("answer", "")
            if answer:
                parts.append(f"Calculation: {answer}")

        if "COMPOSE" in results:
            compose = results["COMPOSE"]
            content = compose.get("content", "")
            if content:
                parts.append(f"Draft:\n{content}")

        if "SUMMARIZE" in results:
            summ = results["SUMMARIZE"]
            summary = summ.get("summary", "")
            if summary:
                parts.append(f"Summary:\n{summary}")

        if "ACTION" in results:
            action = results["ACTION"]
            message = action.get("message", "")
            status = action.get("status", "")
            parts.append(f"Action: {message} (status: {status})")

        if "CHAT" in results:
            chat = results["CHAT"]
            response = chat.get("response", "")
            if response:
                parts.append(response)

        return "\n\n".join(parts) if parts else "I processed your request."

    # =========================================================================
    # NOTIFY SYNC SERVICE — Called after actions
    # =========================================================================

    async def _notify_action_complete(self, action_type):
        """
        Notify SyncService that an action was performed.

        Call this at the END of _do_action() in child agents
        after any mutation (send, delete, move, etc.).

        Args:
            action_type (str): What was done — "SEND_EMAIL", "DELETE_MESSAGE", etc.
        """
        await self.sync.on_action_complete(
            self.user_id, self.tool_name, action_type
        )


# =============================================================================
# TEST BLOCK — Run with: python agents/base_agent.py
# =============================================================================
if __name__ == "__main__":
    import shutil

    sys.stdout.reconfigure(encoding='utf-8')

    TEST_DB = os.path.join(_backend_dir, "test_base_agent.db")
    TEST_CHROMA = os.path.join(_backend_dir, "test_base_agent_chroma")

    async def run_tests():
        print("Testing BaseAgent...")
        print("=" * 60)

        from database.sqlite_manager import DatabaseManager
        from database.vector_store import VectorStore
        from tools.local_tools import LocalToolManager
        from services.sync_service import SyncService

        # Clean previous test files
        for f in [TEST_DB]:
            if os.path.exists(f):
                os.remove(f)
        try:
            if os.path.exists(TEST_CHROMA):
                shutil.rmtree(TEST_CHROMA)
        except PermissionError:
            pass

        # Setup test databases
        db = DatabaseManager(db_path=TEST_DB)
        await db.init_db()
        vs = VectorStore(persist_dir=TEST_CHROMA)

        lt = LocalToolManager()
        lt.db = db
        lt.vs = vs

        sync_svc = SyncService()
        sync_svc.local_tools = lt

        # Create a test agent (using BaseAgent directly)
        agent = BaseAgent(user_id="test_user", tool_name="gmail")
        agent.local_tools = lt
        agent.sync = sync_svc

        passed = 0
        failed = 0

        def check(name, condition):
            nonlocal passed, failed
            if condition:
                passed += 1
                print(f"  [OK] {name}")
            else:
                failed += 1
                print(f"  [FAIL] {name}")

        # ----- Seed test data -----
        emails = [
            {"id": "msg_001", "subject": "Invoice from vendor",
             "from": "billing@vendor.com", "body": "Total amount: $500"},
            {"id": "msg_002", "subject": "Team standup notes",
             "from": "john@company.com", "body": "Discussed deployment plan"},
            {"id": "msg_003", "subject": "Meeting tomorrow",
             "from": "boss@company.com", "body": "Let's meet at 3pm"},
            {"id": "msg_004", "subject": "Quarterly report",
             "from": "hr@company.com", "body": "Revenue: $10000"},
            {"id": "msg_005", "subject": "Slack integration update",
             "from": "dev@company.com", "body": "Bot is ready for testing"},
        ]
        await lt.cache_results("test_user", "gmail", "email", emails)

        # ----- Test 1: Operation classification -----
        classification = agent.classifier.classify("how many unread emails?")
        check("Classifier detects SEARCH+CALCULATE",
              set(classification["operations"]) == {"SEARCH", "CALCULATE"})

        # ----- Test 2: _do_search (local cache hit) -----
        search_result = await agent._do_search("invoice", {})
        check(f"Search finds results: count={search_result['count']}",
              search_result["count"] > 0)
        check("Search source is local (not composio)",
              search_result["source"] in ["sqlite", "chromadb", "hybrid"])

        # ----- Test 3: _do_search (all data, threshold met) -----
        search_all = await agent._do_search("email", {})
        check(f"Search all: count={search_all['count']} (should be >= 3)",
              search_all["count"] >= 3)

        # ----- Test 4: _do_calculate (count) -----
        context = {"search_results": emails}
        calc_result = await agent._do_calculate("how many emails?", context)
        check(f"Calculate count: {calc_result['answer']} (expected 5)",
              calc_result["value"] == 5)
        check("Calculate used Python (not LLM)",
              calc_result["method"] == "python")

        # ----- Test 5: _do_calculate (sum) -----
        calc_sum = await agent._do_calculate("total amount", context)
        check(f"Calculate sum found numbers",
              calc_sum["method"] == "python" and calc_sum["value"] > 0)

        # ----- Test 6: _extract_numbers -----
        numbers = agent._extract_numbers(emails)
        check(f"Extract numbers: {numbers}",
              500.0 in numbers and 10000.0 in numbers)

        # ----- Test 7: _do_action (base returns not_implemented) -----
        action_result = await agent._do_action("send email", {})
        check("Base _do_action returns not_implemented",
              action_result["status"] == "not_implemented")

        # ----- Test 8: _do_chat -----
        chat_result = await agent._do_chat("hello", {})
        check("Chat returns a response",
              chat_result.get("response") is not None)

        # ----- Test 9: Full execute pipeline -----
        exec_result = await agent.execute("how many emails?",
                                          operations=["SEARCH", "CALCULATE"])
        check("Execute returns all expected keys",
              all(k in exec_result for k in
                  ["query", "operations", "results", "final_answer"]))
        check("Execute has SEARCH result",
              "SEARCH" in exec_result["results"])
        check("Execute has CALCULATE result",
              "CALCULATE" in exec_result["results"])

        # ----- Test 10: _normalize_api_results -----
        check("Normalize list -> list",
              agent._normalize_api_results([{"a": 1}]) == [{"a": 1}])
        check("Normalize dict with data -> list",
              agent._normalize_api_results({"data": [{"a": 1}]}) == [{"a": 1}])
        check("Normalize string -> wrapped list",
              agent._normalize_api_results("hello") == [{"content": "hello"}])

        # ----- Test 11: _build_final_answer -----
        answer = agent._build_final_answer(
            "test", ["SEARCH", "CALCULATE"],
            {
                "SEARCH": {"count": 5, "source": "sqlite"},
                "CALCULATE": {"answer": "5"},
            }
        )
        check("Final answer includes search count",
              "5 results" in answer)
        check("Final answer includes calculation",
              "5" in answer)

        # ----- Cleanup -----
        try:
            if os.path.exists(TEST_DB):
                os.remove(TEST_DB)
        except PermissionError:
            pass
        try:
            if os.path.exists(TEST_CHROMA):
                shutil.rmtree(TEST_CHROMA)
        except PermissionError:
            print("  [INFO] Could not delete test ChromaDB (locked, OK on Windows)")

        print()
        print("=" * 60)
        print(f"[RESULTS] {passed} passed, {failed} failed "
              f"out of {passed + failed} tests")

    asyncio.run(run_tests())
