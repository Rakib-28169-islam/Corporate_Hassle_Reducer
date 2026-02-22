"""Inline tests for BaseAgent — extracted from base_agent.py."""

import os
import sys
import shutil
import asyncio

# Ensure imports work from any location
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

sys.stdout.reconfigure(encoding='utf-8')

from agents.base_agent import BaseAgent

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


if __name__ == "__main__":
    asyncio.run(run_tests())
