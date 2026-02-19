"""
=============================================================================
PHASE 4 TESTS — LangGraph Workflow Pipeline
=============================================================================

Tests the full workflow: routing, classification, multi-agent detection,
graph invocation, and response format.

Runs WITHOUT API keys by using cached/seeded data and mocking Composio.

Run with:
    cd backend
    python test/test_phase4_workflow.py
=============================================================================
"""

import sys
import os
import asyncio
import shutil

# Setup path
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _backend_dir)
sys.stdout.reconfigure(encoding='utf-8')

passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")


async def run_tests():
    global passed, failed

    print("Phase 4: LangGraph Workflow Tests")
    print("=" * 60)

    # -----------------------------------------------------------------
    # TEST GROUP 1: QueryRouter (extracted from supervisor)
    # -----------------------------------------------------------------
    print("\n--- 1. QueryRouter ---")

    from core.routing import QueryRouter, get_router

    router = QueryRouter()

    # Keyword routing
    route, method = router.route_query("check my unread emails")
    check("Email -> GMAIL (keyword)", route == "GMAIL" and method == "keyword")

    route, method = router.route_query("Send a Slack message to #general")
    check("Slack message -> SLACK (keyword)",
          route == "SLACK" and method == "keyword")

    route, method = router.route_query("What meetings do I have today?")
    check("Meeting -> OUTLOOK (keyword)",
          route == "OUTLOOK" and method == "keyword")

    route, method = router.route_query("Check gmail for emails from Alice")
    check("Gmail explicit -> GMAIL (keyword)",
          route == "GMAIL" and method == "keyword")

    # Singleton
    r1 = get_router()
    r2 = get_router()
    check("get_router() returns singleton", r1 is r2)

    # -----------------------------------------------------------------
    # TEST GROUP 2: OperationClassifier (unchanged, verify still works)
    # -----------------------------------------------------------------
    print("\n--- 2. OperationClassifier ---")

    from core.operation_classifier import get_classifier

    classifier = get_classifier()

    result = classifier.classify("show my unread emails")
    check("'show unread emails' -> SEARCH",
          "SEARCH" in result["operations"])

    result = classifier.classify("how many emails from John?")
    check("'how many emails' -> SEARCH + CALCULATE",
          set(result["operations"]) == {"SEARCH", "CALCULATE"})

    result = classifier.classify("send email to boss")
    check("'send email' -> ACTION",
          "ACTION" in result["operations"])

    result = classifier.classify("hello")
    check("'hello' -> CHAT",
          result["operations"] == ["CHAT"])

    # -----------------------------------------------------------------
    # TEST GROUP 3: Multi-agent detection
    # -----------------------------------------------------------------
    print("\n--- 3. Multi-Agent Detection ---")

    from core.workflow_graph import _detect_multi_agent

    targets = _detect_multi_agent("check gmail and slack")
    check("'gmail and slack' -> [GMAIL, SLACK]",
          set(targets) == {"GMAIL", "SLACK"})

    targets = _detect_multi_agent("check both email and calendar")
    check("'both email and calendar' -> [GMAIL, OUTLOOK]",
          set(targets) == {"GMAIL", "OUTLOOK"})

    targets = _detect_multi_agent("check my emails")
    check("'check my emails' -> [] (no conjunction)",
          targets == [])

    targets = _detect_multi_agent("gmail slack outlook")
    check("'gmail slack outlook' -> [] (no conjunction)",
          targets == [])

    targets = _detect_multi_agent(
        "show gmail and slack and outlook messages"
    )
    check("'gmail and slack and outlook' -> 3 platforms",
          len(targets) == 3)

    # -----------------------------------------------------------------
    # TEST GROUP 4: WorkflowState TypedDict
    # -----------------------------------------------------------------
    print("\n--- 4. WorkflowState ---")

    from core.workflow_state import WorkflowState

    # Verify it's a proper TypedDict with expected keys
    expected_keys = {
        "user_id", "query", "route", "routed_by", "operations",
        "pipeline", "is_multi_agent", "target_agents",
        "agent_results", "final_response", "error",
    }
    actual_keys = set(WorkflowState.__annotations__.keys())
    check("WorkflowState has all expected fields",
          expected_keys == actual_keys)

    # -----------------------------------------------------------------
    # TEST GROUP 5: Graph builds without error
    # -----------------------------------------------------------------
    print("\n--- 5. Graph Construction ---")

    from core.workflow_graph import build_workflow, get_workflow

    try:
        workflow = build_workflow()
        check("build_workflow() succeeds", workflow is not None)
    except Exception as e:
        check(f"build_workflow() succeeds (ERROR: {e})", False)

    w1 = get_workflow()
    w2 = get_workflow()
    check("get_workflow() returns singleton", w1 is w2)

    # -----------------------------------------------------------------
    # TEST GROUP 6: route_decision function
    # -----------------------------------------------------------------
    print("\n--- 6. Route Decision ---")

    from core.workflow_graph import route_decision

    # Single agent
    result = route_decision({"route": "GMAIL"})
    check("GMAIL -> ['execute_single_agent']",
          result == ["execute_single_agent"])

    result = route_decision({"route": "SLACK"})
    check("SLACK -> ['execute_single_agent']",
          result == ["execute_single_agent"])

    result = route_decision({"route": "OUTLOOK"})
    check("OUTLOOK -> ['execute_single_agent']",
          result == ["execute_single_agent"])

    # General
    result = route_decision({"route": "GENERAL"})
    check("GENERAL -> ['handle_general']",
          result == ["handle_general"])

    # Multi-agent
    result = route_decision({
        "route": "MULTI",
        "target_agents": ["GMAIL", "SLACK"],
    })
    check("MULTI [GMAIL, SLACK] -> parallel nodes",
          set(result) == {"execute_gmail", "execute_slack"})

    # -----------------------------------------------------------------
    # TEST GROUP 7: Full graph invocation with seeded cache data
    # -----------------------------------------------------------------
    print("\n--- 7. Full Graph Invocation (with cache) ---")

    from database.sqlite_manager import DatabaseManager
    from database.vector_store import VectorStore
    from tools.local_tools import LocalToolManager
    from services.sync_service import SyncService

    TEST_DB = os.path.join(_backend_dir, "test_phase4.db")
    TEST_CHROMA = os.path.join(_backend_dir, "test_phase4_chroma")

    # Clean previous test data
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

    # Seed test data
    emails = [
        {"id": "msg_001", "subject": "Invoice from vendor",
         "from": "billing@vendor.com", "body": "Total amount: $500"},
        {"id": "msg_002", "subject": "Team standup notes",
         "from": "john@company.com", "body": "Discussed deployment plan"},
        {"id": "msg_003", "subject": "Meeting tomorrow",
         "from": "boss@company.com", "body": "Let's meet at 3pm"},
    ]
    await lt.cache_results("test_user", "gmail", "email", emails)

    # Monkey-patch the singletons so graph nodes use our test data
    import tools.local_tools as lt_module
    import services.sync_service as sync_module

    original_lt = lt_module._local_tools_instance
    original_sync = sync_module._sync_instance
    lt_module._local_tools_instance = lt
    sync_module._sync_instance = sync_svc

    try:
        workflow = build_workflow()

        # Test: single-agent GMAIL query (uses cached data)
        state = {
            "user_id": "test_user",
            "query": "show my emails from gmail",
            "agent_results": [],
        }
        final_state = await workflow.ainvoke(state)

        check("Graph invocation completes",
              final_state is not None)
        check("final_response exists",
              "final_response" in final_state)

        resp = final_state.get("final_response", {})
        check("Response type is 'response'",
              resp.get("type") == "response")
        check("Route is GMAIL",
              resp.get("route") == "GMAIL")
        check("Agent is GmailAgent",
              "Gmail" in resp.get("agent", ""))
        check("Data is present",
              resp.get("data") is not None)
        check("Query is preserved",
              resp.get("query") == "show my emails from gmail")

        # Test: CHAT/GENERAL query
        state2 = {
            "user_id": "test_user",
            "query": "hello how are you",
            "agent_results": [],
        }
        final_state2 = await workflow.ainvoke(state2)
        resp2 = final_state2.get("final_response", {})
        check("Chat query produces response",
              resp2.get("type") == "response")

    finally:
        # Restore original singletons
        lt_module._local_tools_instance = original_lt
        sync_module._sync_instance = original_sync

    # -----------------------------------------------------------------
    # TEST GROUP 8: SupervisorAgent wrapper
    # -----------------------------------------------------------------
    print("\n--- 8. SupervisorAgent Wrapper ---")

    from agents.supervisor_agent import SupervisorAgent

    supervisor = SupervisorAgent(user_id="test_user")

    check("SupervisorAgent initializes", supervisor is not None)
    check("Has arun method", hasattr(supervisor, 'arun'))
    check("Has run method (backward compat)", hasattr(supervisor, 'run'))
    check("Has route_query method", hasattr(supervisor, 'route_query'))

    status = supervisor.status()
    check("Status shows 'langgraph' workflow",
          status.get("workflow") == "langgraph")

    # Test routing still works through supervisor
    route, method = supervisor.route_query("check my gmail")
    check("Supervisor.route_query('gmail') -> GMAIL",
          route == "GMAIL")

    # -----------------------------------------------------------------
    # TEST GROUP 9: Response format matches WebSocket expected shape
    # -----------------------------------------------------------------
    print("\n--- 9. Response Format ---")

    # Verify the response from graph has all expected WebSocket fields
    expected_fields = {"type", "route", "agent", "routed_by", "data", "query"}
    actual_fields = set(resp.keys())
    check("Response has all WebSocket fields",
          expected_fields.issubset(actual_fields))

    # -----------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------
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

    # -----------------------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------------------
    print()
    print("=" * 60)
    print(f"[RESULTS] {passed} passed, {failed} failed "
          f"out of {passed + failed} tests")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"WARNING: {failed} test(s) failed")


if __name__ == "__main__":
    asyncio.run(run_tests())
